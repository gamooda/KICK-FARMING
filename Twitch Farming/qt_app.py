import ctypes
import hashlib
import html
import json
import logging
import math
import random
import re
import sys
import time
import urllib.request
import weakref
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QEasingCurve, QPoint, QPointF, QRect, QRectF, QPropertyAnimation, Property, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal, QObject, QUrl
from PySide6.QtGui import QAction, QColor, QCursor, QDesktopServices, QPainter, QPainterPath, QPen, QPolygonF, QPixmap, QIcon, QIconEngine
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


SVG_ICON_PATHS = {
    "external": '<path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M17 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h6"/>',
    "swap": '<path d="M4 8h15"/><path d="m15 4 4 4-4 4"/><path d="M20 16H5"/><path d="m9 12-4 4 4 4"/>',
    "settings": "<circle cx=\"12\" cy=\"12\" r=\"3\"/><path d=\"M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z\"/>",
    "trash": '<path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="m6 7 1 13h10l1-13"/><path d="M9 7V4h6v3"/>',
    "minimize": '<path d="M5 18h14"/>',
    "maximize": '<rect x="5" y="5" width="14" height="14" rx="1.5"/>',
    "restore": '<rect x="8" y="4" width="12" height="12" rx="1.5"/><path d="M16 16v3a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1h3"/>',
    "close": '<path d="m6 6 12 12M18 6 6 18"/>',
    "plus": "<rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"4\"/><line x1=\"12\" y1=\"8\" x2=\"12\" y2=\"16\"/><line x1=\"8\" y1=\"12\" x2=\"16\" y2=\"12\"/>",
    "play": '<path d="m8 5 11 7-11 7V5Z"/>',
    "stop": '<rect x="6" y="4" width="4" height="16" rx="1.5"/><rect x="14" y="4" width="4" height="16" rx="1.5"/>',
    "refresh": "<path d=\"M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8\"/><path d=\"M21 3v5h-5\"/><path d=\"M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16\"/><path d=\"M8 16H3v5\"/>",
    "folder": '<path d="M3.5 7.5h6l2 2h9v9a1.5 1.5 0 0 1-1.5 1.5h-14A1.5 1.5 0 0 1 3.5 18.5v-11Z"/><path d="M3.5 9.5V6.8A1.8 1.8 0 0 1 5.3 5h4.2l2 2H18"/>',
    "save": '<path d="M5 4h12l3 3v13H4V4h1Z"/><path d="M8 4v6h8V4M8 20v-6h8v6"/>',
    "logout": '<path d="M10 5H5a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h5"/><path d="M14 8l4 4-4 4M18 12H9"/>',
    "login": '<path d="M14 5h5a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1h-5"/><path d="m10 8-4 4 4 4M6 12h10"/>',
    "download": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 19v2h16v-2"/>',
    "palette": '<path d="M12 3a9 9 0 1 0 0 18h1.2a1.8 1.8 0 0 0 1.4-3c-.9-1.1-.1-2.7 1.3-2.7H18a3 3 0 0 0 3-3A9 9 0 0 0 12 3Z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7.5" r="1"/><circle cx="15" cy="7.5" r="1"/><circle cx="17" cy="11" r="1"/>',
    "image": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="m5 17 5-5 3.5 3.5 2.5-2.5 3 3"/>',
    "github": '<path d="M9 19c-4 .9-4-2-5-2.5M14 21v-3.1a2.7 2.7 0 0 0-.8-2.1c2.7-.3 5.5-1.3 5.5-6A4.7 4.7 0 0 0 17.4 6.5 4.3 4.3 0 0 0 17.3 3S16.3 2.7 14 4.2a12.4 12.4 0 0 0-5 0C6.7 2.7 5.7 3 5.7 3a4.3 4.3 0 0 0-.1 3.5 4.7 4.7 0 0 0-1.3 3.3c0 4.7 2.8 5.7 5.5 6A2.7 2.7 0 0 0 9 18v3"/>',
    "channels": '<rect x="3.5" y="4" width="7" height="7" rx="1.4"/><rect x="13.5" y="4" width="7" height="7" rx="1.4"/><rect x="3.5" y="14" width="7" height="7" rx="1.4"/><rect x="13.5" y="14" width="7" height="7" rx="1.4"/>',
    "drops": '<path d="M12 3.5c2.6 3.3 6.5 7.3 6.5 11.1a6.5 6.5 0 0 1-13 0C5.5 10.8 9.4 6.8 12 3.5Z"/><path d="M9 15.5a3 3 0 0 0 3 3"/>',
    "followers": '<circle cx="9" cy="8" r="3"/><path d="M3.5 19a5.5 5.5 0 0 1 11 0"/><path d="M16 5.5a3 3 0 0 1 0 5.8M17 14a4.5 4.5 0 0 1 3.5 4.4"/>',
    "bets": '<path d="M4 19V5M4 19h16"/><path d="m6.5 15 4-4 3 2 5-6"/><path d="M15.5 7H19v3.5"/>',
    "events": '<path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "about": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.5h.01"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6h.01M4 12h.01M4 18h.01"/>',
    "inventory": '<path d="m4 8 8-4 8 4-8 4-8-4Z"/><path d="M4 8v8l8 4 8-4V8M12 12v8"/>',
    "gift": '<path d="M4 10h16v10H4zM3 7h18v3H3z"/><path d="M12 7v13"/><path d="M12 7H8.5a2 2 0 1 1 2-2c0 1.1 1.5 2 1.5 2ZM12 7h3.5a2 2 0 1 0-2-2c0 1.1-1.5 2-1.5 2Z"/>',
    "check": '<path d="m5 12 4.5 4.5L19 7"/>',
    "power": '<path d="M12 3v9"/><path d="M6.3 6.8a8 8 0 1 0 11.4 0"/>',
    "coin": '<ellipse cx="12" cy="7" rx="8" ry="3.5"/><path d="M4 7v10c0 1.9 3.6 3.5 8 3.5s8-1.6 8-3.5V7M4 12c0 1.9 3.6 3.5 8 3.5s8-1.6 8-3.5"/>'
}
_SVG_ICON_STROKE_WIDTHS = {"refresh": 2.0, "settings": 1.9, "plus": 2.0, "stop": 2.0}
_SVG_ICON_CACHE = {}
_SVG_ICON_RENDERERS = {}
_BUTTON_ANIMATION_MANAGER = None


def _svg_icon_renderer(name, color):
    key = (str(name), str(color))
    renderer = _SVG_ICON_RENDERERS.get(key)
    if renderer is not None:
        return renderer
    path = SVG_ICON_PATHS.get(str(name))
    if not path:
        return None
    stroke_width = _SVG_ICON_STROKE_WIDTHS.get(str(name), 1.8)
    document = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" color="' +
        str(color) + '" stroke="currentColor" stroke-width="' + str(stroke_width) +
        '" stroke-linecap="round" stroke-linejoin="round">' + path + '</svg>'
    )
    renderer = QSvgRenderer(QByteArray(document.encode("utf-8")))
    if not renderer.isValid():
        return None
    _SVG_ICON_RENDERERS[key] = renderer
    return renderer


class AnimatedSvgIconEngine(QIconEngine):
    def __init__(self, button):
        super().__init__()
        self.button_ref = weakref.ref(button) if button is not None else lambda: None

    def key(self):
        return "TwitchFarmingAnimatedSvgIcon"

    def clone(self):
        return AnimatedSvgIconEngine(self.button_ref())

    def actualSize(self, size, mode, state):
        return size

    def pixmap(self, size, mode, state):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, QRectF(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return pixmap

    def paint(self, painter, rect, mode, state):
        button = self.button_ref()
        if button is None or not button._svg_icon_name:
            return
        renderer = _svg_icon_renderer(button._svg_icon_name, button._svg_icon_color)
        if renderer is None:
            return
        angle, scale_x, scale_y, shift_x, shift_y = button._icon_transform()
        center = rect.center()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(center.x() + shift_x, center.y() + shift_y)
        painter.rotate(angle)
        painter.scale(scale_x, scale_y)
        painter.translate(-center.x(), -center.y())
        renderer.render(painter, QRectF(rect))
        painter.restore()


class ButtonAnimationManager(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._buttons = weakref.WeakSet()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def activate(self, button):
        self._buttons.add(button)
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self):
        now = time.monotonic()
        active = False
        for button in tuple(self._buttons):
            try:
                button._ripple_waves = [
                    wave for wave in button._ripple_waves
                    if now - wave[2] < 0.38
                ]
                icon_active = bool(
                    button._icon_animation_started
                    and now - button._icon_animation_started < button._icon_animation_duration
                )
                if not icon_active:
                    button._icon_animation_started = 0.0
                if button._ripple_waves or icon_active:
                    button.update()
                    active = True
                else:
                    self._buttons.discard(button)
            except RuntimeError:
                self._buttons.discard(button)
        if not active:
            self._timer.stop()


def _button_animation_manager():
    global _BUTTON_ANIMATION_MANAGER
    if _BUTTON_ANIMATION_MANAGER is None:
        _BUTTON_ANIMATION_MANAGER = ButtonAnimationManager(QApplication.instance())
    return _BUTTON_ANIMATION_MANAGER



def _svg_icon_pixmap(name, color, size, device_pixel_ratio=1.0):
    dpr = max(1.0, float(device_pixel_ratio or 1.0))
    key = (str(name), str(color), int(size), round(dpr, 3))
    cached = _SVG_ICON_CACHE.get(key)
    if cached is not None:
        return cached
    path = SVG_ICON_PATHS.get(str(name))
    if not path:
        return QPixmap()
    stroke_width = _SVG_ICON_STROKE_WIDTHS.get(str(name), 1.8)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" color="' +
           str(color) + '" stroke="currentColor" stroke-width="' + str(stroke_width) +
           '" stroke-linecap="round" stroke-linejoin="round">' + path + '</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QPixmap()
    physical_size = max(1, int(round(int(size) * dpr)))
    pixmap = QPixmap(physical_size, physical_size)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter, QRectF(0, 0, int(size), int(size)))
    painter.end()
    _SVG_ICON_CACHE[key] = pixmap
    return pixmap


class QPushButton(QtPushButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._svg_icon_name = ""
        self._svg_icon_size = 16
        self._svg_icon_danger = False
        self._svg_icon_hovered = False
        self._svg_icon_dpr = 0.0
        self._svg_icon_color = "#A1A1AA"
        self._icon_animation_started = 0.0
        self._icon_animation_duration = 0.28
        self._ripple_waves = []

    def setObjectName(self, name):
        super().setObjectName(name)
        if getattr(self, "_svg_icon_name", ""):
            self._refreshSvgIcon()

    def setSvgIcon(self, name, size=16, danger=False):
        self._svg_icon_name = str(name or "")
        self._svg_icon_size = max(8, int(size))
        self._svg_icon_danger = bool(danger)
        self._refreshSvgIcon()

    def _refreshSvgIcon(self):
        if not self._svg_icon_name:
            return
        dpr = float(self.devicePixelRatioF())
        accent = self.objectName() == "primaryButton"
        color = "#EF4444" if self._svg_icon_danger and self._svg_icon_hovered else (
            "#FFFFFF" if accent or self._svg_icon_hovered else "#A1A1AA"
        )
        if not self.isEnabled():
            color = "#777181" if accent else "#666671"
        self._svg_icon_dpr = dpr
        if color == self._svg_icon_color and not self.icon().isNull():
            return
        self._svg_icon_color = color
        self.setIcon(QIcon(AnimatedSvgIconEngine(self)))
        self.setIconSize(QSize(self._svg_icon_size, self._svg_icon_size))

    def _icon_transform(self):
        if not self._icon_animation_started:
            return 0.0, 1.0, 1.0, 0.0, 0.0
        progress = (time.monotonic() - self._icon_animation_started) / self._icon_animation_duration
        progress = max(0.0, min(1.0, progress))
        peak = math.sin(math.pi * progress)
        name = self._svg_icon_name
        angle = 0.0
        scale_x = 1.0
        scale_y = 1.0
        shift_x = 0.0
        shift_y = 0.0
        if name == "refresh":
            angle = 360.0 * progress
        elif name == "settings":
            angle = 60.0 * peak
        elif name in ("plus", "close"):
            angle = 90.0 * peak
            scale_x = scale_y = 1.0 + 0.12 * peak
        elif name in ("stop", "play", "power", "check"):
            if progress < 0.4:
                scale = 0.82 + 0.26 * (progress / 0.4)
            else:
                scale = 1.08 - 0.08 * ((progress - 0.4) / 0.6)
            scale_x = scale_y = scale
        elif name in ("external", "login"):
            shift_x = 3.0 * peak
            shift_y = -3.0 * peak if name == "external" else 0.0
        elif name == "logout":
            shift_x = -3.0 * peak
        elif name == "swap":
            shift_x = 2.5 * math.sin(progress * math.pi * 2.0)
        elif name in ("trash", "folder", "palette"):
            angle = (
                -12.0 + 18.0 * min(1.0, progress / 0.45)
                if progress < 0.45 else 6.0 * (1.0 - (progress - 0.45) / 0.55)
            )
        elif name in ("drops", "gift", "coin", "download", "bets"):
            shift_y = -3.0 * peak
        else:
            if progress < 0.45:
                scale = 0.86 + 0.20 * (progress / 0.45)
            else:
                scale = 1.06 - 0.06 * ((progress - 0.45) / 0.55)
            scale_x = scale_y = scale
        return angle, scale_x, scale_y, shift_x, shift_y

    def _ripple_corner_radius(self):
        radii = {
            "windowControl": 6.0,
            "navButton": 9.0,
            "cardAction": 6.0,
            "followerAddButton": 7.0,
            "followerAddedButton": 7.0,
            "dropsFilter": 7.0,
            "smallDanger": 6.0
        }
        return radii.get(self.objectName(), 7.0)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            position = event.position()
            now = time.monotonic()
            self._ripple_waves.append((float(position.x()), float(position.y()), now))
            self._ripple_waves = self._ripple_waves[-4:]
            if self._svg_icon_name:
                self._icon_animation_started = now
            _button_animation_manager().activate(self)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._ripple_waves:
            return
        now = time.monotonic()
        accent = self.objectName() == "primaryButton" or (
            self.objectName() == "navButton" and self.isChecked()
        )
        color = QColor(255, 255, 255, 0) if accent else QColor(167, 139, 250, 0)
        max_radius = math.hypot(float(self.width()), float(self.height()))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip = QPainterPath()
        radius = self._ripple_corner_radius()
        clip.addRoundedRect(QRectF(self.rect()), radius, radius)
        painter.setClipPath(clip)
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, started in self._ripple_waves:
            progress = max(0.0, min(1.0, (now - started) / 0.38))
            eased = 1.0 - (1.0 - progress) ** 3
            alpha = max(0, min(255, int(round(255.0 * 0.24 * (1.0 - progress)))))
            color.setAlpha(alpha)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(x, y), max_radius * eased, max_radius * eased)
        painter.end()

    def enterEvent(self, event):
        self._svg_icon_hovered = True
        super().enterEvent(event)
        self._refreshSvgIcon()

    def leaveEvent(self, event):
        self._svg_icon_hovered = False
        super().leaveEvent(event)
        self._refreshSvgIcon()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._refreshSvgIcon()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._svg_icon_name and abs(float(self.devicePixelRatioF()) - self._svg_icon_dpr) > 0.01:
            self._refreshSvgIcon()


class SvgIconLabel(QLabel):
    def __init__(self, name, size=16, color="#D9C4FF", parent=None):
        super().__init__(parent)
        self.icon_name = str(name or "")
        self.icon_size = max(8, int(size))
        self.icon_color = str(color)
        self.setFixedSize(self.icon_size, self.icon_size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.refresh()

    def refresh(self):
        pixmap = _svg_icon_pixmap(self.icon_name, self.icon_color, self.icon_size, self.devicePixelRatioF())
        if not pixmap.isNull():
            self.setPixmap(pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh()


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
        try:
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
        except Exception as error:
            self.pending.discard(value)
            self.waiting.pop(value, None)
            _log_ui_exception("загрузка иконки " + value[:120], error)

    def _image_ready(self, url, data, error):
        self.pending.discard(url)
        if error:
            _log_ui_exception("загрузка иконки " + str(url)[:120], RuntimeError(str(error)))
        if data:
            self.data[url] = data
        targets = self.waiting.pop(url, [])
        for reference, circular in targets:
            try:
                label = reference()
                if label is not None and label.property("imageUrl") == url and data:
                    self._set_label(label, data, circular)
            except Exception as render_error:
                _log_ui_exception("отрисовка загруженной иконки " + str(url)[:120], render_error)

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
        width = max(1, label.width() or label.minimumWidth() or label.sizeHint().width())
        height = max(1, label.height() or label.minimumHeight() or label.sizeHint().height())
        edge = max(1, min(width, height))
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(52, 52)
        self.setMouseTracking(True)
        self._hovered = False
        self._look_x = 0.0
        self._look_y = 0.0
        self._target_x = 0.0
        self._target_y = 0.0
        self._eye_height = 4.8
        self._target_eye_height = 4.8
        self._action_until = 0.0
        self._blink_until = 0.0
        self._blink_phase = 0
        self._next_action = time.monotonic() + random.uniform(2.2, 4.0)
        self.eye_timer = QTimer(self)
        self.eye_timer.timeout.connect(self._tick)
        self.eye_timer.start(30)

    def _set_gaze_from_position(self, position):
        width = max(1.0, float(self.width()))
        height = max(1.0, float(self.height()))
        self._target_x = max(-1.5, min(1.5, (float(position.x()) / width - 0.5) * 3.0))
        self._target_y = max(-0.8, min(0.8, (float(position.y()) / height - 0.5) * 1.6))

    def enterEvent(self, event):
        self._hovered = True
        self._set_gaze_from_position(self.mapFromGlobal(QCursor.pos()))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._target_x = 0.0
        self._target_y = 0.0
        self._target_eye_height = 4.8
        self._action_until = 0.0
        self._blink_until = 0.0
        self._blink_phase = 0
        self._next_action = time.monotonic() + random.uniform(2.2, 4.0)
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        if self._hovered:
            self._set_gaze_from_position(event.position())
        super().mouseMoveEvent(event)

    def _tick(self):
        now = time.monotonic()
        if self._hovered:
            self._blink_phase = 0
            self._target_eye_height = 3.2
            self._eye_height += (self._target_eye_height - self._eye_height) * 0.28
        elif self._blink_phase == 1:
            self._eye_height += (0.3 - self._eye_height) * 0.82
            if self._eye_height <= 0.55:
                self._eye_height = 0.3
                self._blink_phase = 2
                self._blink_until = now + 0.12
        elif self._blink_phase == 2:
            self._eye_height = 0.3
            if now >= self._blink_until:
                self._blink_phase = 3
        elif self._blink_phase == 3:
            self._eye_height += (4.8 - self._eye_height) * 0.38
            if self._eye_height >= 4.55:
                self._eye_height = 4.8
                self._blink_phase = 0
        else:
            if now >= self._action_until:
                self._target_x = 0.0
                self._target_y = 0.0
                self._target_eye_height = 4.8
                if now >= self._next_action:
                    action = random.choice(("look", "squint", "blink"))
                    self._next_action = now + random.uniform(2.2, 4.0)
                    if action == "look":
                        self._action_until = now + random.uniform(0.65, 1.2)
                        self._target_x = random.uniform(-1.5, 1.5)
                        self._target_y = random.uniform(-0.8, 0.8)
                    elif action == "squint":
                        self._action_until = now + random.uniform(0.65, 1.2)
                        self._target_eye_height = 2.2
                    else:
                        self._blink_phase = 1
            self._eye_height += (self._target_eye_height - self._eye_height) * 0.28
        self._look_x += (self._target_x - self._look_x) * 0.2
        self._look_y += (self._target_y - self._look_y) * 0.2
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.scale(self.width() / 24.0, self.height() / 24.0)
        parallax_x = self._look_x * 0.35
        parallax_y = self._look_y * 0.35
        extrusion = QPolygonF([
            QPointF(4 - 1.6, 2 + 1.6),
            QPointF(21 - 1.6, 2 + 1.6),
            QPointF(21 - 1.6, 14 + 1.6),
            QPointF(16 - 1.6, 19 + 1.6),
            QPointF(11 - 1.6, 19 + 1.6),
            QPointF(8 - 1.6, 22 + 1.6),
            QPointF(8 - 1.6, 19 + 1.6),
            QPointF(4 - 1.6, 19 + 1.6)
        ])
        painter.setBrush(QColor("#5B21B6"))
        painter.drawPolygon(extrusion)
        body = QPolygonF([
            QPointF(5 + parallax_x, 1.5 + parallax_y),
            QPointF(22 + parallax_x, 1.5 + parallax_y),
            QPointF(22 + parallax_x, 13.5 + parallax_y),
            QPointF(17 + parallax_x, 18.5 + parallax_y),
            QPointF(12 + parallax_x, 18.5 + parallax_y),
            QPointF(9 + parallax_x, 21.5 + parallax_y),
            QPointF(9 + parallax_x, 18.5 + parallax_y),
            QPointF(5 + parallax_x, 18.5 + parallax_y)
        ])
        painter.setBrush(QColor("#8B5CF6"))
        painter.drawPolygon(body)
        screen = QPolygonF([
            QPointF(7.5 + parallax_x, 4 + parallax_y),
            QPointF(19.5 + parallax_x, 4 + parallax_y),
            QPointF(19.5 + parallax_x, 12.5 + parallax_y),
            QPointF(16.2 + parallax_x, 15.8 + parallax_y),
            QPointF(12.2 + parallax_x, 15.8 + parallax_y),
            QPointF(10.2 + parallax_x, 17.8 + parallax_y),
            QPointF(10.2 + parallax_x, 15.8 + parallax_y),
            QPointF(7.5 + parallax_x, 15.8 + parallax_y)
        ])
        painter.setBrush(QColor("#F8FAFC"))
        painter.drawPolygon(screen)
        eye_y = 7.2 + (4.8 - self._eye_height) / 2.0 + parallax_y + self._look_y
        eye_x = parallax_x + self._look_x
        painter.setBrush(QColor("#1E1B2E"))
        painter.drawRoundedRect(QRectF(10.6 + eye_x, eye_y, 2.0, self._eye_height), 0.6, 0.6)
        painter.drawRoundedRect(QRectF(15.2 + eye_x, eye_y, 2.0, self._eye_height), 0.6, 0.6)
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
        points_holder = QWidget()
        points_row = QHBoxLayout(points_holder)
        points_row.setContentsMargins(4, 0, 4, 0)
        points_row.setSpacing(5)
        self.points_icon = SvgIconLabel("coin", 15, "#D9C4FF")
        points_row.addWidget(self.points_icon)
        self.points = QLabel("+0 очков")
        self.points.setObjectName("sessionPoints")
        self.points.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.points.setMinimumWidth(78)
        self.points.setFixedHeight(24)
        points_row.addWidget(self.points)
        points_holder.setMinimumWidth(104)
        row.addWidget(points_holder)
        self.timer = QLabel("00:00:00")
        self.timer.setObjectName("sessionTimer")
        self.timer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.timer.setFixedHeight(24)
        row.addWidget(self.timer)
        for icon_name, signal in (("minimize", self.minimizeRequested), ("maximize", self.maximizeRequested), ("close", self.closeRequested)):
            button = QPushButton()
            button.setObjectName("windowControl")
            button.setSvgIcon(icon_name, 16)
            button.setFixedSize(30, 28)
            button.clicked.connect(signal.emit)
            row.addWidget(button)
            if icon_name == "maximize":
                self.maximize_button = button

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

    def set_maximized(self, maximized):
        if hasattr(self, "maximize_button"):
            self.maximize_button.setSvgIcon("restore" if maximized else "maximize", 16)

    def set_gained_total(self, value):
        try:
            amount = max(0, int(value or 0))
        except (TypeError, ValueError):
            amount = 0
        self.points.setText("+" + f"{amount:,}".replace(",", " ") + " очков")

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
        self.login = str(login or "").strip().lower()
        login = self.login
        profile = profile if isinstance(profile, dict) else {}
        currency = currency if isinstance(currency, dict) else {}
        self.setObjectName("channelCard")
        self.setFixedWidth(190)
        self.setFixedHeight(232)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 9, 10, 10)
        root.setSpacing(5)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)
        live = QLabel("• В ЭФИРЕ" if profile.get("is_live") else "• ОФЛАЙН")
        live.setObjectName("liveBadge" if profile.get("is_live") else "offlineBadge")
        top.addWidget(live, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)

        self.actions_panel = QFrame()
        self.actions_panel.setObjectName("channelActions")
        self.actions_panel.setFixedHeight(22)
        action_layout = QHBoxLayout(self.actions_panel)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(2)

        open_button = QPushButton()
        open_button.setObjectName("cardAction")
        open_button.setSvgIcon("external", 13)
        open_button.setFixedSize(21, 21)
        open_button.setToolTip("Открыть на Twitch")
        open_button.clicked.connect(lambda checked=False, value=login: QDesktopServices.openUrl(QUrl("https://www.twitch.tv/" + value)))
        action_layout.addWidget(open_button)

        move = QPushButton()
        move.setObjectName("cardAction")
        move.setSvgIcon("swap", 13)
        move.setFixedSize(21, 21)
        move.setEnabled(bool(actions_enabled))
        move.setToolTip("Переместить: выбери вторую карточку")
        move.clicked.connect(lambda checked=False, value=login: move_callback(value))
        action_layout.addWidget(move)

        edit = QPushButton()
        edit.setObjectName("cardAction")
        edit.setSvgIcon("settings", 13)
        edit.setFixedSize(21, 21)
        edit.setEnabled(bool(actions_enabled))
        edit.setToolTip("Редактировать канал")
        edit.clicked.connect(lambda checked=False, value=login: edit_callback(value))
        action_layout.addWidget(edit)

        remove = QPushButton()
        remove.setObjectName("cardAction")
        remove.setSvgIcon("trash", 13, danger=True)
        remove.setFixedSize(21, 21)
        remove.setEnabled(bool(actions_enabled))
        remove.setToolTip("Удалить канал")
        remove.clicked.connect(lambda checked=False, value=login: remove_callback(value))
        action_layout.addWidget(remove)

        self.actions_opacity = QGraphicsOpacityEffect(self.actions_panel)
        self.actions_opacity.setOpacity(0.0)
        self.actions_panel.setGraphicsEffect(self.actions_opacity)
        self.actions_panel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        top.addWidget(self.actions_panel, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(top)

        badges = QHBoxLayout()
        badges.setContentsMargins(0, 0, 0, 0)
        badges.setSpacing(4)
        if slot:
            slot_badge = QLabel("СЛОТ " + str(slot))
            slot_badge.setObjectName("channelSlotBadge")
            badges.addWidget(slot_badge)
        if temporary:
            temporary_badge = QLabel("DROPS")
            temporary_badge.setObjectName("temporaryBadge")
            badges.addWidget(temporary_badge)
        badges.addStretch(1)
        root.addLayout(badges)

        avatar_row = QHBoxLayout()
        avatar_row.addStretch(1)
        self.avatar = QLabel()
        self.avatar.setObjectName("channelAvatar")
        self.avatar.setFixedSize(64, 64)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setText(str(profile.get("display_name") or login or "T")[:1].upper())
        avatar_row.addWidget(self.avatar)
        avatar_row.addStretch(1)
        root.addLayout(avatar_row)

        self.name = QLabel(str(profile.get("display_name") or login or "Канал"))
        self.name.setObjectName("channelName")
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name.setWordWrap(False)
        root.addWidget(self.name)

        self.login_label = QLabel("@" + login)
        self.login_label.setObjectName("channelLogin")
        self.login_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.login_label)

        balance_row = QHBoxLayout()
        balance_row.setContentsMargins(0, 0, 0, 0)
        balance_row.setSpacing(4)
        balance_row.addStretch(1)
        self.currency_icon = QLabel("✦")
        self.currency_icon.setFixedSize(18, 18)
        self.currency_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.currency_icon.setObjectName("channelCurrencyIcon")
        balance_row.addWidget(self.currency_icon)
        points = QLabel(f"{_ui_int(balance):,}".replace(",", " "))
        points.setObjectName("channelBalance")
        balance_row.addWidget(points)
        currency_name = QLabel(str(currency.get("name") or "Channel Points"))
        currency_name.setObjectName("channelCurrencyName")
        currency_name.setMaximumWidth(66)
        currency_name.setMinimumWidth(0)
        currency_name.setWordWrap(False)
        balance_row.addWidget(currency_name)
        balance_row.addStretch(1)
        root.addLayout(balance_row)

        viewers = int(profile.get("viewer_count") or 0)
        self.viewers = QLabel(f"{viewers:,}".replace(",", " ") + " зрителей" if profile.get("is_live") else "")
        self.viewers.setObjectName("channelViewers")
        self.viewers.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.viewers)
        root.addStretch(1)

        self.actions_opacity_animation = QPropertyAnimation(self.actions_opacity, b"opacity", self)
        self.actions_opacity_animation.setDuration(140)
        self.actions_opacity_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.actions_opacity_animation.finished.connect(self._finish_actions_animation)

        if image_cache:
            image_cache.request(self.avatar, profile.get("avatar_url", ""), True)
            image_cache.request(self.currency_icon, currency.get("icon_url", ""), False)

    def _animate_actions(self, visible):
        self.actions_opacity_animation.stop()
        self.actions_panel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not visible)
        self.actions_opacity_animation.setStartValue(self.actions_opacity.opacity())
        self.actions_opacity_animation.setEndValue(1.0 if visible else 0.0)
        self.actions_opacity_animation.start()

    def _finish_actions_animation(self):
        if self.actions_opacity.opacity() <= 0.01 and not self.underMouse():
            self.actions_panel.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

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
        amount = QLabel(f"{_ui_int(balance):,}".replace(",", " ") + " баллов")
        amount.setObjectName("slotBalance")
        row.addWidget(amount)
        if image_cache:
            image_cache.request(self.avatar, profile.get("avatar_url", ""), True)


def _log_ui_exception(context, error):
    try:
        logging.getLogger("twitch_farming.ui").error(
            "Ошибка интерфейса (%s): %s",
            str(context),
            str(error),
            exc_info=(type(error), error, error.__traceback__)
        )
    except Exception:
        pass


def _ui_int(value, default=0):
    try:
        return int(str(value if value is not None else default).replace(" ", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _flag_enabled(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "paid", "subscription")
    return bool(value)


def _campaign_available(campaign):
    status = str(campaign.get("status") or "").strip().upper()
    if status in ("EXPIRED", "ENDED", "COMPLETED", "CANCELLED", "CANCELED", "INACTIVE", "SCHEDULED"):
        return False
    game = campaign.get("game")
    if isinstance(game, dict):
        game = game.get("displayName") or game.get("name")
    return bool(str(game or "").strip())


def _eligible_free_drops(campaign):
    raw_drops = campaign.get("drops", [])
    if isinstance(raw_drops, dict):
        raw_drops = raw_drops.get("nodes") or raw_drops.get("edges") or []
        raw_drops = [item.get("node", item) if isinstance(item, dict) else item for item in raw_drops]
    result = []
    paid_types = ("SUBSCRIPTION", "SUB", "SUBSGOAL", "SUBS_GOAL", "SUBSCRIPTION_GOAL")
    image_keys = ("image_url", "imageURL", "imageUrl", "imageAssetURL", "boxArtURL", "boxArtUrl", "url")
    for item in raw_drops if isinstance(raw_drops, list) else []:
        if not isinstance(item, dict):
            continue
        if _flag_enabled(item.get("claimed")):
            continue
        drop_type = str(item.get("source_type") or item.get("drop_type") or item.get("dropType") or item.get("type") or "watch").strip().upper()
        category = str(item.get("category") or "").strip().upper()
        benefit_types = item.get("benefit_types") or [item.get("benefit_type") or ""]
        if not isinstance(benefit_types, (list, tuple, set)):
            benefit_types = [benefit_types]
        if drop_type in paid_types or any(str(value or "").strip().upper() in paid_types for value in benefit_types):
            continue
        if category in ("SUBSCRIPTION", "PAID", "SUBSCRIBER"):
            continue
        if any(_flag_enabled(item.get(key)) for key in ("is_subscription", "paid", "subsGoal", "subs_goal", "subscriptionGoal", "subscription_goal")):
            continue
        normalized = dict(item)
        image_url = ""
        for key in image_keys:
            candidate = str(item.get(key) or "").strip()
            if candidate.startswith("https://"):
                image_url = candidate
                break
        normalized["image_url"] = image_url
        result.append(normalized)
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


def _log_entry_severity(line, fallback="INFO"):
    match = re.search(r"\[(DEBUG|INFO|WARNING|ERROR|CRITICAL|FATAL)\]", str(line or ""), re.IGNORECASE)
    return match.group(1).upper() if match else str(fallback or "INFO").upper()


def log_entry_matches_level(line, level, fallback="INFO"):
    severity = _log_entry_severity(line, fallback)
    selected = str(level or "INFO / Важные")
    if selected in ("INFO / Важные", "Основные"):
        return severity != "DEBUG"
    if selected == "Все уровни":
        return True
    return severity == selected.upper()


def _format_campaign_date(campaign):
    value = ""
    if isinstance(campaign, dict):
        for key in ("end_at", "endAt", "end_date", "endDate", "ends_at", "endsAt"):
            if campaign.get(key):
                value = campaign.get(key)
                break
    if isinstance(value, (int, float)):
        try:
            date_value = datetime.fromtimestamp(float(value))
        except (OverflowError, OSError, ValueError):
            date_value = None
    else:
        raw = str(value or "").strip()
        date_value = None
        if raw:
            try:
                date_value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except (TypeError, ValueError):
                try:
                    date_value = datetime.strptime(raw[:10], "%Y-%m-%d")
                except (TypeError, ValueError):
                    date_value = None
    if date_value is None:
        return "Дата окончания не указана"
    months = ("янв.", "фев.", "мар.", "апр.", "мая", "июн.", "июл.", "авг.", "сен.", "окт.", "ноя.", "дек.")
    return f"Кампания до {date_value.day} {months[date_value.month - 1]} {date_value.year} г."


class CampaignCard(QFrame):
    def __init__(self, campaign, image_cache, add_callback, parent=None, add_enabled=True):
        super().__init__(parent)
        self.setObjectName("campaignCard")
        self.setMinimumWidth(220)
        self.setMaximumWidth(440)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(9)
        game_image = QLabel()
        game_image.setFixedSize(44, 58)
        game_image.setObjectName("campaignImage")
        game_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        game_image.setText("GAME")
        header.addWidget(game_image, 0, Qt.AlignmentFlag.AlignTop)

        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        game_value = campaign.get("game") or "TWITCH"
        game_text = str(game_value).upper()
        game = QLabel(game_text)
        game.setObjectName("campaignGame")
        game.setWordWrap(False)
        title_box.addWidget(game)

        name = QLabel(str(campaign.get("name") or "Кампания Twitch"))
        name.setObjectName("cardTitle")
        name.setWordWrap(True)
        title_box.addWidget(name)

        date = QLabel(_format_campaign_date(campaign))
        date.setObjectName("smallMuted")
        date.setWordWrap(True)
        title_box.addWidget(date)
        header.addLayout(title_box, 1)

        watch_badge = QLabel("WATCH DROP")
        watch_badge.setObjectName("watchDropBadge")
        header.addWidget(watch_badge, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        game_image_url = ""
        for key in ("game_image_url", "gameImageURL", "gameImageUrl", "box_art_url", "boxArtURL", "boxArtUrl", "image_url", "imageURL"):
            candidate = str(campaign.get(key) or "").strip()
            if candidate.startswith("https://"):
                game_image_url = candidate
                break
        if image_cache and game_image_url:
            image_cache.request(game_image, game_image_url, False)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setObjectName("separator")
        layout.addWidget(separator)

        for drop in campaign.get("visible_drops", []):
            reward_row = QFrame()
            reward_row.setObjectName("dropRewardRow")
            reward_layout = QVBoxLayout(reward_row)
            reward_layout.setContentsMargins(8, 8, 8, 8)
            reward_layout.setSpacing(6)

            reward_header = QHBoxLayout()
            reward_header.setSpacing(8)
            reward_image = QLabel()
            reward_image.setFixedSize(40, 40)
            reward_image.setObjectName("rewardIcon")
            reward_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            reward_image.setText("DROP")
            reward_header.addWidget(reward_image)

            reward_name_value = drop.get("name") or drop.get("benefit") or "Награда Twitch"
            if isinstance(reward_name_value, dict):
                reward_name_value = reward_name_value.get("name") or reward_name_value.get("displayName") or "Награда Twitch"
            reward_name = QLabel(str(reward_name_value))
            reward_name.setObjectName("rewardTitle")
            reward_name.setWordWrap(True)
            reward_header.addWidget(reward_name, 1)

            watched = _ui_int(drop.get("watched_minutes", drop.get("current_minutes", drop.get("currentMinutes", 0))))
            required = _ui_int(drop.get("required_minutes", drop.get("requiredMinutes", drop.get("duration_minutes", 0))))
            percentage_value = drop.get("percentage")
            if percentage_value is None:
                percentage_value = (watched * 100.0 / required) if required > 0 else 0
            percentage = max(0, min(100, int(float(percentage_value or 0))))
            claimed = _flag_enabled(drop.get("claimed")) or _flag_enabled(drop.get("is_claimed"))
            received = claimed or percentage >= 100
            status = QLabel("Получено" if received else "В прогрессе")
            status.setObjectName("dropReceivedBadge" if received else "dropProgressBadge")
            reward_header.addWidget(status, 0, Qt.AlignmentFlag.AlignTop)
            reward_layout.addLayout(reward_header)

            bar = QProgressBar()
            bar.setObjectName("dropProgressBar")
            bar.setRange(0, 100)
            bar.setValue(percentage)
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            reward_layout.addWidget(bar)

            remaining_value = drop.get("remaining_minutes")
            remaining = max(0, _ui_int(remaining_value, max(0, required - watched)))
            footer = QHBoxLayout()
            footer.setSpacing(4)
            time_label = QLabel(f"{watched} / {required} мин · осталось {remaining} мин")
            time_label.setObjectName("smallMuted")
            footer.addWidget(time_label, 1)
            percent_label = QLabel(str(percentage) + "%")
            percent_label.setObjectName("dropPercent")
            footer.addWidget(percent_label, 0, Qt.AlignmentFlag.AlignRight)
            reward_layout.addLayout(footer)
            layout.addWidget(reward_row)
            if image_cache:
                image_cache.request(reward_image, drop.get("image_url", ""), False)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setObjectName("separator")
        layout.addWidget(separator)

        streamers = [item for item in campaign.get("streamers", []) if isinstance(item, dict)]
        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(0, 0, 0, 0)
        toggle = QPushButton("Подходящие стримеры из фолловеров")
        toggle.setObjectName("campaignStreamerToggle")
        toggle.setCheckable(True)
        toggle.setChecked(False)
        toggle_row.addWidget(toggle, 1)
        count = QLabel(str(len(streamers)))
        count.setObjectName("purpleBadge")
        toggle_row.addWidget(count, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(toggle_row)

        streamers_panel = QFrame()
        streamers_panel.setObjectName("campaignStreamers")
        streamers_layout = QVBoxLayout(streamers_panel)
        streamers_layout.setContentsMargins(0, 2, 0, 0)
        streamers_layout.setSpacing(6)
        if streamers:
            for streamer in streamers:
                row = QHBoxLayout()
                row.setSpacing(6)
                avatar = QLabel()
                avatar.setFixedSize(30, 30)
                avatar.setObjectName("miniAvatar")
                avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
                avatar.setText(str(streamer.get("display_name") or streamer.get("login") or "?")[:1].upper())
                row.addWidget(avatar)

                info = QVBoxLayout()
                info.setSpacing(1)
                title = QLabel(str(streamer.get("display_name") or streamer.get("login") or "Стример"))
                title.setObjectName("streamerName")
                info.addWidget(title)
                uptime = format_duration(streamer.get("uptime_seconds") or 0)
                description = QLabel(
                    "@" + str(streamer.get("login") or "") + " · "
                    + f"{int(streamer.get('viewer_count') or 0):,}".replace(",", " ")
                    + " зрителей · " + uptime
                )
                description.setObjectName("smallMuted")
                info.addWidget(description)
                row.addLayout(info, 1)

                login = str(streamer.get("login") or "")
                open_button = QPushButton()
                open_button.setObjectName("cardAction")
                open_button.setSvgIcon("external", 13)
                open_button.setFixedSize(22, 22)
                open_button.setToolTip("Открыть на Twitch")
                open_button.clicked.connect(lambda checked=False, value=login: QDesktopServices.openUrl(QUrl("https://www.twitch.tv/" + value)))
                row.addWidget(open_button)

                add_button = QPushButton()
                add_button.setObjectName("cardAction")
                add_button.setSvgIcon("play", 13)
                add_button.setFixedSize(22, 22)
                add_button.setEnabled(bool(add_enabled))
                add_button.setToolTip("Остановите фарм, чтобы добавить канал." if not add_enabled else "Добавить канал в Drops-фарм")
                add_button.clicked.connect(lambda checked=False, value=login, cid=str(campaign.get("id") or ""): add_callback(value, cid))
                row.addWidget(add_button)
                streamers_layout.addLayout(row)
                if image_cache:
                    image_cache.request(avatar, streamer.get("avatar_url", ""), True)
        else:
            empty = QLabel("Подходящих стримеров для этой кампании не найдено.")
            empty.setObjectName("mutedText")
            empty.setWordWrap(True)
            streamers_layout.addWidget(empty)
        streamers_panel.hide()
        toggle.clicked.connect(lambda checked=False, panel=streamers_panel: panel.setVisible(checked))
        layout.addWidget(streamers_panel)




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
            lower_label = str(label or "").strip().lower()
            if "удал" in lower_label:
                button.setSvgIcon("trash", 14, danger=True)
            elif any(word in lower_label for word in ("закры", "отмена", "нет", "назад")):
                button.setSvgIcon("close", 14)
            elif any(word in lower_label for word in ("понятно", "ок", "да", "сохран")):
                button.setSvgIcon("check" if accepted else "close", 14)
            else:
                button.setSvgIcon("check" if accepted else "close", 14)
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
        self.latest_auth_state = {}
        self._startup_snapshot_received = False
        self._channel_columns = 0
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
        self.sidebar.setFixedWidth(238)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(8, 12, 8, 12)
        side.setSpacing(6)
        logo = TwitchLogo()
        side.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        brand = QLabel("TWITCH FARMING")
        brand.setObjectName("brandText")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(brand)
        side.addSpacing(8)
        self.nav_buttons = []
        nav_items = [
            ("Каналы", 0, "channels"),
            ("Инвентарь Drops", 1, "drops"),
            ("Фолловеры", 2, "followers"),
            ("Настройки", 3, "settings"),
            ("Настройки прогнозов", 4, "bets"),
            ("События и журнал", 5, "events"),
            ("О проекте", 6, "about")
        ]
        for title, index, icon_name in nav_items:
            button = QPushButton(title)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setFixedHeight(44)
            button.setSvgIcon(icon_name, 20)
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
        logo = TwitchLogo()
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
        self.initialization_close.setSvgIcon("power", 15, danger=True)
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
        QLabel#brandText { color:#F5F1FF; font-size:15px; font-weight:800; letter-spacing:1.2px; }
        QPushButton#navButton { min-height:44px; background:transparent; border:1px solid transparent; border-radius:10px; text-align:left; padding:8px 10px; color:#AFA9C0; font-size:14px; font-weight:650; }
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
        QPushButton#cardAction { background:#211E2C; border:1px solid #383247; color:#D2CBE0; padding:0; border-radius:6px; font-size:11px; font-weight:700; }
        QPushButton#cardAction:hover { background:#2B263B; border-color:#7656A2; }
        QPushButton#cardAction:disabled, QPushButton#smallDanger:disabled { color:#696575; background:#181720; border-color:#282632; }
        QLabel#channelAvatar { background:#29243A; border:1px solid #6A5687; border-radius:32px; color:#C9B8F0; font-size:18px; font-weight:800; }
        QLabel#slotAvatar, QLabel#miniAvatar { background:transparent; border:0; border-radius:0; color:#C9B8F0; font-weight:800; }
        QLabel#campaignImage { background:#241D36; border-radius:8px; color:#A98AFF; font-weight:800; }
        QLabel#rewardIcon { background:#242034; border-radius:8px; color:#E7D6FF; }
        QLabel#channelName { color:#FFFFFF; font-size:14px; font-weight:800; }
        QLabel#channelLogin { color:#928BA2; font-size:11px; }
        QLabel#channelViewers { color:#AAA3B8; font-size:9px; }
        QLabel#channelSlotBadge { color:#D8C5FF; background:#392858; border:1px solid #5E4383; border-radius:5px; padding:2px 6px; font-size:8px; font-weight:900; }
        QLabel#channelBalance { color:#FFFFFF; font-size:14px; font-weight:700; }
        QLabel#channelCurrencyName { color:#BDB7C9; font-size:9px; font-weight:600; }
        QLabel#channelCurrencyIcon { background:transparent; border:none; border-radius:0; color:#D8C7F6; font-size:18px; font-weight:900; padding:0; }
        QLabel#liveBadge, QLabel#offlineBadge, QLabel#watchBadge, QLabel#temporaryBadge, QLabel#purpleBadge { font-size:8px; font-weight:900; padding:3px 5px; border-radius:5px; }
        QLabel#liveBadge { color:#FF8593; background:#36202A; }
        QLabel#offlineBadge { color:#8C8799; background:#252530; }
        QLabel#watchBadge { color:#CDB4FF; background:#302345; }
        QLabel#temporaryBadge { color:#F5C36B; background:#322A1B; }
        QLabel#purpleBadge { color:#D4BCFF; background:#302345; }
        QFrame#activeSlotCard { background:#201A30; border:1px solid #44335F; border-radius:8px; }
        QFrame#dropsSummary { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #211B31,stop:1 #171522); border:1px solid #45365E; border-radius:12px; }
        QLabel#dropSummaryValue { color:#FFFFFF; font-size:22px; font-weight:900; }
        QLabel#dropSummaryCaption { color:#A49BB5; font-size:10px; font-weight:700; }
        QLabel#dropSummaryUpdated { color:#BEB5CF; font-size:10px; font-weight:650; }
        QLabel#campaignGame { color:#B79BEB; font-size:9px; font-weight:900; letter-spacing:1px; }
        QLabel#watchDropBadge { color:#D9C6FF; background:#34244D; border:1px solid #574173; border-radius:6px; padding:4px 6px; font-size:8px; font-weight:900; }
        QFrame#dropRewardRow { background:#171622; border:1px solid #302B40; border-radius:9px; }
        QLabel#dropProgressBadge { color:#D7BFFF; background:#302344; border-radius:5px; padding:3px 5px; font-size:8px; font-weight:800; }
        QLabel#dropReceivedBadge { color:#82D8B3; background:#1D352D; border-radius:5px; padding:3px 5px; font-size:8px; font-weight:800; }
        QLabel#dropPercent { color:#C7A9FF; font-size:9px; font-weight:900; }
        QProgressBar#dropProgressBar { background:#282238; border:0; border-radius:3px; }
        QProgressBar#dropProgressBar::chunk { background:#9660F2; border-radius:3px; }
        QPushButton#campaignStreamerToggle { background:#211C2D; border:1px solid #39304C; border-radius:7px; color:#CFC3E4; padding:7px 8px; text-align:left; font-size:9px; font-weight:700; }
        QPushButton#campaignStreamerToggle:hover, QPushButton#campaignStreamerToggle:checked { background:#312342; border-color:#674B8A; color:#F0E6FF; }
        QFrame#campaignStreamers { background:transparent; border:0; }
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
        if title == "Инвентарь Drops":
            self.drops_scroll = scroll
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
        self.add_channel_button = QPushButton("Добавить канал")
        self.add_channel_button.setSvgIcon("plus", 15)
        self.add_channel_button.setObjectName("secondaryButton")
        self.add_channel_button.clicked.connect(self._add_channels)
        top.addWidget(self.add_channel_button)
        self.farm_button = QPushButton("Запустить фарм")
        self.farm_button.setSvgIcon("play", 15)
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
        self.channels_grid.setHorizontalSpacing(12)
        self.channels_grid.setVerticalSpacing(12)
        self.channels_grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._channel_columns = 0
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

        self.drops_summary = QFrame()
        self.drops_summary.setObjectName("dropsSummary")
        summary_row = QHBoxLayout(self.drops_summary)
        summary_row.setContentsMargins(16, 11, 16, 11)
        summary_row.setSpacing(20)

        active_box = QVBoxLayout()
        active_box.setSpacing(1)
        self.drops_active_value = QLabel("0")
        self.drops_active_value.setObjectName("dropSummaryValue")
        active_box.addWidget(self.drops_active_value)
        active_caption = QLabel("АКТИВНЫЕ КАМПАНИИ")
        active_caption.setObjectName("dropSummaryCaption")
        active_box.addWidget(active_caption)
        summary_row.addLayout(active_box)

        rewards_box = QVBoxLayout()
        rewards_box.setSpacing(1)
        self.drops_progress_value = QLabel("0")
        self.drops_progress_value.setObjectName("dropSummaryValue")
        rewards_box.addWidget(self.drops_progress_value)
        rewards_caption = QLabel("НАГРАД В ПРОГРЕССЕ")
        rewards_caption.setObjectName("dropSummaryCaption")
        rewards_box.addWidget(rewards_caption)
        summary_row.addLayout(rewards_box)
        summary_row.addStretch(1)

        updated_box = QVBoxLayout()
        updated_box.setSpacing(2)
        updated_caption = QLabel("ПОСЛЕДНЕЕ ОБНОВЛЕНИЕ")
        updated_caption.setObjectName("dropSummaryCaption")
        updated_box.addWidget(updated_caption, 0, Qt.AlignmentFlag.AlignRight)
        self.drops_updated_label = QLabel("Ещё не обновлялось")
        self.drops_updated_label.setObjectName("dropSummaryUpdated")
        updated_box.addWidget(self.drops_updated_label, 0, Qt.AlignmentFlag.AlignRight)
        summary_row.addLayout(updated_box)
        layout.addWidget(self.drops_summary)

        row = QHBoxLayout()
        self.drops_filter_index = 0
        self.drops_filter_group = QButtonGroup(self)
        self.drops_filter_group.setExclusive(True)
        filters = (
            ("Все кампании", "list"),
            ("Начатые (Инвентарь)", "inventory"),
            ("Награды Twitch (значки и эмоуты за просмотр)", "gift")
        )
        for index, (title, icon_name) in enumerate(filters):
            button = QPushButton(title)
            button.setSvgIcon(icon_name, 14)
            button.setObjectName("dropsFilter")
            button.setCheckable(True)
            button.setChecked(index == 0)
            self.drops_filter_group.addButton(button, index)
            row.addWidget(button)
        self.drops_filter_group.idClicked.connect(self._set_drops_filter)
        row.addStretch()
        refresh = QPushButton("Обновить кампании")
        refresh.setSvgIcon("refresh", 15)
        refresh.setObjectName("primaryButton")
        refresh.clicked.connect(lambda: self._call("refresh_drops_inventory", True, message="Обновление каталога Twitch Drops запущено."))
        row.addWidget(refresh)
        layout.addLayout(row)

        self.drops_status = QLabel("Подключи Twitch и нажми «Обновить кампании».")
        self.drops_status.setObjectName("statusHint")
        layout.addWidget(self.drops_status)
        self.drops_grid = QGridLayout()
        self.drops_grid.setContentsMargins(0, 0, 0, 0)
        self.drops_grid.setHorizontalSpacing(12)
        self.drops_grid.setVerticalSpacing(12)
        self.drops_grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.drops_list = self.drops_grid
        layout.addLayout(self.drops_grid)
        layout.addStretch()


    def _build_followers_page(self):
        layout = self._page("Фолловеры", "Подписки Twitch. Добавляй нужные каналы в список для фарма.")
        row = QHBoxLayout()
        refresh = QPushButton("Обновить")
        refresh.setSvgIcon("refresh", 15)
        refresh.setObjectName("primaryButton")
        refresh.clicked.connect(lambda: self._call("refresh_followed_channels", True, message="Загрузка списка подписок запущена."))
        row.addWidget(refresh)
        self.followed_input = QLineEdit()
        self.followed_input.setPlaceholderText("Логин Twitch")
        row.addWidget(self.followed_input, 1)
        add = QPushButton("Добавить")
        add.setSvgIcon("plus", 15)
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
        self.login_button.setSvgIcon("login", 15)
        self.login_button.setObjectName("primaryButton")
        self.login_button.clicked.connect(self._login)
        account_row.addWidget(self.login_button)
        account.layout().addLayout(account_row)
        account_actions = QHBoxLayout()
        self.logout_button = QPushButton("Выйти")
        self.logout_button.setSvgIcon("logout", 15)
        self.logout_button.setObjectName("secondaryButton")
        self.logout_button.clicked.connect(lambda: self._call("logout", message="Сессия Twitch завершена."))
        account_actions.addWidget(self.logout_button)
        open_folder = QPushButton("Папка с данными")
        open_folder.setSvgIcon("folder", 15)
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
        self.update_check_button.setSvgIcon("refresh", 15)
        self.update_check_button.setObjectName("primaryButton")
        self.update_check_button.clicked.connect(lambda: self._call("check_for_updates", message="Проверка GitHub Releases запущена."))
        update_actions.addWidget(self.update_check_button)
        self.update_download_button = QPushButton("Скачать и обновить")
        self.update_download_button.setSvgIcon("download", 15)
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
        clear_avatars.setSvgIcon("image", 15)
        clear_avatars.setObjectName("secondaryButton")
        clear_avatars.clicked.connect(lambda: self._clear_image_cache("clear_avatar_cache", "avatars"))
        cache_grid.addWidget(clear_avatars, 0, 0)
        clear_currency = QPushButton("Кэш валют")
        clear_currency.setSvgIcon("trash", 15, danger=True)
        clear_currency.setObjectName("secondaryButton")
        clear_currency.clicked.connect(lambda: self._clear_image_cache("clear_currency_cache", "currency"))
        cache_grid.addWidget(clear_currency, 0, 1)
        refresh_branding = QPushButton("Обновить валюты")
        refresh_branding.setSvgIcon("palette", 15)
        refresh_branding.setObjectName("secondaryButton")
        refresh_branding.clicked.connect(lambda: self._call("refresh_channel_points_branding", message="Обновление оформления запущено."))
        cache_grid.addWidget(refresh_branding, 1, 0, 1, 2)
        app_settings.layout().addLayout(cache_grid)
        grid.addWidget(app_settings, 1, 1)
        layout.addWidget(grid_wrap)
        save = QPushButton("Сохранить настройки")
        save.setSvgIcon("save", 15)
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
        save_preset.setSvgIcon("save", 14)
        save_preset.setObjectName("secondaryButton")
        save_preset.clicked.connect(self._save_bet_preset)
        preset_row.addWidget(save_preset)
        delete_preset = QPushButton("Удалить")
        delete_preset.setSvgIcon("trash", 14, danger=True)
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
        save.setSvgIcon("save", 15)
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
        self.log_level.addItems(["INFO / Важные", "Все уровни", "INFO", "WARNING", "ERROR", "DEBUG"])
        self.log_level.setCurrentIndex(0)
        self.log_level.currentIndexChanged.connect(self._render_logs)
        row.addWidget(self.log_level)
        clear = QPushButton("Очистить журнал")
        clear.setSvgIcon("trash", 15, danger=True)
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
        repository.setSvgIcon("github", 15)
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
            try:
                callback(result, error)
            except Exception as callback_error:
                _log_ui_exception("callback " + str(key), callback_error)
                try:
                    if key in ("startup", "session-restore"):
                        self._show_initialization_error("Ошибка интерфейса: " + str(callback_error))
                    else:
                        self._notify("Ошибка интерфейса: " + str(callback_error))
                except Exception as notify_error:
                    _log_ui_exception("показ ошибки callback", notify_error)
        elif result and isinstance(result, dict) and result.get("message"):
            self._notify(str(result.get("message")))

    def _safe_ui_call(self, context, function, *args):
        try:
            result = function(*args)
            return result if isinstance(result, bool) else True
        except Exception as error:
            _log_ui_exception(context, error)
            return False

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
        self.latest_auth_state = auth if isinstance(auth, dict) else {}
        self._startup_snapshot_received = True
        self.drops_state = drops or {}
        self.followed_state = followed or {}
        self.update_state = update or {}
        auth = auth or {}
        self._safe_ui_call("обновление аккаунта", self._update_account_identity, state.get("account_profile") or {}, auth)
        self._safe_ui_call("запрос профиля аккаунта", self._request_account_profile, state.get("account_profile") or {}, auth)
        self._safe_ui_call("обновление состояния инициализации", self._update_initialization_state, auth)
        self._safe_ui_call("отрисовка основного состояния", self._render_state)
        self._safe_ui_call("отрисовка инвентаря Drops", self._render_drops)
        self._safe_ui_call("отрисовка подписок", self._render_followers)
        self._safe_ui_call("отрисовка обновления", self._render_update)
        self._safe_ui_call("обновление статуса аккаунта", self.account_status.setText, str(auth.get("auth_status") or ""))
        if auth.get("auth_error"):
            self.account_status.setToolTip(str(auth.get("auth_error")))
        username = str(self.latest_state.get("username") or "")
        if username and not self.username_input.hasFocus():
            self.username_input.setText(username)
        for key, check in self.option_checks.items():
            try:
                if not check.property("dirty"):
                    check.blockSignals(True)
                    check.setChecked(bool(self.latest_state.get("options", {}).get(key, DEFAULT_OPTIONS.get(key, True))))
                    check.blockSignals(False)
            except Exception as ui_error:
                _log_ui_exception("обновление настройки " + str(key), ui_error)
        for key, check in self.app_setting_checks.items():
            try:
                if not check.property("dirty"):
                    check.blockSignals(True)
                    check.setChecked(bool(self.latest_state.get("app_settings", {}).get(key, DEFAULT_APP_SETTINGS.get(key, False))))
                    check.blockSignals(False)
            except Exception as ui_error:
                _log_ui_exception("обновление параметра приложения " + str(key), ui_error)
        self._safe_ui_call("загрузка настроек ставок", self._load_bet_values, self.latest_state.get("bet_settings") or DEFAULT_BET_SETTINGS)
        self._safe_ui_call("загрузка профилей ставок", self._load_bet_presets, self.latest_state.get("custom_presets") or {}, self.latest_state.get("selected_preset") or "Оптимальный")
        self._safe_ui_call("обновление версии приложения", self.version_sidebar.setText, "v" + str(self.latest_state.get("app_version") or APP_VERSION))

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
        state = self.latest_state if isinstance(self.latest_state, dict) else {}
        status = str(state.get("status_text") or "● НЕ ЗАПУЩЕН")
        color = str(state.get("status_color") or "#9BA3B4")
        notice = self._header_status_notice
        try:
            if notice and time.monotonic() < notice[2]:
                self.title_bar.set_status(notice[0], notice[1])
            else:
                self._header_status_notice = None
                self.title_bar.set_status(status, color)
            self.farm_status.setText(status)
            self.farm_status.setStyleSheet("color:" + color + ";")
            running = bool(state.get("session_running"))
            self.farm_button.setText("Остановить фарм" if running else "Запустить фарм")
            self.farm_button.setSvgIcon("stop" if running else "play", 15)
            self.add_channel_button.setEnabled(not running)
            self.add_channel_button.setToolTip("Остановите фарм, чтобы изменить список каналов." if running else "")
            self.username_input.setEnabled(not running)
            self.login_button.setEnabled(not running)
            self.logout_button.setEnabled(not running)
            account_tip = "Остановите фарм, чтобы сменить аккаунт" if running else ""
            self.login_button.setToolTip(account_tip)
            self.logout_button.setToolTip(account_tip)
        except Exception as error:
            _log_ui_exception("обновление статуса и элементов управления", error)
        self._safe_ui_call("обновление часов сессии", self._update_session_clock)
        try:
            self.title_bar.set_gained_total(state.get("gained_total", 0))
        except Exception as error:
            _log_ui_exception("обновление счётчика очков", error)
        self._safe_ui_call("отрисовка активных слотов", self._render_active_slots)
        try:
            signature = (
                tuple(state.get("channels") or []),
                json.dumps(state.get("channel_profiles") or {}, sort_keys=True, ensure_ascii=False),
                json.dumps(state.get("channel_balances") or {}, sort_keys=True),
                json.dumps(state.get("channel_currency") or {}, sort_keys=True, ensure_ascii=False),
                json.dumps(state.get("watch_status") or {}, sort_keys=True),
                tuple(state.get("watch_slots") or []),
                tuple(state.get("temporary_channels") or [])
            )
        except Exception as error:
            _log_ui_exception("подготовка данных карточек каналов", error)
            signature = None
        if signature is not None and signature != self._last_channel_signature:
            if self._safe_ui_call("отрисовка карточек каналов", self._render_channels):
                self._last_channel_signature = signature
        self._safe_ui_call("отрисовка ленты наград", self._render_reward_feed, state.get("rewards", []))
        self._safe_ui_call("отрисовка ленты событий", self._render_event_feed, state.get("events", []))
        self._safe_ui_call("отрисовка журнала событий", self._render_logs)

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
        auth = self.latest_auth_state if isinstance(self.latest_auth_state, dict) else {}
        auth_state = str(auth.get("worker_state") or "").lower()
        auth_waiting = bool(auth.get("worker_alive") or auth.get("restore_pending") or auth_state in ("restoring", "running"))
        stored_account = str(auth.get("username") or "").strip()
        account_ready = bool(auth.get("authenticated")) or (not stored_account and not auth_waiting)
        data_ready = bool(self._startup_snapshot_received)
        overlay_ready = not hasattr(self, "initialization_overlay") or not self.initialization_overlay.isVisible()
        slots_ready = data_ready and account_ready and not auth_waiting and overlay_ready
        target = 118 if slots_ready else 0
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
        profiles = profiles if isinstance(profiles, dict) else {}
        balances = balances if isinstance(balances, dict) else {}
        slot_data = []
        for number in (1, 2):
            login = next((key for key, value in watch_map.items() if value == number), "")
            profile = profiles.get(login, {}) if login else {}
            if not isinstance(profile, dict):
                profile = {}
            slot_data.append((login, str(profile.get("display_name") or ""), str(profile.get("avatar_url") or ""), _ui_int(balances.get(login, 0))))
        signature = tuple(slot_data)
        if signature == self._last_slot_signature:
            return
        completed = True
        for number, card in enumerate(list(self.slot_cards), 1):
            login, display_name, avatar_url, balance = slot_data[number - 1]
            profile = profiles.get(login, {}) if login else {}
            if not isinstance(profile, dict):
                profile = {}
            try:
                replacement = ActiveSlotCard(number, login, profile, balance, self.image_cache)
                index = self.slot_layout.indexOf(card)
                self.slot_layout.removeWidget(card)
                card.deleteLater()
                self.slot_layout.insertWidget(max(0, index), replacement, 1)
                self.slot_cards[number - 1] = replacement
            except Exception as error:
                _log_ui_exception("обновление активного слота " + str(number), error)
                completed = False
        if completed:
            self._last_slot_signature = signature

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child = item.layout()
            if widget is not None:
                widget.deleteLater()
            if child is not None:
                self._clear_layout(child)

    def _channel_grid_column_count(self):
        width = self.channels_scroll.viewport().width() if hasattr(self, "channels_scroll") else self.width() - 280
        return max(2, min(6, int((max(1, width) + 12) // 202)))

    def _update_channel_grid_columns(self):
        if not hasattr(self, "channels_grid"):
            return
        columns = self._channel_grid_column_count()
        if columns == self._channel_columns:
            return
        self._channel_columns = columns
        self._last_channel_signature = None
        self._render_channels()

    def _render_channels(self):
        try:
            self._clear_layout(self.channels_grid)
            state = self.latest_state if isinstance(self.latest_state, dict) else {}
            channels = state.get("channels") or []
            if not isinstance(channels, (list, tuple)):
                channels = []
            profiles = state.get("channel_profiles")
            balances = state.get("channel_balances")
            currency = state.get("channel_currency")
            profiles = profiles if isinstance(profiles, dict) else {}
            balances = balances if isinstance(balances, dict) else {}
            currency = currency if isinstance(currency, dict) else {}
            temporary = set(state.get("temporary_channels") or [])
            watch = self._watch_slot_map()
            actions_enabled = not bool(state.get("session_running"))
            search = str(getattr(self, "channel_search_text", "") or "").strip().lower()
            visible = []
            for item in channels:
                login = str(item or "").strip().lower()
                if not login:
                    continue
                profile = profiles.get(login)
                if not isinstance(profile, dict):
                    profile = {}
                if not search or search in login or search in str(profile.get("display_name") or "").lower():
                    visible.append(login)
            columns = self._channel_grid_column_count()
            self._channel_columns = columns
            for index, login in enumerate(visible):
                try:
                    profile = profiles.get(login)
                    if not isinstance(profile, dict):
                        profile = {}
                    currency_info = currency.get(login)
                    if not isinstance(currency_info, dict):
                        currency_info = {}
                    card = ChannelCard(
                        login,
                        profile,
                        balances.get(login, 0),
                        currency_info,
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
                except Exception as error:
                    _log_ui_exception("создание карточки канала @" + login, error)
                    card = QLabel("@" + login + " · Ошибка отображения")
                    card.setObjectName("mutedText")
                    card.setMinimumHeight(72)
                self.channels_grid.addWidget(card, index // columns, index % columns)
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
            return True
        except Exception as error:
            _log_ui_exception("отрисовка списка каналов", error)
            return False


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

    def _update_drops_card_widths(self):
        if not hasattr(self, "drops_grid") or not hasattr(self, "drops_scroll"):
            return
        width = self.drops_scroll.viewport().width()
        card_width = max(220, min(440, int((max(1, width) - 24) // 3)))
        for index in range(self.drops_grid.count()):
            widget = self.drops_grid.itemAt(index).widget()
            if isinstance(widget, CampaignCard):
                widget.setFixedWidth(card_width)

    def _render_drops(self):
        if not hasattr(self, "drops_grid"):
            return
        campaigns_all = eligible_watch_campaigns(self.drops_state.get("campaigns", []))
        filter_index = int(getattr(self, "drops_filter_index", 0))
        if filter_index == 1:
            campaigns = [item for item in campaigns_all if item.get("started") or item.get("in_inventory")]
        elif filter_index == 2:
            campaigns = eligible_twitch_campaigns(self.drops_state.get("campaigns", []))
        else:
            campaigns = campaigns_all

        progress_count = 0
        for campaign in campaigns_all:
            for drop in campaign.get("visible_drops", []):
                percentage = drop.get("percentage")
                if percentage is None:
                    watched = _ui_int(drop.get("watched_minutes", drop.get("current_minutes", 0)))
                    required = _ui_int(drop.get("required_minutes", drop.get("requiredMinutes", 0)))
                    percentage = (watched * 100.0 / required) if required > 0 else 0
                if int(float(percentage or 0)) < 100 and not _flag_enabled(drop.get("claimed")):
                    progress_count += 1
        self.drops_active_value.setText(str(len(campaigns_all)))
        self.drops_progress_value.setText(str(progress_count))
        updated_at = self.drops_state.get("updated_at")
        updated_text = "Ещё не обновлялось"
        try:
            if updated_at:
                timestamp = float(updated_at)
                updated_text = datetime.fromtimestamp(timestamp).strftime("%H:%M · %d.%m.%Y")
        except (TypeError, ValueError, OverflowError, OSError):
            updated_text = "Ещё не обновлялось"
        self.drops_updated_label.setText(updated_text)

        session_running = bool(self.latest_state.get("session_running"))
        signature = json.dumps([
            campaigns,
            filter_index,
            self.drops_state.get("loading"),
            self.drops_state.get("error"),
            self.drops_state.get("updated_at"),
            session_running
        ], sort_keys=True, ensure_ascii=False)
        if signature == self._last_campaign_signature:
            return
        self._last_campaign_signature = signature
        self._clear_layout(self.drops_grid)

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
            self.drops_grid.addWidget(empty, 0, 0, 1, 3)
        else:
            width = self.drops_scroll.viewport().width() if hasattr(self, "drops_scroll") else self.width() - 280
            card_width = max(220, min(440, int((max(1, width) - 24) // 3)))
            for index, campaign in enumerate(campaigns):
                card = CampaignCard(campaign, self.image_cache, self._add_drop_streamer, add_enabled=not session_running)
                card.setFixedWidth(card_width)
                self.drops_grid.addWidget(card, index // 3, index % 3)


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
            add = QPushButton("Добавлен" if added else "Добавить")
            add.setSvgIcon("check" if added else "plus", 14)
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
        rendered = []
        for item in rows[-500:]:
            line = str(item.get("line") or item.get("message") or item)
            lower_line = line.lower()
            fallback_level = item.get("level") if isinstance(item, dict) else "INFO"
            severity = _log_entry_severity(line, fallback_level)
            if search and search not in lower_line:
                continue
            if not log_entry_matches_level(line, level, fallback_level):
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
        self.title_bar.set_maximized(self.isMaximized())

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
        if hasattr(self, "channels_grid"):
            QTimer.singleShot(0, self._update_channel_grid_columns)
        if hasattr(self, "drops_grid"):
            QTimer.singleShot(0, self._update_drops_card_widths)

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
