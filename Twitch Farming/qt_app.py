import ctypes
import hashlib
import html
import json
import random
import re
import sys
import time
import urllib.request
import weakref
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QEasingCurve, QPoint, QRect, QPropertyAnimation, Property, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal, QObject, QUrl
from PySide6.QtGui import QAction, QColor, QCursor, QDesktopServices, QPainter, QPainterPath, QPen, QPixmap, QIcon
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QButtonGroup, QCheckBox, QComboBox, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QProgressBar, QPushButton as QtPushButton, QScrollArea, QScrollBar, QSizePolicy, QSlider, QStackedWidget, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget

from backend import APP_DIR, APP_VERSION, DEFAULT_APP_SETTINGS, DEFAULT_BET_SETTINGS, DEFAULT_OPTIONS, FILTER_KEYS, ICON_FILE, OPTION_NAMES, STRATEGIES, TwitchFarmingApi


APP_TITLE = "Twitch Farming"
REPOSITORY_URL = "https://github.com/gamooda/KICK-FARMING"
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
UI_ICON_ATLAS = RESOURCE_ROOT / "assets" / "ui_icons.png"
UI_ICON_CELLS = {"logo": 0, "channels": 1, "drops": 2, "bonuses": 3, "rewards": 4, "predictions": 5, "irc": 6, "events": 7, "settings": 8}
APP_ICON_FILE = RESOURCE_ROOT / "assets" / "app.ico"
_INSTANCE_MUTEX_HANDLE = None


def acquire_single_instance():
    global _INSTANCE_MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    create_mutex.restype = ctypes.c_void_p
    ctypes.set_last_error(0)
    handle = create_mutex(None, 1, "Local\\TwitchFarmingQt6SingleInstance")
    error = ctypes.get_last_error()
    if not handle:
        raise OSError(error, "Не удалось создать mutex экземпляра приложения.")
    if error == 183:
        kernel32.CloseHandle(handle)
        return False
    _INSTANCE_MUTEX_HANDLE = handle
    return True


class QPushButton(QtPushButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)


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
                pixmap = QPixmap(256, 256)
                pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pixmap)
                renderer.render(painter)
                painter.end()
        else:
            pixmap.loadFromData(data)
        if pixmap.isNull():
            return
        width = max(24, label.width() or label.minimumWidth() or label.sizeHint().width())
        height = max(24, label.height() or label.minimumHeight() or label.sizeHint().height())
        edge = max(24, min(width, height))
        if circular:
            scaled = pixmap.scaled(edge, edge, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            result = QPixmap(edge, edge)
            result.fill(Qt.GlobalColor.transparent)
            painter = QPainter(result)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addEllipse(0, 0, edge, edge)
            painter.setClipPath(path)
            source_rect = QRect(max(0, (scaled.width() - edge) // 2), max(0, (scaled.height() - edge) // 2), edge, edge)
            painter.drawPixmap(QRect(0, 0, edge, edge), scaled, source_rect)
            painter.end()
            pixmap = result
        else:
            pixmap = pixmap.scaled(edge, edge, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setPixmap(pixmap)


class TwitchLogo(QWidget):
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.setFixedSize(66, 66)
        self.logo_pixmap = pixmap
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
            self.eye_animation.setKeyValueAt(0.5, random.choice((-1.0, 1.0)))
            self.eye_animation.setEndValue(0.0)
            self.eye_animation.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.width() / 48.0, self.height() / 48.0)
        painter.drawPixmap(0, 0, 48, 48, self.logo_pixmap)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(23, 15, 4, 12, 1, 1)
        painter.drawRoundedRect(29, 15, 4, 12, 1, 1)
        painter.setBrush(QColor("#6522CF"))
        if self._blink:
            painter.drawRoundedRect(22, 20, 6, 2, 1, 1)
            painter.drawRoundedRect(28, 20, 6, 2, 1, 1)
        else:
            offset = int(round(self._eye_offset * 1.5))
            vertical = int(round(self._eye_offset))
            painter.drawRoundedRect(24 + offset, 17 + vertical, 2, 8, 1, 1)
            painter.drawRoundedRect(30 + offset, 17 + vertical, 2, 8, 1, 1)
        painter.end()


class TitleBar(QFrame):
    minimizeRequested = Signal()
    maximizeRequested = Signal()
    closeRequested = Signal()
    searchChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(46)
        self.drag_offset = None
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 8, 0)
        row.setSpacing(9)
        self.search = QLineEdit()
        self.search.setObjectName("titleSearch")
        self.search.setPlaceholderText("⌕   Поиск каналов...")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(340)
        self.search.setMinimumWidth(210)
        self.search.setFixedHeight(28)
        self.search.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.search.textChanged.connect(self.searchChanged.emit)
        row.addWidget(self.search)
        row.addStretch()
        self.status = QLabel("● НЕ ЗАПУЩЕН")
        self.status.setObjectName("titleStatus")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setMinimumWidth(92)
        self.status.setMaximumWidth(150)
        self.status.setFixedHeight(23)
        row.addWidget(self.status)
        self.points = QLabel("◈ +0 очков")
        self.points.setObjectName("sessionPoints")
        self.points.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.points.setMinimumWidth(104)
        self.points.setFixedHeight(24)
        row.addWidget(self.points)
        self.timer = QLabel("00:00:00")
        self.timer.setObjectName("sessionTimer")
        self.timer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timer.setFixedHeight(24)
        row.addWidget(self.timer)
        for glyph, signal in (("—", self.minimizeRequested), ("□", self.maximizeRequested), ("×", self.closeRequested)):
            button = QPushButton(glyph)
            button.setObjectName("windowControl")
            button.setFixedSize(30, 28)
            button.clicked.connect(signal.emit)
            row.addWidget(button)

    def set_status(self, text, color):
        value = str(text or "НЕ ЗАПУЩЕН").replace("●", "").strip()
        aliases = (
            ("РАБОТАЕТ", "РАБОТАЕТ"),
            ("НЕ ЗАПУЩЕН", "НЕ ЗАПУЩЕН"),
            ("ПОДКЛЮЧ", "ПОДКЛЮЧЁН"),
            ("ОШИБ", "ОШИБКА"),
            ("НЕТ ДОСТУПНЫХ", "НЕТ КАНАЛОВ"),
            ("СИНХРОНИЗАЦИЯ", "СИНХРОНИЗАЦИЯ")
        )
        for marker, label in aliases:
            if marker in value.upper():
                value = label
                break
        self.status.setText("● " + value[:18])
        self.status.setStyleSheet("color:" + str(color or "#A9A2B4"))

    def set_timer(self, value):
        self.timer.setText(value)

    def set_gained_total(self, value):
        try:
            amount = max(0, int(value or 0))
        except (TypeError, ValueError):
            amount = 0
        self.points.setText("◈ +" + f"{amount:,}".replace(",", " ") + " очков")

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


class SwitchCheckBox(QCheckBox):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 54, 30)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.42)
        text_rect = self.rect().adjusted(0, 0, -48, 0)
        painter.setPen(QColor("#D9D5E5"))
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.text())
        track = QRect(self.width() - 40, (self.height() - 18) // 2, 38, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#8048E6") if self.isChecked() else QColor("#30303D"))
        painter.drawRoundedRect(track, 9, 9)
        knob_x = track.right() - 14 if self.isChecked() else track.left() + 3
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRect(knob_x, track.top() + 3, 12, 12))
        painter.end()



class ChannelCard(QFrame):
    selected = Signal(str)

    def __init__(self, login, profile, balance, currency, slot, temporary, image_cache, remove_callback, edit_callback, move_callback, actions_enabled=True, parent=None):
        super().__init__(parent)
        self.login = login
        self.setObjectName("channelCard")
        self.setMinimumWidth(220)
        self.setMaximumWidth(360)
        self.setMinimumHeight(232)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(5)
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
        root.addLayout(badges)
        if slot:
            watch_row = QHBoxLayout()
            watch_row.addStretch()
            watch = QLabel("СМОТРИМ • СЛОТ " + str(slot))
            watch.setObjectName("watchBadge")
            watch.setMinimumHeight(24)
            watch_row.addWidget(watch)
            root.addLayout(watch_row)
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
        self.name.setMinimumHeight(20)
        root.addWidget(self.name)
        self.handle = QLabel("@" + login)
        self.handle.setObjectName("channelLogin")
        self.handle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.handle.setMinimumHeight(16)
        root.addWidget(self.handle)
        bottom = QHBoxLayout()
        self.currency_icon = QLabel("✦")
        self.currency_icon.setFixedSize(24, 24)
        self.currency_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bottom.addWidget(self.currency_icon)
        points = QLabel(f"{int(balance or 0):,}".replace(",", " "))
        points.setObjectName("channelBalance")
        bottom.addWidget(points)
        currency_name = QLabel(str(currency.get("name") or "Channel Points"))
        currency_name.setObjectName("mutedText")
        currency_name.setMinimumWidth(0)
        bottom.addWidget(currency_name, 1)
        root.addLayout(bottom)
        self.actions_panel = QFrame()
        self.actions_panel.setObjectName("channelActions")
        action_layout = QHBoxLayout(self.actions_panel)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(5)
        open_button = QPushButton("↗")
        open_button.setObjectName("cardAction")
        open_button.setFixedSize(29, 27)
        open_button.setToolTip("Открыть на Twitch")
        open_button.clicked.connect(lambda checked=False, value=login: QDesktopServices.openUrl(QUrl("https://www.twitch.tv/" + value)))
        action_layout.addWidget(open_button)
        edit = QPushButton("✎")
        edit.setObjectName("cardAction")
        edit.setFixedSize(29, 27)
        edit.setEnabled(bool(actions_enabled))
        edit.setToolTip("Редактировать канал")
        edit.clicked.connect(lambda checked=False, value=login: edit_callback(value))
        action_layout.addWidget(edit)
        move = QPushButton("⇄")
        move.setObjectName("cardAction")
        move.setFixedSize(29, 27)
        move.setEnabled(bool(actions_enabled))
        move.setToolTip("Переместить: выбери вторую карточку")
        move.clicked.connect(lambda checked=False, value=login: move_callback(value))
        action_layout.addWidget(move)
        remove = QPushButton("×")
        remove.setObjectName("smallDanger")
        remove.setFixedSize(29, 27)
        remove.setEnabled(bool(actions_enabled))
        remove.setToolTip("Удалить канал")
        remove.clicked.connect(lambda checked=False, value=login: remove_callback(value))
        action_layout.addWidget(remove)
        action_layout.addStretch()
        self.actions_opacity = QGraphicsOpacityEffect(self.actions_panel)
        self.actions_opacity.setOpacity(0.0)
        self.actions_panel.setGraphicsEffect(self.actions_opacity)
        self.actions_panel.setMaximumHeight(0)
        self.actions_panel.hide()
        self.actions_height_animation = QPropertyAnimation(self.actions_panel, b"maximumHeight", self)
        self.actions_height_animation.setDuration(170)
        self.actions_height_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.actions_height_animation.finished.connect(self._finish_actions_animation)
        self.actions_opacity_animation = QPropertyAnimation(self.actions_opacity, b"opacity", self)
        self.actions_opacity_animation.setDuration(150)
        self.actions_opacity_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        root.addWidget(self.actions_panel)
        if image_cache:
            image_cache.request(self.avatar, profile.get("avatar_url", ""), True)
            image_cache.request(self.currency_icon, currency.get("icon_url", ""), False)

    def _animate_actions(self, visible):
        self.actions_height_animation.stop()
        self.actions_opacity_animation.stop()
        if visible:
            self.actions_panel.show()
            self.actions_height_animation.setStartValue(max(0, self.actions_panel.maximumHeight()))
            self.actions_height_animation.setEndValue(31)
            self.actions_opacity_animation.setStartValue(self.actions_opacity.opacity())
            self.actions_opacity_animation.setEndValue(1.0)
        else:
            if not self.actions_panel.isVisible():
                return
            self.actions_height_animation.setStartValue(max(0, self.actions_panel.maximumHeight()))
            self.actions_height_animation.setEndValue(0)
            self.actions_opacity_animation.setStartValue(self.actions_opacity.opacity())
            self.actions_opacity_animation.setEndValue(0.0)
        self.actions_height_animation.start()
        self.actions_opacity_animation.start()

    def _finish_actions_animation(self):
        if self.actions_panel.maximumHeight() <= 0 and not self.underMouse():
            self.actions_panel.hide()

    def _hide_actions_if_outside(self):
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self._animate_actions(False)

    def enterEvent(self, event):
        self._animate_actions(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        QTimer.singleShot(40, self._hide_actions_if_outside)
        super().leaveEvent(event)

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


def _flag_enabled(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "paid", "subscription")
    return bool(value)


def _campaign_available(campaign):
    status = str(campaign.get("status") or "ACTIVE").strip().upper()
    return status not in ("EXPIRED", "ENDED", "COMPLETED", "CANCELLED", "CANCELED", "INACTIVE", "UPCOMING", "SCHEDULED")


def _eligible_free_drops(campaign):
    raw_drops = campaign.get("drops", [])
    if isinstance(raw_drops, dict):
        raw_drops = raw_drops.get("nodes") or raw_drops.get("edges") or []
        raw_drops = [item.get("node", item) if isinstance(item, dict) else item for item in raw_drops]
    result = []
    for item in raw_drops if isinstance(raw_drops, list) else []:
        if not isinstance(item, dict):
            continue
        if _flag_enabled(item.get("claimed")):
            continue
        if not str(item.get("image_url") or "").startswith("https://"):
            continue
        drop_type = str(item.get("type") or item.get("drop_type") or "watch").strip().lower()
        category = str(item.get("category") or "").strip().lower()
        if drop_type in ("subscription", "sub", "paid", "subscriber", "paid_subscription"):
            continue
        if category in ("subscription", "paid", "subscriber"):
            continue
        if _flag_enabled(item.get("is_subscription")) or _flag_enabled(item.get("paid")):
            continue
        result.append(dict(item))
    return result


def _campaign_card(campaign, drops):
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
    return campaign_copy


def eligible_watch_campaigns(campaigns):
    result = []
    for campaign in campaigns if isinstance(campaigns, list) else []:
        if not isinstance(campaign, dict) or not _campaign_available(campaign):
            continue
        drops = _eligible_free_drops(campaign)
        if not drops:
            continue
        result.append(_campaign_card(campaign, drops))
    result.sort(key=lambda item: max([int(drop.get("remaining_minutes") or 0) for drop in item["visible_drops"]] or [0]))
    return result


def eligible_twitch_campaigns(campaigns):
    result = []
    for campaign in campaigns if isinstance(campaigns, list) else []:
        if not isinstance(campaign, dict) or not _campaign_available(campaign):
            continue
        drops = []
        for drop in _eligible_free_drops(campaign):
            category = str(drop.get("category") or "").strip().lower()
            benefit_type = str(drop.get("benefit_type") or drop.get("benefitType") or "").strip().upper()
            if category in ("twitch", "profile") or benefit_type in ("BADGE", "EMOTE") or _flag_enabled(drop.get("is_twitch_view")):
                drops.append(drop)
        if not drops:
            continue
        result.append(_campaign_card(campaign, drops))
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


class ModalOverlay(QWidget):
    completed = Signal(bool, str)

    def __init__(self, parent, title, message, buttons=None, input_mode="", initial_value="", danger=False):
        super().__init__(parent)
        self.setObjectName("modalOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(parent.rect())
        self.editor = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch()
        center = QHBoxLayout()
        center.addStretch()
        card = QFrame(self)
        card.setObjectName("modalCard")
        card.setMinimumWidth(340)
        card.setMaximumWidth(500)
        box = QVBoxLayout(card)
        box.setContentsMargins(22, 20, 22, 18)
        box.setSpacing(12)
        heading = QLabel(str(title))
        heading.setObjectName("modalTitle")
        box.addWidget(heading)
        body = QLabel(str(message))
        body.setObjectName("modalMessage")
        body.setWordWrap(True)
        box.addWidget(body)
        if input_mode == "single":
            self.editor = QLineEdit()
            self.editor.setObjectName("modalInput")
            self.editor.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self.editor.setText(str(initial_value or ""))
            self.editor.selectAll()
            box.addWidget(self.editor)
        elif input_mode == "multiline":
            self.editor = QTextEdit()
            self.editor.setObjectName("modalEditor")
            self.editor.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self.editor.setPlainText(str(initial_value or ""))
            self.editor.setMinimumHeight(125)
            self.editor.setMaximumHeight(170)
            box.addWidget(self.editor)
        row = QHBoxLayout()
        row.addStretch()
        for label, accepted in buttons or [("Понятно", True)]:
            button = QPushButton(str(label))
            button.setObjectName("dangerButton" if accepted and danger else ("primaryButton" if accepted else "softButton"))
            button.setMinimumWidth(88)
            button.setFixedHeight(35)
            button.clicked.connect(lambda checked=False, value=bool(accepted): self._finish(value))
            row.addWidget(button)
        box.addLayout(row)
        center.addWidget(card)
        center.addStretch()
        outer.addLayout(center)
        outer.addStretch()
        if self.editor is not None:
            self.editor.setFocus()
        else:
            self.setFocus()

    def _finish(self, accepted):
        if self.editor is None:
            value = ""
        elif isinstance(self.editor, QLineEdit):
            value = self.editor.text()
        else:
            value = self.editor.toPlainText()
        self.hide()
        self.completed.emit(bool(accepted), value)
        self.deleteLater()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._finish(False)
            event.accept()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    installRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("mainWindow")
        self.setWindowTitle(APP_TITLE)
        if APP_ICON_FILE.is_file():
            self.setWindowIcon(QIcon(str(APP_ICON_FILE)))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setMinimumSize(980, 660)
        self.resize(1140, 760)
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
        self.channel_search_text = ""
        self.current_page = 0
        self._last_log_html = None
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
        self._modal_overlay = None
        self._startup_restore_requested = False
        self._account_profile_request_user = ""
        self._header_status_notice = None
        self.api.update_install_callback = self.installRequested.emit
        self.installRequested.connect(self._install_ready)
        self.ui_icon_atlas = QPixmap(str(UI_ICON_ATLAS))
        self.nav_icons = {}
        if not self.ui_icon_atlas.isNull():
            for name, index in UI_ICON_CELLS.items():
                self.nav_icons[name] = QIcon(self.ui_icon_atlas.copy((index % 3) * 48, (index // 3) * 48, 48, 48))
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
        self.toast = QLabel(shell)
        self.toast.setObjectName("toast")
        self.toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toast.setWordWrap(True)
        self.toast.hide()
        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(self.toast.hide)
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        self.title_bar = TitleBar()
        self.title_bar.searchChanged.connect(self._search_channels)
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
        self.sidebar.setFixedWidth(196)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(8, 12, 8, 12)
        side.setSpacing(5)
        logo = TwitchLogo(self.ui_icon_atlas.copy(0, 0, 48, 48) if not self.ui_icon_atlas.isNull() else QPixmap())
        side.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        brand = QLabel("TWITCH FARMING")
        brand.setObjectName("brandText")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(brand)
        side.addSpacing(12)
        self.nav_buttons = []
        nav_items = [
            ("Каналы", 0, "channels"),
            ("Инвентарь Drops", 1, "drops"),
            ("Фолловеры", 2, "irc"),
            ("Настройки", 3, "settings"),
            ("Настройки прогнозов", 4, "predictions"),
            ("События и журнал", 5, "events"),
            ("О проекте", 6, "bonuses")
        ]
        for title, index, icon_name in nav_items:
            button = QPushButton(title)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setFixedHeight(36)
            button.setIcon(self.nav_icons.get(icon_name, QIcon()))
            button.setIconSize(QSize(19, 19))
            button.clicked.connect(lambda checked=False, value=index: self._navigate(value))
            side.addWidget(button)
            self.nav_buttons.append(button)
        side.addStretch()
        account_footer = QFrame()
        account_footer.setObjectName("accountFooter")
        account_footer_row = QHBoxLayout(account_footer)
        account_footer_row.setContentsMargins(7, 6, 7, 6)
        account_footer_row.setSpacing(7)
        self.account_avatar_sidebar = QLabel("T")
        self.account_avatar_sidebar.setObjectName("accountAvatar")
        self.account_avatar_sidebar.setFixedSize(34, 34)
        self.account_avatar_sidebar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        account_footer_row.addWidget(self.account_avatar_sidebar)
        self.account_sidebar = QLabel("АККАУНТ НЕ ПОДКЛЮЧЁН")
        self.account_sidebar.setObjectName("sidebarAccount")
        self.account_sidebar.setWordWrap(True)
        self.account_sidebar.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        account_footer_row.addWidget(self.account_sidebar, 1)
        side.addWidget(account_footer)
        self.version_sidebar = QLabel("v" + APP_VERSION)
        self.version_sidebar.setObjectName("sidebarVersion")
        self.version_sidebar.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        self._build_initialization_overlay(shell)
        for widget_type in (QScrollBar, QLineEdit, QTextEdit, QComboBox):
            for widget in self.findChildren(widget_type):
                widget.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

    def _search_channels(self, value):
        self.channel_search_text = str(value or "").strip().lower()
        if self.channel_search_text and self.current_page != 0:
            self._navigate(0)
        self._render_channels()

    def _build_initialization_overlay(self, parent):
        self.initialization_overlay = QFrame(parent)
        self.initialization_overlay.setObjectName("initializationOverlay")
        outer = QVBoxLayout(self.initialization_overlay)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        card = QFrame()
        card.setObjectName("initializationCard")
        card.setMinimumWidth(330)
        card.setMaximumWidth(430)
        box = QVBoxLayout(card)
        box.setContentsMargins(26, 24, 26, 24)
        box.setSpacing(12)
        logo = TwitchLogo(self.ui_icon_atlas.copy(0, 0, 48, 48) if not self.ui_icon_atlas.isNull() else QPixmap())
        box.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        self.initialization_title = QLabel("Инициализация...")
        self.initialization_title.setObjectName("initializationTitle")
        self.initialization_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.initialization_title)
        self.initialization_text = QLabel("Подготавливаем данные и проверяем сохранённую сессию Twitch.")
        self.initialization_text.setObjectName("initializationText")
        self.initialization_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.initialization_text.setWordWrap(True)
        box.addWidget(self.initialization_text)
        self.initialization_progress = QProgressBar()
        self.initialization_progress.setRange(0, 0)
        self.initialization_progress.setTextVisible(False)
        self.initialization_progress.setFixedHeight(5)
        box.addWidget(self.initialization_progress)
        self.initialization_close = QPushButton("Закрыть приложение")
        self.initialization_close.setObjectName("dangerButton")
        self.initialization_close.clicked.connect(QApplication.quit)
        self.initialization_close.hide()
        box.addWidget(self.initialization_close)
        row.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()
        self.initialization_overlay.setGeometry(parent.rect())
        self.initialization_overlay.show()
        self.initialization_overlay.raise_()

    def _show_initialization_error(self, message):
        self.initialization_title.setText("Не удалось запустить приложение")
        self.initialization_text.setText(str(message or "Неизвестная ошибка")[:500])
        self.initialization_progress.hide()
        self.initialization_close.show()
        self.initialization_overlay.show()
        self.initialization_overlay.raise_()


    def _style(self):
        return """
        QWidget { color:#F0EDF7; font-family:"Segoe UI"; font-size:12px; }
        QMainWindow#mainWindow { background:#090911; border:0; }
        QFrame#shell { background:#0C0C15; border:1px solid #29263D; border-radius:0; }
        QFrame#titleBar { background:#10101A; border:0; border-bottom:1px solid #28263A; }
        QLineEdit#titleSearch { background:#191725; border:1px solid #302B48; border-radius:14px; padding:4px 12px; color:#E9E4F5; font-size:11px; }
        QLineEdit#titleSearch:focus { border-color:#7950BD; background:#211B31; }
        QLabel#titleStatus { background:#1A1825; border:1px solid #343047; border-radius:10px; padding:2px 7px; font-size:9px; font-weight:800; }
        QLabel#sessionTimer { color:#B8B1C8; font-family:Consolas; font-size:10px; padding:2px 5px; }
        QLabel#sessionPoints { color:#D9C4FF; font-size:10px; font-weight:800; padding:2px 4px; }
        QPushButton#windowControl { background:transparent; border:0; color:#A8A2B9; font-size:15px; border-radius:6px; }
        QPushButton#windowControl:hover { background:#282438; color:white; }
        QPushButton#windowControl:last-child:hover { background:#A83D55; }
        QFrame#initializationOverlay { background:rgba(8,8,14,238); border:0; }
        QFrame#initializationCard { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #211B31,stop:1 #151420); border:1px solid #51406D; border-radius:16px; }
        QLabel#initializationTitle { color:#F7F2FF; font-size:18px; font-weight:900; }
        QLabel#initializationText { color:#AAA2B9; font-size:11px; }
        QProgressBar { background:#242331; border:0; border-radius:3px; }
        QProgressBar::chunk { background:#9958EE; border-radius:3px; }
        QFrame#accountFooter { background:#191724; border:1px solid #322A43; border-radius:9px; }
        QLabel#accountAvatar, QLabel#accountAvatarLarge { background:#29243A; border:1px solid #5B477B; border-radius:26px; color:#D3BEFF; font-size:12px; font-weight:900; }
        QLabel#accountAvatar { border-radius:17px; }
        QLabel#accountAvatarLarge { border-radius:26px; font-size:16px; }
        QFrame#sidebar { background:#11101B; border:0; border-right:1px solid #2B263D; border-radius:0; }
        QLabel#brandText { color:#F5F1FF; font-size:11px; font-weight:900; letter-spacing:1px; }
        QPushButton#navButton { background:transparent; border:1px solid transparent; border-radius:9px; text-align:left; padding:5px 7px; color:#AFA9C0; font-size:10.5px; font-weight:650; }
        QPushButton#navButton:hover { background:#211D31; color:#FFFFFF; border-color:#302944; }
        QPushButton#navButton:checked { background:#352253; border-color:#65448F; color:#E7D8FF; }
        QPushButton#navButton:checked:hover { background:#3B275F; }
        QLabel#sidebarAccount { color:#E8E2F2; font-size:14px; font-weight:700; padding:1px 0; }
        QLabel#sidebarVersion { color:#6E687D; font-size:9px; padding:3px; }
        QStackedWidget#pageStack { background:#0C0C15; border:0; }
        QWidget#pageContent { background:#0C0C15; }
        QLabel#pageTitle { color:#FFFFFF; font-size:20px; font-weight:850; }
        QLabel#pageSubtitle { color:#8E879F; font-size:11px; }
        QLabel#sectionTitle { color:#F2EDFF; font-size:14px; font-weight:800; }
        QLabel#sectionEyebrow { color:#B596FF; font-size:9px; font-weight:900; letter-spacing:1px; }
        QLabel#mutedText, QLabel#smallMuted { color:#918BA2; }
        QLabel#smallMuted { font-size:10px; }
        QLabel#statusHint { color:#A49DB3; font-size:10px; }
        QLabel#toast { background:#211F2B; border:1px solid #51436F; border-radius:8px; padding:8px 14px; color:#F3EFFA; font-weight:700; }
        QWidget#modalOverlay { background-color:rgba(0, 0, 0, 185); }
        QLabel#modalTitle { color:#FFFFFF; font-size:16px; font-weight:800; }
        QLabel#modalMessage { color:#C8C2D2; font-size:12px; }
        QFrame#modalCard { background:#16161D; border:1px solid #262630; border-radius:10px; }
        QLineEdit#modalInput, QTextEdit#modalEditor { background:#101016; border:1px solid #343143; border-radius:7px; padding:8px; color:#F4F1F9; }
        QFrame#panel, QFrame#channelCard, QFrame#campaignCard, QFrame#followerCard, QFrame#settingCard, QFrame#feedCard { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1B1929,stop:1 #14131F); border:1px solid #37314D; border-radius:11px; }
        QFrame#channelCard:hover, QFrame#campaignCard:hover, QFrame#followerCard:hover, QFrame#settingCard:hover { border-color:#70569B; }
        QFrame#channelActions { background:transparent; border:0; }
        QFrame#followerCard[added="true"] { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #261E38,stop:1 #171522); border:1px solid #7655A5; }
        QPushButton#followerAddButton { background:#22212B; border:1px solid #383744; color:#C4BFCC; padding:5px 7px; border-radius:7px; font-size:9px; font-weight:700; }
        QPushButton#followerAddButton:hover { background:#2D2A38; border-color:#575164; }
        QPushButton#followerAddedButton { background:#302345; border:1px solid #7655A5; color:#DCCAFF; padding:5px 7px; border-radius:7px; font-size:9px; font-weight:800; }
        QPushButton#followerAddedButton:disabled { background:#302345; border:1px solid #7655A5; color:#DCCAFF; }
        QPushButton#followerAddedButton:hover { background:#3A2B52; }
        QFrame#channelCard[selected="true"] { background:#241A36; border-color:#A36BFF; }
        QPushButton#cardAction { background:#211E2C; border:1px solid #383247; color:#D2CBE0; padding:3px 5px; border-radius:6px; font-size:9px; font-weight:700; }
        QPushButton#cardAction:hover { background:#2B263B; border-color:#7656A2; }
        QPushButton#cardAction:disabled, QPushButton#smallDanger:disabled { color:#696575; background:#181720; border-color:#282632; }
        QLabel#channelAvatar { background:#29243A; border:1px solid #4A3A63; border-radius:32px; color:#C9B8F0; font-weight:800; }
        QLabel#slotAvatar, QLabel#miniAvatar { background:transparent; border:0; border-radius:0; color:#C9B8F0; font-weight:800; }
        QLabel#campaignImage { background:#241D36; border-radius:8px; color:#A98AFF; font-weight:800; }
        QLabel#rewardIcon { background:#242034; border-radius:8px; color:#E7D6FF; }
        QLabel#channelName { color:#FFFFFF; font-size:13px; font-weight:800; }
        QLabel#channelLogin { color:#9892A7; font-size:10px; }
        QLabel#channelBalance { color:#F2EDFF; font-size:11px; font-weight:800; }
        QLabel#liveBadge, QLabel#offlineBadge, QLabel#watchBadge, QLabel#temporaryBadge, QLabel#purpleBadge { font-size:8px; font-weight:900; padding:4px 6px; border-radius:5px; }
        QLabel#liveBadge { color:#FF8593; background:#36202A; }
        QLabel#offlineBadge { color:#8C8799; background:#252530; }
        QLabel#watchBadge { color:#CDB4FF; background:#302345; }
        QLabel#temporaryBadge { color:#F5C36B; background:#322A1B; }
        QLabel#purpleBadge { color:#D4BCFF; background:#302345; }
        QFrame#activeSlotCard { background:#201A30; border:1px solid #44335F; border-radius:8px; }
        QLabel#slotCaption { font-size:8px; font-weight:900; color:#AA89FF; }
        QLabel#slotName { font-size:11px; font-weight:800; color:#F7F4FF; }
        QLabel#slotBalance { color:#BDB5CC; font-size:9px; }
        QPushButton#primaryButton { background:#8748E8; border:1px solid #9D69F4; color:white; padding:6px 11px; border-radius:7px; font-size:10px; font-weight:800; }
        QPushButton#primaryButton:hover { background:#9B5BFA; }
        QPushButton#primaryButton:disabled { background:#393044; color:#777181; border-color:#393044; }
        QPushButton#softButton, QPushButton#secondaryButton { background:#211E2C; border:1px solid #383247; color:#D2CBE0; padding:6px 10px; border-radius:7px; font-size:10px; font-weight:700; }
        QPushButton#softButton:hover, QPushButton#secondaryButton:hover { background:#2B263B; border-color:#5B477D; }
        QPushButton#smallDanger { color:#A9A2B4; background:transparent; border:0; border-radius:6px; font-size:17px; }
        QPushButton#smallDanger:hover { color:#FF8593; background:#38212B; }
        QPushButton#dangerButton { background:#3A202B; border:1px solid #673343; color:#FF9BA7; padding:6px 10px; border-radius:7px; font-size:10px; }
        QPushButton:focus { outline:none; border:none; }
        QLineEdit, QTextEdit, QComboBox { background:#11111A; border:1px solid #343143; border-radius:7px; padding:6px 8px; color:#F4F1F9; selection-background-color:#7142B7; font-size:11px; }
        QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border-color:#8750D9; background:#171522; }
        QComboBox { padding-right:24px; }
        QComboBox::drop-down { subcontrol-origin:padding; subcontrol-position:top right; width:22px; border:0; border-left:1px solid #302B40; }
        QComboBox::down-arrow { width:7px; height:7px; border-right:2px solid #B9A0F2; border-bottom:2px solid #B9A0F2; }
        QComboBox QAbstractItemView { background:#181722; border:1px solid #383247; selection-background-color:#403058; color:#F5F2FC; outline:0; padding:4px; }
        QAbstractScrollArea, QAbstractScrollArea::viewport, QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget, QScrollArea QWidget#scrollContent { background-color:#0C0C15; border:0; }
        QScrollBar:vertical { background:transparent; width:6px; margin:1px 0; border:0; }
        QScrollBar::handle:vertical { background:#2A2A36; border-radius:3px; min-height:24px; }
        QScrollBar::handle:vertical:hover { background:#3E3E4E; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; border:0; }
        QScrollBar:horizontal { background:transparent; height:6px; margin:0 1px; border:0; }
        QScrollBar::handle:horizontal { background:#2A2A36; border-radius:3px; min-width:24px; }
        QScrollBar::handle:horizontal:hover { background:#3E3E4E; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; border:0; }
        QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical, QScrollBar::left-arrow:horizontal, QScrollBar::right-arrow:horizontal { width:0; height:0; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical, QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background:transparent; }
        QPushButton#dropsFilter { background:#171720; border:1px solid #302D3A; color:#AFAABD; padding:6px 8px; border-radius:7px; font-size:10px; font-weight:700; }
        QPushButton#dropsFilter:hover { background:#211D2D; border-color:#514064; color:#E6D9FF; }
        QPushButton#dropsFilter:checked { background:#302345; border-color:#7655A5; color:#E1D0FF; }
        QLabel#eventRow { color:#C2BCCD; padding:5px 0; border-bottom:1px solid #262331; font-size:10px; }
        QLabel#eventSuccess { color:#6FD7AE; padding:5px 0; border-bottom:1px solid #262331; font-size:10px; }
        QLabel#eventWarning { color:#F2C56E; padding:5px 0; border-bottom:1px solid #262331; font-size:10px; }
        QLabel#eventError { color:#FF8294; padding:5px 0; border-bottom:1px solid #262331; font-size:10px; }
        QLabel#eventReward { color:#C6A6FF; padding:5px 0; border-bottom:1px solid #262331; font-size:10px; }
        QLabel#rewardTitle, QLabel#streamerName { font-size:11px; font-weight:750; color:#F1EDF8; }
        QLabel#rewardCurrencyIcon { background:transparent; border:0; color:#D8C7F6; }
        QLabel#rewardAmount { color:#F6F2FF; font-size:12px; font-weight:800; }
        QLabel#followerMeta { color:#9E96B0; font-size:9px; }
        QLabel#statusHint { color:#AAA4B8; font-size:10px; }
        QTextEdit#logOutput { background:#0A0A10; border:1px solid #292737; border-radius:9px; color:#C7C1D1; font-family:Consolas; font-size:10px; padding:7px; }
        """

    def _page(self, title, subtitle):
        page = QWidget()
        page.setObjectName("pageContent")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(18, 15, 18, 14)
        page_layout.setSpacing(9)
        header = QVBoxLayout()
        header.setSpacing(3)
        label = QLabel(title)
        label.setObjectName("pageTitle")
        header.addWidget(label)
        sub = QLabel(subtitle)
        sub.setObjectName("pageSubtitle")
        sub.setWordWrap(True)
        header.addWidget(sub)
        page_layout.addLayout(header)
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_content = QWidget()
        scroll_content.setObjectName("scrollContent")
        scroll_content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 3, 7, 7)
        scroll_layout.setSpacing(10)
        scroll.setWidget(scroll_content)
        page_layout.addWidget(scroll, 1)
        self.stack.addWidget(page)
        return scroll_layout

    def _build_channels_page(self):
        page = QWidget()
        page.setObjectName("pageContent")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(7)
        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("Каналы")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        subtitle = QLabel("Управление каналами, двумя активными слотами просмотра и сбором наград.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        header.addWidget(subtitle)
        layout.addLayout(header)
        top = QHBoxLayout()
        self.farm_status = QLabel()
        self.farm_status.hide()
        self.session_label = QLabel()
        self.session_label.hide()
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
        panel_layout.setContentsMargins(12, 9, 12, 9)
        panel_layout.setSpacing(5)
        heading = QLabel("АКТИВНЫЙ ФАРМ СЕЙЧАС")
        heading.setObjectName("sectionEyebrow")
        panel_layout.addWidget(heading)
        slots = QHBoxLayout()
        slots.setSpacing(8)
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
        self.active_animation.finished.connect(self._active_panel_animation_finished)
        layout.addWidget(self.active_panel)
        row = QHBoxLayout()
        channel_heading = QLabel("Список каналов")
        channel_heading.setObjectName("sectionTitle")
        row.addWidget(channel_heading)
        row.addStretch()
        self.selection_hint = QLabel("Выбери две карточки по очереди, чтобы поменять их местами.")
        self.selection_hint.setObjectName("smallMuted")
        row.addWidget(self.selection_hint)
        layout.addLayout(row)
        self.channels_grid = QGridLayout()
        self.channels_grid.setContentsMargins(0, 0, 0, 0)
        self.channels_grid.setHorizontalSpacing(10)
        self.channels_grid.setVerticalSpacing(10)
        for column in range(3):
            self.channels_grid.setColumnStretch(column, 1)
        self.channels_wrap = QWidget()
        self.channels_wrap.setObjectName("scrollContent")
        self.channels_wrap.setLayout(self.channels_grid)
        self.channels_scroll = QScrollArea()
        self.channels_scroll.setObjectName("channelsScroll")
        self.channels_scroll.setWidgetResizable(True)
        self.channels_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.channels_scroll.setWidget(self.channels_wrap)
        layout.addWidget(self.channels_scroll, 1)
        feeds = QHBoxLayout()
        feeds.setContentsMargins(0, 0, 0, 0)
        feeds.setSpacing(10)
        self.rewards_feed = self._feed_panel("Последние награды")
        self.events_feed = self._feed_panel("События")
        feeds.addWidget(self.rewards_feed, 1)
        feeds.addWidget(self.events_feed, 1)
        feeds_row = QWidget()
        feeds_row.setLayout(feeds)
        feeds_row.setFixedHeight(178)
        layout.addWidget(feeds_row)
        self.stack.addWidget(page)

    def _feed_panel(self, title):
        frame = QFrame()
        frame.setObjectName("feedCard")
        box = QVBoxLayout(frame)
        box.setContentsMargins(10, 8, 10, 7)
        box.setSpacing(5)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        box.addWidget(heading)
        scroll = QScrollArea()
        scroll.setObjectName("feedScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("feedContent")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        frame.feed_layout = QVBoxLayout(content)
        frame.feed_layout.setContentsMargins(10 if title == "Последние награды" else 0, 0, 4, 0)
        frame.feed_layout.setSpacing(6)
        scroll.setWidget(content)
        box.addWidget(scroll, 1)
        frame.feed_scroll = scroll
        return frame

    def _build_drops_page(self):
        layout = self._page("Инвентарь Drops", "Все бесплатные награды за просмотр Twitch. Платные Drops для подписки не отображаются.")
        row = QHBoxLayout()
        self.drops_filter_index = 0
        self.drops_filter_group = QButtonGroup(self)
        self.drops_filter_group.setExclusive(True)
        filters = (
            "Все кампании",
            "Начатые (Инвентарь)",
            "Награды Twitch (значки и эмоуты за просмотр)"
        )
        for index, title in enumerate(filters):
            button = QPushButton(title)
            button.setObjectName("dropsFilter")
            button.setCheckable(True)
            button.setChecked(index == 0)
            self.drops_filter_group.addButton(button, index)
            row.addWidget(button)
        self.drops_filter_group.idClicked.connect(self._set_drops_filter)
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
        layout = self._page("Фолловеры", "Подписки Twitch. Добавляй нужные каналы в список для фарма.")
        row = QHBoxLayout()
        refresh = QPushButton("↻  Обновить")
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
        self.followers_grid = QGridLayout()
        self.followers_grid.setContentsMargins(0, 0, 0, 0)
        self.followers_grid.setHorizontalSpacing(10)
        self.followers_grid.setVerticalSpacing(10)
        for column in range(3):
            self.followers_grid.setColumnStretch(column, 1)
        self.followers_wrap = QWidget()
        self.followers_wrap.setLayout(self.followers_grid)
        layout.addWidget(self.followers_wrap)
        layout.addStretch()

    def _build_settings_page(self):
        layout = self._page("Настройки", "Аккаунт Twitch, поведение фарма и обслуживание приложения.")
        grid_wrap = QWidget()
        grid = QGridLayout(grid_wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        account = self._setting_panel("АККАУНТ TWITCH", "Подключение и сохранённая сессия.")
        identity_row = QHBoxLayout()
        identity_row.setSpacing(10)
        self.account_avatar = QLabel("T")
        self.account_avatar.setObjectName("accountAvatarLarge")
        self.account_avatar.setFixedSize(52, 52)
        self.account_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        identity_row.addWidget(self.account_avatar)
        identity_text = QVBoxLayout()
        identity_text.setSpacing(3)
        self.account_display_name = QLabel("Аккаунт не подключён")
        self.account_display_name.setObjectName("sectionTitle")
        identity_text.addWidget(self.account_display_name)
        self.account_status = QLabel("АККАУНТ НЕ ПОДКЛЮЧЁН")
        self.account_status.setObjectName("statusHint")
        identity_text.addWidget(self.account_status)
        identity_row.addLayout(identity_text, 1)
        account.layout().addLayout(identity_row)
        account_row = QHBoxLayout()
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Логин Twitch")
        account_row.addWidget(self.username_input, 1)
        self.login_button = QPushButton("Подключить")
        self.login_button.setObjectName("primaryButton")
        self.login_button.clicked.connect(self._login)
        account_row.addWidget(self.login_button)
        account.layout().addLayout(account_row)
        account_actions = QHBoxLayout()
        self.logout_button = QPushButton("Выйти")
        self.logout_button.setObjectName("secondaryButton")
        self.logout_button.clicked.connect(lambda: self._call("logout", message="Сессия Twitch завершена."))
        account_actions.addWidget(self.logout_button)
        open_folder = QPushButton("Папка с данными")
        open_folder.setObjectName("secondaryButton")
        open_folder.clicked.connect(lambda: self._call("open_app_folder", message="Папка данных открыта."))
        account_actions.addWidget(open_folder)
        account.layout().addLayout(account_actions)
        grid.addWidget(account, 0, 0)
        updater = self._setting_panel("ОБНОВЛЕНИЕ", "Проверка GitHub Releases и установка новой версии.")
        self.update_version = QLabel("Текущая версия: v" + APP_VERSION)
        self.update_version.setObjectName("statusHint")
        updater.layout().addWidget(self.update_version)
        self.update_status = QLabel("Проверить наличие новой версии.")
        self.update_status.setObjectName("mutedText")
        self.update_status.setWordWrap(True)
        updater.layout().addWidget(self.update_status)
        update_actions = QHBoxLayout()
        self.update_check_button = QPushButton("Проверить")
        self.update_check_button.setObjectName("primaryButton")
        self.update_check_button.clicked.connect(lambda: self._call("check_for_updates", message="Проверка GitHub Releases запущена."))
        update_actions.addWidget(self.update_check_button)
        self.update_download_button = QPushButton("Скачать и обновить")
        self.update_download_button.setObjectName("primaryButton")
        self.update_download_button.setVisible(False)
        self.update_download_button.clicked.connect(self._download_update)
        update_actions.addWidget(self.update_download_button)
        updater.layout().addLayout(update_actions)
        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(0)
        self.update_progress.setVisible(False)
        updater.layout().addWidget(self.update_progress)
        self.update_speed = QLabel("")
        self.update_speed.setObjectName("smallMuted")
        updater.layout().addWidget(self.update_speed)
        grid.addWidget(updater, 0, 1)
        options = self._setting_panel("ДЕЙСТВИЯ ФАРМА", "Включи только нужные автоматические действия.")
        self.option_checks = {}
        checks_grid = QGridLayout()
        checks_grid.setHorizontalSpacing(12)
        checks_grid.setVerticalSpacing(2)
        for index, (key, title) in enumerate(OPTION_NAMES):
            check = SwitchCheckBox(title)
            check.setObjectName("optionCheck")
            self.option_checks[key] = check
            check.toggled.connect(lambda value, target=check: target.setProperty("dirty", True))
            checks_grid.addWidget(check, index // 2, index % 2)
        options.layout().addLayout(checks_grid)
        grid.addWidget(options, 1, 0)
        app_settings = self._setting_panel("ПРИЛОЖЕНИЕ", "Поведение окна и локальный кэш.")
        self.app_setting_checks = {}
        app_labels = {
            "minimize_to_tray": "Сворачивать в трей",
            "auto_start_farm": "Запускать фарм автоматически"
        }
        app_grid = QGridLayout()
        app_grid.setHorizontalSpacing(10)
        for index, (key, default) in enumerate(DEFAULT_APP_SETTINGS.items()):
            check = SwitchCheckBox(app_labels.get(key, key))
            self.app_setting_checks[key] = check
            check.toggled.connect(lambda value, target=check: target.setProperty("dirty", True))
            app_grid.addWidget(check, index // 2, index % 2)
        app_settings.layout().addLayout(app_grid)
        cache_grid = QGridLayout()
        cache_grid.setHorizontalSpacing(6)
        clear_avatars = QPushButton("Кэш аватарок")
        clear_avatars.setObjectName("secondaryButton")
        clear_avatars.clicked.connect(lambda: self._clear_image_cache("clear_avatar_cache", "avatars"))
        cache_grid.addWidget(clear_avatars, 0, 0)
        clear_currency = QPushButton("Кэш валют")
        clear_currency.setObjectName("secondaryButton")
        clear_currency.clicked.connect(lambda: self._clear_image_cache("clear_currency_cache", "currency"))
        cache_grid.addWidget(clear_currency, 0, 1)
        refresh_branding = QPushButton("Обновить валюты")
        refresh_branding.setObjectName("secondaryButton")
        refresh_branding.clicked.connect(lambda: self._call("refresh_channel_points_branding", message="Обновление оформления запущено."))
        cache_grid.addWidget(refresh_branding, 1, 0, 1, 2)
        app_settings.layout().addLayout(cache_grid)
        grid.addWidget(app_settings, 1, 1)
        layout.addWidget(grid_wrap)
        save = QPushButton("Сохранить настройки")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save_settings)
        layout.addWidget(save)
        layout.addStretch()

    def _setting_panel(self, eyebrow, description):
        frame = QFrame()
        frame.setObjectName("settingCard")
        box = QVBoxLayout(frame)
        box.setContentsMargins(12, 10, 12, 10)
        box.setSpacing(6)
        heading = QLabel(eyebrow)
        heading.setObjectName("sectionEyebrow")
        box.addWidget(heading)
        text = QLabel(description)
        text.setObjectName("mutedText")
        text.setWordWrap(True)
        if description:
            box.addWidget(text)
        return frame

    def _build_bets_page(self):
        layout = self._page("Настройки прогнозов", "Автоматическое размещение ставок и фильтры участников.")
        presets = self._setting_panel("ПРЕСЕТЫ", "")
        presets.layout().setContentsMargins(10, 6, 10, 6)
        presets.layout().setSpacing(3)
        presets.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        preset_row = QHBoxLayout()
        self.bet_preset = QComboBox()
        self.bet_preset.setEditable(False)
        self.bet_preset.setMaxVisibleItems(12)
        self.bet_preset.setMinimumHeight(30)
        self.bet_preset.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.bet_preset.addItem("Оптимальный")
        self.bet_preset.currentTextChanged.connect(self._select_bet_preset)
        preset_row.addWidget(self.bet_preset, 1)
        self.bet_preset_name = QLineEdit()
        self.bet_preset_name.setPlaceholderText("Название пресета")
        preset_row.addWidget(self.bet_preset_name, 1)
        save_preset = QPushButton("Сохранить")
        save_preset.setObjectName("secondaryButton")
        save_preset.clicked.connect(self._save_bet_preset)
        preset_row.addWidget(save_preset)
        delete_preset = QPushButton("Удалить")
        delete_preset.setObjectName("dangerButton")
        delete_preset.clicked.connect(self._delete_bet_preset)
        preset_row.addWidget(delete_preset)
        presets.layout().addLayout(preset_row)
        layout.addWidget(presets)
        grid_wrap = QWidget()
        grid = QGridLayout(grid_wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        panel = self._setting_panel("СТРАТЕГИЯ И ЛИМИТЫ", "Основные параметры ставки.")
        self.bet_strategy = QComboBox()
        self.bet_strategy.setEditable(False)
        self.bet_strategy.setMaxVisibleItems(12)
        self.bet_strategy.setMinimumHeight(30)
        self.bet_strategy.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.bet_strategy.addItems(STRATEGIES)
        panel.layout().addWidget(QLabel("Стратегия"))
        panel.layout().addWidget(self.bet_strategy)
        values_grid = QGridLayout()
        values_grid.setHorizontalSpacing(8)
        values_grid.setVerticalSpacing(4)
        self.bet_percentage = QLineEdit()
        self.bet_gap = QLineEdit()
        self.bet_max = QLineEdit()
        self.bet_minimum = QLineEdit()
        self.bet_delay = QLineEdit()
        fields = (
            ("Процент баланса", self.bet_percentage),
            ("Разница процентов", self.bet_gap),
            ("Максимум баллов", self.bet_max),
            ("Минимум баллов", self.bet_minimum)
        )
        for index, (title, widget) in enumerate(fields):
            label = QLabel(title)
            label.setObjectName("smallMuted")
            values_grid.addWidget(label, (index // 2) * 2, index % 2)
            values_grid.addWidget(widget, (index // 2) * 2 + 1, index % 2)
        delay_label = QLabel("Задержка")
        delay_label.setObjectName("smallMuted")
        values_grid.addWidget(delay_label, 4, 0)
        values_grid.addWidget(self.bet_delay, 5, 0)
        self.bet_delay_mode = QComboBox()
        self.bet_delay_mode.addItems(["FROM_START", "FROM_END", "PERCENTAGE"])
        mode_label = QLabel("Отсчёт задержки")
        mode_label.setObjectName("smallMuted")
        values_grid.addWidget(mode_label, 4, 1)
        values_grid.addWidget(self.bet_delay_mode, 5, 1)
        panel.layout().addLayout(values_grid)
        self.bet_stealth = SwitchCheckBox("Скрытый режим")
        panel.layout().addWidget(self.bet_stealth)
        grid.addWidget(panel, 0, 0)
        filters = self._setting_panel("ФИЛЬТР УЧАСТНИКОВ", "Ставка размещается только при совпадении условия.")
        self.bet_filter_enabled = SwitchCheckBox("Включить фильтр")
        filters.layout().addWidget(self.bet_filter_enabled)
        self.bet_filter_by = QComboBox()
        self.bet_filter_by.setEditable(False)
        self.bet_filter_by.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.bet_filter_by.setMaxVisibleItems(12)
        self.bet_filter_by.setMinimumHeight(30)
        self.bet_filter_by.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.bet_filter_by.addItems(FILTER_KEYS)
        self.bet_filter_condition = QComboBox()
        self.bet_filter_condition.setEditable(False)
        self.bet_filter_condition.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.bet_filter_condition.setMaxVisibleItems(8)
        self.bet_filter_condition.setMinimumHeight(30)
        self.bet_filter_condition.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.bet_filter_condition.addItems(["GT", "LT", "GTE", "LTE"])
        self.bet_filter_value = QLineEdit()
        self.bet_filter_value.setPlaceholderText("Значение")
        filter_fields = QGridLayout()
        filter_fields.setHorizontalSpacing(7)
        filter_fields.addWidget(QLabel("Показатель"), 0, 0, 1, 2)
        filter_fields.addWidget(self.bet_filter_by, 1, 0, 1, 2)
        filter_fields.addWidget(QLabel("Условие"), 2, 0)
        filter_fields.addWidget(QLabel("Значение"), 2, 1)
        filter_fields.addWidget(self.bet_filter_condition, 3, 0)
        filter_fields.addWidget(self.bet_filter_value, 3, 1)
        filters.layout().addLayout(filter_fields)
        grid.addWidget(filters, 0, 1)
        self.bet_filter_by.setEnabled(False)
        self.bet_filter_condition.setEnabled(False)
        self.bet_filter_value.setEnabled(False)
        self.bet_filter_enabled.toggled.connect(self._set_bet_filter_enabled)
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
        layout.addWidget(grid_wrap)
        save = QPushButton("Сохранить параметры прогнозов")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save_bets)
        layout.addWidget(save)
        layout.addStretch()

    def _build_logs_page(self):
        layout = self._page("События и журнал", "Важные события и технический журнал фарма.")
        row = QHBoxLayout()
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("Фильтр по тексту...")
        self.log_search.textChanged.connect(self._render_logs)
        row.addWidget(self.log_search, 1)
        self.log_level = QComboBox()
        self.log_level.addItems(["Основные", "Все уровни", "INFO", "WARNING", "ERROR", "DEBUG"])
        self.log_level.setCurrentIndex(0)
        self.log_level.currentIndexChanged.connect(self._render_logs)
        row.addWidget(self.log_level)
        clear = QPushButton("Очистить журнал")
        clear.setObjectName("secondaryButton")
        clear.clicked.connect(self._clear_logs)
        row.addWidget(clear)
        layout.addLayout(row)
        self.log_output = QTextEdit()
        self.log_output.setObjectName("logOutput")
        self.log_output.setReadOnly(True)
        layout.addWidget(self.log_output, 1)

    def _build_about_page(self):
        layout = self._page("О проекте", "Twitch Farming — приложение для сбора Channel Points и Drops.")
        panel = self._setting_panel("TWITCH FARMING", "Нативное приложение на PySide6-Essentials (Qt6).")
        version = QLabel("Версия v" + APP_VERSION)
        version.setObjectName("sectionTitle")
        panel.layout().addWidget(version)
        author = QLabel("Автор и дизайн: Dv4roka")
        author.setObjectName("statusHint")
        panel.layout().addWidget(author)
        assistants = QLabel("AI-ассистенты: ChatGPT & Google Gemini")
        assistants.setObjectName("statusHint")
        panel.layout().addWidget(assistants)
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
        if error and key not in ("startup", "session-restore"):
            self._notify("Ошибка: " + error)
        if callback:
            callback(result, error)
        elif result and isinstance(result, dict) and result.get("message"):
            self._notify(str(result.get("message")))

    def _set_header_status_notice(self, message, color="#B596FF", seconds=4.0):
        self._header_status_notice = (str(message or "")[:18], str(color), time.monotonic() + seconds)
        self.title_bar.set_status(self._header_status_notice[0], self._header_status_notice[1])

    def _call(self, method, *args, message=""):
        function = getattr(self.api, method)
        update_action = method in ("check_for_updates", "download_update")
        if update_action:
            label = "ПРОВЕРКА ОБНОВЛЕНИЙ" if method == "check_for_updates" else "ЗАГРУЗКА ОБНОВЛЕНИЯ"
            self._set_header_status_notice(label, "#B596FF", 5.0)
        def completed(result, error):
            if update_action:
                if error or (isinstance(result, dict) and result.get("ok") is False):
                    self._set_header_status_notice("ОШИБКА ОБНОВЛЕНИЯ", "#FF8294", 5.0)
                return
            if not error and message:
                self._notify(message)
            if not error and isinstance(result, dict) and result.get("message"):
                self._notify(str(result["message"]))
        self._run_task(method, function, args, completed)


    def _startup_finished(self, result, error):
        if error:
            self._show_initialization_error("Не удалось подготовить backend: " + error)
            return
        startup_info = self.api.get_startup_status()
        if startup_info.get("error"):
            self._show_initialization_error(startup_info.get("error"))
            return
        self.initialization_text.setText("Проверяем сохранённую сессию Twitch...")
        self._run_task("session-restore", self.api.begin_session_restore, (), self._session_restore_finished)

    def _session_restore_finished(self, result, error):
        if error:
            self._show_initialization_error("Не удалось проверить сохранённую сессию: " + error)
            return
        self._startup_restore_requested = True
        self._poll_state()

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
        auth = auth or {}
        self._update_account_identity(state.get("account_profile") or {}, auth)
        self._request_account_profile(state.get("account_profile") or {}, auth)
        self._update_initialization_state(auth)
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
        self.version_sidebar.setText("v" + str(self.latest_state.get("app_version") or APP_VERSION))

    def _set_avatar_label(self, label, url, fallback):
        image_url = str(url or "")
        previous = str(label.property("avatarUrl") or "")
        if previous == image_url:
            return
        label.setProperty("avatarUrl", image_url)
        label.clear()
        label.setText(str(fallback or "T")[:1].upper())
        if image_url.startswith("https://"):
            self.image_cache.request(label, image_url, True)

    def _update_account_identity(self, profile, auth):
        username = str(auth.get("username") or self.latest_state.get("authenticated_username") or self.latest_state.get("username") or "")
        display_name = str(profile.get("display_name") or username or "Аккаунт не подключён")
        avatar_url = str(profile.get("avatar_url") or "")
        self.account_display_name.setText(display_name)
        self.account_sidebar.setText(display_name if username else "АККАУНТ НЕ ПОДКЛЮЧЁН")
        self._set_avatar_label(self.account_avatar, avatar_url, display_name or username or "T")
        self._set_avatar_label(self.account_avatar_sidebar, avatar_url, display_name or username or "T")

    def _request_account_profile(self, profile, auth):
        username = str(auth.get("username") or self.latest_state.get("authenticated_username") or "").strip().lower()
        if not auth.get("authenticated"):
            self._account_profile_request_user = ""
            return
        if not username or username == self._account_profile_request_user:
            return
        self._account_profile_request_user = username
        if not profile.get("loaded"):
            self._run_task("account-profile", self.api.refresh_channel_profiles, ([],), lambda result, error: None)

    def _update_initialization_state(self, auth):
        if not self._startup_restore_requested or not self.initialization_overlay.isVisible():
            return
        state = str(auth.get("worker_state") or "").lower()
        waiting = bool(auth.get("worker_alive") or auth.get("restore_pending") or state in ("restoring", "running"))
        if not waiting:
            self.initialization_overlay.hide()

    def _render_state(self):
        state = self.latest_state
        status = str(state.get("status_text") or "● НЕ ЗАПУЩЕН")
        color = str(state.get("status_color") or "#9BA3B4")
        notice = self._header_status_notice
        if notice and time.monotonic() < notice[2]:
            self.title_bar.set_status(notice[0], notice[1])
        else:
            self._header_status_notice = None
            self.title_bar.set_status(status, color)
        self.farm_status.setText(status)
        self.farm_status.setStyleSheet("color:" + color + ";")
        running = bool(state.get("session_running"))
        self.farm_button.setText("■  Остановить фарм" if running else "▶  Запустить фарм")
        self.add_channel_button.setEnabled(not running)
        self.add_channel_button.setToolTip("Остановите фарм, чтобы изменить список каналов." if running else "")
        self.username_input.setEnabled(not running)
        self.login_button.setEnabled(not running)
        self.logout_button.setEnabled(not running)
        account_tip = "Остановите фарм, чтобы сменить аккаунт" if running else ""
        self.login_button.setToolTip(account_tip)
        self.logout_button.setToolTip(account_tip)
        self._update_session_clock()
        self.title_bar.set_gained_total(state.get("gained_total", 0))
        self._render_active_slots()
        signature = (
            tuple(state.get("channels", [])),
            json.dumps(state.get("channel_profiles", {}), sort_keys=True, ensure_ascii=False),
            json.dumps(state.get("channel_balances", {}), sort_keys=True),
            json.dumps(state.get("channel_currency", {}), sort_keys=True, ensure_ascii=False),
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
            row.setContentsMargins(0, 1, 0, 1)
            row.setSpacing(9)
            icon = QLabel()
            icon.setFixedSize(30, 30)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon.setObjectName("rewardCurrencyIcon")
            icon.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
            details = QVBoxLayout()
            details.setContentsMargins(0, 0, 0, 0)
            details.setSpacing(2)
            details.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            title = QLabel(str(item.get("title") or item.get("channel_login") or "Награда"))
            title.setObjectName("rewardTitle")
            title.setWordWrap(True)
            title.setMinimumWidth(0)
            title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            details.addWidget(title)
            description = str(item.get("description") or "").strip()
            currency_name = str(item.get("currency_name") or "").strip()
            amount_text = description or currency_name or str(item.get("channel_login") or "")
            if currency_name and description and currency_name.lower() not in description.lower() and description.startswith("+"):
                amount_text = description + " " + currency_name
            amount = QLabel(amount_text)
            amount.setObjectName("rewardAmount")
            amount.setWordWrap(True)
            amount.setMinimumWidth(0)
            amount.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            details.addWidget(amount)
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
            lower = item.lower()
            if any(word in lower for word in ("ошибка", "error", "failed", "не удалось")):
                label.setObjectName("eventError")
            elif any(word in lower for word in ("warning", "предупреждение", "огранич")):
                label.setObjectName("eventWarning")
            elif any(word in lower for word in ("награда", "drop", "балл", "reward")):
                label.setObjectName("eventReward")
            elif any(word in lower for word in ("запущен", "работает", "подключ", "заверш")):
                label.setObjectName("eventSuccess")
            else:
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

    def _active_panel_animation_finished(self):
        if self._active_panel_target == 0:
            self.active_panel.setVisible(False)

    def _render_active_slots(self):
        state = self.latest_state
        running = bool(state.get("session_running"))
        slots = state.get("watch_slots") or []
        has_active = running or bool(slots)
        target = 118 if has_active else 0
        if target != self._active_panel_target:
            self._active_panel_target = target
            self.active_animation.stop()
            if has_active:
                self.active_panel.setVisible(True)
            current = self.active_panel.maximumHeight()
            self.active_animation.setStartValue(current)
            self.active_animation.setEndValue(target)
            self.active_animation.start()
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
        actions_enabled = not bool(state.get("session_running"))
        search = str(getattr(self, "channel_search_text", "") or "").strip().lower()
        visible = [
            login for login in channels
            if not search
            or search in str(login).lower()
            or search in str(profiles.get(login, {}).get("display_name") or "").lower()
        ]
        for index, login in enumerate(visible):
            profile = profiles.get(login, {})
            card = ChannelCard(
                login,
                profile,
                balances.get(login, 0),
                currency.get(login, {}),
                watch.get(login),
                login in temporary,
                self.image_cache,
                self._remove_channel,
                self._edit_channel,
                self._select_channel,
                actions_enabled
            )
            card.selected.connect(self._select_channel)
            card.setProperty("selected", login == self.selected_channel)
            card.style().unpolish(card)
            card.style().polish(card)
            self.channels_grid.addWidget(card, index // 3, index % 3)
        if not channels:
            empty_text = "Список каналов пуст. Добавь первый Twitch-канал."
        elif not visible:
            empty_text = "По запросу ничего не найдено."
        else:
            empty_text = ""
        if empty_text:
            empty = QLabel(empty_text)
            empty.setObjectName("mutedText")
            self.channels_grid.addWidget(empty, 0, 0)

    def _search_channels(self, value):
        self.channel_search_text = str(value or "").strip().lower()
        if self.channel_search_text and self.current_page != 0:
            self._navigate(0)
        self._render_channels()


    def _select_channel(self, login):
        if self.latest_state.get("session_running"):
            self._notify("Останови фарм перед изменением порядка каналов.")
            return
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
        self._show_dialog(
            "Удалить канал",
            "Удалить @" + login + " из списка?",
            buttons=[("Отмена", False), ("Удалить", True)],
            danger=True,
            callback=lambda accepted, value: self._call("remove_channel", login, message="Канал удалён.") if accepted else None
        )

    def _edit_channel(self, login):
        if self.latest_state.get("session_running"):
            self._notify("Останови фарм перед изменением каналов.")
            return
        self._show_dialog(
            "Редактировать канал",
            "Укажи новый Twitch-логин или ссылку для @" + login + ":",
            input_mode="single",
            initial_value=login,
            callback=lambda accepted, value: self._call("save_channel_editor", "edit", login, value, [], message="Канал сохранён.") if accepted and value.strip() else None
        )

    def _add_channels(self):
        if self.latest_state.get("session_running"):
            self._notify("Останови фарм перед изменением каналов.")
            return
        self._show_dialog(
            "Добавить каналы",
            "Вставь Twitch-логины или ссылки, по одному на строку:",
            input_mode="multiline",
            callback=lambda accepted, value: self._call("save_channel_editor", "add", "", value, [], message="Каналы добавлены.") if accepted and value.strip() else None
        )

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
        if self.latest_state.get("session_running"):
            self._notify("Остановите фарм, чтобы сменить аккаунт.")
            return
        username = self.username_input.text().strip()
        if not username:
            self._show_dialog("Twitch", "Укажи логин Twitch.")
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
            self._show_dialog("Прогнозы", "Проверь числовые параметры.")
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
            self._show_dialog("Пресеты", "Введи название пресета.")
            return
        try:
            values = self._collect_bet_values()
        except ValueError:
            self._show_dialog("Пресеты", "Проверь числовые параметры.")
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
            self._show_dialog("Пресеты", "Стандартный пресет нельзя удалить.")
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

    def _set_bet_filter_enabled(self, enabled):
        enabled = bool(enabled)
        self.bet_filter_by.setEnabled(enabled)
        self.bet_filter_condition.setEnabled(enabled)
        self.bet_filter_value.setEnabled(enabled)

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

    def _set_drops_filter(self, index):
        self.drops_filter_index = int(index)
        self._last_campaign_signature = None
        self._render_drops()

    def _render_drops(self):
        if not hasattr(self, "drops_list"):
            return
        campaigns_all = eligible_watch_campaigns(self.drops_state.get("campaigns", []))
        filter_index = int(getattr(self, "drops_filter_index", 0))
        if filter_index == 1:
            campaigns = [item for item in campaigns_all if item.get("started") or item.get("in_inventory")]
        elif filter_index == 2:
            campaigns = eligible_twitch_campaigns(self.drops_state.get("campaigns", []))
        else:
            campaigns = campaigns_all
        session_running = bool(self.latest_state.get("session_running"))
        signature = json.dumps([campaigns, filter_index, self.drops_state.get("loading"), self.drops_state.get("error"), session_running], sort_keys=True, ensure_ascii=False)
        if signature == self._last_campaign_signature:
            return
        self._last_campaign_signature = signature
        self._clear_layout(self.drops_list)
        if self.drops_state.get("loading"):
            self.drops_status.setText("Загрузка ViewerDropsDashboard и Inventory…")
        elif self.drops_state.get("error"):
            self.drops_status.setText("Ошибка загрузки: " + str(self.drops_state.get("error")))
        else:
            self.drops_status.setText(f"Найдено кампаний с бесплатными наградами за просмотр: {len(campaigns)}")
        if not campaigns:
            empty = QLabel("Подходящих бесплатных Drops за просмотр сейчас нет. Обнови кампании после входа в Twitch.")
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
        if not hasattr(self, "followers_grid"):
            return
        configured = {str(login).strip().lower() for login in self.latest_state.get("channels", [])}
        items = [dict(item) for item in self.followed_state.get("channels", []) if isinstance(item, dict)]
        items.sort(
            key=lambda item: (
                str(item.get("login") or "").strip().lower() in configured,
                bool(item.get("is_live")),
                max(0, int(item.get("viewer_count") or 0)),
            ),
            reverse=True,
        )
        session_running = bool(self.latest_state.get("session_running"))
        signature = json.dumps([items, session_running, sorted(configured)], sort_keys=True, ensure_ascii=False)
        if signature == self._last_followed_signature:
            return
        self._last_followed_signature = signature
        self._clear_layout(self.followers_grid)
        self.followers_status.setText(
            "Загрузка подписок…" if self.followed_state.get("loading")
            else str(self.followed_state.get("error") or f"Найдено подписок: {len(items)}")
        )
        for index, item in enumerate(items):
            login = str(item.get("login") or "").strip().lower()
            added = login in configured
            frame = QFrame()
            frame.setObjectName("followerCard")
            frame.setProperty("added", added)
            frame.setMinimumHeight(72)
            frame.style().unpolish(frame)
            frame.style().polish(frame)
            row = QHBoxLayout(frame)
            row.setContentsMargins(8, 7, 8, 7)
            row.setSpacing(7)
            avatar = QLabel()
            avatar.setFixedSize(40, 40)
            avatar.setObjectName("miniAvatar")
            avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            avatar.setText(str(item.get("display_name") or login or "?")[:1].upper())
            row.addWidget(avatar)
            details = QVBoxLayout()
            details.setSpacing(2)
            name = QLabel(str(item.get("display_name") or login or "Канал"))
            name.setObjectName("streamerName")
            name.setWordWrap(False)
            details.addWidget(name)
            followed_at = str(item.get("followed_at") or "").strip()
            followed_date = ""
            if followed_at:
                try:
                    followed_date = datetime.fromisoformat(followed_at.replace("Z", "+00:00")).strftime("%d.%m.%Y")
                except (TypeError, ValueError):
                    followed_date = followed_at[:10]
            if item.get("is_live"):
                meta_text = "@" + login + " · В ЭФИРЕ"
            elif followed_date:
                meta_text = "@" + login + " · с " + followed_date
            else:
                meta_text = "@" + login
            meta = QLabel(meta_text)
            meta.setObjectName("followerMeta")
            details.addWidget(meta)
            row.addLayout(details, 1)
            add = QPushButton("✓ Добавлен" if added else "+ Добавить")
            add.setObjectName("followerAddedButton" if added else "followerAddButton")
            add.setFixedWidth(82)
            add.setFixedHeight(27)
            add.setEnabled(not session_running and not added)
            add.setToolTip("Канал уже находится в списке." if added else ("Остановите фарм, чтобы изменить каналы." if session_running else "Добавить канал в список фарма"))
            add.clicked.connect(lambda checked=False, value=login: self._call("add_followed_channel", value, message="Канал добавлен."))
            row.addWidget(add)
            self.followers_grid.addWidget(frame, index // 3, index % 3)
            self.image_cache.request(avatar, item.get("avatar_url", ""), True)
        if not items and not self.followed_state.get("loading") and not self.followed_state.get("error"):
            empty = QLabel("Подписок не найдено. Обнови список после подключения Twitch.")
            empty.setObjectName("mutedText")
            self.followers_grid.addWidget(empty, 0, 0)



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
        status_labels = {
            "checking": ("ПРОВЕРКА ОБНОВЛЕНИЙ", "#B596FF"),
            "downloading": ("ЗАГРУЗКА ОБНОВЛЕНИЯ", "#B596FF"),
            "extracting": ("РАСПАКОВКА ОБНОВЛЕНИЯ", "#B596FF"),
            "installing": ("УСТАНОВКА ОБНОВЛЕНИЯ", "#B596FF"),
            "available": ("ЕСТЬ ОБНОВЛЕНИЕ", "#75D5A6"),
            "error": ("ОШИБКА ОБНОВЛЕНИЯ", "#FF8294"),
        }
        if phase in status_labels:
            label, color = status_labels[phase]
            self._set_header_status_notice(label, color, 2.0)

    def _download_update(self):
        if self.latest_state.get("session_running"):
            self._set_header_status_notice("ОСТАНОВИТЕ ФАРМ", "#F0C36B", 5.0)
            return
        self._call("download_update", message="Скачивание обновления запущено.")

    def _clear_logs(self):
        def completed(result, error):
            if error or not isinstance(result, dict) or not result.get("ok"):
                if not error:
                    self._notify(str((result or {}).get("message") or "Не удалось очистить журнал."))
                return
            self.last_log_id = max(self.last_log_id, int(result.get("last_log_id") or 0))
            self._log_cache.clear()
            self.latest_state["logs"] = []
            self.log_output.clear()
            self._last_log_html = None
            self._notify(str(result.get("message") or "Журнал очищен."))
        self._run_task("clear-logs", self.api.clear_logs, (), completed)

    def _render_logs(self):
        if not hasattr(self, "log_output"):
            return
        rows = self.latest_state.get("logs", [])
        search = self.log_search.text().strip().lower()
        level = self.log_level.currentText()
        colors = {
            "DEBUG": "#777185",
            "INFO": "#C8C2D4",
            "WARNING": "#F0C36B",
            "ERROR": "#FF8294",
            "CRITICAL": "#FF657E",
            "FATAL": "#FF657E"
        }
        debug_signatures = (
            "[debug]",
            "starting new https connection",
            "starting new http connection",
            "httpsconnectionpool(",
            "httpconnectionpool(",
            "urllib3.connectionpool",
            "head /v1/segment",
            "get /v1/segment",
        )
        rendered = []
        for item in rows[-500:]:
            line = str(item.get("line") or item.get("message") or item)
            lower_line = line.lower()
            severity = str(item.get("level") or "INFO").upper()
            looks_debug = severity == "DEBUG" or any(marker in lower_line for marker in debug_signatures)
            if search and search not in lower_line:
                continue
            if level in ("Основные", "INFO") and looks_debug:
                continue
            if level not in ("Основные", "Все уровни") and severity != level:
                continue
            color = colors.get(severity, "#C8C2D4")
            rendered.append(f'<div style="color:{color}; white-space:pre-wrap;">{html.escape(line)}</div>')
        document = '<html><body style="background:#0A0A10; margin:0; font-family:Consolas; font-size:10px;">' + "".join(rendered) + "</body></html>"
        if document != self._last_log_html:
            self._last_log_html = document
            self.log_output.setHtml(document)
            scrollbar = self.log_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())



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

    def _show_dialog(self, title, message, buttons=None, input_mode="", initial_value="", danger=False, callback=None):
        if self._modal_overlay is not None:
            self._modal_overlay.hide()
            self._modal_overlay.deleteLater()
        parent = self.centralWidget()
        overlay = ModalOverlay(parent, title, message, buttons, input_mode, initial_value, danger)
        self._modal_overlay = overlay
        def finished(accepted, value):
            if self._modal_overlay is overlay:
                self._modal_overlay = None
            if callback:
                callback(accepted, value)
        overlay.completed.connect(finished)
        overlay.setGeometry(parent.rect())
        overlay.raise_()
        overlay.show()
        if overlay.editor is not None:
            overlay.editor.setFocus()
        else:
            overlay.setFocus()

    def _position_toast(self):
        if not hasattr(self, "toast") or self.toast is None or not self.toast.isVisible():
            return
        parent = self.centralWidget()
        width = min(max(self.toast.sizeHint().width() + 30, 240), max(240, parent.width() - 40), 560)
        self.toast.setFixedWidth(width)
        self.toast.adjustSize()
        self.toast.move((parent.width() - self.toast.width()) // 2, max(12, parent.height() - self.toast.height() - 20))
        self.toast.raise_()

    def _notify(self, message):
        if not hasattr(self, "toast"):
            return
        text_value = str(message)
        if "обновлен" in text_value.lower():
            self._set_header_status_notice(text_value.upper(), "#B596FF", 5.0)
            return
        self.toast.setText(text_value)
        self._position_toast()
        self.toast.show()
        self.toast.raise_()
        self.toast_timer.start(3500)

    def _install_ready(self):
        self._set_header_status_notice("ОБНОВЛЕНИЕ ГОТОВО", "#75D5A6", 8.0)
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        parent = self.centralWidget()
        if hasattr(self, "initialization_overlay"):
            self.initialization_overlay.setGeometry(parent.rect())
            if self.initialization_overlay.isVisible():
                self.initialization_overlay.raise_()
        if self._modal_overlay is not None:
            self._modal_overlay.setGeometry(parent.rect())
            self._modal_overlay.raise_()
        self._position_toast()

    def closeEvent(self, event):
        if self.latest_state.get("app_settings", {}).get("minimize_to_tray") and self.tray and self.tray.isVisible():
            self.hide()
            event.ignore()
            return
        event.accept()
        self._shutdown_and_exit()


def main():
    if not acquire_single_instance():
        return
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    if APP_ICON_FILE.is_file():
        app.setWindowIcon(QIcon(str(APP_ICON_FILE)))
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
