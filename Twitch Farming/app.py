import json
import logging
import os
import queue
import re
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

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


APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Twitch Farming"
CONFIG_FILE = APP_DIR / "config.json"
ACTIVATION_URL = "https://www.twitch.tv/activate"


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
        ctk.CTkButton(account, text="Открыть Twitch", width=132, height=35, corner_radius=9, fg_color="#29213F", hover_color="#3B2C58", command=self._open_activation).grid(row=1, column=2, padx=(8, 17), pady=(0, 14))

        content_row = ctk.CTkFrame(main, fg_color="transparent")
        content_row.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        content_row.grid_columnconfigure(0, weight=3)
        content_row.grid_columnconfigure(1, weight=2)

        channels_card = ctk.CTkFrame(content_row, fg_color="#141C2B", corner_radius=15)
        channels_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        ctk.CTkLabel(channels_card, text="КАНАЛЫ ДЛЯ ПРОСМОТРА", font=("Segoe UI", 10, "bold"), text_color="#A9B4C9").pack(anchor="w", padx=16, pady=(13, 7))
        self.channels_box = ctk.CTkTextbox(channels_card, height=112, corner_radius=9, fg_color="#0D1421", border_width=1, border_color="#26324A", text_color="#E5E9F2", font=("Consolas", 12))
        self.channels_box.pack(fill="both", expand=True, padx=13, pady=(0, 13))

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
        self.channels_box.insert("1.0", "\n".join(data.get("channels", [])))
        for key, variable in self.option_vars.items():
            if key in data.get("settings", {}):
                variable.set(bool(data["settings"][key]))

    def _get_channels(self):
        text = self.channels_box.get("1.0", "end").strip()
        values = re.split(r"[\n,;]+", text)
        return list(dict.fromkeys(x.strip().lstrip("@").lower() for x in values if x.strip()))

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
        channels = self._get_channels()
        if not username:
            messagebox.showwarning("Twitch Farming", "Укажи логин Twitch.")
            return
        if not channels:
            messagebox.showwarning("Twitch Farming", "Добавь хотя бы один канал.")
            return
        self._save_config()
        self.stop_requested = False
        self.auth_opened = False
        self.auth_code = ""
        self.gained_total = 0
        self.event_total = 0
        self.online_channels.clear()
        self.points_value.configure(text="0")
        self.online_value.configure(text="0")
        self.events_value.configure(text="0")
        self.event_box.configure(state="normal")
        self.event_box.delete("1.0", "end")
        self.event_box.configure(state="disabled")
        self.auth_code_label.configure(text="Код авторизации: ожидание…")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._set_status("●  ПОДКЛЮЧЕНИЕ", "#F0B65A")
        settings = {key: variable.get() for key, variable in self.option_vars.items()}
        self.worker = threading.Thread(target=self._run_engine, args=(username, channels, settings), daemon=True)
        self.worker.start()

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
                elif item[0] == "error":
                    self._append_event("Ошибка · " + str(item[1]).replace("\n", " ")[:220])
                    self._set_status("●  ОШИБКА", "#FF6B78")
                elif item[0] == "finished":
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(120, self._process_events)

    def _handle_log(self, message, level, created, event):
        if "and enter this code:" in message:
            code = message.split("and enter this code:", 1)[1].strip().split()[0]
            self.auth_code = code
            self.auth_code_label.configure(text=f"Код авторизации: {code}")
            self._append_event(f"Код входа готов · {code}")
            if not self.auth_opened:
                self.auth_opened = True
                webbrowser.open(ACTIVATION_URL)
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
        if "Twitch session saved securely" in message or "Restored the saved Twitch session" in message:
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
        if self.miner is not None:
            self.miner.request_stop()
        self.destroy()


if __name__ == "__main__":
    app = FarmingApp()
    app.mainloop()
