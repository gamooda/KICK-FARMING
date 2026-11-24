import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QObject, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "engine"))

from TwitchChannelPointsMiner import TwitchChannelPointsMiner
from TwitchChannelPointsMiner.classes.Chat import ChatPresence
from TwitchChannelPointsMiner.classes.Settings import Priority
from TwitchChannelPointsMiner.classes.entities.Streamer import StreamerSettings
from TwitchChannelPointsMiner.classes.entities.Bet import BetSettings, Condition, DelayMode, FilterCondition, OutcomeKeys, Strategy
from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.logger import LoggerSettings
from TwitchChannelPointsMiner.utils import get_user_agent

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Twitch Farming"
CONFIG_FILE = APP_DIR / "config.json"
LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "TwitchFarming.log"
ACTIVATION_URL = "https://www.twitch.tv/activate"
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ICON_FILE = RESOURCE_DIR / "assets" / "twitch.ico"

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
OPTION_NAMES = [
    ("claim_drops", "Twitch Drops"),
    ("watch_streak", "Серии просмотров"),
    ("claim_moments", "Моменты"),
    ("follow_raid", "Рейды"),
    ("make_predictions", "Прогнозы"),
    ("chat", "IRC-чат"),
]
STRATEGIES = ["SMART", "MOST_VOTED", "HIGH_ODDS", "PERCENTAGE", "SMART_MONEY", "NUMBER_1", "NUMBER_2", "NUMBER_3", "NUMBER_4", "NUMBER_5", "NUMBER_6", "NUMBER_7", "NUMBER_8"]
FILTER_KEYS = ["TOTAL_USERS", "DECISION_USERS", "TOTAL_POINTS", "DECISION_POINTS", "ODDS", "ODDS_PERCENTAGE", "PERCENTAGE_USERS", "TOP_POINTS"]


class LogBus(QObject):
    message = Signal(str, str, float, object)


class QtLogHandler(logging.Handler):
    def __init__(self, bus):
        super().__init__(logging.INFO)
        self.bus = bus

    def emit(self, record):
        try:
            self.bus.message.emit(record.getMessage(), record.levelname, record.created, getattr(record, "event", None))
        except Exception:
            pass


class AuthWorker(QThread):
    result = Signal(str, object, bool, bool, str)

    def __init__(self, username, restoring, parent=None):
        super().__init__(parent)
        self.username = username
        self.restoring = restoring
        self.client = None

    def run(self):
        try:
            self.client = Twitch(self.username, get_user_agent("CHROME"))
            success = self.client.login()
            self.result.emit(self.username, self.client if success else None, success, self.restoring, "" if success else "Twitch не подтвердил авторизацию.")
        except Exception as error:
            self.result.emit(self.username, None, False, self.restoring, str(error))
            if self.client is not None:
                try:
                    self.client.twitch_login.session.close()
                except Exception:
                    pass


class EngineWorker(QThread):
    error = Signal(str)

    def __init__(self, username, channels, options, client, parent=None):
        super().__init__(parent)
        self.username = username
        self.channels = list(channels)
        self.options = dict(options)
        self.client = client
        self.miner = None
        self.stop_requested = False

    def request_stop(self):
        self.stop_requested = True
        if self.miner is not None:
            try:
                self.miner.request_stop()
            except Exception:
                pass

    def run(self):
        miner = None
        try:
            data = self.options.get("bet_settings", dict(DEFAULT_BET_SETTINGS))
            condition = None
            if data.get("filter_enabled"):
                condition = FilterCondition(
                    by=getattr(OutcomeKeys, data["filter_by"]),
                    where=getattr(Condition, data["filter_condition"]),
                    value=float(data["filter_value"]),
                )
            bet = BetSettings(
                strategy=getattr(Strategy, data["strategy"]),
                percentage=float(data["percentage"]),
                percentage_gap=float(data["percentage_gap"]),
                max_points=int(data["max_points"]),
                minimum_points=int(data["minimum_points"]),
                stealth_mode=bool(data["stealth_mode"]),
                filter_condition=condition,
                delay=float(data["delay"]),
                delay_mode=getattr(DelayMode, data["delay_mode"]),
            )
            settings = StreamerSettings(
                make_predictions=self.options["make_predictions"],
                follow_raid=self.options["follow_raid"],
                claim_drops=self.options["claim_drops"],
                claim_moments=self.options["claim_moments"],
                watch_streak=self.options["watch_streak"],
                community_goals=False,
                bet=bet,
                chat=ChatPresence.ONLINE if self.options["chat"] else ChatPresence.NEVER,
            )
            if self.client is None or not self.client.twitch_login.get_auth_token():
                raise RuntimeError("Авторизация Twitch недоступна. Подключи аккаунт заново.")
            miner = TwitchChannelPointsMiner(
                username=self.username,
                claim_drops_startup=False,
                enable_analytics=False,
                priority=[Priority.STREAK, Priority.DROPS, Priority.ORDER],
                logger_settings=LoggerSettings(save=False, less=True, console_level=logging.CRITICAL, file_level=logging.CRITICAL, emoji=False, colored=False),
                streamer_settings=settings,
                twitch_client=self.client,
                check_for_updates=False,
            )
            self.miner = miner
            self.client.running = True
            self.client.twitch_login.cancelled = False
            if self.stop_requested:
                miner.request_stop()
                return
            miner.mine(streamers=self.channels, followers=False)
        except BaseException as error:
            self.error.emit(str(error) or error.__class__.__name__)
        finally:
            if miner is not None:
                try:
                    miner.stop()
                except Exception as error:
                    self.error.emit(str(error))
            self.miner = None


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Twitch Farming")
        if ICON_FILE.is_file():
            self.setWindowIcon(QIcon(str(ICON_FILE)))
        self.resize(1120, 760)
        self.setMinimumSize(960, 660)

        self.bus = LogBus(self)
        self.bus.message.connect(self._on_log_message)
        self.log_handler = QtLogHandler(self.bus)
        self.root_logger = logging.getLogger()
        self.root_logger.setLevel(logging.INFO)
        self.root_logger.addHandler(self.log_handler)

        self.auth_worker = None
        self.engine_worker = None
        self.api_client = None
        self.authenticated = False
        self.authenticated_username = ""
        self.auth_opened = False
        self.auth_code = ""
        self.auth_url = ACTIVATION_URL
        self.channel_list = []
        self.online_channels = set()
        self.session_running = False
        self.bet_settings = dict(DEFAULT_BET_SETTINGS)
        self.custom_presets = {}
        self.selected_preset = "Оптимальный"
        self.log_records = []
        self.pending_log_lines = []
        self.gained_total = 0
        self.event_total = 0
        self._shutdown_started = False
        self._shutdown_complete = False

        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(self._hide_reward_banner)

        self._build_ui()
        self._load_config()
        self._sanitize_existing_log()
        self.log_timer = QTimer(self)
        self.log_timer.setInterval(400)
        self.log_timer.timeout.connect(self._flush_logs)
        self.log_timer.start()

        if self.username_edit.text().strip():
            QTimer.singleShot(350, self._restore_session)

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(214)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 24, 18, 18)
        side.setSpacing(10)
        brand = QLabel("TWITCH")
        brand.setObjectName("brand")
        side.addWidget(brand)
        sub = QLabel("FARMING")
        sub.setObjectName("brandSub")
        side.addWidget(sub)
        side.addSpacing(16)
        self.dashboard_nav = self._nav("◈  Панель управления", self.show_dashboard)
        self.logs_nav = self._nav("▤  Журнал событий", self.show_logs)
        side.addWidget(self.dashboard_nav)
        side.addWidget(self.logs_nav)
        side.addStretch(1)
        caption = QLabel("СОСТОЯНИЕ")
        caption.setObjectName("sideCaption")
        side.addWidget(caption)
        self.status_label = QLabel("●  НЕ ЗАПУЩЕН")
        self.status_label.setObjectName("sidebarStatus")
        self.status_label.setWordWrap(True)
        side.addWidget(self.status_label)
        side.addSpacing(16)
        version = QLabel("PySide6 · Windows")
        version.setObjectName("versionLabel")
        side.addWidget(version)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pageStack")
        self.dashboard_page = self._make_dashboard()
        self.logs_page = self._make_logs_page()
        self.channel_page = self._make_channel_page()
        self.bet_page = self._make_bet_page()
        for page in (self.dashboard_page, self.logs_page, self.channel_page, self.bet_page):
            self.pages.addWidget(page)
        outer.addWidget(sidebar)
        outer.addWidget(self.pages, 1)
        self.setStyleSheet(self._style_sheet())
        self.show_dashboard()

    def _nav(self, text, callback):
        button = QPushButton(text)
        button.setObjectName("navButton")
        button.setMinimumHeight(42)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    def _card(self, name="card"):
        frame = QFrame()
        frame.setObjectName(name)
        return frame

    def _caption(self, text):
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _make_dashboard(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(11)

        title = QLabel("Панель управления")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Просмотр трансляций и автоматический сбор наград")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        account = self._card()
        account_layout = QGridLayout(account)
        account_layout.setContentsMargins(14, 11, 14, 11)
        account_layout.addWidget(self._caption("АККАУНТ TWITCH"), 0, 0, 1, 3)
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("Логин Twitch")
        self.username_edit.setMinimumHeight(37)
        account_layout.addWidget(self.username_edit, 1, 0)
        self.auth_code_label = QLabel("Код авторизации: —")
        self.auth_code_label.setObjectName("authCode")
        account_layout.addWidget(self.auth_code_label, 1, 1)
        self.auth_button = QPushButton("Войти через Twitch")
        self.auth_button.setObjectName("primaryButton")
        self.auth_button.setMinimumHeight(37)
        self.auth_button.clicked.connect(self._login)
        account_layout.addWidget(self.auth_button, 1, 2)
        self.auth_status = QLabel("АККАУНТ НЕ ПОДКЛЮЧЁН")
        self.auth_status.setObjectName("mutedLabel")
        account_layout.addWidget(self.auth_status, 2, 0, 1, 3)
        layout.addWidget(account)

        middle = QHBoxLayout()
        middle.setSpacing(11)
        channels = self._card()
        channels_box = QVBoxLayout(channels)
        channels_box.setContentsMargins(12, 10, 12, 10)
        ch_head = QHBoxLayout()
        ch_head.addWidget(self._caption("КАНАЛЫ ДЛЯ ПРОСМОТРА"))
        ch_head.addStretch(1)
        self.add_channel_button = QPushButton("＋")
        self.add_channel_button.setObjectName("iconButton")
        self.add_channel_button.setFixedSize(32, 29)
        self.add_channel_button.clicked.connect(lambda: self.open_channel_editor("add"))
        ch_head.addWidget(self.add_channel_button)
        channels_box.addLayout(ch_head)
        self.channel_scroll = QScrollArea()
        self.channel_scroll.setWidgetResizable(True)
        self.channel_scroll.setFrameShape(QFrame.NoFrame)
        self.channel_body = QWidget()
        self.channel_layout = QVBoxLayout(self.channel_body)
        self.channel_layout.setContentsMargins(2, 2, 2, 2)
        self.channel_layout.setSpacing(5)
        self.channel_scroll.setWidget(self.channel_body)
        channels_box.addWidget(self.channel_scroll, 1)
        middle.addWidget(channels, 3)

        options = self._card()
        options_box = QVBoxLayout(options)
        options_box.setContentsMargins(13, 10, 13, 10)
        options_box.addWidget(self._caption("АВТОМАТИЗАЦИЯ"))
        self.options = {}
        for key, name in OPTION_NAMES:
            check = QCheckBox(name)
            check.setObjectName("optionCheck")
            self.options[key] = check
            options_box.addWidget(check)
        options_box.addStretch(1)
        self.bet_button = QPushButton("⚙  Прогнозы · Оптимальный")
        self.bet_button.setObjectName("secondaryButton")
        self.bet_button.clicked.connect(self.show_bet_settings)
        options_box.addWidget(self.bet_button)
        middle.addWidget(options, 2)
        layout.addLayout(middle, 3)

        actions = QHBoxLayout()
        self.start_button = QPushButton("▶  Запустить")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setMinimumHeight(37)
        self.start_button.clicked.connect(self._start)
        self.stop_button = QPushButton("■  Стоп")
        self.stop_button.setObjectName("secondaryButton")
        self.stop_button.setMinimumHeight(37)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        save = QPushButton("Сохранить")
        save.setObjectName("secondaryButton")
        save.clicked.connect(self._save_config)
        logs = QPushButton("▤  Логи")
        logs.setObjectName("secondaryButton")
        logs.clicked.connect(self.show_logs)
        folder = QPushButton("Папка")
        folder.setObjectName("secondaryButton")
        folder.clicked.connect(self._open_app_folder)
        for button in (self.start_button, self.stop_button, save):
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(logs)
        actions.addWidget(folder)
        layout.addLayout(actions)

        stats = QHBoxLayout()
        self.points_value = self._stat(stats, "ПОЛУЧЕНО ЗА СЕССИЮ", "0", "#B98AFF")
        self.online_value = self._stat(stats, "КАНАЛЫ В ЭФИРЕ", "0", "#42D6A4")
        self.events_value = self._stat(stats, "СОБЫТИЯ", "0", "#67B6FF")
        layout.addLayout(stats)

        self.reward_banner = QFrame()
        self.reward_banner.setObjectName("rewardBanner")
        reward_box = QVBoxLayout(self.reward_banner)
        reward_box.setContentsMargins(13, 7, 13, 7)
        reward_title = QLabel("НАГРАДА ПОЛУЧЕНА")
        reward_title.setObjectName("rewardTitle")
        self.reward_text = QLabel("")
        self.reward_text.setObjectName("rewardText")
        reward_box.addWidget(reward_title)
        reward_box.addWidget(self.reward_text)
        self.reward_banner.hide()
        layout.addWidget(self.reward_banner)

        activity = self._card()
        activity_box = QVBoxLayout(activity)
        activity_box.setContentsMargins(12, 9, 12, 9)
        top = QHBoxLayout()
        top.addWidget(self._caption("ПОСЛЕДНИЕ НАГРАДЫ И СОБЫТИЯ"))
        top.addStretch(1)
        open_journal = QPushButton("Открыть журнал")
        open_journal.setObjectName("smallButton")
        open_journal.clicked.connect(self.show_logs)
        top.addWidget(open_journal)
        activity_box.addLayout(top)
        self.events_view = QPlainTextEdit()
        self.events_view.setReadOnly(True)
        self.events_view.setMaximumBlockCount(90)
        self.events_view.setObjectName("eventBox")
        activity_box.addWidget(self.events_view, 1)
        layout.addWidget(activity, 2)
        return page

    def _stat(self, parent, title, value, accent):
        card = self._card("statCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(12, 8, 12, 8)
        name = QLabel(title)
        name.setObjectName("statTitle")
        number = QLabel(value)
        number.setObjectName("statValue")
        number.setStyleSheet(f"color:{accent};")
        box.addWidget(name)
        box.addWidget(number)
        parent.addWidget(card, 1)
        return number

    def _make_logs_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 18, 22, 16)
        header = QHBoxLayout()
        back = QPushButton("←  Назад")
        back.setObjectName("secondaryButton")
        back.clicked.connect(self.show_dashboard)
        title = QLabel("Журнал событий")
        title.setObjectName("pageTitle")
        header.addWidget(back)
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)
        note = QLabel("Секреты авторизации и IP-адреса скрываются автоматически.")
        note.setObjectName("pageSubtitle")
        layout.addWidget(note)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("logView")
        self.log_view.setMaximumBlockCount(2500)
        layout.addWidget(self.log_view, 1)
        return page

    def _make_channel_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 18, 22, 16)
        header = QHBoxLayout()
        back = QPushButton("←  Назад")
        back.setObjectName("secondaryButton")
        back.clicked.connect(self.show_dashboard)
        self.channel_title = QLabel("Добавить канал")
        self.channel_title.setObjectName("pageTitle")
        header.addWidget(back)
        header.addWidget(self.channel_title)
        header.addStretch(1)
        layout.addLayout(header)
        panel = self._card()
        box = QVBoxLayout(panel)
        box.setContentsMargins(18, 16, 18, 16)
        box.addWidget(self._caption("КАНАЛ TWITCH"))
        self.channel_entry = QLineEdit()
        self.channel_entry.setMinimumHeight(39)
        self.channel_entry.setPlaceholderText("Логин или twitch.tv/channel")
        self.channel_entry.returnPressed.connect(self._save_channel_editor)
        box.addWidget(self.channel_entry)
        hint = QLabel("Введи логин канала или ссылку Twitch.")
        hint.setObjectName("pageSubtitle")
        box.addWidget(hint)
        self.channel_feedback = QLabel("")
        self.channel_feedback.setObjectName("feedback")
        box.addWidget(self.channel_feedback)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.setObjectName("secondaryButton")
        cancel.clicked.connect(self.show_dashboard)
        save = QPushButton("Сохранить")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save_channel_editor)
        row.addWidget(cancel)
        row.addWidget(save)
        box.addLayout(row)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    def _make_bet_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 18, 22, 16)
        header = QHBoxLayout()
        back = QPushButton("←  Назад")
        back.setObjectName("secondaryButton")
        back.clicked.connect(self.show_dashboard)
        title = QLabel("Настройки прогнозов")
        title.setObjectName("pageTitle")
        header.addWidget(back)
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)
        panel = self._card()
        box = QVBoxLayout(panel)
        box.setContentsMargins(14, 12, 14, 12)
        box.addWidget(self._caption("СТРАТЕГИЯ АВТОМАТИЧЕСКИХ ПРОГНОЗОВ"))
        self.preset_combo = QComboBox()
        self.preset_combo.currentTextChanged.connect(self._fill_preset)
        box.addWidget(self.preset_combo)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.bet_fields = {}
        self._bet_combo(grid, 0, "strategy", "Стратегия выбора", STRATEGIES)
        self._bet_input(grid, 1, "percentage", "Процент баланса")
        self._bet_input(grid, 2, "percentage_gap", "Порог SMART, %")
        self._bet_input(grid, 3, "max_points", "Максимум очков за прогноз")
        self._bet_input(grid, 4, "minimum_points", "Минимальный баланс")
        self._bet_combo(grid, 5, "delay_mode", "Когда размещать прогноз", ["FROM_START", "FROM_END", "PERCENTAGE"])
        self._bet_input(grid, 6, "delay", "Задержка / доля таймера")
        box.addLayout(grid)
        self.filter_check = QCheckBox("Использовать числовой фильтр участия")
        self.filter_check.setObjectName("optionCheck")
        box.addWidget(self.filter_check)
        fg = QGridLayout()
        self._bet_combo(fg, 0, "filter_by", "Параметр", FILTER_KEYS)
        self._bet_combo(fg, 1, "filter_condition", "Условие", ["GT", "GTE", "LT", "LTE"])
        self._bet_input(fg, 2, "filter_value", "Значение")
        box.addLayout(fg)
        self.stealth_check = QCheckBox("Скрытная ставка")
        self.stealth_check.setObjectName("optionCheck")
        box.addWidget(self.stealth_check)
        self.bet_feedback = QLabel("Настройки применяются при следующем запуске сбора.")
        self.bet_feedback.setObjectName("pageSubtitle")
        box.addWidget(self.bet_feedback)
        layout.addWidget(panel)
        bottom = QHBoxLayout()
        self.preset_name = QLineEdit()
        self.preset_name.setPlaceholderText("Название пресета")
        bottom.addWidget(self.preset_name, 1)
        for title, object_name, method in (
            ("Загрузить", "secondaryButton", lambda: self._fill_preset(self.preset_combo.currentText())),
            ("Сохранить пресет", "secondaryButton", self._save_preset),
            ("Удалить", "dangerButton", self._delete_preset),
            ("Применить", "primaryButton", self._apply_bet),
        ):
            button = QPushButton(title)
            button.setObjectName(object_name)
            button.clicked.connect(method)
            bottom.addWidget(button)
        layout.addLayout(bottom)
        layout.addStretch(1)
        return page

    def _bet_input(self, grid, row, key, title):
        cell = QWidget()
        box = QVBoxLayout(cell)
        box.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setObjectName("fieldLabel")
        field = QLineEdit()
        field.setMinimumHeight(33)
        self.bet_fields[key] = field
        box.addWidget(label)
        box.addWidget(field)
        grid.addWidget(cell, row // 2, row % 2)

    def _bet_combo(self, grid, row, key, title, values):
        cell = QWidget()
        box = QVBoxLayout(cell)
        box.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setObjectName("fieldLabel")
        field = QComboBox()
        field.addItems(values)
        field.setMinimumHeight(33)
        self.bet_fields[key] = field
        box.addWidget(label)
        box.addWidget(field)
        grid.addWidget(cell, row // 2, row % 2)

    def _style_sheet(self):
        return """
        QWidget#root, QWidget#pageStack { background:#0B0F19; color:#E7EAF2; font-family:"Segoe UI"; font-size:10pt; }
        QFrame#sidebar { background:#101625; border:none; }
        QLabel#brand { color:#F4F3FA; font-size:25pt; font-weight:800; }
        QLabel#brandSub { color:#A970FF; font-size:11pt; font-weight:800; letter-spacing:2px; }
        QLabel#sideCaption, QLabel#sectionTitle, QLabel#statTitle, QLabel#fieldLabel { color:#9EACC3; font-size:8pt; font-weight:700; }
        QLabel#sidebarStatus { color:#B7C0D1; font-size:9pt; font-weight:700; }
        QLabel#versionLabel { color:#68758B; font-size:8pt; }
        QLabel#pageTitle { color:#F4F3FA; font-size:19pt; font-weight:750; }
        QLabel#pageSubtitle, QLabel#mutedLabel { color:#8792A8; font-size:8.5pt; }
        QLabel#authCode { color:#D7C2FF; font-weight:700; }
        QFrame#card, QFrame#statCard { background:#141C2B; border:1px solid #202B40; border-radius:13px; }
        QFrame#rewardBanner { background:#211A36; border:1px solid #9146FF; border-radius:10px; }
        QLabel#rewardTitle { color:#C7A6FF; font-size:8pt; font-weight:800; }
        QLabel#rewardText { color:#F4F3FA; font-size:10pt; font-weight:700; }
        QLabel#statValue { color:#F4F3FA; font-size:22pt; font-weight:800; }
        QPushButton { color:#E7EAF2; background:#202A3D; border:1px solid #2B3850; border-radius:8px; padding:7px 12px; font-weight:600; }
        QPushButton:hover { background:#303E58; }
        QPushButton:disabled { color:#667187; background:#192233; border-color:#202A3D; }
        QPushButton#primaryButton { background:#9146FF; color:white; border:1px solid #A36AFF; font-weight:800; }
        QPushButton#primaryButton:hover { background:#7B32E8; }
        QPushButton#secondaryButton, QPushButton#smallButton, QPushButton#iconButton, QPushButton#navButton { background:#1C2638; border:1px solid #2A3850; }
        QPushButton#smallButton { padding:4px 9px; font-size:8pt; }
        QPushButton#iconButton { background:#9146FF; font-size:16pt; padding:0; }
        QPushButton#dangerButton { background:#3A2636; border-color:#633247; color:#FF9AA8; }
        QPushButton#navButton { text-align:left; padding:10px; color:#AEB8CB; border:none; }
        QPushButton#navButton:hover, QPushButton#navButton[active="true"] { color:white; background:#27233C; border:1px solid #453765; }
        QLineEdit, QComboBox, QPlainTextEdit { color:#E7EAF2; background:#0D1421; border:1px solid #35425B; border-radius:7px; padding:7px 9px; selection-background-color:#9146FF; }
        QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border:1px solid #9146FF; }
        QComboBox::drop-down { border:none; width:24px; }
        QCheckBox#optionCheck { color:#DDE3EF; spacing:8px; padding:4px 0; }
        QCheckBox::indicator { width:16px; height:16px; }
        QCheckBox::indicator:unchecked { background:#0D1421; border:1px solid #46546D; border-radius:4px; }
        QCheckBox::indicator:checked { background:#9146FF; border:1px solid #B58AFF; border-radius:4px; }
        QScrollArea { background:#0D1421; border:none; }
        QScrollBar:vertical { background:#111A29; width:8px; margin:2px; }
        QScrollBar::handle:vertical { background:#394762; border-radius:4px; min-height:24px; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
        QPlainTextEdit#eventBox, QPlainTextEdit#logView { background:#0D1421; border:1px solid #26324A; border-radius:8px; font-family:"Consolas"; font-size:9pt; }
        QLabel#feedback { color:#E5B866; }
        """

    def _load_config(self):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        self.username_edit.setText(str(data.get("username", "")))
        channels = data.get("channels", [])
        if isinstance(channels, str):
            channels = re.split(r"[\n,;]+", channels)
        self.channel_list = list(dict.fromkeys(self._normalize_channel(x) for x in channels if self._normalize_channel(x)))
        settings = data.get("settings", {})
        for key, check in self.options.items():
            check.setChecked(bool(settings.get(key, key != "make_predictions")))
        self.bet_settings.update(data.get("bet_settings", {}))
        presets = data.get("custom_presets", {})
        self.custom_presets = presets if isinstance(presets, dict) else {}
        self.selected_preset = str(data.get("selected_preset", "Оптимальный"))
        self._render_channels()
        self._update_bet_button()
        self._fill_preset(self.selected_preset)

    def _config_data(self):
        return {
            "username": self.username_edit.text().strip(),
            "channels": self.channel_list,
            "settings": {key: check.isChecked() for key, check in self.options.items()},
            "bet_settings": dict(self.bet_settings),
            "custom_presets": self.custom_presets,
            "selected_preset": self.selected_preset,
        }

    def _save_config(self):
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(self._config_data(), ensure_ascii=False, indent=2), encoding="utf-8")
            self._append_event("Настройки сохранены")
        except OSError as error:
            self._append_event("Не удалось сохранить настройки · " + str(error)[:180])

    def _normalize_channel(self, value):
        value = str(value or "").strip()
        value = re.sub(r"^(?:https?://)?(?:www\.)?twitch\.tv/", "", value, flags=re.IGNORECASE)
        value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        value = value.lstrip("@").strip().lower()
        return value if re.fullmatch(r"[a-z0-9_]{1,25}", value) else ""

    def _render_channels(self):
        while self.channel_layout.count():
            item = self.channel_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if not self.channel_list:
            empty = QLabel("Нажми + и добавь канал по логину или ссылке Twitch")
            empty.setObjectName("pageSubtitle")
            self.channel_layout.addWidget(empty)
        for login in self.channel_list:
            card = QFrame()
            card.setStyleSheet("QFrame { background:#151E2E; border:1px solid #26334A; border-radius:9px; }")
            row = QHBoxLayout(card)
            row.setContentsMargins(10, 7, 8, 7)
            avatar = QLabel(login[:1].upper())
            avatar.setFixedSize(38, 38)
            avatar.setAlignment(Qt.AlignCenter)
            avatar.setStyleSheet("background:#2D2445; color:#EDE9F7; border-radius:19px; font-weight:800;")
            row.addWidget(avatar)
            labels = QVBoxLayout()
            name = QLabel(f"@{login}")
            detail = QLabel("● В ЭФИРЕ" if login in self.online_channels else "ОЖИДАЕТСЯ ПРОВЕРКА")
            detail.setStyleSheet("color:#42D6A4;" if login in self.online_channels else "color:#8792A8;")
            labels.addWidget(name)
            labels.addWidget(detail)
            row.addLayout(labels, 1)
            edit = QPushButton("✎")
            edit.setFixedSize(29, 29)
            edit.setEnabled(not self.session_running)
            edit.clicked.connect(lambda checked=False, channel=login: self.open_channel_editor("edit", channel))
            remove = QPushButton("×")
            remove.setObjectName("dangerButton")
            remove.setFixedSize(29, 29)
            remove.setEnabled(not self.session_running)
            remove.clicked.connect(lambda checked=False, channel=login: self._remove_channel(channel))
            row.addWidget(edit)
            row.addWidget(remove)
            self.channel_layout.addWidget(card)
        self.channel_layout.addStretch(1)
        self.add_channel_button.setEnabled(not self.session_running)

    def _set_channels_locked(self, locked):
        self.session_running = bool(locked)
        self._render_channels()

    def open_channel_editor(self, mode, old_login=None):
        if self.session_running:
            return
        self.editor_mode = mode
        self.editor_old_login = old_login
        self.channel_title.setText("Добавить канал" if mode == "add" else f"Изменить @{old_login}")
        self.channel_entry.setText(old_login or "")
        self.channel_feedback.clear()
        self.pages.setCurrentWidget(self.channel_page)
        self.channel_entry.setFocus()
        self.channel_entry.selectAll()

    def _save_channel_editor(self):
        login = self._normalize_channel(self.channel_entry.text())
        if not login:
            self.channel_feedback.setText("Укажи корректный логин или ссылку Twitch.")
            return
        if self.editor_mode == "add":
            if login in self.channel_list:
                self.channel_feedback.setText("Этот канал уже добавлен.")
                return
            self.channel_list.append(login)
        else:
            old = self.editor_old_login
            if login != old and login in self.channel_list:
                self.channel_feedback.setText("Этот канал уже есть в списке.")
                return
            if old not in self.channel_list:
                self.show_dashboard()
                return
            self.channel_list[self.channel_list.index(old)] = login
            self.online_channels.discard(old)
        self._render_channels()
        self._save_config()
        self.show_dashboard()

    def _remove_channel(self, login):
        if self.session_running:
            return
        if login in self.channel_list:
            self.channel_list.remove(login)
            self.online_channels.discard(login)
            self._render_channels()
            self._save_config()

    def _restore_session(self):
        username = self.username_edit.text().strip().lower()
        if username:
            self._begin_auth(username, True)

    def _login(self):
        username = self.username_edit.text().strip().lower()
        if not username:
            self._append_event("Укажи логин Twitch перед подключением.")
            return
        self._save_config()
        self._begin_auth(username, False)

    def _begin_auth(self, username, restoring):
        if self.auth_worker is not None and self.auth_worker.isRunning():
            return
        if self.engine_worker is not None and self.engine_worker.isRunning():
            self._append_event("Останови текущий сбор перед сменой аккаунта.")
            return
        self.auth_opened = False
        self.auth_code = ""
        self.auth_url = ACTIVATION_URL
        self.auth_code_label.setText("Код авторизации: подготовка…")
        self.auth_status.setText("ПРОВЕРКА СОХРАНЁННОЙ СЕССИИ…" if restoring else "ПОДКЛЮЧЕНИЕ К TWITCH…")
        self.auth_button.setEnabled(False)
        self.auth_button.setText("Проверка…" if restoring else "Подключение…")
        self.start_button.setEnabled(False)
        self._set_status("●  АВТОРИЗАЦИЯ", "#F0B65A")
        self.auth_worker = AuthWorker(username, restoring, self)
        self.auth_worker.result.connect(self._auth_result)
        self.auth_worker.start()

    def _auth_result(self, username, client, success, restored, error):
        self.auth_button.setEnabled(True)
        self.auth_button.setText("Подключено" if success else "Войти через Twitch")
        self.start_button.setEnabled(True)
        if success:
            self.api_client = client
            self.authenticated = True
            self.authenticated_username = username
            self.username_edit.setText(username)
            self.auth_status.setText(f"АККАУНТ ПОДКЛЮЧЁН · {username}")
            self.auth_status.setStyleSheet("color:#42D6A4; font-weight:700;")
            self.auth_code_label.setText("Сессия восстановлена" if restored else "Код авторизации: подтверждён")
            self._append_event(f"Сессия Twitch восстановлена · {username}" if restored else f"Аккаунт Twitch подключён · {username}")
            self._set_status("●  АККАУНТ ПОДКЛЮЧЁН", "#42D6A4")
            self._save_config()
        else:
            self.api_client = None
            self.authenticated = False
            self.authenticated_username = ""
            self.auth_status.setText("АВТОРИЗАЦИЯ НЕ УДАЛАСЬ")
            self.auth_status.setStyleSheet("color:#FF7B89; font-weight:700;")
            self._append_event("Вход не выполнен · " + str(error)[:220])
            self._set_status("●  ОШИБКА АВТОРИЗАЦИИ", "#FF6B78")

    def _start(self):
        username = self.username_edit.text().strip().lower()
        if not username:
            self._append_event("Укажи логин Twitch перед подключением.")
            return
        if self.auth_worker is not None and self.auth_worker.isRunning():
            self._append_event("Ожидается подтверждение Twitch.")
            return
        if not self.authenticated or self.authenticated_username != username or self.api_client is None:
            self._login()
            return
        if self.engine_worker is not None and self.engine_worker.isRunning():
            return
        if not self.channel_list:
            self._append_event("Добавь хотя бы один канал перед запуском.")
            return
        self._save_config()
        self._set_channels_locked(True)
        self.online_channels.clear()
        self.gained_total = 0
        self.event_total = 0
        self.points_value.setText("0")
        self.online_value.setText("0")
        self.events_value.setText("0")
        self.events_view.clear()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._set_status("●  ЗАПУСК", "#F0B65A")
        options = {key: check.isChecked() for key, check in self.options.items()}
        options["bet_settings"] = dict(self.bet_settings)
        self.engine_worker = EngineWorker(username, self.channel_list, options, self.api_client, self)
        self.engine_worker.error.connect(self._engine_error)
        self.engine_worker.finished.connect(self._engine_finished)
        self.engine_worker.start()

    def _stop(self):
        self.stop_button.setEnabled(False)
        self._set_status("●  ОСТАНОВКА", "#F0B65A")
        if self.engine_worker is not None:
            self.engine_worker.request_stop()

    def _engine_error(self, error):
        self._append_event("Ошибка · " + str(error).replace("\n", " ")[:220])
        self._set_status("●  ОШИБКА", "#FF6B78")

    def _engine_finished(self):
        self._set_channels_locked(False)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._set_status("●  НЕ ЗАПУЩЕН", "#A9B2C3")

    def _set_status(self, text, color="#A9B2C3"):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color:{color}; font-weight:700;")

    def _on_log_message(self, message, level, created, event):
        raw = str(message)
        self._append_log(raw, level, created)
        safe = raw if "and enter this code:" in raw else self._redact(raw)
        self._handle_log(safe, level, created)

    def _handle_log(self, message, level, created):
        if message.startswith("DEVICE_AUTH_URL:"):
            self.auth_url = message.split(":", 1)[1].strip()
            return
        if "and enter this code:" in message:
            code = message.split("and enter this code:", 1)[1].strip().split()[0]
            changed = code != self.auth_code
            self.auth_code = code
            self.auth_code_label.setText(f"Код авторизации: {code}")
            self.auth_status.setText("ОЖИДАЕТСЯ ПОДТВЕРЖДЕНИЕ В TWITCH")
            self._append_event(f"Код входа готов · {code}")
            if changed or not self.auth_opened:
                self.auth_opened = True
                url = self.auth_url
                if "device-code=" not in url:
                    url = f"{ACTIVATION_URL}?public=true&device-code={quote(code)}"
                QDesktopServices.openUrl(QUrl(url))
            return
        if "Twitch authorization confirmed." in message:
            self._set_status("●  ПОДГОТОВКА КАНАЛОВ", "#F0B65A")
            return
        progress = re.search(r"Loading channel (\d+)/(\d+): (.+)", message)
        if progress:
            current, total, channel = progress.groups()
            self._set_status(f"●  ЗАГРУЗКА КАНАЛОВ {current}/{total}", "#F0B65A")
            self._append_event(f"Загружаю канал · @{channel}")
            return
        if "Synchronizing Drops inventory and dashboard." in message:
            self._set_status("●  СИНХРОНИЗАЦИЯ DROPS", "#F0B65A")
            return
        if "Monitoring started for " in message:
            self._set_status("●  РАБОТАЕТ", "#42D6A4")
            self._append_event(message)
            return
        if "No valid Twitch channels were loaded." in message:
            self._set_status("●  НЕТ ДОСТУПНЫХ КАНАЛОВ", "#FF6B78")
            self._append_event(message)
            return
        if "Join IRC Chat:" in message or "Leave IRC Chat:" in message:
            return
        stamp = datetime.fromtimestamp(created).strftime("%H:%M:%S")
        online = re.search(r"^(.*?) is Online!$", message)
        offline = re.search(r"^(.*?) is Offline!$", message)
        if online:
            name = online.group(1).split(" (", 1)[0].strip().lstrip("@").lower()
            self.online_channels.add(name)
            self.online_value.setText(str(len(self.online_channels)))
            self._append_event(f"{stamp}  ●  {name} · В эфире")
            self._render_channels()
            self._set_status("●  РАБОТАЕТ", "#42D6A4")
            return
        if offline:
            name = offline.group(1).split(" (", 1)[0].strip().lstrip("@").lower()
            self.online_channels.discard(name)
            self.online_value.setText(str(len(self.online_channels)))
            self._append_event(f"{stamp}  ○  {name} · Не в эфире")
            self._render_channels()
            return
        gain = re.search(r"\+([\d,]+)\s*(?:→|-->)\s*(.*?)\s*-\s*Reason:\s*([A-Z_]+)", message)
        if gain:
            amount = int(gain.group(1).replace(",", ""))
            channel = gain.group(2).split(" (", 1)[0].strip()
            reason = gain.group(3)
            label = {"WATCH":"Просмотр","CLAIM":"Бонус","WATCH_STREAK":"Серия просмотров","RAID":"Рейд","PREDICTION":"Прогноз","REFUND":"Возврат"}.get(reason, reason)
            self.gained_total += amount
            self.event_total += 1
            self.points_value.setText(f"{self.gained_total:,}".replace(",", " "))
            self.events_value.setText(str(self.event_total))
            self._append_event(f"{stamp}  +{amount}  ·  {channel}  ·  {label}")
            self._reward(channel, amount, label)
            return
        if "Twitch Drop received:" in message:
            name = message.split("Twitch Drop received:", 1)[1].strip()[:100]
            self.event_total += 1
            self.events_value.setText(str(self.event_total))
            self._append_event(f"{stamp}  Twitch Drop · {name}")
            self._reward(self.authenticated_username or "Twitch", "Drop", name)
            return
        if "Twitch Moment claimed:" in message:
            name = message.split("Twitch Moment claimed:", 1)[1].strip()[:100]
            self._append_event(f"{stamp}  Момент · {name}")
            self._reward(self.authenticated_username or "Twitch", "✓", name)
            return
        if "Joining raid from" in message:
            self._append_event(f"{stamp}  Рейд · {message.split('Joining raid from', 1)[1].strip()[:140]}")
            return
        if "Place " in message and "channel points on:" in message:
            self._append_event(f"{stamp}  Прогноз · {message[:150]}")
            return
        if level in ("ERROR", "CRITICAL"):
            self._append_event(f"{stamp}  Ошибка · {message[:180]}")
        elif level == "WARNING" and any(word in message.lower() for word in ("failed", "unable", "invalid", "expired", "ошибка")):
            self._append_event(f"{stamp}  Предупреждение · {message[:180]}")

    def _reward(self, channel, amount, reward):
        if self.pages.currentWidget() is not self.dashboard_page:
            return
        self.reward_text.setText(f"@{channel}  ·  +{amount} очков  ·  {reward}")
        self.reward_banner.show()
        self.toast_timer.start(5000)

    def _hide_reward_banner(self):
        self.reward_banner.hide()

    def _append_event(self, text):
        self.events_view.appendPlainText(str(text))
        bar = self.events_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _redact(self, value):
        text = str(value).replace("\r", " ").replace("\n", " ")
        pairs = [
            (r"""(?i)(["']?(?:access[_-]?token|refresh[_-]?token|auth[_-]?token|auth-token|client[_-]?secret|device[_-]?code|user[_-]?code|authorization|token)["']?\s*[:=]\s*["'])(.*?)(["'])""", r"\1[REDACTED]\3"),
            (r"(?i)([?&](?:token|sig|auth_token|access_token|refresh_token|dna)=)[^&\s]+", r"\1[REDACTED]"),
            (r"(?i)(\b(?:access[_-]?token|refresh[_-]?token|auth[_-]?token|auth-token|client[_-]?secret|device[_-]?code|user[_-]?code|token)=)[^&,\s]+", r"\1[REDACTED]"),
            (r"(?i)(enter this code:\s*)[A-Z0-9-]+", r"\1[REDACTED]"),
            (r"""(?i)(["']user_ip["']\s*:\s*["'])[^"']+(["'])""", r"\1[REDACTED]\2"),
            (r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]"),
            (r"(?i)(\bOAuth\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]"),
        ]
        for pattern, replacement in pairs:
            text = re.sub(pattern, replacement, text)
        text = re.sub(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", "[IP REDACTED]", text)
        return text[:1200]

    def _sanitize_existing_log(self):
        try:
            if not LOG_FILE.is_file():
                return
            old = LOG_FILE.read_text(encoding="utf-8", errors="replace")
            clean = "\n".join(self._redact(line) for line in old.splitlines())
            if clean != old:
                LOG_FILE.write_text(clean + ("\n" if old.endswith("\n") else ""), encoding="utf-8")
        except OSError:
            pass

    def _append_log(self, message, level, created):
        stamp = datetime.fromtimestamp(created).strftime("%d.%m.%Y %H:%M:%S")
        line = f"{stamp}  [{level}]  {self._redact(message)}"
        self.log_records.append(line)
        if len(self.log_records) > 5000:
            self.log_records = self.log_records[-5000:]
        self.pending_log_lines.append(line)
        if self.pages.currentWidget() is self.logs_page:
            self.log_view.appendPlainText(line)

    def _flush_logs(self):
        if not self.pending_log_lines:
            return
        data = "\n".join(self.pending_log_lines) + "\n"
        self.pending_log_lines.clear()
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as file:
                file.write(data)
        except OSError:
            self.pending_log_lines.insert(0, data.rstrip("\n"))

    def show_dashboard(self):
        self.pages.setCurrentWidget(self.dashboard_page)
        self.dashboard_nav.setProperty("active", True)
        self.logs_nav.setProperty("active", False)
        for button in (self.dashboard_nav, self.logs_nav):
            button.style().unpolish(button)
            button.style().polish(button)

    def show_logs(self):
        self._flush_logs()
        try:
            if LOG_FILE.is_file():
                with LOG_FILE.open("rb") as file:
                    file.seek(0, os.SEEK_END)
                    size = file.tell()
                    file.seek(max(0, size - 20000))
                    content = file.read(20000).decode("utf-8", errors="replace")
                if size > 20000:
                    content = content.split("\n", 1)[-1]
            else:
                content = "\n".join(self.log_records[-300:])
        except OSError:
            content = "\n".join(self.log_records[-300:])
        self.log_view.setPlainText(content)
        self.pages.setCurrentWidget(self.logs_page)
        self.dashboard_nav.setProperty("active", False)
        self.logs_nav.setProperty("active", True)
        for button in (self.dashboard_nav, self.logs_nav):
            button.style().unpolish(button)
            button.style().polish(button)

    def show_bet_settings(self):
        self._fill_preset(self.selected_preset)
        self.pages.setCurrentWidget(self.bet_page)

    def _update_bet_button(self):
        self.bet_button.setText(f"⚙  Прогнозы · {self.selected_preset}")

    def _fill_preset(self, name):
        data = DEFAULT_BET_SETTINGS if name == "Оптимальный" else self.custom_presets.get(name)
        if not isinstance(data, dict):
            data = self.bet_settings
        for key, field in self.bet_fields.items():
            value = data.get(key, self.bet_settings.get(key, DEFAULT_BET_SETTINGS.get(key, "")))
            if isinstance(field, QComboBox):
                field.setCurrentText(str(value))
            else:
                field.setText(str(value))
        self.filter_check.setChecked(bool(data.get("filter_enabled", False)))
        self.stealth_check.setChecked(bool(data.get("stealth_mode", False)))
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItems(["Оптимальный"] + sorted(self.custom_presets))
        self.preset_combo.setCurrentText(name if name in ["Оптимальный"] + list(self.custom_presets) else "Оптимальный")
        self.preset_combo.blockSignals(False)

    def _read_bet_values(self):
        try:
            result = {key: (field.currentText().strip().upper() if isinstance(field, QComboBox) else field.text().strip()) for key, field in self.bet_fields.items()}
            result.update(
                percentage=float(result["percentage"]),
                percentage_gap=float(result["percentage_gap"]),
                max_points=int(result["max_points"]),
                minimum_points=int(result["minimum_points"]),
                delay=float(result["delay"]),
                filter_value=float(result["filter_value"]),
                filter_enabled=self.filter_check.isChecked(),
                stealth_mode=self.stealth_check.isChecked(),
            )
        except (TypeError, ValueError, KeyError):
            self.bet_feedback.setText("Проверь числовые поля.")
            return None
        if result["strategy"] not in STRATEGIES or result["delay_mode"] not in ("FROM_START", "FROM_END", "PERCENTAGE") or result["filter_by"] not in FILTER_KEYS or result["filter_condition"] not in ("GT", "GTE", "LT", "LTE"):
            self.bet_feedback.setText("Проверь стратегию и фильтр.")
            return None
        if not 0 <= result["percentage"] <= 100 or not 0 <= result["percentage_gap"] <= 100 or result["max_points"] < 0 or result["minimum_points"] < 0 or result["delay"] < 0:
            self.bet_feedback.setText("Проценты: 0–100. Очки и задержка не могут быть отрицательными.")
            return None
        if result["delay_mode"] == "PERCENTAGE" and result["delay"] > 1:
            self.bet_feedback.setText("Для PERCENTAGE введи значение от 0 до 1.")
            return None
        return result

    def _apply_bet(self):
        values = self._read_bet_values()
        if values is None:
            return
        self.bet_settings = values
        self.selected_preset = self.preset_combo.currentText()
        if self.selected_preset in self.custom_presets and self.selected_preset != "Оптимальный":
            self.custom_presets[self.selected_preset] = dict(values)
        self._update_bet_button()
        self._save_config()
        self.bet_feedback.setText("Параметры применены для следующего запуска.")

    def _save_preset(self):
        values = self._read_bet_values()
        if values is None:
            return
        name = self.preset_name.text().strip()
        if not name:
            self.bet_feedback.setText("Введи название пресета.")
            return
        if name == "Оптимальный":
            self.bet_feedback.setText("Название «Оптимальный» зарезервировано.")
            return
        self.custom_presets[name] = dict(values)
        self.bet_settings = dict(values)
        self.selected_preset = name
        self._fill_preset(name)
        self._update_bet_button()
        self._save_config()
        self.bet_feedback.setText(f"Пресет «{name}» сохранён.")

    def _delete_preset(self):
        name = self.preset_combo.currentText()
        if name == "Оптимальный" or name not in self.custom_presets:
            self.bet_feedback.setText("Выбери пользовательский пресет.")
            return
        self.custom_presets.pop(name, None)
        self.bet_settings = dict(DEFAULT_BET_SETTINGS)
        self.selected_preset = "Оптимальный"
        self._fill_preset("Оптимальный")
        self._update_bet_button()
        self._save_config()
        self.bet_feedback.setText("Пресет удалён.")

    def _open_app_folder(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(APP_DIR)))

    def closeEvent(self, event):
        if self._shutdown_complete:
            event.accept()
            return
        event.ignore()
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self.setEnabled(False)
        if self.auth_worker is not None and self.auth_worker.isRunning():
            try:
                if self.auth_worker.client is not None:
                    self.auth_worker.client.twitch_login.cancel()
            except Exception:
                pass
        if self.engine_worker is not None and self.engine_worker.isRunning():
            self.engine_worker.request_stop()
        QTimer.singleShot(250, self._check_shutdown)

    def _check_shutdown(self):
        workers = [self.auth_worker, self.engine_worker]
        if any(worker is not None and worker.isRunning() for worker in workers):
            QTimer.singleShot(250, self._check_shutdown)
            return
        self._flush_logs()
        try:
            self.root_logger.removeHandler(self.log_handler)
        except Exception:
            pass
        if self.api_client is not None:
            try:
                self.api_client.twitch_login.session.close()
            except Exception:
                pass
        self._shutdown_complete = True
        self.close()


def main():
    application = QApplication(sys.argv)
    application.setApplicationName("Twitch Farming")
    application.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(application.exec())


if __name__ == "__main__":
    main()
