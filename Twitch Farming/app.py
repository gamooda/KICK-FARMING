import json
import logging
import io
import os
import queue
import re
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "engine"))

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
from TwitchChannelPointsMiner import TwitchChannelPointsMiner
from TwitchChannelPointsMiner.classes.Chat import ChatPresence
from TwitchChannelPointsMiner.classes.Settings import Priority
from TwitchChannelPointsMiner.classes.entities.Streamer import StreamerSettings
from TwitchChannelPointsMiner.logger import LoggerSettings
from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.utils import get_user_agent


APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Twitch Farming"
CONFIG_FILE = APP_DIR / "config.json"
ACTIVATION_URL = "https://www.twitch.tv/activate"
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ICON_FILE = RESOURCE_DIR / "assets" / "twitch.ico"


class UIHandler(logging.Handler):
    def __init__(self, event_queue):
        super().__init__(logging.INFO)
        self.event_queue = event_queue

    def emit(self, record):
        try:
            self.event_queue.put(("log", record.getMessage(), record.levelname, record.created, getattr(record, "event", None)))
        except Exception:
            pass


class FarmingApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.title("Twitch Farming")
        try:
            self.iconbitmap(str(ICON_FILE))
        except Exception:
            pass
        self.geometry("1120x780")
        self.minsize(980, 700)
        self.configure(fg_color="#0B0F19")
        self.events = queue.Queue()
        self.miner = None
        self.worker = None
        self.log_handler = None
        self.stop_requested = False
        self.auth_opened = False
        self.auth_code = ""
        self.auth_url = ACTIVATION_URL
        self.authenticated = False
        self.authenticated_username = ""
        self.auth_thread = None
        self.auth_client = None
        self.restore_thread = None
        self.api_client = None
        self.channel_list = []
        self.channel_profiles = {}
        self.channel_images = {}
        self.gained_total = 0
        self.event_total = 0
        self.online_channels = set()
        self._build()
        self._load_config()
        self.after(120, self._process_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=208, corner_radius=0, fg_color="#101625")
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(sidebar, text="TWITCH", font=("Segoe UI", 24, "bold"), text_color="#F4F3FA").pack(anchor="w", padx=22, pady=(28, 0))
        ctk.CTkLabel(sidebar, text="FARMING", font=("Segoe UI", 13, "bold"), text_color="#A970FF").pack(anchor="w", padx=23, pady=(0, 32))
        ctk.CTkFrame(sidebar, height=1, fg_color="#242D40").pack(fill="x", padx=18, pady=(0, 22))
        ctk.CTkLabel(sidebar, text="РАБОЧЕЕ ПРОСТРАНСТВО", font=("Segoe UI", 9, "bold"), text_color="#768198").pack(anchor="w", padx=20, pady=(0, 12))
        ctk.CTkLabel(sidebar, text="◈  Панель управления", font=("Segoe UI", 13, "bold"), text_color="#EDE9F7", fg_color="#20243A", corner_radius=9, height=38, anchor="w").pack(fill="x", padx=12)
        ctk.CTkLabel(sidebar, text="◎  Автоматический сбор", font=("Segoe UI", 12), text_color="#929DB2", height=36, anchor="w").pack(fill="x", padx=20, pady=(5, 0))
        ctk.CTkFrame(sidebar, height=1, fg_color="#242D40").pack(fill="x", padx=18, pady=(24, 18))
        ctk.CTkLabel(sidebar, text="СОСТОЯНИЕ", font=("Segoe UI", 9, "bold"), text_color="#768198").pack(anchor="w", padx=20, pady=(0, 10))
        self.status_label = ctk.CTkLabel(sidebar, text="●  НЕ ЗАПУЩЕН", font=("Segoe UI", 11, "bold"), text_color="#A9B2C3", anchor="w")
        self.status_label.pack(fill="x", padx=20)
        ctk.CTkLabel(sidebar, text="v2  •  Windows", font=("Segoe UI", 10), text_color="#59647A").pack(side="bottom", anchor="w", padx=20, pady=20)

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=24, pady=20)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(5, weight=1)

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        ctk.CTkLabel(header, text="Панель управления", font=("Segoe UI", 25, "bold"), text_color="#F4F3FA").pack(anchor="w")
        ctk.CTkLabel(header, text="Управление просмотром и наградами Twitch", font=("Segoe UI", 11), text_color="#8792A8").pack(anchor="w", pady=(3, 0))

        account = ctk.CTkFrame(main, fg_color="#141C2B", corner_radius=15)
        account.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        account.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(account, text="АККАУНТ TWITCH", font=("Segoe UI", 10, "bold"), text_color="#A9B4C9").grid(row=0, column=0, columnspan=3, sticky="w", padx=17, pady=(13, 8))
        self.username_entry = ctk.CTkEntry(account, height=38, width=250, placeholder_text="Логин Twitch", fg_color="#0D1421", border_color="#2B3650")
        self.username_entry.grid(row=1, column=0, sticky="w", padx=(17, 10), pady=(0, 14))
        self.auth_code_label = ctk.CTkLabel(account, text="Код авторизации: —", font=("Segoe UI", 12, "bold"), text_color="#D8C7FF", anchor="w")
        self.auth_code_label.grid(row=1, column=1, sticky="w", padx=8, pady=(0, 14))
        self.auth_button = ctk.CTkButton(account, text="Войти через Twitch", width=150, height=35, corner_radius=9, fg_color="#9146FF", hover_color="#7B32E8", command=self._login)
        self.auth_button.grid(row=1, column=2, padx=(8, 17), pady=(0, 14))
        self.auth_status_label = ctk.CTkLabel(account, text="АККАУНТ НЕ ПОДКЛЮЧЁН", font=("Segoe UI", 9, "bold"), text_color="#8792A8", anchor="w")
        self.auth_status_label.grid(row=2, column=0, columnspan=3, sticky="w", padx=17, pady=(0, 12))

        content_row = ctk.CTkFrame(main, fg_color="transparent")
        content_row.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        content_row.grid_columnconfigure(0, weight=3)
        content_row.grid_columnconfigure(1, weight=2)

        channels_card = ctk.CTkFrame(content_row, fg_color="#141C2B", corner_radius=15)
        channels_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        channel_header = ctk.CTkFrame(channels_card, fg_color="transparent")
        channel_header.pack(fill="x", padx=15, pady=(11, 6))
        ctk.CTkLabel(channel_header, text="КАНАЛЫ ДЛЯ ПРОСМОТРА", font=("Segoe UI", 10, "bold"), text_color="#A9B4C9").pack(side="left")
        ctk.CTkButton(channel_header, text="+", width=31, height=28, corner_radius=8, fg_color="#9146FF", hover_color="#7B32E8", font=("Segoe UI", 18, "bold"), command=self._add_channel).pack(side="right")
        self.channel_list_frame = ctk.CTkScrollableFrame(channels_card, height=112, corner_radius=9, fg_color="#0D1421", scrollbar_button_color="#37425A", scrollbar_button_hover_color="#9146FF")
        self.channel_list_frame.pack(fill="both", expand=True, padx=13, pady=(0, 12))

        options = ctk.CTkFrame(content_row, fg_color="#141C2B", corner_radius=15)
        options.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        ctk.CTkLabel(options, text="АВТОМАТИЗАЦИЯ", font=("Segoe UI", 10, "bold"), text_color="#A9B4C9").pack(anchor="w", padx=16, pady=(13, 5))
        self.option_vars = {
            "claim_drops": tk.BooleanVar(value=True),
            "watch_streak": ctk.BooleanVar(value=True),
            "claim_moments": ctk.BooleanVar(value=True),
            "follow_raid": ctk.BooleanVar(value=True),
            "make_predictions": ctk.BooleanVar(value=False),
            "chat": ctk.BooleanVar(value=True),
        }
        option_names = [
            ("claim_drops", "Twitch Drops"),
            ("watch_streak", "Серии просмотров"),
            ("claim_moments", "Моменты"),
            ("follow_raid", "Рейды"),
            ("make_predictions", "Прогнозы"),
            ("chat", "IRC-чат"),
        ]
        grid = ctk.CTkFrame(options, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=13, pady=(0, 10))
        for column in (0, 1):
            grid.grid_columnconfigure(column, weight=1)
        for index, (key, name) in enumerate(option_names):
            ctk.CTkCheckBox(grid, text=name, variable=self.option_vars[key], font=("Segoe UI", 11), checkbox_width=17, checkbox_height=17, corner_radius=5, fg_color="#9146FF", hover_color="#A970FF").grid(row=index // 2, column=index % 2, sticky="w", padx=3, pady=7)

        actions = ctk.CTkFrame(main, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        actions.grid_columnconfigure(3, weight=1)
        self.start_button = ctk.CTkButton(actions, text="▶  Запустить", width=150, height=40, corner_radius=10, fg_color="#9146FF", hover_color="#7B32E8", font=("Segoe UI", 12, "bold"), command=self._start)
        self.start_button.grid(row=0, column=0, padx=(0, 8))
        self.stop_button = ctk.CTkButton(actions, text="■  Остановить", width=140, height=40, corner_radius=10, fg_color="#30384B", hover_color="#424E66", font=("Segoe UI", 12, "bold"), state="disabled", command=self._stop)
        self.stop_button.grid(row=0, column=1, padx=8)
        ctk.CTkButton(actions, text="Сохранить", width=110, height=40, corner_radius=10, fg_color="#1C2638", hover_color="#2A3850", command=self._save_config).grid(row=0, column=2, padx=8)

        stats = ctk.CTkFrame(main, fg_color="transparent")
        stats.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        for column in (0, 1, 2):
            stats.grid_columnconfigure(column, weight=1)
        self.points_value = self._stat_card(stats, 0, "ПОЛУЧЕНО ЗА СЕССИЮ", "0", "#A970FF")
        self.online_value = self._stat_card(stats, 1, "КАНАЛЫ В ЭФИРЕ", "0", "#42D6A4")
        self.events_value = self._stat_card(stats, 2, "СОБЫТИЯ", "0", "#67B6FF")

        events_card = ctk.CTkFrame(main, fg_color="#141C2B", corner_radius=15)
        events_card.grid(row=5, column=0, sticky="nsew")
        events_card.grid_rowconfigure(1, weight=1)
        events_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(events_card, text="ПОСЛЕДНИЕ СОБЫТИЯ", font=("Segoe UI", 10, "bold"), text_color="#A9B4C9").grid(row=0, column=0, sticky="w", padx=16, pady=(13, 7))
        self.event_box = ctk.CTkTextbox(events_card, corner_radius=9, fg_color="#0D1421", border_width=1, border_color="#26324A", text_color="#DDE4F1", font=("Consolas", 11), wrap="word")
        self.event_box.grid(row=1, column=0, sticky="nsew", padx=13, pady=(0, 13))
        self.event_box.configure(state="disabled")

    def _stat_card(self, parent, column, title, value, accent):
        card = ctk.CTkFrame(parent, fg_color="#141C2B", corner_radius=13)
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 6 if column < 2 else 0))
        ctk.CTkLabel(card, text=title, font=("Segoe UI", 9, "bold"), text_color="#8792A8").pack(anchor="w", padx=14, pady=(12, 2))
        label = ctk.CTkLabel(card, text=value, font=("Segoe UI", 22, "bold"), text_color=accent)
        label.pack(anchor="w", padx=14, pady=(0, 10))
        return label

    def _load_config(self):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        self.username_entry.insert(0, data.get("username", ""))
        saved_channels = data.get("channels", [])
        if isinstance(saved_channels, str):
            saved_channels = re.split(r"[\n,;]+", saved_channels)
        self.channel_list = list(dict.fromkeys(self._normalize_channel(x) for x in saved_channels if self._normalize_channel(x)))
        for key, variable in self.option_vars.items():
            if key in data.get("settings", {}):
                variable.set(bool(data["settings"][key]))
        self._render_channels()
        if self.username_entry.get().strip():
            self.after(350, self._restore_session)

    def _normalize_channel(self, value):
        value = str(value or "").strip()
        value = re.sub(r"^https?://(www\.)?twitch\.tv/", "", value, flags=re.IGNORECASE)
        value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        value = value.lstrip("@").strip().lower()
        return value if re.fullmatch(r"[a-z0-9_]{1,25}", value) else ""

    def _get_channels(self):
        return list(self.channel_list)

    def _render_channels(self):
        for child in self.channel_list_frame.winfo_children():
            child.destroy()
        if not self.channel_list:
            ctk.CTkLabel(self.channel_list_frame, text="Нажми + и добавь канал по логину или ссылке Twitch", font=("Segoe UI", 10), text_color="#758198").pack(anchor="w", padx=10, pady=12)
            return
        for login in self.channel_list:
            profile = self.channel_profiles.get(login, {})
            stream = profile.get("stream")
            display_name = profile.get("display_name") or login
            title = (stream or {}).get("title", "")
            if stream:
                status = "● В ЭФИРЕ"
                status_color = "#42D6A4"
            elif profile.get("error"):
                status = "КАНАЛ НЕ НАЙДЕН" if profile.get("error") == "Канал не найден" else "ОШИБКА ПРОВЕРКИ"
                status_color = "#FF7B89"
            elif profile.get("loaded"):
                status = "НЕ В ЭФИРЕ"
                status_color = "#8792A8"
            else:
                status = "ПРОВЕРКА…"
                status_color = "#F0B65A"
            card = ctk.CTkFrame(self.channel_list_frame, fg_color="#151E2E", corner_radius=9, height=50)
            card.pack(fill="x", padx=3, pady=3)
            card.pack_propagate(False)
            card.grid_columnconfigure(1, weight=1)
            card.grid_rowconfigure(0, weight=1)
            avatar = self.channel_images.get(login)
            avatar_label = ctk.CTkLabel(card, text="" if avatar else login[:1].upper(), image=avatar, width=36, height=36, corner_radius=18, fg_color="#2D2445", text_color="#EDE9F7", font=("Segoe UI", 13, "bold"))
            avatar_label.grid(row=0, column=0, padx=(8, 7), pady=6)
            text_box = ctk.CTkFrame(card, fg_color="transparent")
            text_box.grid(row=0, column=1, sticky="nsew", pady=4)
            text_box.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(text_box, text=f"{display_name}  ·  @{login}", font=("Segoe UI", 10, "bold"), text_color="#E8EAF2", anchor="w").grid(row=0, column=0, sticky="ew")
            details = f"● В ЭФИРЕ · {title[:38]}" if stream and title else status
            ctk.CTkLabel(text_box, text=details, font=("Segoe UI", 9), text_color=status_color, anchor="w").grid(row=1, column=0, sticky="ew")
            ctk.CTkButton(card, text="✎", width=27, height=27, corner_radius=7, fg_color="#26334A", hover_color="#3A4964", command=lambda name=login: self._edit_channel(name)).grid(row=0, column=2, padx=(3, 2))
            ctk.CTkButton(card, text="×", width=27, height=27, corner_radius=7, fg_color="#3A2636", hover_color="#633247", text_color="#FF9AA8", command=lambda name=login: self._remove_channel(name)).grid(row=0, column=3, padx=(2, 7))

    def _add_channel(self):
        dialog = ctk.CTkInputDialog(text="Введи логин канала или ссылку twitch.tv/канал", title="Добавить канал")
        value = dialog.get_input()
        login = self._normalize_channel(value)
        if not login:
            if value:
                messagebox.showwarning("Twitch Farming", "Нужен логин канала или ссылка Twitch, а не название трансляции.")
            return
        if login in self.channel_list:
            messagebox.showinfo("Twitch Farming", "Этот канал уже добавлен.")
            return
        self.channel_list.append(login)
        self._render_channels()
        self._save_config()
        self._refresh_channel_profiles([login])

    def _edit_channel(self, old_login):
        dialog = ctk.CTkInputDialog(text="Укажи новый логин или ссылку Twitch", title=f"Изменить @{old_login}")
        value = dialog.get_input()
        new_login = self._normalize_channel(value)
        if not new_login:
            return
        if new_login != old_login and new_login in self.channel_list:
            messagebox.showinfo("Twitch Farming", "Этот канал уже есть в списке.")
            return
        index = self.channel_list.index(old_login)
        self.channel_list[index] = new_login
        self.channel_profiles.pop(old_login, None)
        self.channel_images.pop(old_login, None)
        self._render_channels()
        self._save_config()
        self._refresh_channel_profiles([new_login])

    def _remove_channel(self, login):
        if login in self.channel_list:
            self.channel_list.remove(login)
            self.channel_profiles.pop(login, None)
            self.channel_images.pop(login, None)
            self._render_channels()
            self._save_config()

    def _refresh_channel_profiles(self, logins=None):
        if not self.authenticated or self.api_client is None:
            return
        targets = list(logins if logins is not None else self.channel_list)
        if not targets:
            return
        threading.Thread(target=self._channel_profiles_worker, args=(self.api_client, targets), daemon=True).start()

    def _channel_profiles_worker(self, client, logins):
        for login in logins:
            try:
                user = client.get_user_info(login)
                if not user:
                    self.events.put(("channel_profile", login, None, None, None))
                    continue
                stream = client.get_stream_by_login(login)
                image_data = None
                avatar_url = user.get("profile_image_url")
                if avatar_url:
                    response = requests.get(avatar_url, timeout=8)
                    if response.status_code == 200:
                        image_data = response.content
                self.events.put(("channel_profile", login, user, stream, image_data))
            except Exception as error:
                self.events.put(("channel_profile_error", login, str(error)))

    def _restore_session(self):
        username = self.username_entry.get().strip().lower()
        if not username or (self.restore_thread is not None and self.restore_thread.is_alive()):
            return
        self.auth_status_label.configure(text="ПРОВЕРКА СОХРАНЁННОЙ СЕССИИ…", text_color="#F0B65A")
        self.auth_button.configure(state="disabled", text="Проверка…")
        self.start_button.configure(state="disabled")
        self.restore_thread = threading.Thread(target=self._restore_session_worker, args=(username,), daemon=True)
        self.restore_thread.start()

    def _restore_session_worker(self, username):
        client = Twitch(username, get_user_agent("CHROME"))
        try:
            if client.restore_session():
                self.events.put(("auth_success", username, client, True))
            else:
                client.twitch_login.session.close()
                self.events.put(("session_missing", None))
        except Exception as error:
            try:
                client.twitch_login.session.close()
            except Exception:
                pass
            self.events.put(("session_missing", str(error)))
        finally:
            self.events.put(("auth_finished", None))

    def _config_data(self):
        return {
            "username": self.username_entry.get().strip(),
            "channels": self._get_channels(),
            "settings": {key: variable.get() for key, variable in self.option_vars.items()},
        }

    def _save_config(self):
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(self._config_data(), ensure_ascii=False, indent=2), encoding="utf-8")
            self._append_event("Настройки сохранены")
        except OSError as error:
            messagebox.showerror("Twitch Farming", f"Не удалось сохранить настройки:\n{error}")

    def _start(self):
        username = self.username_entry.get().strip().lower()
        if not username:
            messagebox.showwarning("Twitch Farming", "Укажи логин Twitch.")
            return
        if self.restore_thread is not None and self.restore_thread.is_alive():
            messagebox.showinfo("Twitch Farming", "Проверяется сохранённая сессия Twitch.")
            return
        if not self.authenticated or self.authenticated_username != username:
            self._login()
            return
        channels = self._get_channels()
        if not channels:
            messagebox.showwarning("Twitch Farming", "Добавь хотя бы один канал.")
            return
        self._save_config()
        self.stop_requested = False
        self.gained_total = 0
        self.event_total = 0
        self.online_channels.clear()
        self.points_value.configure(text="0")
        self.online_value.configure(text="0")
        self.events_value.configure(text="0")
        self.event_box.configure(state="normal")
        self.event_box.delete("1.0", "end")
        self.event_box.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._set_status("●  ЗАПУСК", "#F0B65A")
        settings = {key: variable.get() for key, variable in self.option_vars.items()}
        self.worker = threading.Thread(target=self._run_engine, args=(username, channels, settings), daemon=True)
        self.worker.start()

    def _login(self):
        username = self.username_entry.get().strip().lower()
        if not username:
            messagebox.showwarning("Twitch Farming", "Укажи логин Twitch.")
            return
        self._save_config()
        if self.auth_thread is not None and self.auth_thread.is_alive():
            return
        if self.worker is not None and self.worker.is_alive():
            messagebox.showwarning("Twitch Farming", "Останови текущий сбор перед сменой аккаунта.")
            return
        self.auth_opened = False
        self.auth_code = ""
        self.auth_url = ACTIVATION_URL
        self.auth_code_label.configure(text="Код авторизации: подготовка…")
        self.auth_status_label.configure(text="ПОДКЛЮЧЕНИЕ К TWITCH…", text_color="#F0B65A")
        self.auth_button.configure(state="disabled", text="Подключение…")
        self.start_button.configure(state="disabled")
        self._set_status("●  АВТОРИЗАЦИЯ", "#F0B65A")
        self.auth_thread = threading.Thread(target=self._login_worker, args=(username,), daemon=True)
        self.auth_thread.start()

    def _login_worker(self, username):
        handler = UIHandler(self.events)
        root_logger = logging.getLogger()
        previous_level = root_logger.level
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)
        client = None
        success = False
        try:
            client = Twitch(username, get_user_agent("CHROME"))
            self.auth_client = client
            success = client.login()
            if success:
                self.events.put(("auth_success", username, client, False))
            else:
                self.events.put(("auth_failure", "Twitch не подтвердил авторизацию. Запроси новый код и повтори вход."))
        except Exception as error:
            self.events.put(("auth_failure", str(error)))
        finally:
            root_logger.removeHandler(handler)
            root_logger.setLevel(previous_level)
            if client is not None and not success:
                try:
                    client.twitch_login.session.close()
                except Exception:
                    pass
            self.auth_client = None
            self.events.put(("auth_finished", None))

    def _run_engine(self, username, channels, options):
        miner = None
        handler = None
        try:
            streamer_settings = StreamerSettings(
                make_predictions=options["make_predictions"],
                follow_raid=options["follow_raid"],
                claim_drops=options["claim_drops"],
                claim_moments=options["claim_moments"],
                watch_streak=options["watch_streak"],
                community_goals=False,
                chat=ChatPresence.ONLINE if options["chat"] else ChatPresence.NEVER,
            )
            miner = TwitchChannelPointsMiner(
                username=username,
                claim_drops_startup=False,
                enable_analytics=False,
                priority=[Priority.STREAK, Priority.DROPS, Priority.ORDER],
                logger_settings=LoggerSettings(save=False, less=True, console_level=logging.CRITICAL, file_level=logging.CRITICAL, emoji=False, colored=False),
                streamer_settings=streamer_settings,
            )
            self.miner = miner
            handler = UIHandler(self.events)
            logging.getLogger().addHandler(handler)
            if self.stop_requested:
                miner.request_stop()
                return
            self.events.put(("status", "●  ОЖИДАНИЕ АВТОРИЗАЦИИ", "#F0B65A"))
            miner.mine(streamers=channels, followers=False)
        except SystemExit:
            pass
        except Exception as error:
            self.events.put(("error", str(error)))
        finally:
            if miner is not None:
                try:
                    miner.stop()
                except Exception as error:
                    self.events.put(("error", str(error)))
            if handler is not None:
                try:
                    logging.getLogger().removeHandler(handler)
                except Exception:
                    pass
            self.miner = None
            self.events.put(("status", "●  НЕ ЗАПУЩЕН", "#A9B2C3"))
            self.events.put(("finished", None, None))

    def _stop(self):
        self.stop_requested = True
        self.stop_button.configure(state="disabled")
        self._set_status("●  ОСТАНОВКА", "#F0B65A")
        miner = self.miner
        if miner is not None:
            miner.request_stop()

    def _process_events(self):
        try:
            while True:
                item = self.events.get_nowait()
                if item[0] == "log":
                    self._handle_log(item[1], item[2], item[3], item[4])
                elif item[0] == "status":
                    self._set_status(item[1], item[2])
                elif item[0] == "auth_success":
                    self.authenticated = True
                    self.authenticated_username = item[1]
                    self.api_client = item[2]
                    restored = bool(item[3])
                    self.auth_status_label.configure(text=f"АККАУНТ ПОДКЛЮЧЁН · {item[1]}", text_color="#42D6A4")
                    self.auth_button.configure(text="Подключено")
                    self.auth_code_label.configure(text="Сессия восстановлена" if restored else "Код авторизации: подтверждён")
                    self._append_event(f"Сессия Twitch восстановлена · {item[1]}" if restored else f"Аккаунт Twitch подключён · {item[1]}")
                    self._set_status("●  АККАУНТ ПОДКЛЮЧЁН", "#42D6A4")
                    self._refresh_channel_profiles()
                elif item[0] == "session_missing":
                    self.authenticated = False
                    self.authenticated_username = ""
                    self.auth_status_label.configure(text="АККАУНТ НЕ ПОДКЛЮЧЁН", text_color="#8792A8")
                    self.auth_button.configure(state="normal", text="Войти через Twitch")
                    self.start_button.configure(state="normal")
                    self._set_status("●  НЕ ЗАПУЩЕН", "#A9B2C3")
                    if item[1]:
                        self._append_event("Сессию не удалось восстановить · " + str(item[1])[:180])
                elif item[0] == "channel_profile":
                    login, user, stream, image_data = item[1], item[2], item[3], item[4]
                    if login in self.channel_list:
                        profile = {"loaded": True, "stream": stream}
                        if user:
                            profile["display_name"] = user.get("display_name") or login
                            profile["profile_image_url"] = user.get("profile_image_url")
                        else:
                            profile["error"] = "Канал не найден"
                        self.channel_profiles[login] = profile
                        if image_data:
                            try:
                                image = Image.open(io.BytesIO(image_data)).convert("RGBA")
                                image.thumbnail((72, 72), Image.Resampling.LANCZOS)
                                self.channel_images[login] = ctk.CTkImage(light_image=image, dark_image=image, size=(36, 36))
                            except Exception:
                                self.channel_images.pop(login, None)
                        self._render_channels()
                elif item[0] == "channel_profile_error":
                    login, error = item[1], item[2]
                    if login in self.channel_list:
                        self.channel_profiles[login] = {"loaded": True, "error": error}
                        self._render_channels()
                elif item[0] == "auth_failure":
                    self.authenticated = False
                    self.authenticated_username = ""
                    self.auth_status_label.configure(text="АВТОРИЗАЦИЯ НЕ УДАЛАСЬ", text_color="#FF6B78")
                    self._append_event("Вход не выполнен · " + str(item[1]).replace("\\n", " ")[:220])
                    self._set_status("●  ОШИБКА АВТОРИЗАЦИИ", "#FF6B78")
                elif item[0] == "auth_finished":
                    self.auth_button.configure(state="normal", text="Подключено" if self.authenticated else "Войти через Twitch")
                    self.start_button.configure(state="normal")
                elif item[0] == "error":
                    self._append_event("Ошибка · " + str(item[1]).replace("\\n", " ")[:220])
                    self._set_status("●  ОШИБКА", "#FF6B78")
                elif item[0] == "finished":
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(120, self._process_events)

    def _handle_log(self, message, level, created, event):
        if message.startswith("DEVICE_AUTH_URL:"):
            self.auth_url = message.split(":", 1)[1].strip()
            return
        if "and enter this code:" in message:
            code = message.split("and enter this code:", 1)[1].strip().split()[0]
            self.auth_code = code
            self.auth_code_label.configure(text=f"Код авторизации: {code}")
            self.auth_status_label.configure(text="ОЖИДАЕТСЯ ПОДТВЕРЖДЕНИЕ В TWITCH", text_color="#F0B65A")
            self._append_event(f"Код входа готов · {code}")
            if not self.auth_opened:
                self.auth_opened = True
                url = self.auth_url
                if "device-code=" not in url:
                    url = f"{ACTIVATION_URL}?public=true&device-code={quote(code)}"
                webbrowser.open(url)
            return
        if "Join IRC Chat:" in message or "Leave IRC Chat:" in message:
            return
        if any(text in message for text in ("Claiming the bonus", "Saving cookies", "Trying the TV login")):
            return
        stamp = datetime.fromtimestamp(created).strftime("%H:%M:%S")
        online = re.search(r"^(.*?) is Online!$", message)
        offline = re.search(r"^(.*?) is Offline!$", message)
        if online:
            name = online.group(1).split(" (", 1)[0]
            self.online_channels.add(name)
            self.online_value.configure(text=str(len(self.online_channels)))
            self._append_event(f"{stamp}  ●  {name} · В эфире")
            self._set_status("●  РАБОТАЕТ", "#42D6A4")
            return
        if offline:
            name = offline.group(1).split(" (", 1)[0]
            self.online_channels.discard(name)
            self.online_value.configure(text=str(len(self.online_channels)))
            self._append_event(f"{stamp}  ○  {name} · Не в эфире")
            return
        if "Twitch session saved securely" in message or "Restored the saved Twitch session" in message or "Restored the saved Twitch session with a refreshed token" in message:
            self.authenticated = True
            self.authenticated_username = self.username_entry.get().strip().lower()
            self.auth_status_label.configure(text=f"АККАУНТ ПОДКЛЮЧЁН · {self.authenticated_username}", text_color="#42D6A4")
            self.auth_button.configure(text="Подключено")
            self._append_event(f"{stamp}  Аккаунт подключён")
            self._set_status("●  АККАУНТ ПОДКЛЮЧЁН", "#42D6A4")
            return
        gain = re.search(r"\+([\d,]+)\s*→\s*(.*?)\s*-\s*Reason:\s*([A-Z_]+)", message)
        if gain:
            amount = int(gain.group(1).replace(",", ""))
            channel = gain.group(2).split(" (", 1)[0]
            reason = gain.group(3)
            labels = {
                "WATCH": "Просмотр",
                "CLAIM": "Бонус",
                "WATCH_STREAK": "Серия просмотров",
                "RAID": "Рейд",
                "PREDICTION": "Прогноз",
                "REFUND": "Возврат",
            }
            self.gained_total += amount
            self.event_total += 1
            self.points_value.configure(text=f"{self.gained_total:,}".replace(",", " "))
            self.events_value.configure(text=str(self.event_total))
            self._append_event(f"{stamp}  +{amount}  ·  {channel}  ·  {labels.get(reason, reason)}")
            return
        if "Joining raid from" in message:
            self._append_event(f"{stamp}  Рейд · {message.split('Joining raid from', 1)[1].strip()[:140]}")
            return
        if str(event) == "DROP_CLAIM":
            self._append_event(f"{stamp}  Twitch Drop · Награда получена")
            return
        if "Place " in message and "channel points on:" in message:
            self._append_event(f"{stamp}  Прогноз · {message[:150]}")
            return
        if level in ("ERROR", "CRITICAL"):
            self._append_event(f"{stamp}  Ошибка · {message.replace(chr(10), ' ')[:200]}")
        elif level == "WARNING" and any(word in message.lower() for word in ("failed", "unable", "invalid", "expired")):
            self._append_event(f"{stamp}  Предупреждение · {message[:180]}")

    def _append_event(self, text):
        self.event_box.configure(state="normal")
        self.event_box.insert("end", text + "\n")
        lines = int(self.event_box.index("end-1c").split(".")[0])
        if lines > 90:
            self.event_box.delete("1.0", f"{lines - 90}.0")
        self.event_box.see("end")
        self.event_box.configure(state="disabled")

    def _set_status(self, text, color):
        self.status_label.configure(text=text, text_color=color)

    def _open_activation(self):
        webbrowser.open(ACTIVATION_URL)

    def _close(self):
        self.stop_requested = True
        if self.auth_client is not None:
            try:
                self.auth_client.twitch_login.cancel()
            except Exception:
                pass
        if self.miner is not None:
            self.miner.request_stop()
        if self.api_client is not None:
            try:
                self.api_client.twitch_login.session.close()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    app = FarmingApp()
    app.mainloop()
