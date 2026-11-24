import os
import sys
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

from TwitchChannelPointsMiner.classes.entities.Bet import BetSettings, FilterCondition, Condition, OutcomeKeys, DelayMode, Strategy


APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Twitch Farming"
LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "TwitchFarming.log"
ICON_FILE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets" / "twitch.ico"


DEFAULT_BET_SETTINGS = {
    "strategy": "SMART",
    "percentage": 5,
    "percentage_gap": 20,
    "max_points": 50000,
    "minimum_points": 0,
    "stealth_mode": False,
    "delay": 6,
    "delay_mode": "FROM_END",
    "filter_enabled": False,
    "filter_by": "TOTAL_USERS",
    "filter_condition": "GT",
    "filter_value": 200,
}


class TwitchFarmingFeatures:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _input_dialog(self, title, placeholder, initial=""):
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("410x184")
        dialog.resizable(False, False)
        dialog.configure(fg_color="#0B0F19")
        dialog.transient(self)
        try:
            dialog.iconbitmap(str(ICON_FILE))
        except Exception:
            pass
        dialog.grab_set()
        panel = ctk.CTkFrame(dialog, fg_color="#141C2B", corner_radius=13)
        panel.pack(fill="both", expand=True, padx=12, pady=12)
        ctk.CTkLabel(panel, text=title, font=("Segoe UI", 15, "bold"), text_color="#F4F3FA").pack(anchor="w", padx=16, pady=(13, 7))
        entry = ctk.CTkEntry(panel, height=36, placeholder_text=placeholder, fg_color="#0D1421", border_color="#35425B")
        entry.pack(fill="x", padx=16)
        if initial:
            entry.insert(0, initial)
            entry.select_range(0, "end")
        result = {"value": None}
        buttons = ctk.CTkFrame(panel, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(12, 11))
        def accept():
            result["value"] = entry.get().strip()
            dialog.destroy()
        ctk.CTkButton(buttons, text="Отмена", width=90, height=31, fg_color="#293448", hover_color="#394861", command=dialog.destroy).pack(side="right", padx=(7, 0))
        ctk.CTkButton(buttons, text="Готово", width=100, height=31, fg_color="#9146FF", hover_color="#7B32E8", command=accept).pack(side="right")
        dialog.bind("<Return>", lambda event: accept())
        dialog.wait_window()
        return result["value"]

    def _set_channels_locked(self, locked):
        self.session_running = bool(locked)
        if getattr(self, "channel_add_button", None) is not None:
            self.channel_add_button.configure(state="disabled" if locked else "normal")
        self._render_channels()

    def _update_bet_button(self):
        if hasattr(self, "bet_settings_button"):
            self.bet_settings_button.configure(text=f"⚙  Прогнозы · {self.selected_preset}")

    def _open_bet_settings(self):
        window = ctk.CTkToplevel(self)
        window.title("Автоматизация прогнозов")
        window.geometry("650x640")
        window.minsize(600, 580)
        window.configure(fg_color="#0B0F19")
        window.transient(self)
        try:
            window.iconbitmap(str(ICON_FILE))
        except Exception:
            pass
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(window, fg_color="#141C2B", corner_radius=12)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        ctk.CTkLabel(header, text="НАСТРОЙКИ ПРОГНОЗОВ", font=("Segoe UI", 12, "bold"), text_color="#F0EAFB").pack(anchor="w", padx=14, pady=(11, 2))
        ctk.CTkLabel(header, text="Параметры применяются только к автоматическим прогнозам Twitch.", font=("Segoe UI", 9), text_color="#929DB2").pack(anchor="w", padx=14, pady=(0, 10))
        body = ctk.CTkScrollableFrame(window, fg_color="#141C2B", corner_radius=12)
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        body.grid_columnconfigure(1, weight=1)
        preset_values = ["Оптимальный"] + sorted(self.custom_presets.keys())
        preset_var = tk.StringVar(value=self.selected_preset if self.selected_preset in preset_values else "Оптимальный")
        ctk.CTkLabel(body, text="ПРЕСЕТ", font=("Segoe UI", 9, "bold"), text_color="#A9B4C9").grid(row=0, column=0, sticky="w", padx=13, pady=(13, 5))
        preset_combo = ctk.CTkComboBox(body, values=preset_values, variable=preset_var, height=32, width=230, fg_color="#0D1421", border_color="#35425B", button_color="#9146FF")
        preset_combo.grid(row=0, column=1, sticky="ew", padx=12, pady=(9, 5))
        variables = {
            "strategy": tk.StringVar(value=str(self.bet_settings.get("strategy", "SMART"))),
            "percentage": tk.StringVar(value=str(self.bet_settings.get("percentage", 5))),
            "percentage_gap": tk.StringVar(value=str(self.bet_settings.get("percentage_gap", 20))),
            "max_points": tk.StringVar(value=str(self.bet_settings.get("max_points", 50000))),
            "minimum_points": tk.StringVar(value=str(self.bet_settings.get("minimum_points", 0))),
            "delay": tk.StringVar(value=str(self.bet_settings.get("delay", 6))),
            "delay_mode": tk.StringVar(value=str(self.bet_settings.get("delay_mode", "FROM_END"))),
            "filter_by": tk.StringVar(value=str(self.bet_settings.get("filter_by", "TOTAL_USERS"))),
            "filter_condition": tk.StringVar(value=str(self.bet_settings.get("filter_condition", "GT"))),
            "filter_value": tk.StringVar(value=str(self.bet_settings.get("filter_value", 200))),
        }
        filter_enabled = tk.BooleanVar(value=bool(self.bet_settings.get("filter_enabled", False)))
        stealth_mode = tk.BooleanVar(value=bool(self.bet_settings.get("stealth_mode", False)))
        def row_label(row, title, hint=None):
            box = ctk.CTkFrame(body, fg_color="transparent")
            box.grid(row=row, column=0, sticky="w", padx=13, pady=5)
            ctk.CTkLabel(box, text=title, font=("Segoe UI", 10, "bold"), text_color="#E6E9F2").pack(anchor="w")
            if hint:
                ctk.CTkLabel(box, text=hint, font=("Segoe UI", 8), text_color="#8792A8").pack(anchor="w")
        def add_combo(row, key, title, values, hint=None):
            row_label(row, title, hint)
            combo = ctk.CTkComboBox(body, values=values, variable=variables[key], height=31, fg_color="#0D1421", border_color="#35425B", button_color="#9146FF")
            combo.grid(row=row, column=1, sticky="ew", padx=12, pady=5)
        def add_entry(row, key, title, hint=None):
            row_label(row, title, hint)
            entry = ctk.CTkEntry(body, textvariable=variables[key], height=31, fg_color="#0D1421", border_color="#35425B")
            entry.grid(row=row, column=1, sticky="ew", padx=12, pady=5)
        add_combo(1, "strategy", "Стратегия выбора", ["SMART", "MOST_VOTED", "HIGH_ODDS", "PERCENTAGE", "SMART_MONEY", "NUMBER_1", "NUMBER_2", "NUMBER_3", "NUMBER_4", "NUMBER_5", "NUMBER_6", "NUMBER_7", "NUMBER_8"], "SMART учитывает голоса и коэффициенты.")
        add_entry(2, "percentage", "Процент баланса", "Доля накопленных очков на прогноз, от 0 до 100.")
        add_entry(3, "percentage_gap", "Порог SMART, %", "Разница долей голосов для выбора большинства.")
        add_entry(4, "max_points", "Максимум очков за прогноз")
        add_entry(5, "minimum_points", "Минимальный баланс для участия")
        add_combo(6, "delay_mode", "Когда размещать прогноз", ["FROM_START", "FROM_END", "PERCENTAGE"], "FROM_END — за указанное число секунд до закрытия.")
        add_entry(7, "delay", "Задержка / доля таймера", "Для PERCENTAGE используй число от 0 до 1, например 0.2.")
        filter_box = ctk.CTkFrame(body, fg_color="#101827", corner_radius=10)
        filter_box.grid(row=8, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 5))
        ctk.CTkCheckBox(filter_box, text="Использовать числовой фильтр участия", variable=filter_enabled, font=("Segoe UI", 10, "bold"), checkbox_width=16, checkbox_height=16, fg_color="#9146FF", hover_color="#A970FF").pack(anchor="w", padx=11, pady=(10, 3))
        ctk.CTkLabel(filter_box, text="Ставка будет пропущена, если выбранное условие не выполнено.", font=("Segoe UI", 8), text_color="#8792A8").pack(anchor="w", padx=11, pady=(0, 5))
        filter_grid = ctk.CTkFrame(filter_box, fg_color="transparent")
        filter_grid.pack(fill="x", padx=8, pady=(0, 8))
        filter_grid.grid_columnconfigure((0, 1, 2), weight=1)
        def add_filter_combo(column, key, values, title):
            cell = ctk.CTkFrame(filter_grid, fg_color="transparent")
            cell.grid(row=0, column=column, sticky="ew", padx=3)
            ctk.CTkLabel(cell, text=title, font=("Segoe UI", 8), text_color="#A9B4C9").pack(anchor="w")
            combo = ctk.CTkComboBox(cell, values=values, variable=variables[key], height=29, fg_color="#0D1421", border_color="#35425B", button_color="#9146FF")
            combo.pack(fill="x", pady=(3, 0))
        add_filter_combo(0, "filter_by", ["TOTAL_USERS", "DECISION_USERS", "TOTAL_POINTS", "DECISION_POINTS", "ODDS", "ODDS_PERCENTAGE", "PERCENTAGE_USERS", "TOP_POINTS"], "Параметр")
        add_filter_combo(1, "filter_condition", ["GT", "GTE", "LT", "LTE"], "Условие")
        value_cell = ctk.CTkFrame(filter_grid, fg_color="transparent")
        value_cell.grid(row=0, column=2, sticky="ew", padx=3)
        ctk.CTkLabel(value_cell, text="Значение", font=("Segoe UI", 8), text_color="#A9B4C9").pack(anchor="w")
        ctk.CTkEntry(value_cell, textvariable=variables["filter_value"], height=29, fg_color="#0D1421", border_color="#35425B").pack(fill="x", pady=(3, 0))
        ctk.CTkCheckBox(body, text="Скрытная ставка", variable=stealth_mode, font=("Segoe UI", 9), checkbox_width=16, checkbox_height=16, fg_color="#9146FF", hover_color="#A970FF").grid(row=9, column=0, columnspan=2, sticky="w", padx=13, pady=(9, 9))
        foot = ctk.CTkFrame(window, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        def fill_from_preset():
            name = preset_var.get()
            preset = DEFAULT_BET_SETTINGS if name == "Оптимальный" else self.custom_presets.get(name)
            if not isinstance(preset, dict):
                return
            for key in variables:
                if key in preset:
                    variables[key].set(str(preset[key]))
            filter_enabled.set(bool(preset.get("filter_enabled", False)))
            stealth_mode.set(bool(preset.get("stealth_mode", False)))
        def read_values():
            try:
                result = {
                    "strategy": variables["strategy"].get().strip().upper(),
                    "percentage": float(variables["percentage"].get()),
                    "percentage_gap": float(variables["percentage_gap"].get()),
                    "max_points": int(variables["max_points"].get()),
                    "minimum_points": int(variables["minimum_points"].get()),
                    "delay": float(variables["delay"].get()),
                    "delay_mode": variables["delay_mode"].get().strip().upper(),
                    "filter_enabled": filter_enabled.get(),
                    "filter_by": variables["filter_by"].get().strip().upper(),
                    "filter_condition": variables["filter_condition"].get().strip().upper(),
                    "filter_value": float(variables["filter_value"].get()),
                    "stealth_mode": stealth_mode.get(),
                }
            except (TypeError, ValueError):
                messagebox.showerror("Прогнозы", "Проверь числовые поля: нужны корректные числа.", parent=window)
                return None
            strategies = ["SMART", "MOST_VOTED", "HIGH_ODDS", "PERCENTAGE", "SMART_MONEY", "NUMBER_1", "NUMBER_2", "NUMBER_3", "NUMBER_4", "NUMBER_5", "NUMBER_6", "NUMBER_7", "NUMBER_8"]
            filter_keys = ["TOTAL_USERS", "DECISION_USERS", "TOTAL_POINTS", "DECISION_POINTS", "ODDS", "ODDS_PERCENTAGE", "PERCENTAGE_USERS", "TOP_POINTS"]
            if result["strategy"] not in strategies or result["delay_mode"] not in ["FROM_START", "FROM_END", "PERCENTAGE"] or result["filter_by"] not in filter_keys or result["filter_condition"] not in ["GT", "GTE", "LT", "LTE"]:
                messagebox.showerror("Прогнозы", "Выбрана неизвестная стратегия, фильтр или режим задержки.", parent=window)
                return None
            if not 0 <= result["percentage"] <= 100 or not 0 <= result["percentage_gap"] <= 100 or result["max_points"] < 0 or result["minimum_points"] < 0 or result["delay"] < 0:
                messagebox.showerror("Прогнозы", "Проценты должны быть от 0 до 100, очки и задержка не могут быть отрицательными.", parent=window)
                return None
            if result["delay_mode"] == "PERCENTAGE" and result["delay"] > 1:
                messagebox.showerror("Прогнозы", "Для PERCENTAGE задержка должна быть от 0 до 1.", parent=window)
                return None
            return result
        def apply_values():
            values = read_values()
            if values is None:
                return
            self.bet_settings = values
            self.selected_preset = preset_var.get()
            if self.selected_preset in self.custom_presets and self.selected_preset != "Оптимальный":
                self.custom_presets[self.selected_preset] = dict(values)
            self._update_bet_button()
            self._save_config()
            window.destroy()
        def save_preset():
            values = read_values()
            if values is None:
                return
            name = self._input_dialog("Сохранить пресет", "Название пресета")
            if not name:
                return
            if name == "Оптимальный":
                messagebox.showwarning("Пресеты", "Это имя зарезервировано для стандартного пресета.", parent=window)
                return
            self.custom_presets[name] = dict(values)
            self.bet_settings = dict(values)
            self.selected_preset = name
            preset_combo.configure(values=["Оптимальный"] + sorted(self.custom_presets.keys()))
            preset_var.set(name)
            self._update_bet_button()
            self._save_config()
        def delete_preset():
            name = preset_var.get()
            if name == "Оптимальный" or name not in self.custom_presets:
                return
            self.custom_presets.pop(name, None)
            preset_combo.configure(values=["Оптимальный"] + sorted(self.custom_presets.keys()))
            preset_var.set("Оптимальный")
            fill_from_preset()
        ctk.CTkButton(foot, text="Загрузить", width=86, height=33, fg_color="#26334A", hover_color="#394861", command=fill_from_preset).pack(side="left")
        ctk.CTkButton(foot, text="Сохранить как…", width=112, height=33, fg_color="#26334A", hover_color="#394861", command=save_preset).pack(side="left", padx=6)
        ctk.CTkButton(foot, text="Удалить", width=72, height=33, fg_color="#3A2636", hover_color="#633247", command=delete_preset).pack(side="left")
        ctk.CTkButton(foot, text="Применить", width=104, height=33, fg_color="#9146FF", hover_color="#7B32E8", command=apply_values).pack(side="right")
        window.grab_set()

    def _open_logs(self):
        if self.log_window is not None and self.log_window.winfo_exists():
            self.log_window.focus_force()
            return
        window = ctk.CTkToplevel(self)
        self.log_window = window
        window.title("Журнал Twitch Farming")
        window.geometry("820x540")
        window.minsize(650, 420)
        window.configure(fg_color="#0B0F19")
        window.transient(self)
        try:
            window.iconbitmap(str(ICON_FILE))
        except Exception:
            pass
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(window, fg_color="#141C2B", corner_radius=11)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        ctk.CTkLabel(header, text="ПОЛНЫЙ ЖУРНАЛ СОБЫТИЙ", font=("Segoe UI", 11, "bold"), text_color="#F4F3FA").pack(side="left", padx=13, pady=10)
        ctk.CTkButton(header, text="Открыть файл", width=100, height=28, fg_color="#26334A", hover_color="#394861", command=self._open_log_file).pack(side="right", padx=10)
        textbox = ctk.CTkTextbox(window, corner_radius=9, fg_color="#0D1421", border_width=1, border_color="#26324A", font=("Consolas", 10), text_color="#DDE4F1", wrap="none")
        textbox.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.log_textbox = textbox
        try:
            stored = LOG_FILE.read_text(encoding="utf-8", errors="replace") if LOG_FILE.is_file() else ""
        except OSError:
            stored = ""
        textbox.insert("1.0", stored[-250000:] or "\n".join(self.log_records))
        textbox.configure(state="disabled")
        def close_logs():
            self.log_textbox = None
            self.log_window = None
            window.destroy()
        window.protocol("WM_DELETE_WINDOW", close_logs)

    def _open_log_file(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if not LOG_FILE.exists():
            LOG_FILE.touch()
        try:
            os.startfile(str(LOG_FILE))
        except Exception:
            webbrowser.open(str(LOG_FILE))

    def _open_app_folder(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(APP_DIR))
        except Exception:
            webbrowser.open(str(APP_DIR))

    def _append_log_record(self, message, level, created):
        stamp = datetime.fromtimestamp(created).strftime("%d.%m.%Y %H:%M:%S")
        line = f"{stamp}  [{level}]  {str(message).replace(chr(10), ' ')[:1200]}"
        self.log_records.append(line)
        if len(self.log_records) > 5000:
            self.log_records = self.log_records[-5000:]
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
        except OSError:
            pass
        if self.log_textbox is not None:
            try:
                self.log_textbox.configure(state="normal")
                self.log_textbox.insert("end", line + "\n")
                self.log_textbox.see("end")
                self.log_textbox.configure(state="disabled")
            except Exception:
                self.log_textbox = None

    def _show_intro(self):
        if self.intro_frame is not None and self.intro_frame.winfo_exists():
            return
        self.intro_frame = ctk.CTkFrame(self, fg_color="#0B0F19", corner_radius=0)
        self.intro_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        center = ctk.CTkFrame(self.intro_frame, fg_color="#141C2B", corner_radius=20, border_width=1, border_color="#27334B")
        center.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.68, relheight=0.58)
        ctk.CTkLabel(center, text="TWITCH", font=("Segoe UI", 30, "bold"), text_color="#FFFFFF").pack(pady=(34, 0))
        ctk.CTkLabel(center, text="FARMING", font=("Segoe UI", 16, "bold"), text_color="#A970FF").pack(pady=(0, 15))
        ctk.CTkLabel(center, text="Подключи аккаунт Twitch", font=("Segoe UI", 18, "bold"), text_color="#F4F3FA").pack(pady=(0, 5))
        ctk.CTkLabel(center, text="Авторизация выполняется через официальный сайт Twitch.\nПароль вводить в приложении не нужно.", font=("Segoe UI", 10), text_color="#929DB2", justify="center").pack(pady=(0, 18))
        self.intro_username_entry = ctk.CTkEntry(center, width=300, height=39, placeholder_text="Логин Twitch", fg_color="#0D1421", border_color="#35425B")
        self.intro_username_entry.pack()
        saved_username = self.username_entry.get().strip()
        if saved_username:
            self.intro_username_entry.insert(0, saved_username)
        self.intro_status_label = ctk.CTkLabel(center, text="Подключение сохранит сессию на этом компьютере.", font=("Segoe UI", 9), text_color="#8792A8")
        self.intro_status_label.pack(pady=(12, 10))
        ctk.CTkButton(center, text="Подключить Twitch", width=210, height=40, corner_radius=10, fg_color="#9146FF", hover_color="#7B32E8", font=("Segoe UI", 12, "bold"), command=self._intro_login).pack(pady=(0, 24))

    def _intro_login(self):
        username = self.intro_username_entry.get().strip().lower() if self.intro_username_entry else ""
        if not username:
            self.intro_status_label.configure(text="Сначала введи логин Twitch.", text_color="#FF7B89")
            return
        self._login(username)

    def _set_intro_status(self, text, color="#F0B65A"):
        if self.intro_status_label is not None and self.intro_status_label.winfo_exists():
            self.intro_status_label.configure(text=text, text_color=color)

    def _complete_onboarding(self):
        if self.intro_frame is not None and self.intro_frame.winfo_exists():
            self.intro_frame.destroy()
        self.intro_frame = None
        self.intro_username_entry = None
        self.intro_status_label = None
        if not self.tutorial_done and self.tutorial_frame is None:
            self.after(350, self._show_tutorial)

    def _show_tutorial(self):
        if self.tutorial_frame is not None and self.tutorial_frame.winfo_exists():
            return
        self.tutorial_step = 0
        self.tutorial_frame = ctk.CTkFrame(self, fg_color="#141C2B", corner_radius=14, border_width=2, border_color="#9146FF")
        self.tutorial_frame.place(relx=0.5, rely=0.53, anchor="center", relwidth=0.43, relheight=0.27)
        self.tutorial_title = ctk.CTkLabel(self.tutorial_frame, text="", font=("Segoe UI", 15, "bold"), text_color="#F4F3FA")
        self.tutorial_title.pack(anchor="w", padx=16, pady=(14, 5))
        self.tutorial_text = ctk.CTkLabel(self.tutorial_frame, text="", font=("Segoe UI", 10), text_color="#B6C0D2", justify="left", wraplength=390)
        self.tutorial_text.pack(anchor="w", padx=16, pady=(0, 8))
        foot = ctk.CTkFrame(self.tutorial_frame, fg_color="transparent")
        foot.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkButton(foot, text="Пропустить", width=88, height=30, fg_color="#26334A", hover_color="#394861", command=self._finish_tutorial).pack(side="left")
        self.tutorial_next_button = ctk.CTkButton(foot, text="Далее", width=92, height=30, fg_color="#9146FF", hover_color="#7B32E8", command=self._next_tutorial)
        self.tutorial_next_button.pack(side="right")
        self._render_tutorial_step()

    def _render_tutorial_step(self):
        steps = [
            ("1 / 3 · Каналы", "Нажми + и добавь логины или ссылки каналов Twitch. Аватары, название трансляции и статус загрузятся автоматически.", self.channel_list_frame),
            ("2 / 3 · Автоматизация", "Здесь включаются Drops, серии просмотров, моменты, рейды и чат. В настройках прогнозов можно задать числовые фильтры и свои пресеты.", self.bet_settings_button),
            ("3 / 3 · Запуск и журнал", "Нажми «Запустить». Награды появятся в ленте и всплывающих уведомлениях. Кнопка «Логи» открывает полный технический журнал.", self.start_button),
        ]
        title, description, target = steps[self.tutorial_step]
        if self.tutorial_target is not None:
            try:
                self.tutorial_target.configure(border_width=0)
            except Exception:
                pass
        self.tutorial_target = target
        try:
            target.configure(border_width=2, border_color="#A970FF")
        except Exception:
            pass
        self.tutorial_title.configure(text=title)
        self.tutorial_text.configure(text=description)
        self.tutorial_next_button.configure(text="Готово" if self.tutorial_step == 2 else "Далее")

    def _next_tutorial(self):
        if self.tutorial_step >= 2:
            self._finish_tutorial()
            return
        self.tutorial_step += 1
        self._render_tutorial_step()

    def _finish_tutorial(self):
        if self.tutorial_target is not None:
            try:
                self.tutorial_target.configure(border_width=0)
            except Exception:
                pass
        self.tutorial_target = None
        if self.tutorial_frame is not None and self.tutorial_frame.winfo_exists():
            self.tutorial_frame.destroy()
        self.tutorial_frame = None
        self.tutorial_done = True
        self._save_config()

    def _show_reward_toast(self, channel, amount, reward):
        self.toast_queue.append((str(channel), str(amount), str(reward)))
        if self.toast_active is None:
            self._next_reward_toast()

    def _next_reward_toast(self):
        if not self.toast_queue:
            self.toast_active = None
            return
        channel, amount, reward = self.toast_queue.pop(0)
        toast = ctk.CTkToplevel(self)
        self.toast_active = toast
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        try:
            toast.iconbitmap(str(ICON_FILE))
            toast.attributes("-alpha", 0.0)
        except Exception:
            pass
        width = 330
        height = 88
        self.update_idletasks()
        x = self.winfo_rootx() + max(10, self.winfo_width() - width - 18)
        y = self.winfo_rooty() + 48
        toast.geometry(f"{width}x{height}+{x}+{y}")
        panel = ctk.CTkFrame(toast, fg_color="#171F31", corner_radius=13, border_width=2, border_color="#9146FF")
        panel.pack(fill="both", expand=True)
        ctk.CTkLabel(panel, text="НАГРАДА ПОЛУЧЕНА", font=("Segoe UI", 9, "bold"), text_color="#B98AFF").pack(anchor="w", padx=14, pady=(10, 1))
        ctk.CTkLabel(panel, text=f"@{channel}  ·  +{amount} очков", font=("Segoe UI", 12, "bold"), text_color="#F4F3FA").pack(anchor="w", padx=14)
        ctk.CTkLabel(panel, text=reward, font=("Segoe UI", 9), text_color="#A9B4C9").pack(anchor="w", padx=14, pady=(0, 8))
        self._fade_toast_in(toast, 0)

    def _fade_toast_in(self, toast, step):
        if not toast.winfo_exists():
            return
        try:
            toast.attributes("-alpha", min(1.0, (step + 1) / 7))
        except Exception:
            pass
        if step < 6:
            self.after(32, lambda: self._fade_toast_in(toast, step + 1))
        else:
            self.after(5000, lambda: self._fade_toast_out(toast, 0))

    def _fade_toast_out(self, toast, step):
        if not toast.winfo_exists():
            return
        try:
            toast.attributes("-alpha", max(0.0, 1.0 - (step + 1) / 8))
        except Exception:
            pass
        if step < 7:
            self.after(38, lambda: self._fade_toast_out(toast, step + 1))
        else:
            toast.destroy()
            self.toast_active = None
            self._next_reward_toast()
