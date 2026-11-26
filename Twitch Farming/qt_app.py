import hashlib
import json
import random
import re
import sys
import time
import urllib.request
import weakref
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QEasingCurve, QPoint, QPropertyAnimation, Property, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal, QObject, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QPainter, QPainterPath, QPen, QPixmap, QIcon
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QStackedWidget, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget

from backend import APP_DIR, APP_VERSION, DEFAULT_APP_SETTINGS, DEFAULT_BET_SETTINGS, DEFAULT_OPTIONS, FILTER_KEYS, ICON_FILE, OPTION_NAMES, STRATEGIES, TwitchFarmingApi


APP_TITLE = "Twitch Farming"
REPOSITORY_URL = "https://github.com/gamooda/KICK-FARMING"


class TaskSignals(QObject):
    finished = Signal(str, object, str)


class ApiTask(QRunnable):
    def __init__(self, token, function, args=()):
        super().__init__()
        self.token = token
        self.function = function
        self.args = args
        self.signals = TaskSignals()

    def run(self):
        try:
            result = self.function(*self.args)
            self.signals.finished.emit(self.token, result, "")
        except BaseException as error:
            self.signals.finished.emit(self.token, None, str(error) or error.__class__.__name__)


class ImageSignals(QObject):
    finished = Signal(str, bytes, str)


class ImageTask(QRunnable):
    def __init__(self, url, cache_path):
        super().__init__()
        self.url = url
        self.cache_path = cache_path
        self.signals = ImageSignals()

    def run(self):
        try:
            if self.cache_path.is_file():
                data = self.cache_path.read_bytes()
            else:
                request = urllib.request.Request(self.url, headers={"User-Agent": "Twitch-Farming/Qt"})
                with urllib.request.urlopen(request, timeout=12) as response:
                    data = response.read(4 * 1024 * 1024 + 1)
                if not data or len(data) > 4 * 1024 * 1024:
                    raise RuntimeError("Изображение пустое или слишком большое.")
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_bytes(data)
            self.signals.finished.emit(self.url, data, "")
        except Exception as error:
            self.signals.finished.emit(self.url, b"", str(error))


class ImageCache(QObject):
    def __init__(self, pool, parent=None):
        super().__init__(parent)
        self.pool = pool
        self.data = {}
        self.pending = set()
        self.waiting = {}

    def request(self, label, url, circular=False):
        value = str(url or "")
        if not value.startswith("https://"):
            return
        label.setProperty("imageUrl", value)
        if value in self.data:
            self._set_label(label, self.data[value], circular)
            return
        self.waiting.setdefault(value, []).append((weakref.ref(label), circular))
        if value in self.pending:
            return
        self.pending.add(value)
        suffix = Path(value.split("?", 1)[0]).suffix.lower()
        if suffix not in (".png", ".webp", ".jpg", ".jpeg", ".svg", ".gif"):
            suffix = ".img"
        key = hashlib.sha256(value.encode("utf-8")).hexdigest()
        folder = "avatars" if circular else "currency"
        cache_path = APP_DIR / "image_cache" / folder / (key + suffix)
        task = ImageTask(value, cache_path)
        task.signals.finished.connect(self._image_ready)
        self.pool.start(task)

    def _image_ready(self, url, data, error):
        self.pending.discard(url)
        if data:
            self.data[url] = data
        targets = self.waiting.pop(url, [])
        for reference, circular in targets:
            label = reference()
            if label is not None and label.property("imageUrl") == url and data:
                self._set_label(label, data, circular)

    def _set_label(self, label, data, circular):
        pixmap = QPixmap()
        if data.lstrip().startswith(b"<svg") or b"<svg" in data[:500]:
            renderer = QSvgRenderer(QByteArray(data))
            if renderer.isValid():
                pixmap = QPixmap(128, 128)
                pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pixmap)
                renderer.render(painter)
                painter.end()
        else:
            pixmap.loadFromData(data)
        if pixmap.isNull():
            return
        size = label.size()
        edge = max(24, min(size.width(), size.height()))
        pixmap = pixmap.scaled(edge, edge, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        if circular:
            result = QPixmap(edge, edge)
            result.fill(Qt.GlobalColor.transparent)
            painter = QPainter(result)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addEllipse(0, 0, edge, edge)
            painter.setClipPath(path)
            painter.drawPixmap(0, 0, pixmap)
            painter.end()
            pixmap = result
        label.setPixmap(pixmap)


class TwitchLogo(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(62, 62)
        self._eye_offset = 0.0
        self._blink = 0
        self._next_blink = time.monotonic() + random.uniform(2.2, 4.8)
        self.eye_animation = QPropertyAnimation(self, b"eyeOffset", self)
        self.eye_animation.setDuration(520)
        self.eye_animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.eye_timer = QTimer(self)
        self.eye_timer.timeout.connect(self._tick)
        self.eye_timer.start(80)

    def get_eye_offset(self):
        return self._eye_offset

    def set_eye_offset(self, value):
        self._eye_offset = float(value)
        self.update()

    eyeOffset = Property(float, get_eye_offset, set_eye_offset)

    def _tick(self):
        now = time.monotonic()
        if self._blink > 0:
            self._blink -= 1
            self.update()
        elif now >= self._next_blink:
            self._blink = 5
            self._next_blink = now + random.uniform(2.4, 5.5)
            self.update()
        if self.eye_animation.state() != QPropertyAnimation.State.Running and random.random() < 0.018:
            self.eye_animation.stop()
            self.eye_animation.setStartValue(self._eye_offset)
            self.eye_animation.setKeyValueAt(0.5, random.choice((-3.0, 0.0, 3.0)))
            self.eye_animation.setEndValue(0.0)
            self.eye_animation.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QPainterPath()
        body.addRoundedRect(8, 8, 46, 39, 9, 9)
        painter.fillPath(body, QColor("#9147FF"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#9147FF"))
        tail = QPainterPath()
        tail.moveTo(18, 42)
        tail.lineTo(18, 55)
        tail.lineTo(31, 42)
        tail.closeSubpath()
        painter.fillPath(tail, QColor("#9147FF"))
        painter.setBrush(QColor("#FFFFFF"))
        if self._blink:
            painter.setPen(QPen(QColor("#FFFFFF"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(18, 27, 27, 27)
            painter.drawLine(35, 27, 44, 27)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(17, 18, 11, 15)
            painter.drawEllipse(34, 18, 11, 15)
            painter.setBrush(QColor("#17131F"))
            offset = int(self._eye_offset)
            painter.drawEllipse(20 + offset, 22, 5, 7)
            painter.drawEllipse(37 + offset, 22, 5, 7)
        painter.end()


class TitleBar(QFrame):
    minimizeRequested = Signal()
    maximizeRequested = Signal()
    closeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(54)
        self.drag_offset = None
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 0, 10, 0)
        row.setSpacing(10)
        self.caption = QLabel(APP_TITLE)
        self.caption.setObjectName("titleCaption")
        row.addWidget(self.caption)
        row.addStretch()
        self.status = QLabel("●  ПОДКЛЮЧЕНИЕ")
        self.status.setObjectName("titleStatus")
        row.addWidget(self.status)
        self.timer = QLabel("00:00:00")
        self.timer.setObjectName("sessionTimer")
        row.addWidget(self.timer)
        for glyph, signal in (("—", self.minimizeRequested), ("□", self.maximizeRequested), ("×", self.closeRequested)):
            button = QPushButton(glyph)
            button.setObjectName("windowControl")
            button.setFixedSize(34, 30)
            button.clicked.connect(signal.emit)
            row.addWidget(button)

    def set_status(self, text, color):
        self.status.setText(str(text or ""))
        self.status.setStyleSheet("color:" + str(color or "#9BA3B4"))

    def set_timer(self, value):
        self.timer.setText(value)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self.drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_offset = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximizeRequested.emit()


class ChannelCard(QFrame):
    selected = Signal(str)

    def __init__(self, login, profile, balance, currency, slot, temporary, image_cache, remove_callback, parent=None):
        super().__init__(parent)
        self.login = login
        self.setObjectName("channelCard")
        self.setMinimumWidth(225)
        self.setMaximumWidth(360)
        self.setMinimumHeight(228)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        badges = QHBoxLayout()
        badges.setSpacing(6)
        live = QLabel("● В ЭФИРЕ" if profile.get("is_live") else "● НЕ В ЭФИРЕ")
        live.setObjectName("liveBadge" if profile.get("is_live") else "offlineBadge")
        badges.addWidget(live)
        if temporary:
            badge = QLabel("ВРЕМЕННЫЙ • DROPS")
            badge.setObjectName("temporaryBadge")
            badges.addWidget(badge)
        badges.addStretch()
        if slot:
            watch = QLabel("СМОТРИМ • СЛОТ " + str(slot))
            watch.setObjectName("watchBadge")
            badges.addWidget(watch)
        remove = QPushButton("×")
        remove.setObjectName("smallDanger")
        remove.setFixedSize(24, 24)
        remove.setToolTip("Удалить канал")
        remove.clicked.connect(lambda: remove_callback(login))
        badges.addWidget(remove)
        root.addLayout(badges)
        avatar_row = QHBoxLayout()
        avatar_row.addStretch()
        self.avatar = QLabel()
        self.avatar.setObjectName("channelAvatar")
        self.avatar.setFixedSize(64, 64)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setText((profile.get("display_name") or login)[:1].upper())
        avatar_row.addWidget(self.avatar)
        avatar_row.addStretch()
        root.addLayout(avatar_row)
        self.name = QLabel(str(profile.get("display_name") or login))
        self.name.setObjectName("channelName")
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name.setWordWrap(True)
        root.addWidget(self.name)
        self.handle = QLabel("@" + login)
        self.handle.setObjectName("channelLogin")
        self.handle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.handle)
        bottom = QHBoxLayout()
        self.currency_icon = QLabel()
        self.currency_icon.setFixedSize(24, 24)
        self.currency_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bottom.addWidget(self.currency_icon)
        points = QLabel(f"{int(balance or 0):,}".replace(",", " "))
        points.setObjectName("channelBalance")
        bottom.addWidget(points)
        currency_name = QLabel(str(currency.get("name") or "Channel Points"))
        currency_name.setObjectName("mutedText")
        bottom.addWidget(currency_name, 1)
        root.addLayout(bottom)
        if image_cache:
            image_cache.request(self.avatar, profile.get("avatar_url", ""), True)
            image_cache.request(self.currency_icon, currency.get("icon_url", ""), False)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.login)
        super().mousePressEvent(event)


class ActiveSlotCard(QFrame):
    def __init__(self, slot_number, login, profile, balance, image_cache, parent=None):
        super().__init__(parent)
        self.setObjectName("activeSlotCard")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 9, 12, 9)
        self.avatar = QLabel()
        self.avatar.setFixedSize(42, 42)
        self.avatar.setObjectName("slotAvatar")
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setText(str(login or "—")[:1].upper())
        row.addWidget(self.avatar)
        texts = QVBoxLayout()
        title = QLabel("СЛОТ " + str(slot_number))
        title.setObjectName("slotCaption")
        texts.addWidget(title)
        name = QLabel(str(profile.get("display_name") or login or "Ожидание канала"))
        name.setObjectName("slotName")
        texts.addWidget(name)
        row.addLayout(texts, 1)
        amount = QLabel(f"{int(balance or 0):,}".replace(",", " ") + " баллов")
        amount.setObjectName("slotBalance")
        row.addWidget(amount)
        if image_cache:
            image_cache.request(self.avatar, profile.get("avatar_url", ""), True)


def eligible_twitch_campaigns(campaigns):
    result = []
    now = time.time()
    for campaign in campaigns if isinstance(campaigns, list) else []:
        if not isinstance(campaign, dict) or str(campaign.get("status") or "ACTIVE").upper() != "ACTIVE":
            continue
        drops = [
            dict(drop)
            for drop in campaign.get("drops", [])
            if isinstance(drop, dict)
            and bool(drop.get("is_twitch_view"))
            and str(drop.get("category") or "").lower() == "twitch"
            and not bool(drop.get("claimed"))
        ]
        if not drops:
            continue
        campaign_copy = dict(campaign)
        campaign_copy["visible_drops"] = drops
        campaign_copy["streamers"] = sorted(
            [dict(item) for item in campaign.get("streamers", []) if isinstance(item, dict)],
            key=lambda item: (
                max(0, int(item.get("uptime_seconds") or 0)),
                max(0, int(item.get("viewer_count") or 0))
            ),
            reverse=True
        )[:3]
        result.append(campaign_copy)
    result.sort(key=lambda item: max([int(drop.get("remaining_minutes") or 0) for drop in item["visible_drops"]] or [0]))
    return result


def format_duration(seconds):
    try:
        seconds = max(0, int(seconds))
    except (TypeError, ValueError):
        seconds = 0
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


class CampaignCard(QFrame):
    def __init__(self, campaign, image_cache, add_callback, parent=None, add_enabled=True):
        super().__init__(parent)
        self.setObjectName("campaignCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        image = QLabel()
        image.setFixedSize(54, 68)
        image.setObjectName("campaignImage")
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setText("DROP")
        header.addWidget(image)
        title_box = QVBoxLayout()
        name = QLabel(str(campaign.get("name") or "Кампания Twitch"))
        name.setObjectName("cardTitle")
        title_box.addWidget(name)
        game = QLabel(str(campaign.get("game") or "Награды Twitch"))
        game.setObjectName("mutedText")
        title_box.addWidget(game)
        date = QLabel("До: " + str(campaign.get("end_at") or "не указано"))
        date.setObjectName("smallMuted")
        title_box.addWidget(date)
        header.addLayout(title_box, 1)
        count = QLabel(str(len(campaign.get("visible_drops", []))) + " наград")
        count.setObjectName("purpleBadge")
        header.addWidget(count, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)
        for drop in campaign.get("visible_drops", []):
            row = QHBoxLayout()
            reward_image = QLabel()
            reward_image.setFixedSize(44, 44)
            reward_image.setObjectName("rewardIcon")
            reward_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            reward_image.setText("★")
            row.addWidget(reward_image)
            details = QVBoxLayout()
            reward_name = QLabel(str(drop.get("name") or drop.get("benefit") or "Награда Twitch"))
            reward_name.setObjectName("rewardTitle")
            details.addWidget(reward_name)
            progress = QLabel(
                f"{int(drop.get('watched_minutes') or 0)} / {int(drop.get('required_minutes') or 0)} мин · "
                f"{int(drop.get('percentage') or 0)}%"
            )
            progress.setObjectName("smallMuted")
            details.addWidget(progress)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(max(0, min(100, int(drop.get("percentage") or 0))))
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            details.addWidget(bar)
            row.addLayout(details, 1)
            layout.addLayout(row)
            if image_cache:
                image_cache.request(reward_image, drop.get("image_url", ""), False)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setObjectName("separator")
        layout.addWidget(separator)
        streamers = campaign.get("streamers", [])
        if streamers:
            heading = QLabel("ПОДХОДЯЩИЕ СТРИМЕРЫ · СНАЧАЛА ДОЛГИЕ ЭФИРЫ")
            heading.setObjectName("sectionEyebrow")
            layout.addWidget(heading)
            for streamer in streamers:
                row = QHBoxLayout()
                avatar = QLabel()
                avatar.setFixedSize(34, 34)
                avatar.setObjectName("miniAvatar")
                avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
                avatar.setText(str(streamer.get("display_name") or streamer.get("login") or "?")[:1].upper())
                row.addWidget(avatar)
                info = QVBoxLayout()
                title = QLabel(str(streamer.get("display_name") or streamer.get("login") or "Стример"))
                title.setObjectName("streamerName")
                info.addWidget(title)
                uptime = format_duration(streamer.get("uptime_seconds") or 0)
                description = QLabel(
                    "@" + str(streamer.get("login") or "") + " · "
                    + f"{int(streamer.get('viewer_count') or 0):,}".replace(",", " ")
                    + " зрителей · " + uptime + " в эфире"
                )
                description.setObjectName("smallMuted")
                info.addWidget(description)
                row.addLayout(info, 1)
                open_button = QPushButton("Открыть")
                open_button.setObjectName("softButton")
                login = str(streamer.get("login") or "")
                open_button.clicked.connect(lambda checked=False, value=login: QDesktopServices.openUrl(QUrl("https://www.twitch.tv/" + value)))
                row.addWidget(open_button)
                add_button = QPushButton("Фармить")
                add_button.setObjectName("primaryButton")
                add_button.setEnabled(bool(add_enabled))
                add_button.setToolTip("Остановите фарм, чтобы изменить список каналов." if not add_enabled else "")
                add_button.clicked.connect(lambda checked=False, value=login, cid=str(campaign.get("id") or ""): add_callback(value, cid))
                row.addWidget(add_button)
                layout.addLayout(row)
                if image_cache:
                    image_cache.request(avatar, streamer.get("avatar_url", ""), True)
        else:
            empty = QLabel("Подходящих активных стримеров Twitch сейчас не найдено.")
            empty.setObjectName("mutedText")
            layout.addWidget(empty)


class MainWindow(QMainWindow):
    installRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("mainWindow")
        self.setWindowTitle(APP_TITLE)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setMinimumSize(1080, 720)
        self.resize(1280, 850)
        self.api = TwitchFarmingApi()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(8)
        self.image_cache = ImageCache(self.pool, self)
        self.pending_callbacks = {}
        self.poll_running = False
        self.last_log_id = 0
        self.latest_state = {}
        self.drops_state = {}
        self.followed_state = {}
        self.update_state = self.api.get_update_state()
        self.selected_channel = ""
        self.current_page = 0
        self._last_channel_signature = None
        self._last_campaign_signature = None
        self._last_slot_signature = None
        self._log_cache = []
        self._last_followed_signature = None
        self._last_log_signature = None
        self._last_events_signature = None
        self._last_reward_signature = None
        self._last_preset_signature = None
        self._active_panel_target = 0
        self.api.update_install_callback = self.installRequested.emit
        self.installRequested.connect(self._install_ready)
        self._build_ui()
        self._load_settings_defaults()
        self._setup_timers()
        self._setup_tray()
        self._run_task("startup", self.api._startup_tasks, (), self._startup_finished)

    def _build_ui(self):
        self.setStyleSheet(self._style())
        shell = QFrame()
        shell.setObjectName("shell")
        self.setCentralWidget(shell)
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        self.title_bar = TitleBar()
        self.title_bar.minimizeRequested.connect(self.showMinimized)
        self.title_bar.maximizeRequested.connect(self._toggle_maximize)
        self.title_bar.closeRequested.connect(self.close)
        outer.addWidget(self.title_bar)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(218)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(14, 16, 14, 16)
        side.setSpacing(7)
        logo_row = QHBoxLayout()
        logo = TwitchLogo()
        logo_row.addWidget(logo)
        brand_box = QVBoxLayout()
        brand = QLabel("TWITCH")
        brand.setObjectName("brandText")
        brand_box.addWidget(brand)
        brand_sub = QLabel("FARMING")
        brand_sub.setObjectName("brandSub")
        brand_box.addWidget(brand_sub)
        logo_row.addLayout(brand_box)
        logo_row.addStretch()
        side.addLayout(logo_row)
        side.addSpacing(16)
        self.nav_buttons = []
        nav_items = [
            ("Каналы", 0, "▦"),
            ("Инвентарь Drops", 1, "◇"),
            ("Фолловеры", 2, "♙"),
            ("Настройки", 3, "⚙"),
            ("Настройки прогнозов", 4, "◈"),
            ("События и журнал", 5, "≡"),
            ("О проекте", 6, "ⓘ")
        ]
        for title, index, glyph in nav_items:
            button = QPushButton(glyph + "   " + title)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, value=index: self._navigate(value))
            side.addWidget(button)
            self.nav_buttons.append(button)
        side.addStretch()
        self.account_sidebar = QLabel("АККАУНТ НЕ ПОДКЛЮЧЁН")
        self.account_sidebar.setObjectName("sidebarAccount")
        self.account_sidebar.setWordWrap(True)
        side.addWidget(self.account_sidebar)
        self.version_sidebar = QLabel("v" + APP_VERSION)
        self.version_sidebar.setObjectName("sidebarVersion")
        side.addWidget(self.version_sidebar)
        body.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        self.stack.setObjectName("pageStack")
        body.addWidget(self.stack, 1)
        self._build_channels_page()
        self._build_drops_page()
        self._build_followers_page()
        self._build_settings_page()
        self._build_bets_page()
        self._build_logs_page()
        self._build_about_page()
        self._navigate(0)

    def _style(self):
        return """
        QWidget { color:#F2F1F7; font-family:"Segoe UI"; font-size:13px; }
        QMainWindow#mainWindow { background:#0B0B12; }
        QFrame#shell { background:#0E0E16; border:1px solid #282633; border-radius:16px; }
        QFrame#titleBar { background:#11111B; border-top-left-radius:15px; border-top-right-radius:15px; border-bottom:1px solid #292737; }
        QLabel#titleCaption { font-size:14px; font-weight:700; color:#F4F1FF; }
        QLabel#titleStatus { font-size:11px; font-weight:700; color:#45D6A5; padding:6px 10px; background:#17261F; border-radius:8px; }
        QLabel#sessionTimer { color:#B9B2C9; font-family:Consolas; font-size:12px; padding:5px 7px; }
        QPushButton#windowControl { background:transparent; border:0; color:#A9A4B9; font-size:17px; border-radius:7px; }
        QPushButton#windowControl:hover { background:#282638; color:white; }
        QPushButton#windowControl:last-child:hover { background:#A83D55; }
        QFrame#sidebar { background:#10101A; border-right:1px solid #292737; border-bottom-left-radius:15px; }
        QLabel#brandText { font-size:16px; font-weight:900; letter-spacing:2px; color:#FFFFFF; }
        QLabel#brandSub { font-size:10px; font-weight:800; letter-spacing:3px; color:#A98AFF; }
        QPushButton#navButton { border:0; border-radius:9px; text-align:left; padding:11px 12px; color:#AFAABC; background:transparent; font-weight:600; }
        QPushButton#navButton:hover { background:#1E1B2B; color:#FFFFFF; }
        QPushButton#navButton:checked { background:#29213D; color:#D1B9FF; border-left:3px solid #9147FF; }
        QLabel#sidebarAccount { color:#6DDBB0; font-size:10px; font-weight:800; padding:9px 8px; background:#17251F; border-radius:7px; }
        QLabel#sidebarVersion { color:#686477; font-size:10px; padding-left:8px; }
        QWidget#pageStack { background:#0E0E16; }
        QFrame#pageContent { background:#0E0E16; }
        QLabel#pageTitle { font-size:25px; font-weight:800; color:#FFFFFF; }
        QLabel#pageSubtitle { color:#8E899F; font-size:12px; }
        QLabel#sectionTitle { font-size:16px; font-weight:750; color:#F5F2FC; }
        QLabel#sectionEyebrow { color:#A98AFF; font-size:10px; font-weight:800; letter-spacing:1.2px; }
        QLabel#mutedText, QLabel#smallMuted { color:#918BA2; }
        QLabel#smallMuted { font-size:11px; }
        QFrame#panel, QFrame#channelCard, QFrame#campaignCard, QFrame#followerCard, QFrame#settingCard, QFrame#feedCard { background:#151520; border:1px solid #292737; border-radius:12px; }
        QFrame#channelCard:hover, QFrame#campaignCard:hover, QFrame#followerCard:hover { border-color:#564177; }
        QLabel#channelAvatar, QLabel#slotAvatar, QLabel#miniAvatar { background:#29243A; border:1px solid #4A3A63; border-radius:32px; color:#C9B8F0; font-weight:800; }
        QLabel#campaignImage { background:#241D36; border-radius:8px; color:#A98AFF; font-weight:800; }
        QLabel#rewardIcon { background:#242034; border-radius:8px; color:#E7D6FF; }
        QLabel#channelName { color:#FFFFFF; font-size:15px; font-weight:800; }
        QLabel#channelLogin { color:#9892A7; font-size:11px; }
        QLabel#channelBalance { color:#F2EDFF; font-weight:800; }
        QLabel#liveBadge, QLabel#offlineBadge, QLabel#watchBadge, QLabel#temporaryBadge, QLabel#purpleBadge { font-size:9px; font-weight:900; padding:5px 7px; border-radius:6px; }
        QLabel#liveBadge { color:#FF8593; background:#36202A; }
        QLabel#offlineBadge { color:#8C8799; background:#252530; }
        QLabel#watchBadge { color:#CDB4FF; background:#302345; }
        QLabel#temporaryBadge { color:#F5C36B; background:#322A1B; }
        QLabel#purpleBadge { color:#D4BCFF; background:#302345; }
        QLabel#slotCaption { font-size:9px; font-weight:900; color:#AA89FF; }
        QLabel#slotName { font-weight:800; color:#F7F4FF; }
        QLabel#slotBalance { color:#BDB5CC; font-size:11px; }
        QFrame#activeSlotCard { background:#201A30; border:1px solid #44335F; border-radius:9px; }
        QPushButton#primaryButton { background:#9147FF; border:1px solid #A266FF; color:white; padding:9px 14px; border-radius:8px; font-weight:800; }
        QPushButton#primaryButton:hover { background:#A15BFF; }
        QPushButton#primaryButton:disabled { background:#393044; color:#777181; border-color:#393044; }
        QPushButton#softButton, QPushButton#secondaryButton { background:#211E2C; border:1px solid #383247; color:#D2CBE0; padding:8px 12px; border-radius:8px; font-weight:650; }
        QPushButton#softButton:hover, QPushButton#secondaryButton:hover { background:#2B263B; border-color:#5B477D; }
        QPushButton#smallDanger { color:#A9A2B4; background:transparent; border:0; border-radius:6px; font-size:18px; }
        QPushButton#smallDanger:hover { color:#FF8593; background:#38212B; }
        QPushButton#dangerButton { background:#3A202B; border:1px solid #673343; color:#FF9BA7; padding:8px 12px; border-radius:8px; }
        QLineEdit, QTextEdit, QComboBox { background:#11111A; border:1px solid #343143; border-radius:8px; padding:8px 10px; color:#F4F1F9; selection-background-color:#7142B7; }
        QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border-color:#8750D9; }
        QComboBox::drop-down { border:0; width:24px; }
        QComboBox QAbstractItemView { background:#181722; border:1px solid #383247; selection-background-color:#403058; color:#F5F2FC; }
        QCheckBox { spacing:9px; color:#D7D2E2; padding:6px 2px; }
        QCheckBox::indicator { width:17px; height:17px; border:1px solid #575168; border-radius:5px; background:#171621; }
        QCheckBox::indicator:checked { background:#9147FF; border-color:#A66CFF; }
        QProgressBar { background:#282434; border:0; border-radius:4px; height:8px; text-align:center; color:transparent; }
        QProgressBar::chunk { background:#9147FF; border-radius:4px; }
        QScrollArea { border:0; background:transparent; }
        QScrollBar:vertical { background:#10101A; width:9px; margin:2px; }
        QScrollBar::handle:vertical { background:#393447; border-radius:4px; min-height:30px; }
        QScrollBar::handle:vertical:hover { background:#64547D; }
        QFrame#separator { color:#302C3B; max-height:1px; }
        QLabel#rewardTitle, QLabel#streamerName { font-weight:750; color:#F1EDF8; }
        QLabel#eventRow { color:#C2BCCD; padding:6px 0; border-bottom:1px solid #262331; }
        QLabel#statusHint { color:#AAA4B8; font-size:11px; }
        QTextEdit#logOutput { background:#0A0A10; border:1px solid #292737; border-radius:10px; color:#C7C1D1; font-family:Consolas; font-size:11px; }
        """

    def _page(self, title, subtitle):
        page = QWidget()
        page.setObjectName("pageContent")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(24, 22, 24, 20)
        page_layout.setSpacing(14)
        header = QVBoxLayout()
        label = QLabel(title)
        label.setObjectName("pageTitle")
        header.addWidget(label)
        sub = QLabel(subtitle)
        sub.setObjectName("pageSubtitle")
        sub.setWordWrap(True)
        header.addWidget(sub)
        page_layout.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 4, 10, 8)
        scroll_layout.setSpacing(13)
        scroll.setWidget(scroll_content)
        page_layout.addWidget(scroll, 1)
        self.stack.addWidget(page)
        return scroll_layout

    def _build_channels_page(self):
        layout = self._page("Каналы", "Управление каналами, двумя активными слотами просмотра и сбором наград.")
        top = QHBoxLayout()
        self.farm_status = QLabel("● НЕ ЗАПУЩЕН")
        self.farm_status.setObjectName("titleStatus")
        top.addWidget(self.farm_status)
        self.session_label = QLabel("Сессия: 00:00:00")
        self.session_label.setObjectName("statusHint")
        top.addWidget(self.session_label)
        top.addStretch()
        self.add_channel_button = QPushButton("＋ Добавить канал")
        self.add_channel_button.setObjectName("secondaryButton")
        self.add_channel_button.clicked.connect(self._add_channels)
        top.addWidget(self.add_channel_button)
        self.farm_button = QPushButton("▶  Запустить фарм")
        self.farm_button.setObjectName("primaryButton")
        self.farm_button.clicked.connect(self._toggle_farm)
        top.addWidget(self.farm_button)
        layout.addLayout(top)
        self.active_panel = QFrame()
        self.active_panel.setObjectName("panel")
        panel_layout = QVBoxLayout(self.active_panel)
        panel_layout.setContentsMargins(14, 12, 14, 12)
        heading = QLabel("АКТИВНЫЙ ФАРМ СЕЙЧАС")
        heading.setObjectName("sectionEyebrow")
        panel_layout.addWidget(heading)
        slots = QHBoxLayout()
        self.slot_layout = slots
        self.slot_cards = []
        for number in (1, 2):
            card = ActiveSlotCard(number, "", {}, 0, self.image_cache)
            self.slot_cards.append(card)
            slots.addWidget(card, 1)
        panel_layout.addLayout(slots)
        self.active_panel.setMaximumHeight(0)
        self.active_panel.setVisible(False)
        self.active_animation = QPropertyAnimation(self.active_panel, b"maximumHeight", self)
        self.active_animation.setDuration(260)
        self.active_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        layout.addWidget(self.active_panel)
        row = QHBoxLayout()
        title = QLabel("Список каналов")
        title.setObjectName("sectionTitle")
        row.addWidget(title)
        row.addStretch()
        self.selection_hint = QLabel("Выбери две карточки по очереди, чтобы поменять их местами.")
        self.selection_hint.setObjectName("smallMuted")
        row.addWidget(self.selection_hint)
        layout.addLayout(row)
        self.channels_grid = QGridLayout()
        self.channels_grid.setContentsMargins(0, 0, 0, 0)
        self.channels_grid.setHorizontalSpacing(12)
        self.channels_grid.setVerticalSpacing(12)
        self.channels_wrap = QWidget()
        self.channels_wrap.setLayout(self.channels_grid)
        layout.addWidget(self.channels_wrap)
        feeds = QHBoxLayout()
        self.rewards_feed = self._feed_panel("Последние награды")
        self.events_feed = self._feed_panel("События")
        feeds.addWidget(self.rewards_feed, 1)
        feeds.addWidget(self.events_feed, 1)
        layout.addLayout(feeds)

    def _feed_panel(self, title):
        frame = QFrame()
        frame.setObjectName("feedCard")
        box = QVBoxLayout(frame)
        box.setContentsMargins(14, 12, 14, 12)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        box.addWidget(heading)
        frame.feed_layout = QVBoxLayout()
        frame.feed_layout.setSpacing(7)
        box.addLayout(frame.feed_layout)
        return frame

    def _build_drops_page(self):
        layout = self._page("Инвентарь Drops", "Только бесплатные награды профиля Twitch: значки и эмоуты. Игровые предметы и уже забранные награды скрыты.")
        row = QHBoxLayout()
        self.drops_filter = QComboBox()
        self.drops_filter.addItems(["Все доступные", "Начатые", "Ещё не начатые"])
        self.drops_filter.currentIndexChanged.connect(self._render_drops)
        row.addWidget(self.drops_filter)
        row.addStretch()
        refresh = QPushButton("↻  Обновить кампании")
        refresh.setObjectName("primaryButton")
        refresh.clicked.connect(lambda: self._call("refresh_drops_inventory", True, message="Обновление каталога Twitch Drops запущено."))
        row.addWidget(refresh)
        layout.addLayout(row)
        self.drops_status = QLabel("Подключи Twitch и нажми «Обновить кампании».")
        self.drops_status.setObjectName("statusHint")
        layout.addWidget(self.drops_status)
        self.drops_list = QVBoxLayout()
        self.drops_list.setSpacing(12)
        layout.addLayout(self.drops_list)
        layout.addStretch()

    def _build_followers_page(self):
        layout = self._page("Фолловеры", "Подписки Twitch и поиск каналов, на которых доступны Drops.")
        row = QHBoxLayout()
        refresh = QPushButton("↻  Обновить подписки")
        refresh.setObjectName("primaryButton")
        refresh.clicked.connect(lambda: self._call("refresh_followed_channels", True, message="Загрузка списка подписок запущена."))
        row.addWidget(refresh)
        self.followed_input = QLineEdit()
        self.followed_input.setPlaceholderText("Логин Twitch")
        row.addWidget(self.followed_input, 1)
        add = QPushButton("Добавить")
        add.setObjectName("secondaryButton")
        add.clicked.connect(self._add_followed)
        row.addWidget(add)
        layout.addLayout(row)
        self.followers_status = QLabel("Список ещё не загружен.")
        self.followers_status.setObjectName("statusHint")
        layout.addWidget(self.followers_status)
        self.followers_list = QVBoxLayout()
        self.followers_list.setSpacing(8)
        layout.addLayout(self.followers_list)
        layout.addStretch()

    def _build_settings_page(self):
        layout = self._page("Настройки", "Учётная запись Twitch, параметры фарма и обновление приложения.")
        account = self._setting_panel("АККАУНТ TWITCH", "Сохранённая сессия используется для авторизации через cookies.")
        self.account_status = QLabel("АККАУНТ НЕ ПОДКЛЮЧЁН")
        self.account_status.setObjectName("statusHint")
        account.layout().addWidget(self.account_status)
        account_row = QHBoxLayout()
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Логин Twitch")
        account_row.addWidget(self.username_input, 1)
        login = QPushButton("Подключить")
        login.setObjectName("primaryButton")
        login.clicked.connect(self._login)
        account_row.addWidget(login)
        logout = QPushButton("Выйти")
        logout.setObjectName("secondaryButton")
        logout.clicked.connect(lambda: self._call("logout", message="Сессия Twitch завершена."))
        account_row.addWidget(logout)
        account.layout().addLayout(account_row)
        layout.addWidget(account)
        updater = self._setting_panel("ОБНОВЛЕНИЕ ПРИЛОЖЕНИЯ", "Проверка GitHub Releases и установка новой версии с автоматическим перезапуском.")
        self.update_version = QLabel("Текущая версия: v" + APP_VERSION)
        self.update_version.setObjectName("statusHint")
        updater.layout().addWidget(self.update_version)
        self.update_status = QLabel("Нажмите кнопку, чтобы проверить наличие новой версии на GitHub.")
        self.update_status.setObjectName("mutedText")
        self.update_status.setWordWrap(True)
        updater.layout().addWidget(self.update_status)
        update_actions = QHBoxLayout()
        self.update_check_button = QPushButton("Проверить обновления")
        self.update_check_button.setObjectName("primaryButton")
        self.update_check_button.clicked.connect(lambda: self._call("check_for_updates", message="Проверка GitHub Releases запущена."))
        update_actions.addWidget(self.update_check_button)
        self.update_download_button = QPushButton("Скачать и обновить")
        self.update_download_button.setObjectName("primaryButton")
        self.update_download_button.setVisible(False)
        self.update_download_button.clicked.connect(self._download_update)
        update_actions.addWidget(self.update_download_button)
        update_actions.addStretch()
        updater.layout().addLayout(update_actions)
        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(0)
        self.update_progress.setVisible(False)
        updater.layout().addWidget(self.update_progress)
        self.update_speed = QLabel("")
        self.update_speed.setObjectName("smallMuted")
        updater.layout().addWidget(self.update_speed)
        layout.addWidget(updater)
        options = self._setting_panel("ДЕЙСТВИЯ ФАРМА", "Включи только те действия, которые должны выполняться автоматически.")
        self.option_checks = {}
        for key, title in OPTION_NAMES:
            check = QCheckBox(title)
            check.setObjectName("optionCheck")
            self.option_checks[key] = check
            check.toggled.connect(lambda value, target=check: target.setProperty("dirty", True))
            options.layout().addWidget(check)
        layout.addWidget(options)
        app_settings = self._setting_panel("ПРИЛОЖЕНИЕ", "Поведение окна и системный трей.")
        self.app_setting_checks = {}
        app_labels = {
            "minimize_to_tray": "Сворачивать приложение в системный трей",
            "auto_start_farm": "Автоматически запускать фарм после восстановления сессии"
        }
        for key, default in DEFAULT_APP_SETTINGS.items():
            check = QCheckBox(app_labels.get(key, key))
            self.app_setting_checks[key] = check
            check.toggled.connect(lambda value, target=check: target.setProperty("dirty", True))
            app_settings.layout().addWidget(check)
        cache_row = QHBoxLayout()
        clear_avatars = QPushButton("Очистить кэш аватарок")
        clear_avatars.setObjectName("secondaryButton")
        clear_avatars.clicked.connect(lambda: self._clear_image_cache("clear_avatar_cache", "avatars"))
        cache_row.addWidget(clear_avatars)
        clear_currency = QPushButton("Очистить кэш иконок валют")
        clear_currency.setObjectName("secondaryButton")
        clear_currency.clicked.connect(lambda: self._clear_image_cache("clear_currency_cache", "currency"))
        cache_row.addWidget(clear_currency)
        refresh_branding = QPushButton("Обновить оформление Channel Points")
        refresh_branding.setObjectName("secondaryButton")
        refresh_branding.clicked.connect(lambda: self._call("refresh_channel_points_branding", message="Обновление оформления запущено."))
        cache_row.addWidget(refresh_branding)
        app_settings.layout().addLayout(cache_row)
        layout.addWidget(app_settings)
        save = QPushButton("Сохранить настройки")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save_settings)
        layout.addWidget(save)
        layout.addStretch()

    def _setting_panel(self, eyebrow, description):
        frame = QFrame()
        frame.setObjectName("settingCard")
        box = QVBoxLayout(frame)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(9)
        heading = QLabel(eyebrow)
        heading.setObjectName("sectionEyebrow")
        box.addWidget(heading)
        text = QLabel(description)
        text.setObjectName("mutedText")
        text.setWordWrap(True)
        box.addWidget(text)
        return frame

    def _build_bets_page(self):
        layout = self._page("Настройки прогнозов", "Параметры автоматического выбора исходов и размещения Channel Points.")
        presets = self._setting_panel("ПРЕСЕТЫ", "Загружай, сохраняй и удаляй наборы настроек прогнозов.")
        preset_row = QHBoxLayout()
        self.bet_preset = QComboBox()
        self.bet_preset.addItem("Оптимальный")
        self.bet_preset.currentTextChanged.connect(self._select_bet_preset)
        preset_row.addWidget(self.bet_preset, 1)
        self.bet_preset_name = QLineEdit()
        self.bet_preset_name.setPlaceholderText("Название нового пресета")
        preset_row.addWidget(self.bet_preset_name, 1)
        save_preset = QPushButton("Сохранить пресет")
        save_preset.setObjectName("secondaryButton")
        save_preset.clicked.connect(self._save_bet_preset)
        preset_row.addWidget(save_preset)
        delete_preset = QPushButton("Удалить")
        delete_preset.setObjectName("dangerButton")
        delete_preset.clicked.connect(self._delete_bet_preset)
        preset_row.addWidget(delete_preset)
        presets.layout().addLayout(preset_row)
        layout.addWidget(presets)
        panel = self._setting_panel("СТРАТЕГИЯ И ЛИМИТЫ", "Сохраняются в config.json и передаются текущему движку фарма.")
        self.bet_strategy = QComboBox()
        self.bet_strategy.addItems(STRATEGIES)
        panel.layout().addWidget(QLabel("Стратегия"))
        panel.layout().addWidget(self.bet_strategy)
        self.bet_percentage = QLineEdit()
        self.bet_gap = QLineEdit()
        self.bet_max = QLineEdit()
        self.bet_minimum = QLineEdit()
        self.bet_delay = QLineEdit()
        for title, widget in (
            ("Процент баланса", self.bet_percentage),
            ("Разница процентов", self.bet_gap),
            ("Максимум баллов", self.bet_max),
            ("Минимум баллов", self.bet_minimum),
            ("Задержка", self.bet_delay)
        ):
            panel.layout().addWidget(QLabel(title))
            panel.layout().addWidget(widget)
        self.bet_delay_mode = QComboBox()
        self.bet_delay_mode.addItems(["FROM_START", "FROM_END", "PERCENTAGE"])
        panel.layout().addWidget(QLabel("Отсчёт задержки"))
        panel.layout().addWidget(self.bet_delay_mode)
        self.bet_stealth = QCheckBox("Скрытый режим")
        panel.layout().addWidget(self.bet_stealth)
        layout.addWidget(panel)
        filters = self._setting_panel("ФИЛЬТР УЧАСТНИКОВ", "Размещай прогноз только когда показатель удовлетворяет заданному условию.")
        self.bet_filter_enabled = QCheckBox("Включить фильтр участников")
        filters.layout().addWidget(self.bet_filter_enabled)
        self.bet_filter_by = QComboBox()
        self.bet_filter_by.addItems(FILTER_KEYS)
        self.bet_filter_condition = QComboBox()
        self.bet_filter_condition.addItems(["GT", "LT", "GTE", "LTE"])
        self.bet_filter_value = QLineEdit()
        self.bet_filter_value.setPlaceholderText("Значение фильтра")
        filter_row = QHBoxLayout()
        filter_row.addWidget(self.bet_filter_by, 2)
        filter_row.addWidget(self.bet_filter_condition, 1)
        filter_row.addWidget(self.bet_filter_value, 1)
        filters.layout().addLayout(filter_row)
        layout.addWidget(filters)
        self.bet_filter_enabled.toggled.connect(lambda value: (self.bet_filter_by.setEnabled(value), self.bet_filter_condition.setEnabled(value), self.bet_filter_value.setEnabled(value)))
        tracked_widgets = [
            self.bet_strategy, self.bet_percentage, self.bet_gap, self.bet_max,
            self.bet_minimum, self.bet_delay, self.bet_delay_mode,
            self.bet_stealth, self.bet_filter_enabled, self.bet_filter_by,
            self.bet_filter_condition, self.bet_filter_value
        ]
        for widget in tracked_widgets:
            if isinstance(widget, QLineEdit):
                widget.textEdited.connect(lambda value, target=widget: target.setProperty("dirty", True))
            elif isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(lambda value, target=widget: target.setProperty("dirty", True))
            else:
                widget.toggled.connect(lambda value, target=widget: target.setProperty("dirty", True))
        save = QPushButton("Сохранить параметры прогнозов")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save_bets)
        layout.addWidget(save)
        layout.addStretch()

    def _build_logs_page(self):
        layout = self._page("События и журнал", "Системные события, награды и подробные записи фарма.")
        row = QHBoxLayout()
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("Фильтр по тексту...")
        self.log_search.textChanged.connect(self._render_logs)
        row.addWidget(self.log_search, 1)
        self.log_level = QComboBox()
        self.log_level.addItems(["Все уровни", "INFO", "WARNING", "ERROR", "DEBUG"])
        self.log_level.currentIndexChanged.connect(self._render_logs)
        row.addWidget(self.log_level)
        clear = QPushButton("Очистить журнал")
        clear.setObjectName("secondaryButton")
        clear.clicked.connect(lambda: self._call("clear_logs", message="Журнал очищен."))
        row.addWidget(clear)
        layout.addLayout(row)
        self.log_output = QTextEdit()
        self.log_output.setObjectName("logOutput")
        self.log_output.setReadOnly(True)
        layout.addWidget(self.log_output, 1)
        self.events_output = QTextEdit()
        self.events_output.setObjectName("logOutput")
        self.events_output.setReadOnly(True)
        self.events_output.setMaximumHeight(150)
        layout.addWidget(self.events_output)

    def _build_about_page(self):
        layout = self._page("О проекте", "Twitch Farming — приложение для сбора Channel Points и Drops.")
        panel = self._setting_panel("TWITCH FARMING", "Нативное приложение Qt6. Twitch backend сохраняет авторизацию, настройки, ротацию слотов, инвентарь и систему наград.")
        version = QLabel("Версия v" + APP_VERSION)
        version.setObjectName("sectionTitle")
        panel.layout().addWidget(version)
        repository = QPushButton("Открыть репозиторий GitHub")
        repository.setObjectName("primaryButton")
        repository.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPOSITORY_URL)))
        panel.layout().addWidget(repository)
        layout.addWidget(panel)
        layout.addStretch()

    def _load_settings_defaults(self):
        for key, check in self.option_checks.items():
            check.blockSignals(True)
            check.setChecked(DEFAULT_OPTIONS.get(key, True))
            check.setProperty("dirty", False)
            check.blockSignals(False)
        for key, check in self.app_setting_checks.items():
            check.blockSignals(True)
            check.setChecked(DEFAULT_APP_SETTINGS.get(key, False))
            check.setProperty("dirty", False)
            check.blockSignals(False)

    def _setup_timers(self):
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_state)
        self.poll_timer.start(650)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._update_session_clock)
        self.clock_timer.start(1000)
        QTimer.singleShot(80, self._poll_state)

    def _setup_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(str(ICON_FILE)), self)
        menu = QMenu()
        show_action = QAction("Открыть Twitch Farming", self)
        show_action.triggered.connect(self._restore_from_tray)
        menu.addAction(show_action)
        exit_action = QAction("Выход", self)
        exit_action.triggered.connect(self._exit_from_tray)
        menu.addAction(exit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self._restore_from_tray() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.setToolTip("Twitch Farming")
        self.tray.show()

    def _run_task(self, key, function, args=(), callback=None):
        token = key + ":" + str(time.monotonic_ns())
        task = ApiTask(token, function, args)
        self.pending_callbacks[token] = (key, callback)
        task.signals.finished.connect(self._task_finished)
        self.pool.start(task)

    def _task_finished(self, token, result, error):
        item = self.pending_callbacks.pop(token, None)
        if item is None:
            return
        key, callback = item
        if error:
            self._notify("Ошибка: " + error)
        if callback:
            callback(result, error)
        elif result and isinstance(result, dict) and result.get("message"):
            self._notify(str(result.get("message")))

    def _call(self, method, *args, message=""):
        function = getattr(self.api, method)
        def completed(result, error):
            if not error and message:
                self._notify(message)
            if not error and isinstance(result, dict) and result.get("message"):
                self._notify(str(result["message"]))
        self._run_task(method, function, args, completed)

    def _startup_finished(self, result, error):
        if error:
            self._notify("Не удалось подготовить backend: " + error)
            return
        self._run_task("session-restore", self.api.begin_session_restore, (), lambda value, failure: self._notify("Проверка сохранённой сессии Twitch запущена."))

    def _poll_state(self):
        if self.poll_running:
            return
        self.poll_running = True
        def collect():
            state = self.api.get_state(self.last_log_id)
            drops = self.api.get_drops_inventory_state()
            followed = self.api.get_followed_channels_state()
            update = self.api.get_update_state()
            auth = self.api.get_auth_state()
            return state, drops, followed, update, auth
        self._run_task("poll", collect, (), self._apply_snapshot)

    def _apply_snapshot(self, result, error):
        self.poll_running = False
        if error or not result:
            return
        state, drops, followed, update, auth = result
        state = state or {}
        incoming_logs = state.get("logs") or []
        known_ids = {int(item.get("id") or 0) for item in self._log_cache}
        for item in incoming_logs:
            item_id = int(item.get("id") or 0)
            if item_id and item_id not in known_ids:
                self._log_cache.append(dict(item))
                known_ids.add(item_id)
        self._log_cache = self._log_cache[-500:]
        state["logs"] = list(self._log_cache)
        if incoming_logs:
            self.last_log_id = max(self.last_log_id, max(int(item.get("id") or 0) for item in incoming_logs))
        self.latest_state = state
        self.drops_state = drops or {}
        self.followed_state = followed or {}
        self.update_state = update or {}
        self._render_state()
        self._render_drops()
        self._render_followers()
        self._render_update()
        self.account_status.setText(str(auth.get("auth_status") or ""))
        if auth.get("auth_error"):
            self.account_status.setToolTip(str(auth.get("auth_error")))
        username = str(self.latest_state.get("username") or "")
        if username and not self.username_input.hasFocus():
            self.username_input.setText(username)
        for key, check in self.option_checks.items():
            if not check.property("dirty"):
                check.blockSignals(True)
                check.setChecked(bool(self.latest_state.get("options", {}).get(key, DEFAULT_OPTIONS.get(key, True))))
                check.blockSignals(False)
        for key, check in self.app_setting_checks.items():
            if not check.property("dirty"):
                check.blockSignals(True)
                check.setChecked(bool(self.latest_state.get("app_settings", {}).get(key, DEFAULT_APP_SETTINGS.get(key, False))))
                check.blockSignals(False)
        self._load_bet_values(self.latest_state.get("bet_settings") or DEFAULT_BET_SETTINGS)
        self._load_bet_presets(self.latest_state.get("custom_presets") or {}, self.latest_state.get("selected_preset") or "Оптимальный")
        self.account_sidebar.setText("●  " + str(self.latest_state.get("authenticated_username") or "АККАУНТ НЕ ПОДКЛЮЧЁН"))
        self.version_sidebar.setText("v" + str(self.latest_state.get("app_version") or APP_VERSION))

    def _render_state(self):
        state = self.latest_state
        status = str(state.get("status_text") or "● НЕ ЗАПУЩЕН")
        color = str(state.get("status_color") or "#9BA3B4")
        self.title_bar.set_status(status, color)
        self.farm_status.setText(status)
        self.farm_status.setStyleSheet("color:" + color + "; background:#1B1925; padding:7px 10px; border-radius:7px; font-weight:800;")
        running = bool(state.get("session_running"))
        self.farm_button.setText("■  Остановить фарм" if running else "▶  Запустить фарм")
        self.add_channel_button.setEnabled(not running)
        self.add_channel_button.setToolTip("Остановите фарм, чтобы изменить список каналов." if running else "")
        self._update_session_clock()
        self._render_active_slots()
        signature = (
            tuple(state.get("channels", [])),
            json.dumps(state.get("channel_profiles", {}), sort_keys=True, ensure_ascii=False),
            json.dumps(state.get("channel_balances", {}), sort_keys=True),
            json.dumps(state.get("watch_status", {}), sort_keys=True),
            tuple(state.get("watch_slots", [])),
            tuple(state.get("temporary_channels", []))
        )
        if signature != self._last_channel_signature:
            self._last_channel_signature = signature
            self._render_channels()
        self._render_reward_feed(state.get("rewards", []))
        self._render_event_feed(state.get("events", []))
        self._render_logs()

    def _render_reward_feed(self, rewards):
        items = [dict(item) for item in (rewards or [])[-5:] if isinstance(item, dict)]
        signature = json.dumps(items, sort_keys=True, ensure_ascii=False)
        if signature == self._last_reward_signature:
            return
        self._last_reward_signature = signature
        self._clear_layout(self.rewards_feed.feed_layout)
        if not items:
            label = QLabel("Пока нет наград.")
            label.setObjectName("mutedText")
            self.rewards_feed.feed_layout.addWidget(label)
            return
        for item in reversed(items):
            row = QHBoxLayout()
            icon = QLabel("✦")
            icon.setFixedSize(30, 30)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon.setObjectName("rewardIcon")
            row.addWidget(icon)
            details = QVBoxLayout()
            title = QLabel(str(item.get("title") or item.get("description") or "Награда"))
            title.setObjectName("rewardTitle")
            title.setWordWrap(True)
            details.addWidget(title)
            meta_parts = []
            channel = str(item.get("channel_login") or "").strip()
            if channel:
                meta_parts.append("@" + channel)
            description = str(item.get("description") or "")
            if description and description != title.text():
                meta_parts.append(description)
            meta = QLabel(" · ".join(meta_parts))
            meta.setObjectName("smallMuted")
            meta.setWordWrap(True)
            details.addWidget(meta)
            row.addLayout(details, 1)
            self.rewards_feed.feed_layout.addLayout(row)
            self.image_cache.request(icon, item.get("currency_icon") or item.get("icon_url") or "", False)

    def _render_event_feed(self, events):
        items = [str(item) for item in (events or [])[-5:]]
        signature = tuple(items)
        if signature == self._last_events_signature:
            return
        self._last_events_signature = signature
        self._clear_layout(self.events_feed.feed_layout)
        if not items:
            label = QLabel("Пока нет событий.")
            label.setObjectName("mutedText")
            self.events_feed.feed_layout.addWidget(label)
            return
        for item in reversed(items):
            label = QLabel(item)
            label.setObjectName("eventRow")
            label.setWordWrap(True)
            self.events_feed.feed_layout.addWidget(label)

    def _watch_slot_map(self):
        result = {}
        slots = self.latest_state.get("watch_slots") or []
        for index, item in enumerate(slots[:2], 1):
            if isinstance(item, dict):
                login = str(item.get("username") or item.get("login") or "").lower()
            else:
                login = str(getattr(item, "username", item) or "").lower()
            if login:
                result[login] = index
        return result

    def _render_active_slots(self):
        state = self.latest_state
        running = bool(state.get("session_running"))
        slots = state.get("watch_slots") or []
        has_active = running or bool(slots)
        target = 148 if has_active else 0
        if target != self._active_panel_target:
            self._active_panel_target = target
            self.active_animation.stop()
            if has_active:
                self.active_panel.setVisible(True)
            current = self.active_panel.maximumHeight()
            self.active_animation.setStartValue(current)
            self.active_animation.setEndValue(target)
            self.active_animation.start()
            if not has_active:
                QTimer.singleShot(280, lambda: self.active_panel.setVisible(False))
        watch_map = self._watch_slot_map()
        profiles = state.get("channel_profiles", {})
        balances = state.get("channel_balances", {})
        slot_data = []
        for number in (1, 2):
            login = next((key for key, value in watch_map.items() if value == number), "")
            profile = profiles.get(login, {})
            slot_data.append((login, str(profile.get("display_name") or ""), str(profile.get("avatar_url") or ""), int(balances.get(login, 0) or 0)))
        signature = tuple(slot_data)
        if signature == self._last_slot_signature:
            return
        self._last_slot_signature = signature
        for number, card in enumerate(list(self.slot_cards), 1):
            login, display_name, avatar_url, balance = slot_data[number - 1]
            profile = profiles.get(login, {}) if login else {}
            replacement = ActiveSlotCard(number, login, profile, balance, self.image_cache)
            index = self.slot_layout.indexOf(card)
            self.slot_layout.removeWidget(card)
            card.deleteLater()
            self.slot_layout.insertWidget(max(0, index), replacement, 1)
            self.slot_cards[number - 1] = replacement

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child = item.layout()
            if widget is not None:
                widget.deleteLater()
            if child is not None:
                self._clear_layout(child)

    def _render_channels(self):
        self._clear_layout(self.channels_grid)
        state = self.latest_state
        channels = state.get("channels", [])
        profiles = state.get("channel_profiles", {})
        balances = state.get("channel_balances", {})
        currency = state.get("channel_currency", {})
        temporary = set(state.get("temporary_channels", []))
        watch = self._watch_slot_map()
        for index, login in enumerate(channels):
            profile = profiles.get(login, {})
            card = ChannelCard(
                login,
                profile,
                balances.get(login, 0),
                currency.get(login, {}),
                watch.get(login),
                login in temporary,
                self.image_cache,
                self._remove_channel
            )
            card.selected.connect(self._select_channel)
            if login == self.selected_channel:
                card.setStyleSheet("border:1px solid #A36BFF; background:#211A30;")
            self.channels_grid.addWidget(card, index // 3, index % 3)
        if not channels:
            empty = QLabel("Список каналов пуст. Добавь первый Twitch-канал.")
            empty.setObjectName("mutedText")
            self.channels_grid.addWidget(empty, 0, 0)

    def _select_channel(self, login):
        if not self.selected_channel:
            self.selected_channel = login
            self.selection_hint.setText("Выбран @" + login + ". Теперь выбери вторую карточку.")
            self._render_channels()
            return
        if self.selected_channel == login:
            self.selected_channel = ""
            self.selection_hint.setText("Выбор отменён.")
            self._render_channels()
            return
        order = list(self.latest_state.get("channels", []))
        first = self.selected_channel
        second = login
        if first in order and second in order:
            a, b = order.index(first), order.index(second)
            order[a], order[b] = order[b], order[a]
            self._call("reorder_channels", order, message="Порядок каналов изменён.")
        self.selected_channel = ""
        self.selection_hint.setText("Выбери две карточки по очереди, чтобы поменять их местами.")

    def _remove_channel(self, login):
        answer = QMessageBox.question(self, "Удалить канал", "Удалить @" + login + " из списка?")
        if answer == QMessageBox.StandardButton.Yes:
            self._call("remove_channel", login, message="Канал удалён.")

    def _add_channels(self):
        value, ok = QInputDialog.getMultiLineText(self, "Добавить каналы", "Вставь Twitch-логины или ссылки, по одному на строку:")
        if ok and value.strip():
            self._call("save_channel_editor", "add", "", value, [], message="Каналы добавлены.")

    def _toggle_farm(self):
        if self.latest_state.get("session_running"):
            self._call("stop_farm", message="Остановка фарма запрошена.")
        else:
            self._call("start_farm", self._collect_payload(), message="Запуск фарма запрошен.")

    def _collect_payload(self):
        state = self.latest_state
        return {
            "username": self.username_input.text().strip() or str(state.get("username") or ""),
            "channels": list(state.get("channels") or []),
            "settings": {key: check.isChecked() for key, check in self.option_checks.items()},
            "app_settings": {key: check.isChecked() for key, check in self.app_setting_checks.items()},
            "bet_settings": dict(state.get("bet_settings") or DEFAULT_BET_SETTINGS),
            "custom_presets": dict(state.get("custom_presets") or {}),
            "selected_preset": str(state.get("selected_preset") or "Оптимальный")
        }

    def _login(self):
        username = self.username_input.text().strip()
        if not username:
            QMessageBox.warning(self, "Twitch", "Укажи логин Twitch.")
            return
        self._call("login", username, message="Запрос авторизации Twitch отправлен.")

    def _save_settings(self):
        def completed(result, error):
            if not error and isinstance(result, dict) and result.get("ok"):
                for check in list(self.option_checks.values()) + list(self.app_setting_checks.values()):
                    check.setProperty("dirty", False)
                self._notify(str(result.get("message") or "Настройки сохранены."))
            elif not error:
                self._notify(str((result or {}).get("message") or "Не удалось сохранить настройки."))
        self._run_task("save-settings", self.api.save_settings, (self._collect_payload(),), completed)

    def _collect_bet_values(self):
        return {
            "strategy": self.bet_strategy.currentText(),
            "percentage": float(self.bet_percentage.text()),
            "percentage_gap": float(self.bet_gap.text()),
            "max_points": int(self.bet_max.text()),
            "minimum_points": int(self.bet_minimum.text()),
            "delay": float(self.bet_delay.text()),
            "delay_mode": self.bet_delay_mode.currentText(),
            "stealth_mode": self.bet_stealth.isChecked(),
            "filter_enabled": self.bet_filter_enabled.isChecked(),
            "filter_by": self.bet_filter_by.currentText(),
            "filter_condition": self.bet_filter_condition.currentText(),
            "filter_value": float(self.bet_filter_value.text() or 0)
        }

    def _reset_bet_dirty(self):
        for widget in (
            self.bet_strategy, self.bet_percentage, self.bet_gap, self.bet_max,
            self.bet_minimum, self.bet_delay, self.bet_delay_mode,
            self.bet_stealth, self.bet_filter_enabled, self.bet_filter_by,
            self.bet_filter_condition, self.bet_filter_value
        ):
            widget.setProperty("dirty", False)

    def _save_bets(self):
        try:
            values = self._collect_bet_values()
        except ValueError:
            QMessageBox.warning(self, "Прогнозы", "Проверь числовые параметры.")
            return
        selected = self.bet_preset.currentText() or "Оптимальный"
        def completed(result, error):
            if not error and isinstance(result, dict) and result.get("ok"):
                self._reset_bet_dirty()
                self._notify(str(result.get("message") or "Параметры прогнозов сохранены."))
            elif not error:
                self._notify(str((result or {}).get("message") or "Не удалось сохранить параметры."))
        self._run_task("apply-bet-settings", self.api.apply_bet_settings, (values, selected), completed)

    def _save_bet_preset(self):
        name = self.bet_preset_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Пресеты", "Введи название пресета.")
            return
        try:
            values = self._collect_bet_values()
        except ValueError:
            QMessageBox.warning(self, "Пресеты", "Проверь числовые параметры.")
            return
        def completed(result, error):
            if not error and isinstance(result, dict) and result.get("ok"):
                self._reset_bet_dirty()
                self._last_preset_signature = None
                self._poll_state()
                self._notify(str(result.get("message") or "Пресет сохранён."))
            elif not error:
                self._notify(str((result or {}).get("message") or "Не удалось сохранить пресет."))
        self._run_task("save-bet-preset", self.api.save_preset, (name, values), completed)
        self.bet_preset_name.clear()

    def _delete_bet_preset(self):
        selected = self.bet_preset.currentText()
        if selected == "Оптимальный":
            QMessageBox.information(self, "Пресеты", "Стандартный пресет нельзя удалить.")
            return
        def completed(result, error):
            if not error and isinstance(result, dict) and result.get("ok"):
                self._reset_bet_dirty()
                self._last_preset_signature = None
                self._poll_state()
                self._notify(str(result.get("message") or "Пресет удалён."))
            elif not error:
                self._notify(str((result or {}).get("message") or "Не удалось удалить пресет."))
        self._run_task("delete-bet-preset", self.api.delete_preset, (selected,), completed)

    def _select_bet_preset(self, name):
        if not name:
            return
        presets = self.latest_state.get("custom_presets") or {}
        values = presets.get(name) if name != "Оптимальный" else DEFAULT_BET_SETTINGS
        if isinstance(values, dict):
            self._reset_bet_dirty()
            self._load_bet_values(values)

    def _load_bet_presets(self, presets, selected):
        signature = json.dumps([presets, selected], sort_keys=True, ensure_ascii=False)
        if signature == self._last_preset_signature:
            return
        self._last_preset_signature = signature
        self.bet_preset.blockSignals(True)
        self.bet_preset.clear()
        self.bet_preset.addItem("Оптимальный")
        for name in sorted(presets):
            if name and name != "Оптимальный":
                self.bet_preset.addItem(name)
        self.bet_preset.setCurrentText(selected if self.bet_preset.findText(selected) >= 0 else "Оптимальный")
        self.bet_preset.blockSignals(False)

    def _load_bet_values(self, values):
        mapping = (
            (self.bet_strategy, "strategy"),
            (self.bet_percentage, "percentage"),
            (self.bet_gap, "percentage_gap"),
            (self.bet_max, "max_points"),
            (self.bet_minimum, "minimum_points"),
            (self.bet_delay, "delay"),
            (self.bet_delay_mode, "delay_mode"),
            (self.bet_filter_by, "filter_by"),
            (self.bet_filter_condition, "filter_condition"),
            (self.bet_filter_value, "filter_value")
        )
        for widget, key in mapping:
            if widget.property("dirty") or (isinstance(widget, QLineEdit) and widget.hasFocus()):
                continue
            value = str(values.get(key, DEFAULT_BET_SETTINGS.get(key, "")))
            if isinstance(widget, QComboBox):
                widget.blockSignals(True)
                widget.setCurrentText(value)
                widget.blockSignals(False)
            else:
                widget.setText(value)
        if not self.bet_stealth.property("dirty"):
            self.bet_stealth.blockSignals(True)
            self.bet_stealth.setChecked(bool(values.get("stealth_mode", False)))
            self.bet_stealth.blockSignals(False)
        if not self.bet_filter_enabled.property("dirty"):
            self.bet_filter_enabled.blockSignals(True)
            self.bet_filter_enabled.setChecked(bool(values.get("filter_enabled", False)))
            self.bet_filter_enabled.blockSignals(False)
        enabled = self.bet_filter_enabled.isChecked()
        self.bet_filter_by.setEnabled(enabled)
        self.bet_filter_condition.setEnabled(enabled)
        self.bet_filter_value.setEnabled(enabled)

    def _render_drops(self):
        if not hasattr(self, "drops_list"):
            return
        self._clear_layout(self.drops_list)
        campaigns = eligible_twitch_campaigns(self.drops_state.get("campaigns", []))
        filter_index = self.drops_filter.currentIndex()
        if filter_index == 1:
            campaigns = [item for item in campaigns if item.get("started") or item.get("in_inventory")]
        elif filter_index == 2:
            campaigns = [item for item in campaigns if not item.get("started") and not item.get("in_inventory")]
        session_running = bool(self.latest_state.get("session_running"))
        signature = json.dumps([campaigns, filter_index, self.drops_state.get("loading"), self.drops_state.get("error"), session_running], sort_keys=True, ensure_ascii=False)
        if signature == self._last_campaign_signature:
            return
        self._last_campaign_signature = signature
        if self.drops_state.get("loading"):
            self.drops_status.setText("Загрузка ViewerDropsDashboard и Inventory…")
        elif self.drops_state.get("error"):
            self.drops_status.setText("Ошибка загрузки: " + str(self.drops_state.get("error")))
        else:
            self.drops_status.setText(f"Найдено кампаний с незабранными значками и эмоутами Twitch: {len(campaigns)}")
        if not campaigns:
            empty = QLabel("Подходящих незабранных Twitch-наград сейчас нет. Обнови кампании после входа в Twitch.")
            empty.setObjectName("mutedText")
            empty.setWordWrap(True)
            self.drops_list.addWidget(empty)
        for campaign in campaigns:
            self.drops_list.addWidget(CampaignCard(campaign, self.image_cache, self._add_drop_streamer, add_enabled=not session_running))
        self.drops_list.addStretch()

    def _add_drop_streamer(self, login, campaign_id):
        if self.latest_state.get("session_running"):
            self._notify("Останови текущий фарм перед запуском Drops-канала.")
            return
        self._call("add_drop_channel_to_farm", login, campaign_id, message="Запуск временного Drops-фарма отправлен.")

    def _render_followers(self):
        if not hasattr(self, "followers_list"):
            return
        items = self.followed_state.get("channels", [])
        session_running = bool(self.latest_state.get("session_running"))
        signature = json.dumps([items, session_running], sort_keys=True, ensure_ascii=False)
        if signature == self._last_followed_signature:
            return
        self._last_followed_signature = signature
        self._clear_layout(self.followers_list)
        self.followers_status.setText(
            "Загрузка подписок…" if self.followed_state.get("loading")
            else str(self.followed_state.get("error") or f"Найдено подписок: {len(items)}")
        )
        for item in items:
            frame = QFrame()
            frame.setObjectName("followerCard")
            row = QHBoxLayout(frame)
            row.setContentsMargins(10, 8, 10, 8)
            avatar = QLabel()
            avatar.setFixedSize(38, 38)
            avatar.setObjectName("miniAvatar")
            avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            avatar.setText(str(item.get("display_name") or item.get("login") or "?")[:1].upper())
            row.addWidget(avatar)
            details = QVBoxLayout()
            name = QLabel(str(item.get("display_name") or item.get("login") or "Канал"))
            name.setObjectName("streamerName")
            details.addWidget(name)
            meta = QLabel("@" + str(item.get("login") or "") + (" · В ЭФИРЕ" if item.get("is_live") else ""))
            meta.setObjectName("smallMuted")
            details.addWidget(meta)
            row.addLayout(details, 1)
            add = QPushButton("Добавить в каналы")
            add.setObjectName("secondaryButton")
            add.setEnabled(not session_running)
            add.setToolTip("Остановите фарм, чтобы изменить список каналов." if session_running else "")
            login = str(item.get("login") or "")
            add.clicked.connect(lambda checked=False, value=login: self._call("add_followed_channel", value, message="Канал добавлен."))
            row.addWidget(add)
            self.followers_list.addWidget(frame)
            self.image_cache.request(avatar, item.get("avatar_url", ""), True)
        self.followers_list.addStretch()

    def _clear_image_cache(self, method, category):
        def completed(result, error):
            if error:
                return
            self.image_cache.data.clear()
            self._last_channel_signature = None
            self._poll_state()
            self._notify(str((result or {}).get("message") or "Кэш изображений очищен."))
        self._run_task(method, getattr(self.api, method), (), completed)

    def _add_followed(self):
        login = self.followed_input.text().strip()
        if login:
            self._call("add_followed_channel", login, message="Канал добавлен.")
            self.followed_input.clear()

    def _render_update(self):
        if not hasattr(self, "update_status"):
            return
        state = self.update_state or {}
        phase = str(state.get("phase") or "idle")
        self.update_version.setText("Текущая версия: v" + str(state.get("current_version") or APP_VERSION))
        self.update_status.setText(str(state.get("status") or "Нажмите кнопку, чтобы проверить наличие новой версии на GitHub."))
        self.update_check_button.setEnabled(phase not in ("checking", "downloading", "extracting", "installing"))
        self.update_download_button.setVisible(bool(state.get("update_available")))
        self.update_download_button.setEnabled(phase == "available")
        progress_visible = phase in ("downloading", "extracting", "installing")
        self.update_progress.setVisible(progress_visible)
        self.update_progress.setValue(max(0, min(100, int(float(state.get("percent") or 0)))))
        downloaded = int(state.get("downloaded_bytes") or 0) / (1024 * 1024)
        total = int(state.get("total_bytes") or 0) / (1024 * 1024)
        speed = float(state.get("speed_bps") or 0) / (1024 * 1024)
        if phase == "downloading":
            self.update_speed.setText(f"Скачивание: {self.update_progress.value()}% · {downloaded:.1f} / {total:.1f} МБ · {speed:.2f} МБ/с")
        else:
            self.update_speed.setText(str(state.get("error") or state.get("latest_version") or ""))

    def _download_update(self):
        if self.latest_state.get("session_running"):
            QMessageBox.warning(self, "Обновление", "Останови фарм перед установкой обновления.")
            return
        self._call("download_update", message="Скачивание обновления запущено.")

    def _render_logs(self):
        if not hasattr(self, "log_output"):
            return
        rows = self.latest_state.get("logs", [])
        search = self.log_search.text().strip().lower()
        level = self.log_level.currentText()
        filtered = []
        for item in rows[-500:]:
            line = str(item.get("line") or item.get("message") or item)
            severity = str(item.get("level") or "INFO").upper()
            if search and search not in line.lower():
                continue
            if level != "Все уровни" and severity != level:
                continue
            filtered.append(line)
        text = "\n".join(filtered[-500:])
        if text != self.log_output.toPlainText():
            self.log_output.setPlainText(text)
            scrollbar = self.log_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        events = self.latest_state.get("events", [])
        event_text = "\n".join(str(item) for item in events[-100:])
        if event_text != self.events_output.toPlainText():
            self.events_output.setPlainText(event_text)

    def _update_session_clock(self):
        started = float(self.latest_state.get("session_started_at") or 0)
        elapsed = max(0, int(time.time() - started)) if started else 0
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        value = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        self.title_bar.set_timer(value)
        self.session_label.setText("Сессия: " + value)

    def _navigate(self, index):
        self.current_page = index
        self.stack.setCurrentIndex(index)
        for current, button in enumerate(self.nav_buttons):
            button.setChecked(current == index)
        if index == 1 and self.latest_state.get("authenticated") and not self.drops_state.get("loaded"):
            self._call("refresh_drops_inventory", False)
        if index == 2 and self.latest_state.get("authenticated") and not self.followed_state.get("loaded"):
            self._call("refresh_followed_channels", False)

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _notify(self, message):
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(APP_TITLE, str(message), QSystemTrayIcon.MessageIcon.Information, 2500)
        self.statusBar().showMessage(str(message), 4500)

    def _install_ready(self):
        self._notify("Обновление подготовлено. Приложение перезапустится после закрытия.")
        QTimer.singleShot(400, self._quit_for_update)

    def _quit_for_update(self):
        self.api.update_install_callback = None
        self._shutdown_and_exit()

    def _restore_from_tray(self):
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _exit_from_tray(self):
        self._shutdown_and_exit()

    def _shutdown_and_exit(self):
        self.poll_timer.stop()
        self.clock_timer.stop()
        self._run_task("shutdown", self.api.shutdown, (), lambda result, error: QApplication.quit())
        QTimer.singleShot(2500, QApplication.quit)

    def closeEvent(self, event):
        if self.latest_state.get("app_settings", {}).get("minimize_to_tray") and self.tray and self.tray.isVisible():
            self.hide()
            self.tray.showMessage(APP_TITLE, "Приложение продолжает работать в фоне.", QSystemTrayIcon.MessageIcon.Information, 1800)
            event.ignore()
            return
        event.accept()
        self._shutdown_and_exit()


def main():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
