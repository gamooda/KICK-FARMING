import json
import os
import random
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

from android_diagnostics import get_log_path, get_log_text, initialize_android_logging, log_event, log_exception

initialize_android_logging("ui-module-import")

from kivy.app import App
from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.properties import StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image as KivyImage
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import FadeTransition, NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.graphics import Color, RoundedRectangle, Rectangle, Line
from kivy.utils import platform

if platform == "android":
    try:
        from android.storage import app_storage_path
        os.environ.setdefault("APPDATA", app_storage_path())
        log_event("APP_STORAGE_READY", "APPDATA=" + str(os.environ.get("APPDATA") or ""))
    except Exception as error:
        log_exception("APP_STORAGE_PATH_FAILED", error)

try:
    from i18n import get_language, set_language, translate_text
except Exception:
    get_language = lambda: "ru"
    set_language = lambda value: value
    translate_text = lambda value: str(value or "")

APP_NAME = "Twitch Farming"
RPC_HOST = "127.0.0.1"
RPC_PORT = 43771
PACKAGE_NAME = "org.gamooda.twitchfarming"
GITHUB_RELEASES = "https://github.com/gamooda/KICK-FARMING/releases/latest"
ROOT_DIR = Path(__file__).resolve().parent
ICON_DIR = ROOT_DIR / "icons"

BG = (0.055, 0.055, 0.085, 1)
SURFACE = (0.095, 0.095, 0.135, 1)
SURFACE_ALT = (0.125, 0.12, 0.18, 1)
PURPLE = (0.49, 0.31, 0.94, 1)
PURPLE_SOFT = (0.25, 0.18, 0.39, 1)
TEXT = (0.94, 0.93, 0.98, 1)
MUTED = (0.60, 0.60, 0.69, 1)
GREEN = (0.25, 0.82, 0.61, 1)
RED = (0.98, 0.39, 0.48, 1)
AMBER = (0.94, 0.68, 0.28, 1)
LINE_COLOR = (0.20, 0.19, 0.27, 1)


def rounded(widget, color, radius=dp(16), border=None):
    widget.canvas.before.clear()
    with widget.canvas.before:
        Color(*color)
        widget._rounded_bg = RoundedRectangle(pos=widget.pos, size=widget.size, radius=[radius])
        if border:
            Color(*border)
            widget._rounded_border = Line(rounded_rectangle=(widget.x, widget.y, widget.width, widget.height, radius), width=1.1)

    def update(*_):
        widget._rounded_bg.pos = widget.pos
        widget._rounded_bg.size = widget.size
        if border:
            widget._rounded_border.rounded_rectangle = (widget.x, widget.y, widget.width, widget.height, radius)

    widget.bind(pos=update, size=update)


class Panel(BoxLayout):
    def __init__(self, color=SURFACE, radius=dp(16), border=None, **kwargs):
        super().__init__(**kwargs)
        rounded(self, color, radius, border)


class FarmButton(Button):
    def __init__(self, text="", color=PURPLE, text_color=TEXT, radius=dp(12), icon=None, **kwargs):
        self._icon_name = ""
        self._icon_widget = None
        self._plain_text = translate_text(str(text))
        super().__init__(text=self._plain_text, **kwargs)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self.text = self._plain_text
        self.color = text_color
        self.bold = True
        self.font_size = sp(13)
        self.halign = "center"
        self.valign = "middle"
        self._button_color = color
        self._button_radius = radius
        self.padding = [dp(27), dp(4)] if icon else [dp(8), dp(4)]
        self.bind(pos=self._draw_bg, size=self._draw_bg)
        with self.canvas.before:
            Color(*self._button_color)
            self._button_bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._button_radius])
        if icon:
            self.set_icon(icon)
        self._draw_bg()

    def set_icon(self, name):
        name = str(name or "")
        if name == self._icon_name:
            return
        if self._icon_widget is not None:
            self.remove_widget(self._icon_widget)
            self._icon_widget = None
        self._icon_name = name
        self.text = ("        " if name else "") + self._plain_text
        path = ICON_DIR / (name + ".png") if name else None
        if path is not None and path.is_file():
            self._icon_widget = KivyImage(
                source=str(path),
                size_hint=(None, None),
                size=(dp(17), dp(17)),
                allow_stretch=True,
                keep_ratio=True
            )
            self.add_widget(self._icon_widget)
            self.padding = [dp(27), dp(4)]
        else:
            self.padding = [dp(8), dp(4)]
        self._draw_bg()

    def set_label(self, value):
        self._plain_text = translate_text(str(value or ""))
        self.text = ("        " if self._icon_name else "") + self._plain_text

    def _draw_bg(self, *_):
        self._button_bg.pos = self.pos
        self._button_bg.size = self.size
        if self._icon_widget is not None:
            self._icon_widget.pos = (self.x + dp(10), self.center_y - dp(8.5))


class NavButton(ButtonBehavior, BoxLayout):
    def __init__(self, key, title, icon, active=False, **kwargs):
        super().__init__(orientation="vertical", size_hint_x=1, padding=[dp(2), dp(4)], spacing=dp(1), **kwargs)
        self.key = key
        self.icon_name = icon
        self.active = bool(active)
        self.icon_widget = KivyImage(
            source=str(ICON_DIR / (icon + ("_active" if active else "") + ".png")),
            size_hint=(None, None),
            size=(dp(20), dp(20)),
            allow_stretch=True,
            keep_ratio=True,
            pos_hint={"center_x": 0.5}
        )
        self.label = AppText(text=title, size=9, color=(0.82, 0.70, 1, 1) if active else MUTED, align="center", size_hint_y=None, height=dp(18))
        self.add_widget(self.icon_widget)
        self.add_widget(self.label)
        self.bind(pos=self._draw_bg, size=self._draw_bg)
        self._draw_bg()

    def set_active(self, active):
        self.active = bool(active)
        self.label.color = (0.82, 0.70, 1, 1) if self.active else MUTED
        suffix = "_active" if self.active else ""
        self.icon_widget.source = str(ICON_DIR / (self.icon_name + suffix + ".png"))
        self._draw_bg()

    def _draw_bg(self, *_):
        self.canvas.before.clear()
        with self.canvas.before:
            Color(*(PURPLE_SOFT if self.active else (0.075, 0.075, 0.115, 1)))
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])


class LogoEye(Widget):
    def __init__(self, **kwargs):
        super().__init__(size_hint=(None, None), **kwargs)
        with self.canvas:
            Color(0.13, 0.075, 0.23, 1)
            self.shape = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(1.2)])
        self.bind(pos=self._draw, size=self._draw)

    def _draw(self, *_):
        self.shape.pos = self.pos
        self.shape.size = self.size


class TwitchLogo(Widget):
    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        super().__init__(**kwargs)
        self.background = KivyImage(source=str(ICON_DIR / "logo_bg.png"), size_hint=(None, None), allow_stretch=True, keep_ratio=False)
        self.add_widget(self.background)
        self.eyes = [LogoEye(), LogoEye()]
        for eye in self.eyes:
            self.add_widget(eye)
        self.look_x = 0.0
        self.target_look_x = 0.0
        self.next_action = time.monotonic() + random.uniform(2.2, 4.0)
        self.bind(pos=self._layout, size=self._layout)
        self._layout()
        Clock.schedule_interval(self._tick, 0.05)

    def _layout(self, *_):
        self.background.pos = self.pos
        self.background.size = self.size
        for index, eye in enumerate(self.eyes):
            eye.width = self.width * 5 / 48
            eye.height = max(dp(1), self.height * 13 / 48)
            eye.center_x = self.x + self.width * ((19.5 if index == 0 else 30.5) / 48) + self.look_x
            eye.center_y = self.y + self.height * 28.5 / 48

    def _blink(self):
        for index, eye in enumerate(self.eyes):
            original = self.height * 13 / 48
            Animation.cancel_all(eye, "height")
            sequence = Animation(height=max(dp(1), original * 0.07), duration=0.07, t="in_out_quad")
            sequence += Animation(height=original, duration=0.14, t="out_quad")
            Clock.schedule_once(lambda _dt, animation=sequence, target=eye: animation.start(target), index * 0.045)

    def _tick(self, _dt):
        now = time.monotonic()
        if now >= self.next_action:
            action = random.choice(("blink", "look", "blink", "squint"))
            self.next_action = now + random.uniform(2.3, 4.2)
            if action == "blink":
                self._blink()
            elif action == "look":
                self.target_look_x = random.choice((-1, 1)) * self.width * 0.035
                Clock.schedule_once(lambda _dt: setattr(self, "target_look_x", 0.0), 0.75)
            else:
                for eye in self.eyes:
                    original = self.height * 13 / 48
                    Animation(height=original * 0.48, duration=0.12).start(eye)
                    Clock.schedule_once(lambda _dt, target=eye, value=original: Animation(height=value, duration=0.18).start(target), 0.6)
        self.look_x += (self.target_look_x - self.look_x) * 0.18
        self._layout()


class AppText(Label):
    def __init__(self, text="", size=14, color=TEXT, bold=False, align="left", **kwargs):
        super().__init__(text=str(text), **kwargs)
        self.text = translate_text(str(text))
        self.font_size = sp(size)
        self.color = color
        self.bold = bold
        self.halign = align
        self.valign = "middle"
        self.text_size = (self.width, None)
        self.bind(width=lambda *_: setattr(self, "text_size", (self.width, None)))



class TutorialProgress(Widget):
    def __init__(self, **kwargs):
        super().__init__(size_hint_y=None, height=dp(4), **kwargs)
        self.value = 0.0
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def set_value(self, value):
        self.value = min(1.0, max(0.0, float(value)))
        self._draw()

    def _draw(self, *_):
        self.canvas.before.clear()
        with self.canvas.before:
            Color(0.20, 0.18, 0.27, 1)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(3)])
            Color(0.57, 0.34, 1.0, 1)
            RoundedRectangle(pos=self.pos, size=(self.width * self.value, self.height), radius=[dp(3)])


class TutorialOverlay(FloatLayout):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.target = None
        self.hole = None
        self.card_height = dp(196)
        self.card = Panel(
            color=(0.105, 0.095, 0.16, 1),
            radius=dp(17),
            border=(0.43, 0.29, 0.74, 1),
            orientation="vertical",
            padding=[dp(12), dp(8)],
            spacing=dp(4),
            size_hint=(0.94, None),
            height=self.card_height
        )
        self.heading = AppText(text="TWITCH FARMING", size=8, color=(0.74, 0.60, 1, 1), bold=True, align="center", size_hint_y=None, height=dp(15))
        self.progress_label = AppText(text="", size=9, color=MUTED, bold=True, align="center", size_hint_y=None, height=dp(15))
        self.progress_bar = TutorialProgress()
        self.title_label = AppText(text="", size=14, bold=True, align="center", size_hint_y=None, height=dp(23))
        self.description_label = AppText(text="", size=10, color=MUTED, align="center", size_hint_y=None, height=dp(52))
        row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(38))
        self.skip_button = FarmButton("Пропустить", color=SURFACE_ALT, icon="close", size_hint_x=0.43, size_hint_y=1)
        self.skip_button.bind(on_release=lambda *_: self.app.finish_tutorial())
        self.next_button = FarmButton("Далее", color=PURPLE, icon="arrow_right", size_hint_x=0.57, size_hint_y=1)
        self.next_button.bind(on_release=lambda *_: self.app.advance_tutorial())
        row.add_widget(self.skip_button)
        row.add_widget(self.next_button)
        for widget in (self.heading, self.progress_label, self.progress_bar, self.title_label, self.description_label, row):
            self.card.add_widget(widget)
        self.add_widget(self.card)
        self.bind(pos=self._update_spotlight, size=self._update_spotlight)
        self._update_spotlight()

    def set_step(self, index, total, title, description, final=False):
        self.progress_label.text = f"Шаг {index + 1} из {total}"
        self.progress_bar.set_value((index + 1) / max(1, total))
        self.title_label.text = title
        self.description_label.text = description
        self.next_button.set_label("Начать работу" if final else "Далее")
        self.next_button.set_icon("check" if final else "arrow_right")

    def set_target(self, target):
        self.target = target
        self._update_spotlight()

    def _update_spotlight(self, *_):
        if self.width <= 0 or self.height <= 0:
            return
        hole = None
        target = self.target
        if target is not None and target.parent is not None:
            try:
                window_x, window_y = target.to_window(0, 0)
                x, y = self.to_widget(window_x, window_y)
                pad = dp(6)
                x = max(0, x - pad)
                y = max(0, y - pad)
                width = min(self.width - x, target.width + pad * 2)
                height = min(self.height - y, target.height + pad * 2)
                if width > 0 and height > 0:
                    hole = (x, y, width, height)
            except BaseException as error:
                log_exception("TUTORIAL_SPOTLIGHT_TARGET_FAILED", error)
        self.hole = hole
        self.canvas.before.clear()
        with self.canvas.before:
            Color(0.015, 0.012, 0.035, 0.76)
            if hole is None:
                Rectangle(pos=self.pos, size=self.size)
            else:
                x, y, width, height = hole
                pieces = (
                    ((0, 0), (x, self.height)),
                    ((x + width, 0), (max(0, self.width - x - width), self.height)),
                    ((x, 0), (width, y)),
                    ((x, y + height), (width, max(0, self.height - y - height)))
                )
                for position, size in pieces:
                    if size[0] > 0 and size[1] > 0:
                        Rectangle(pos=(self.x + position[0], self.y + position[1]), size=size)
                Color(0.67, 0.40, 1.0, 1)
                Line(rounded_rectangle=(self.x + x, self.y + y, width, height, dp(13)), width=dp(2.2))
                Color(0.82, 0.68, 1.0, 0.12)
                RoundedRectangle(pos=(self.x + x, self.y + y), size=(width, height), radius=[dp(13)])
        card_width = self.width * 0.94
        self.card.size = (card_width, self.card_height)
        card_x = (self.width - card_width) / 2
        if hole is None:
            card_y = dp(12)
        else:
            x, y, width, height = hole
            below_space = y - dp(14)
            above_space = self.height - y - height - dp(14)
            if below_space >= self.card_height:
                card_y = dp(12)
            elif above_space >= self.card_height:
                card_y = y + height + dp(12)
            else:
                card_y = dp(12) if y > self.height * 0.52 else max(dp(12), self.height - self.card_height - dp(12))
        self.card.pos = (card_x, card_y)

    def on_touch_down(self, touch):
        if super().on_touch_down(touch):
            return True
        x, y = self.to_widget(*touch.pos)
        if self.hole is not None:
            hx, hy, hw, hh = self.hole
            if hx <= x <= hx + hw and hy <= y <= hy + hh:
                return False
        return True

    def on_touch_move(self, touch):
        if super().on_touch_move(touch):
            return True
        x, y = self.to_widget(*touch.pos)
        if self.hole is not None:
            hx, hy, hw, hh = self.hole
            if hx <= x <= hx + hw and hy <= y <= hy + hh:
                return False
        return True

    def on_touch_up(self, touch):
        if super().on_touch_up(touch):
            return True
        x, y = self.to_widget(*touch.pos)
        if self.hole is not None:
            hx, hy, hw, hh = self.hole
            if hx <= x <= hx + hw and hy <= y <= hy + hh:
                return False
        return True


class Field(TextInput):
    def __init__(self, hint="", text="", password=False, **kwargs):
        super().__init__(**kwargs)
        self.hint_text = translate_text(hint)
        self.text = text
        self.password = password
        self.multiline = False
        self.size_hint_y = None
        self.height = dp(46)
        self.padding = [dp(14), dp(12)]
        self.foreground_color = TEXT
        self.hint_text_color = MUTED
        self.cursor_color = PURPLE
        self.background_normal = ""
        self.background_active = ""
        self.background_color = SURFACE_ALT
        self.font_size = sp(14)


class RPCClient:
    def call(self, method, args=None, kwargs=None, timeout=12):
        payload = json.dumps({
            "method": method,
            "args": list(args or []),
            "kwargs": dict(kwargs or {})
        }, ensure_ascii=False).encode("utf-8")
        deadline = time.monotonic() + min(max(float(timeout), 1.0), 15.0)
        last_error = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Фоновый сервис не отвечает. Повторный запуск уже запрошен.") from last_error
            try:
                with socket.create_connection((RPC_HOST, RPC_PORT), timeout=min(2.0, remaining)) as connection:
                    connection.settimeout(timeout)
                    connection.sendall(payload + b"\n")
                    chunks = []
                    while True:
                        block = connection.recv(65536)
                        if not block:
                            break
                        chunks.append(block)
                        if b"\n" in block:
                            break
                raw = b"".join(chunks).split(b"\n", 1)[0]
                if not raw:
                    raise RuntimeError("Фоновый сервис вернул пустой ответ.")
                envelope = json.loads(raw.decode("utf-8"))
                if not envelope.get("ok"):
                    raise RuntimeError(envelope.get("error") or "Ошибка фонового сервиса.")
                return envelope.get("result")
            except ConnectionRefusedError as error:
                last_error = error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Фоновый сервис не отвечает. Повторный запуск уже запрошен.") from error
                time.sleep(min(0.3, remaining))

class ChannelCard(Panel):
    def __init__(self, login, profile, points, slot, queued=False, **kwargs):
        super().__init__(
            color=(0.105, 0.105, 0.15, 1),
            radius=dp(16),
            border=LINE_COLOR,
            orientation="horizontal",
            size_hint_y=None,
            height=dp(91),
            padding=[dp(12), dp(10)],
            spacing=dp(11),
            **kwargs
        )
        avatar = Panel(
            color=PURPLE_SOFT if not profile.get("is_live") else (0.24, 0.18, 0.37, 1),
            radius=dp(22),
            size_hint=(None, None),
            size=(dp(44), dp(44)),
            orientation="vertical"
        )
        avatar.add_widget(AppText(text=str(login[:1]).upper(), size=18, bold=True, align="center"))
        self.add_widget(avatar)

        info = BoxLayout(orientation="vertical", spacing=dp(4))
        top = BoxLayout(orientation="horizontal", spacing=dp(5), size_hint_y=None, height=dp(24))
        top.add_widget(AppText(text=profile.get("display_name") or login, size=14, bold=True))
        badge = "СЛОТ " + str(slot) if slot else ("В ОЧЕРЕДИ" if queued else ("В ЭФИРЕ" if profile.get("is_live") else "ОФЛАЙН"))
        badge_color = AMBER if queued else (GREEN if slot else MUTED)
        top.add_widget(AppText(text=badge, size=9, color=badge_color, bold=True, size_hint_x=None, width=dp(82), align="right"))
        info.add_widget(top)

        details = []
        if profile.get("is_live"):
            try:
                viewers = int(profile.get("viewer_count") or 0)
            except (TypeError, ValueError):
                viewers = 0
            details.append(f"{viewers:,}".replace(",", " ") + " зрителей")
        try:
            amount = int(points or 0)
        except (TypeError, ValueError):
            amount = 0
        details.append(f"{amount:,}".replace(",", " ") + " баллов")
        info.add_widget(AppText(text="  ·  ".join(details), size=11, color=MUTED))
        self.add_widget(info)


class MobileScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.body = BoxLayout(orientation="vertical", spacing=dp(10), padding=[dp(14), dp(8)])
        self.scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        self.items = BoxLayout(orientation="vertical", spacing=dp(10), size_hint_y=None, padding=[0, 0, 0, dp(12)])
        self.items.bind(minimum_height=self.items.setter("height"))
        self.scroll.add_widget(self.items)
        self.body.add_widget(self.scroll)
        self.add_widget(self.body)

    def clear(self):
        self.items.clear_widgets()

    def title(self, text, subtitle=""):
        self.items.add_widget(AppText(text=text, size=21, bold=True, size_hint_y=None, height=dp(34)))
        if subtitle:
            self.items.add_widget(AppText(text=subtitle, size=11, color=MUTED, size_hint_y=None, height=dp(24)))


class TwitchMobileApp(App):
    def build(self):
        initialize_android_logging("ui-build")
        log_event("APP_BUILD_BEGIN", "python=" + sys.version + " platform=" + str(platform) + " app_file=" + str(__file__))
        try:
            icon_files = sorted(path.name for path in ICON_DIR.glob("*.png"))
            log_event("APP_ASSETS", "icons=" + str(len(icon_files)) + " icon_dir=" + str(ICON_DIR))
        except Exception as error:
            log_exception("APP_ASSET_CHECK_FAILED", error)
        Window.clearcolor = BG
        Window.softinput_mode = "below_target"
        if "--preview" in sys.argv:
            Window.size = (360, 800)
        self.language = get_language()
        self.rpc = RPCClient()
        self.snapshot = {}
        self.state = {}
        self.auth = {}
        self.followers = {}
        self.drops = {}
        self.last_log_id = 0
        self.log_cache = []
        self.polling = False
        self.busy = False
        self.toast_event = None
        self.preview = "--preview" in sys.argv
        self.screens = {}
        self.render_signatures = {}
        self.screen_dirty = {}
        self.startup = {}
        self.auth_flow = {}
        self.update_state = {}
        self.followers_limit = 40
        self.login_popup = None
        self.login_prompt_shown = False
        self.last_auth_code_opened = ""
        self.login_status_label = None
        self.login_code_label = None
        self.login_open_button = None
        self.login_app_button = None
        self.login_copy_button = None
        self.login_submit_button = None
        self.tutorial_popup = None
        self.tutorial_title_label = None
        self.tutorial_description_label = None
        self.tutorial_progress_label = None
        self.tutorial_next_button = None
        self.tutorial_step = 0
        self.tutorial_shown = False
        self.tutorial_targets = {}
        self.last_service_start_at = 0.0
        self.root_box = BoxLayout(orientation="vertical", spacing=0)
        self.header = self._build_header()
        self.root_box.add_widget(self.header)
        self.screen_manager = ScreenManager(transition=NoTransition())
        for name in ("channels", "drops", "followers", "predictions", "events", "settings"):
            screen = MobileScreen(name=name)
            self.screens[name] = screen
            self.screen_dirty[name] = True
            self.screen_manager.add_widget(screen)
        self.root_box.add_widget(self.screen_manager)
        self.nav = self._build_nav()
        self.root_box.add_widget(self.nav)
        self.toast = AppText(text="", size=12, color=TEXT, align="center", size_hint_y=None, height=dp(42))
        self.toast.opacity = 0
        self.root_box.add_widget(self.toast)
        self.root_layer = FloatLayout()
        self.root_layer.add_widget(self.root_box)
        self.refresh_screen_content(force=True)
        log_event("APP_BUILD_OK", "root widget constructed")
        return self.root_layer

    def _build_header(self):
        header = Panel(color=(0.075, 0.075, 0.115, 1), radius=0, orientation="horizontal", size_hint_y=None, height=dp(62), padding=[dp(12), dp(7)], spacing=dp(8))
        header.add_widget(TwitchLogo(size=(dp(42), dp(42))))
        brand = BoxLayout(orientation="vertical", spacing=0)
        brand.add_widget(AppText(text="TWITCH FARMING", size=12, bold=True))
        self.header_user = AppText(text="Не подключён", size=9, color=MUTED)
        brand.add_widget(self.header_user)
        header.add_widget(brand)
        self.header_auth_button = FarmButton("Войти", color=SURFACE_ALT, icon="login", font_size=sp(9), size_hint=(None, None), size=(dp(82), dp(36)))
        self.header_auth_button.bind(on_release=lambda *_: self._header_auth_action())
        header.add_widget(self.header_auth_button)
        self.header_status = AppText(text="● ПОДКЛЮЧЕНИЕ...", size=8, color=MUTED, bold=True, align="right", size_hint_x=0.8)
        header.add_widget(self.header_status)
        return header

    def _build_nav(self):
        nav = Panel(color=(0.075, 0.075, 0.115, 1), radius=0, orientation="horizontal", size_hint_y=None, height=dp(68), padding=[dp(4), dp(4)], spacing=dp(3))
        self.nav_buttons = {}
        for key, title, icon in (
            ("channels", "Каналы", "channels"),
            ("drops", "Drops", "drops"),
            ("followers", "Фолл.", "followers"),
            ("predictions", "Ставки", "bets"),
            ("events", "Событ.", "events"),
            ("settings", "Настройки", "settings"),
        ):
            button = NavButton(key, title, icon, active=key == "channels")
            button.bind(on_release=lambda _button, target=key: self.navigate(target))
            nav.add_widget(button)
            self.nav_buttons[key] = button
        return nav

    def navigate(self, name):
        if name not in self.screens:
            return
        self.screen_manager.current = name
        for key, button in self.nav_buttons.items():
            button.set_active(key == name)
        self._render_screen(name, force=True)
        if self.auth.get("authenticated") and name == "followers" and not self.followers.get("loaded") and not self.followers.get("loading"):
            self.rpc_async("refresh_followed_channels")
        elif self.auth.get("authenticated") and name == "drops" and not self.drops.get("loaded") and not self.drops.get("loading"):
            self.rpc_async("refresh_drops_inventory")
        self.poll_state()

    def on_start(self):
        initialize_android_logging("ui-on-start")
        log_event("APP_ON_START", "preview=" + str(self.preview))
        if not self.preview:
            Clock.schedule_once(lambda _dt: self.open_login_popup(), 0)
            try:
                self.start_android_service(force=True)
            except BaseException as error:
                log_exception("ANDROID_SERVICE_START_CALL_FAILED", error)
            Clock.schedule_once(lambda _dt: self.poll_state(), 1.0)
            Clock.schedule_interval(lambda _dt: self.poll_state(), 3.0)
        else:
            self.apply_snapshot(self.preview_snapshot())
            Clock.schedule_once(self.capture_preview, 1.0)

    def on_pause(self):
        log_event("APP_ON_PAUSE", "background lifecycle callback")
        return True

    def on_resume(self):
        log_event("APP_ON_RESUME", "foreground lifecycle callback")
        initialize_android_logging("ui-on-resume")
        if not self.preview:
            Clock.schedule_once(lambda _dt: self.start_android_service(force=True), 0.2)
            Clock.schedule_once(lambda _dt: self.poll_state(), 1.0)
        return True

    def on_stop(self):
        log_event("APP_ON_STOP", "Kivy application stopped")

    def start_android_service(self, force=False):
        if platform != "android" or self.preview:
            return
        now = time.monotonic()
        cooldown = 1.5 if force else 4.5
        if now - self.last_service_start_at < cooldown:
            return
        self.last_service_start_at = now
        log_event("ANDROID_SERVICE_START_BEGIN", "service_class=" + PACKAGE_NAME + ".ServiceFarm force=" + str(bool(force)))
        try:
            from jnius import autoclass
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            if activity is None:
                raise RuntimeError("PythonActivity.mActivity is None")
            service_class = autoclass(PACKAGE_NAME + ".ServiceFarm")
            service_class.start(activity, "notification", APP_NAME, "", "")
            log_event("ANDROID_SERVICE_START_REQUESTED", "ServiceFarm.start returned")
        except BaseException as error:
            log_exception("ANDROID_SERVICE_START_FAILED", error)
            self.show_toast("Не удалось запустить фоновый сервис: " + str(error)[:100])



    def rpc_async(self, method, args=None, kwargs=None, callback=None, timeout=12):
        if self.busy and method != "snapshot":
            self.show_toast("Предыдущее действие ещё выполняется.")
            return
        if not self.preview:
            self.start_android_service(force=True)
        if method != "snapshot":
            self.busy = True

        def worker():
            started = time.monotonic()
            log_event("RPC_BEGIN", "method=" + str(method))
            try:
                result = self.rpc.call(method, args, kwargs, timeout=timeout)
                error = ""
                log_event("RPC_OK", "method=" + str(method) + " elapsed=" + str(round(time.monotonic() - started, 3)))
            except BaseException as exc:
                result = None
                error = str(exc)
                log_exception("RPC_FAILED method=" + str(method), exc)

            def complete(_dt):
                if method != "snapshot":
                    self.busy = False
                if error:
                    self.show_toast(error)
                else:
                    if method == "clear_logs":
                        self.log_cache.clear()
                        self.screen_dirty["events"] = True
                    if callback:
                        callback(result)
                    elif isinstance(result, dict) and result.get("message"):
                        self.show_toast(result["message"])
                if method != "snapshot":
                    self.poll_state()
            def safe_complete(_dt):
                try:
                    complete(_dt)
                except BaseException as exc:
                    log_exception("RPC_UI_CALLBACK_FAILED method=" + str(method), exc)
                    self.busy = False
            Clock.schedule_once(safe_complete, 0)

        threading.Thread(target=worker, name="MobileRPC-" + method, daemon=True).start()

    def poll_state(self):
        if self.preview or self.polling:
            return
        self.start_android_service()
        self.polling = True

        def worker():
            started = time.monotonic()
            try:
                result = self.rpc.call("snapshot", kwargs={"after_log_id": self.last_log_id, "screen": self.screen_manager.current}, timeout=7)
                error = ""
                log_event("STATE_POLL_OK", "elapsed=" + str(round(time.monotonic() - started, 3)) + " screen=" + str(self.screen_manager.current))
            except BaseException as exc:
                result = None
                error = str(exc)
                log_exception("STATE_POLL_FAILED", exc)

            def complete(_dt):
                self.polling = False
                if result and result.get("ok"):
                    self.apply_snapshot(result)
                elif error and not self.state:
                    self.header_status.text = "● ПОДКЛЮЧЕНИЕ..."
            def safe_complete(_dt):
                try:
                    complete(_dt)
                except BaseException as exc:
                    log_exception("STATE_POLL_UI_CALLBACK_FAILED", exc)
                    self.polling = False
            Clock.schedule_once(safe_complete, 0)

        threading.Thread(target=worker, name="MobileStatePoll", daemon=True).start()

    def apply_snapshot(self, snapshot):
        self.snapshot = snapshot or {}
        self.state = self.snapshot.get("state") or {}
        incoming_logs = self.state.get("logs") or []
        known_log_ids = {int(item.get("id") or 0) for item in self.log_cache if isinstance(item, dict)}
        for item in incoming_logs:
            if isinstance(item, dict):
                log_id = int(item.get("id") or 0)
                if log_id and log_id not in known_log_ids:
                    self.log_cache.append(dict(item))
                    known_log_ids.add(log_id)
        self.log_cache = self.log_cache[-80:]
        self.state["logs"] = list(self.log_cache)
        self.auth = self.snapshot.get("auth") or {}
        self.startup = self.snapshot.get("startup") or {}
        self.auth_flow = self.snapshot.get("auth_flow") or {}
        self.update_state = self.snapshot.get("update") or {}
        if isinstance(self.snapshot.get("followers"), dict):
            self.followers = self.snapshot.get("followers") or {}
        if isinstance(self.snapshot.get("drops"), dict):
            self.drops = self.snapshot.get("drops") or {}
        next_id = self.state.get("next_log_id")
        if next_id:
            self.last_log_id = max(self.last_log_id, int(next_id) - 1)
        username = self.auth.get("username") or self.state.get("authenticated_username") or self.state.get("username") or ""
        self.header_user.text = ("@" + username) if self.auth.get("authenticated") else "Не подключён"
        status = self.state.get("status_text") or self.auth.get("auth_status") or "● НЕ ЗАПУЩЕН"
        self.header_status.text = status.replace("●  ", "● ")
        self.header_status.color = self._hex_color(self.state.get("status_color", "#A9B2C3"))
        authenticated = bool(self.auth.get("authenticated"))
        self.header_auth_button.set_label("Войти")
        self.header_auth_button.set_icon("login")
        self.header_auth_button.disabled = authenticated
        self.header_auth_button.opacity = 0 if authenticated else 1
        self.header_auth_button.size = (0, 0) if authenticated else (dp(82), dp(36))
        self._render_screen(self.screen_manager.current)
        self._update_login_popup()
        self._update_auth_gate()

    def _screen_signature(self, name):
        state = self.state if isinstance(self.state, dict) else {}
        if name == "channels":
            data = {
                "channels": state.get("channels"),
                "profiles": state.get("channel_profiles"),
                "balances": state.get("channel_balances"),
                "online": state.get("online_channels"),
                "slots": state.get("watch_slots"),
                "running": state.get("session_running"),
                "gained": state.get("gained_total"),
                "status": state.get("status_text"),
            }
        elif name == "drops":
            data = self.drops
        elif name == "followers":
            data = {"followers": self.followers, "channels": state.get("channels"), "running": state.get("session_running")}
        elif name == "predictions":
            data = state.get("bet_settings")
        elif name == "events":
            data = {"events": (state.get("events") or [])[-20:], "logs": self.log_cache[-25:]}
        else:
            data = {"options": state.get("options"), "app_settings": state.get("app_settings"), "auth": self.auth, "update": self.update_state, "language": self.language}
        return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)

    def _render_screen(self, name, force=False):
        if name not in self.screens:
            return
        signature = self._screen_signature(name)
        if not force and not self.screen_dirty.get(name, True) and signature == self.render_signatures.get(name):
            return
        renderers = {
            "channels": self._render_channels,
            "drops": self._render_drops,
            "followers": self._render_followers,
            "predictions": self._render_predictions,
            "events": self._render_events,
            "settings": self._render_settings,
        }
        renderers[name]()
        self.render_signatures[name] = signature
        self.screen_dirty[name] = False

    def refresh_screen_content(self, force=False):
        self._render_screen(self.screen_manager.current, force=force)

    def _header_auth_action(self):
        if self.auth.get("authenticated"):
            self.confirm_logout()
        else:
            self.open_login_popup(self.state.get("username") or "")

    def _update_auth_gate(self):
        if self.preview:
            return
        authenticated = bool(self.auth.get("authenticated"))
        if authenticated:
            self.login_prompt_shown = True
            if self.login_popup is not None:
                popup = self.login_popup
                self.login_popup = None
                try:
                    popup.dismiss()
                except BaseException as error:
                    log_exception("AUTH_GATE_LOGIN_POPUP_CLOSE_FAILED", error)
            if not self.state.get("tutorial_done") and not self.tutorial_shown and self.tutorial_popup is None:
                self.tutorial_shown = True
                log_event("AUTH_GATE_TUTORIAL_SCHEDULED", "Twitch authentication confirmed; starting mobile tutorial")
                Clock.schedule_once(lambda _dt: self.show_tutorial(), 0.25)
            return
        if not self.startup.get("ready"):
            return
        worker_state = str(self.auth.get("worker_state") or "").lower()
        waiting = bool(self.auth.get("worker_alive") or self.auth.get("restore_pending") or worker_state in ("restoring", "running"))
        if waiting:
            return
        if self.tutorial_popup is not None:
            overlay = self.tutorial_popup
            self.tutorial_popup = None
            try:
                self.root_layer.remove_widget(overlay)
            except BaseException as error:
                log_exception("AUTH_GATE_TUTORIAL_REMOVE_FAILED", error)
        if not self.login_prompt_shown:
            self.login_prompt_shown = True
            username = self.state.get("username") or self.auth.get("username") or ""
            Clock.schedule_once(lambda _dt, value=username: self.open_login_popup(value), 0.25)

    def _update_login_popup(self):
        if self.login_popup is None:
            return
        if self.auth.get("authenticated"):
            popup = self.login_popup
            self.login_popup = None
            try:
                popup.dismiss()
                log_event("LOGIN_POPUP_CLOSED", "Twitch authentication confirmed")
            except BaseException as error:
                log_exception("LOGIN_POPUP_CLOSE_FAILED", error)
            return
        auth_error = str(self.auth.get("auth_error") or "")
        auth_status = str(self.auth.get("auth_status") or "Подготовка входа в Twitch…")
        worker_alive = bool(self.auth.get("worker_alive"))
        code = str(self.auth_flow.get("code") or self.auth.get("auth_code") or "").strip()
        code_ready = bool(code and code.lower() not in ("подтверждён", "сессия восстановлена"))
        if self.login_submit_button is not None:
            self.login_submit_button.disabled = worker_alive
            if worker_alive:
                self.login_submit_button.set_label("Ожидание подтверждения…" if code_ready else "Подключение к Twitch…")
            elif auth_error:
                self.login_submit_button.set_label("Повторить вход")
            else:
                self.login_submit_button.set_label("Войти через Twitch")
        if code_ready:
            if self.login_code_label is not None:
                self.login_code_label.text = code
            if self.login_open_button is not None:
                self.login_open_button.disabled = False
            if self.login_copy_button is not None:
                self.login_copy_button.disabled = False
        elif self.login_code_label is not None:
            self.login_code_label.text = "Код ещё не получен" if worker_alive else "Новый код будет создан при повторном входе."
        if self.login_status_label is not None:
            if auth_error:
                self.login_status_label.text = auth_error
            elif worker_alive:
                self.login_status_label.text = auth_status
            elif code_ready:
                self.login_status_label.text = "Подтверждение не завершено. Проверь код или запроси новый."
            else:
                self.login_status_label.text = "Укажи логин и нажми «Войти через Twitch»."

    def _auth_activation_url(self):
        code = str(self.auth_flow.get("code") or self.auth.get("auth_code") or "").strip()
        url = str(self.auth_flow.get("url") or "https://www.twitch.tv/activate")
        if code and "device-code=" not in url:
            url += ("&" if "?" in url else "?") + "public=true&device-code=" + quote(code)
        return url

    def open_login_popup(self, prefill=""):
        if self.login_popup is not None:
            self.login_popup.open()
            return
        self.login_prompt_shown = True
        content = BoxLayout(orientation="vertical", padding=[dp(26), dp(22)], spacing=dp(10))
        content.add_widget(Widget(size_hint_y=1))
        content.add_widget(TwitchLogo(size_hint=(None, None), size=(dp(58), dp(58)), pos_hint={"center_x": 0.5}))
        content.add_widget(AppText(text="Добро пожаловать в Twitch Farming", size=17, bold=True, align="center", size_hint_y=None, height=dp(36)))
        content.add_widget(AppText(text="Подключи аккаунт Twitch, чтобы собирать Channel Points и Drops. Пароль вводится только в официальном приложении или на сайте Twitch.", size=11, color=MUTED, align="center", size_hint_y=None, height=dp(52)))
        field = Field(hint="Логин Twitch", text=str(prefill or self.state.get("username") or ""))
        content.add_widget(field)
        self.login_status_label = AppText(text="Укажи логин и нажми «Войти через Twitch». После этого появится код подтверждения.", size=10, color=MUTED, align="center", size_hint_y=None, height=dp(48))
        content.add_widget(self.login_status_label)
        code_panel = Panel(color=SURFACE_ALT, radius=dp(12), orientation="vertical", padding=[dp(10), dp(7)], size_hint_y=None, height=dp(58))
        code_panel.add_widget(AppText(text="КОД ПОДТВЕРЖДЕНИЯ", size=8, color=MUTED, bold=True, align="center"))
        self.login_code_label = AppText(text="Ожидание кода…", size=16, bold=True, align="center")
        code_panel.add_widget(self.login_code_label)
        content.add_widget(code_panel)
        self.login_app_button = FarmButton("Войти в приложении Twitch", color=SURFACE_ALT, icon="external", size_hint_y=None, height=dp(44))
        self.login_app_button.bind(on_release=lambda *_: self.open_twitch_app_login())
        self.login_open_button = FarmButton("Открыть подтверждение в браузере", color=SURFACE_ALT, icon="external", size_hint_y=None, height=dp(44))
        self.login_open_button.disabled = True
        self.login_open_button.bind(on_release=lambda *_: self.open_twitch_activation())
        self.login_copy_button = FarmButton("Скопировать код", color=SURFACE_ALT, icon="copy", size_hint_y=None, height=dp(38))
        self.login_copy_button.disabled = True
        self.login_copy_button.bind(on_release=lambda *_: self.copy_login_code())
        content.add_widget(self.login_app_button)
        content.add_widget(self.login_open_button)
        content.add_widget(self.login_copy_button)
        submit = FarmButton("Войти через Twitch", color=PURPLE, icon="login", size_hint_y=None, height=dp(46))
        self.login_submit_button = submit
        content.add_widget(submit)
        content.add_widget(Widget(size_hint_y=1))
        self.login_popup = Popup(title="", content=content, size_hint=(1, 1), auto_dismiss=False, background_color=BG, separator_color=BG)

        def begin_login():
            username = field.text.strip()
            if not username:
                self.login_status_label.text = "Сначала укажи Twitch-логин."
                field.focus = True
                return
            submit.disabled = True
            submit.set_label("Подключение к Twitch…")
            self.login_status_label.text = "Создаём запрос авторизации Twitch…"

            def completed(result):
                if not isinstance(result, dict) or not result.get("ok"):
                    submit.disabled = False
                    submit.set_label("Повторить вход")
                    self.login_status_label.text = str((result or {}).get("message") or "Не удалось начать вход.")
                else:
                    self.login_status_label.text = str(result.get("message") or "Запрос отправлен. Ожидаем код подтверждения…")
                    self._update_login_popup()

            self.rpc_async("login", [username], callback=completed, timeout=15)

        submit.bind(on_release=lambda *_: begin_login())
        self.login_popup.open()
        if self.auth_flow.get("code"):
            self._update_login_popup()

    def open_twitch_app_login(self):
        if platform != "android":
            self.open_external("https://www.twitch.tv/login")
            return
        try:
            from jnius import autoclass
            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            if activity is None:
                raise RuntimeError("Android activity is unavailable")
            package = "tv.twitch.android.app"
            for route in ("twitch://following", "twitch://login"):
                intent = Intent(Intent.ACTION_VIEW)
                intent.setData(Uri.parse(route))
                intent.setPackage(package)
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                try:
                    activity.startActivity(intent)
                    log_event("TWITCH_APP_LOGIN_OPENED", "package=" + package + " route=" + route)
                    return
                except BaseException as route_error:
                    log_event("TWITCH_APP_DEEP_LINK_FAILED", route + " · " + str(route_error))
            launch = activity.getPackageManager().getLaunchIntentForPackage(package)
            if launch is None:
                raise RuntimeError("Приложение Twitch не найдено.")
            activity.startActivity(launch)
            log_event("TWITCH_APP_LOGIN_OPENED", "target=launcher_fallback")
            self.show_toast("Twitch открыт. На экране профиля выбери «Войти».")
        except BaseException as error:
            log_exception("TWITCH_APP_LOGIN_FAILED", error)
            self._open_twitch_web_url("https://www.twitch.tv/login")

    def _open_twitch_web_url(self, url):
        if platform != "android":
            self.show_toast(url)
            return False
        try:
            from jnius import autoclass
            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            if activity is None:
                raise RuntimeError("Android activity is unavailable")
            uri = Uri.parse(str(url))
            packages = (
                "com.android.chrome",
                "com.mi.globalbrowser",
                "com.android.browser",
                "org.mozilla.firefox",
                "com.opera.browser",
            )
            for package in packages:
                intent = Intent(Intent.ACTION_VIEW)
                intent.setData(uri)
                intent.setPackage(package)
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                try:
                    activity.startActivity(intent)
                    log_event("TWITCH_WEB_URL_OPENED", "package=" + package)
                    return True
                except BaseException as package_error:
                    log_event("TWITCH_BROWSER_PACKAGE_FAILED", package + " · " + str(package_error))
            intent = Intent(Intent.ACTION_VIEW)
            intent.setData(uri)
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            try:
                activity.startActivity(intent)
                log_event("TWITCH_WEB_URL_OPENED", "target=default_browser")
                return True
            except BaseException as browser_error:
                log_exception("TWITCH_DEFAULT_BROWSER_FAILED", browser_error)
                Clipboard.copy(str(url))
                self.show_toast("Браузер не открылся. Ссылка скопирована — вставь её в браузер.")
                return False
        except BaseException as error:
            log_exception("TWITCH_WEB_URL_OPEN_FAILED", error)
            try:
                Clipboard.copy(str(url))
                self.show_toast("Не удалось открыть браузер. Ссылка скопирована.")
            except Exception:
                self.show_toast("Не удалось открыть браузер: " + str(error)[:100])
            return False

    def open_twitch_activation(self):
        url = self._auth_activation_url()
        if not url.startswith("https://www.twitch.tv/activate"):
            log_event("TWITCH_ACTIVATION_URL_INVALID", "Unexpected activation URL host/path")
            url = "https://www.twitch.tv/activate"
            code = str(self.auth_flow.get("code") or self.auth.get("auth_code") or "").strip()
            if code and code.lower() not in ("подтверждён", "сессия восстановлена"):
                url += "?public=true&device-code=" + quote(code)
        if platform != "android":
            self.open_external(url)
            return
        if not self._open_twitch_web_url(url):
            log_event("TWITCH_ACTIVATION_OPEN_FAILED", "URL copied to clipboard")

    def copy_login_code(self):
        code = str(self.auth_flow.get("code") or self.auth.get("auth_code") or "").strip()
        if code and code.lower() not in ("подтверждён", "сессия восстановлена"):
            Clipboard.copy(code)
            self.show_toast("Код подтверждения скопирован.")

    def confirm_logout(self):
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(12))
        content.add_widget(AppText(text="Отключить текущий аккаунт Twitch?", size=13, align="center"))
        popup = Popup(title="Выход из аккаунта", content=content, size_hint=(0.88, None), height=dp(170), background_color=SURFACE)
        row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(44))
        row.add_widget(FarmButton("Отмена", color=SURFACE_ALT, icon="close", on_release=lambda *_: popup.dismiss()))

        def logout():
            popup.dismiss()
            self.rpc_async("logout", callback=lambda result: self.show_toast(str((result or {}).get("message") or "Сеанс завершён.")))

        row.add_widget(FarmButton("Выйти", color=RED, icon="logout", on_release=lambda *_: logout()))
        content.add_widget(row)
        popup.open()

    def show_tutorial(self):
        if self.tutorial_popup is not None or self.state.get("tutorial_done") or not self.auth.get("authenticated"):
            return
        self.tutorial_step = 0
        self.tutorial_popup = TutorialOverlay(self)
        self.root_layer.add_widget(self.tutorial_popup)
        self._show_tutorial_step()

    def _show_tutorial_step(self):
        steps = (
            ("channels", "add_channel", "Добавление каналов", "Добавь стримера кнопкой «Добавить канал». Можно также импортировать каналы из Фолловеров."),
            ("channels", "slots", "Два активных слота и ротация", "Одновременно просматриваются два канала. Слоты автоматически ротируются между доступными эфирами."),
            ("drops", "drop_farm", "Целевой фарм Drops", "Выбери активную кампанию и нажми «Фармить кампанию», чтобы направить сбор на нужный Drops."),
            ("settings", "settings_options", "Настройки автоматизации", "Здесь включаются Drops, моменты и переход по рейдам. Выбери нужные автоматические действия."),
            ("predictions", "prediction_strategy", "Автоставки на прогнозах", "Выбери стратегию ставок и настрой фильтр участников, лимит и процент баланса."),
            ("channels", "start_farm", "Готово к запуску", "После настройки нажми «Запустить фарм» — сервис продолжит работу в фоне.")
        )
        page, target_key, title, description = steps[self.tutorial_step]
        self.navigate(page)
        final = self.tutorial_step == len(steps) - 1
        if self.tutorial_popup is None:
            return
        self.tutorial_popup.set_step(self.tutorial_step, len(steps), title, description, final)

        def position_target(_dt):
            current_target = self.tutorial_targets.get(target_key)
            screen = self.screens[page]
            if current_target is not None and current_target.parent is not None:
                try:
                    screen.scroll.scroll_to(current_target, padding=dp(14), animate=False)
                except BaseException as error:
                    log_exception("TUTORIAL_SCROLL_FAILED step=" + str(self.tutorial_step + 1), error)
            def apply_target(_next_dt):
                if self.tutorial_popup is not None:
                    self.tutorial_popup.set_target(current_target)
            Clock.schedule_once(apply_target, 0.18)

        Clock.schedule_once(position_target, 0.12)

    def advance_tutorial(self):
        if self.tutorial_step >= 5:
            self.finish_tutorial()
            return
        self.tutorial_step += 1
        self._show_tutorial_step()

    def finish_tutorial(self):
        if self.tutorial_popup is not None:
            overlay = self.tutorial_popup
            self.tutorial_popup = None
            try:
                self.root_layer.remove_widget(overlay)
            except BaseException as error:
                log_exception("TUTORIAL_OVERLAY_REMOVE_FAILED", error)
        self.tutorial_shown = True
        self.rpc_async("finish_tutorial")

    def _hex_color(self, value):
        try:
            value = str(value).lstrip("#")
            return tuple(int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)) + (1,)
        except Exception:
            return MUTED

    def _card(self, screen, title, subtitle=None, color=SURFACE):
        card = Panel(color=color, radius=dp(16), orientation="vertical", padding=[dp(14), dp(12)], spacing=dp(8), size_hint_y=None)
        card.bind(minimum_height=card.setter("height"))
        card.add_widget(AppText(text=title, size=15, bold=True, size_hint_y=None, height=dp(24)))
        if subtitle:
            card.add_widget(AppText(text=subtitle, size=11, color=MUTED, size_hint_y=None, height=dp(28)))
        screen.items.add_widget(card)
        return card

    def _render_channels(self):
        self.tutorial_targets["add_channel"] = None
        self.tutorial_targets["slots"] = None
        self.tutorial_targets["start_farm"] = None
        screen = self.screens["channels"]
        screen.clear()
        screen.title("Каналы", "Баллы · Drops · ротация двух слотов")
        state = self.state
        slots = state.get("watch_slots") or []
        if isinstance(slots, dict):
            slots = [value for value in slots.values() if value]
        slot_logins = []
        for slot in slots[:2]:
            if isinstance(slot, dict):
                slot_logins.append(str(slot.get("username") or slot.get("login") or ""))
            else:
                slot_logins.append(str(slot))
        active = bool(state.get("session_running"))
        slot_text = "   ·   ".join(("@"+item) for item in slot_logins if item) or "Ожидают запуска фарма"
        slots_card = self._card(screen, "АКТИВНЫЙ ФАРМ СЕЙЧАС", slot_text)
        self.tutorial_targets["slots"] = slots_card
        summary = AppText(text=f"{len(state.get('channels') or [])} каналов    ·    +{int(state.get('gained_total') or 0):,} баллов".replace(",", " "), size=11, color=GREEN if active else MUTED, size_hint_y=None, height=dp(22))
        slots_card.add_widget(summary)
        start = FarmButton("ОСТАНОВИТЬ ФАРМ" if active else "ЗАПУСТИТЬ ФАРМ", color=RED if active else PURPLE, icon="stop" if active else "play", size_hint_y=None, height=dp(48))
        self.tutorial_targets["start_farm"] = start
        start.bind(on_release=lambda *_: self._toggle_farm())
        slots_card.add_widget(start)
        buttons = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(42))
        add = FarmButton("Добавить канал", color=SURFACE_ALT, icon="plus")
        self.tutorial_targets["add_channel"] = add
        add.bind(on_release=lambda *_: self.open_add_channel())
        refresh = FarmButton("Обновить", color=SURFACE_ALT, icon="refresh")
        refresh.bind(on_release=lambda *_: self.rpc_async("refresh_channel_profiles"))
        buttons.add_widget(add)
        buttons.add_widget(refresh)
        slots_card.add_widget(buttons)

        channel_profiles = state.get("channel_profiles") or {}
        balances = state.get("channel_balances") or {}
        online = set(state.get("online_channels") or [])
        slots_map = {login.lower(): index + 1 for index, login in enumerate(slot_logins)}
        channels = state.get("channels") or []
        if not channels:
            empty = self._card(screen, "Список каналов пуст", "Добавь стримеров, чтобы начать собирать баллы.")
            empty.add_widget(FarmButton("Добавить канал", color=PURPLE, icon="plus", size_hint_y=None, height=dp(44), on_release=lambda *_: self.open_add_channel()))
        for login in channels:
            profile = channel_profiles.get(login, {})
            balance = balances.get(login, {})
            points = balance.get("points", 0) if isinstance(balance, dict) else 0
            queued = active and login in online and login.lower() not in slots_map
            screen.items.add_widget(ChannelCard(login, profile, points, slots_map.get(login.lower()), queued))

    def _toggle_farm(self):
        if self.state.get("session_running"):
            self.rpc_async("stop_farm")
            return
        payload = {
            "username": self.state.get("username") or "",
            "channels": self.state.get("channels") or [],
            "settings": self.state.get("options") or {},
            "app_settings": self.state.get("app_settings") or {},
            "bet_settings": self.state.get("bet_settings") or {},
            "custom_presets": self.state.get("custom_presets") or {},
            "selected_preset": self.state.get("selected_preset") or "Оптимальный",
        }
        self.rpc_async("start_farm", [payload])

    def _render_drops(self):
        self.tutorial_targets["drop_farm"] = None
        screen = self.screens["drops"]
        screen.clear()
        focus = self.drops.get("focus") or {}
        screen.title("Инвентарь Drops", "Только начатые кампании из инвентаря Twitch")
        refresh = FarmButton("Обновить инвентарь", color=SURFACE_ALT, icon="refresh", size_hint_y=None, height=dp(42))
        refresh.bind(on_release=lambda *_: self.rpc_async("refresh_drops_inventory", kwargs={"force": True}))
        screen.items.add_widget(refresh)
        self.tutorial_targets["drop_farm"] = refresh
        if focus:
            card = self._card(screen, "ЦЕЛЕВОЙ ФАРМ", str(focus.get("campaign_name") or "") + " · " + str(focus.get("status") or ""))
            card.add_widget(FarmButton("Снять фокус", color=RED, icon="close", size_hint_y=None, height=dp(38), on_release=lambda *_: self.rpc_async("clear_drops_campaign_focus")))
        if self.drops.get("loading"):
            screen.items.add_widget(AppText(text="Синхронизация Twitch Drops…", size=12, color=MUTED, size_hint_y=None, height=dp(38)))
        campaigns = self.drops.get("campaigns") or []
        if not campaigns and not self.drops.get("loading"):
            self._card(screen, "Нет начатых кампаний", self.drops.get("error") or "Начни просмотр кампании на Twitch, затем обнови инвентарь.")
        for campaign in campaigns:
            title = campaign.get("name") or campaign.get("game") or "Twitch Drops"
            game = campaign.get("game") or "Общая кампания"
            rewards = campaign.get("drops") or campaign.get("rewards") or campaign.get("time_based_drops") or []
            card = self._card(screen, str(title), str(game))
            for reward in rewards[:8]:
                if not isinstance(reward, dict):
                    continue
                reward_name = reward.get("name") or reward.get("title") or "Награда"
                progress = reward.get("watched_minutes") or reward.get("current_minutes") or reward.get("progress") or reward.get("percentage") or 0
                total = reward.get("required_minutes") or reward.get("requiredMinutes") or reward.get("required_progress") or reward.get("total") or 0
                try:
                    pct = min(100, max(0, int(float(progress) * 100 / float(total)))) if float(total) > 0 else 0
                except (TypeError, ValueError, ZeroDivisionError):
                    pct = 0
                card.add_widget(AppText(text=f"{reward_name}  ·  {pct}%", size=12, color=TEXT, size_hint_y=None, height=dp(24)))
            campaign_id = str(campaign.get("id") or "")
            button_text = "Снять фокус" if focus.get("campaign_id") == campaign_id else "Фармить кампанию"
            color = GREEN if focus.get("campaign_id") == campaign_id else PURPLE
            button = FarmButton(button_text, color=color, icon="gift" if focus.get("campaign_id") != campaign_id else "check", size_hint_y=None, height=dp(42))
            button.bind(on_release=lambda _b, cid=campaign_id: self.rpc_async("set_drops_campaign_focus", [cid]))
            card.add_widget(button)
            if self.tutorial_targets.get("drop_farm") is refresh:
                self.tutorial_targets["drop_farm"] = button

    def _render_followers(self):
        screen = self.screens["followers"]
        screen.clear()
        screen.title("Фолловеры", "Онлайн-стримеры выделены отдельно")
        refresh = FarmButton("Обновить фолловеров", color=SURFACE_ALT, icon="refresh", size_hint_y=None, height=dp(42))
        refresh.bind(on_release=lambda *_: self.rpc_async("refresh_followed_channels", kwargs={"force": True}))
        screen.items.add_widget(refresh)
        if self.followers.get("loading"):
            screen.items.add_widget(AppText(text="Загрузка списка…", color=MUTED, size_hint_y=None, height=dp(35)))
        added = {str(value).lower() for value in (self.state.get("channels") or [])}
        all_items = [item for item in self.followers.get("channels", []) if isinstance(item, dict)]
        all_items.sort(key=lambda item: (bool(item.get("is_live")), max(0, int(item.get("viewer_count") or 0))), reverse=True)
        for item in all_items[:self.followers_limit]:
            login = str(item.get("login") or "")
            live = bool(item.get("is_live"))
            card = self._card(screen, str(item.get("display_name") or login), ("● В ЭФИРЕ" if live else "ОФЛАЙН") + "   ·   " + login)
            if live:
                card.add_widget(AppText(text=f"{int(item.get('viewer_count') or 0):,} зрителей".replace(",", " "), size=11, color=GREEN, size_hint_y=None, height=dp(20)))
            button = FarmButton("Добавлен" if login.lower() in added else "Добавить", color=SURFACE_ALT if login.lower() in added else PURPLE, icon="check" if login.lower() in added else "plus", size_hint_y=None, height=dp(38))
            button.disabled = login.lower() in added or bool(self.state.get("session_running"))
            button.bind(on_release=lambda _b, name=login: self.rpc_async("add_followed_channel", [name]))
            card.add_widget(button)
        if len(all_items) > self.followers_limit:
            more = FarmButton("Показать ещё", color=SURFACE_ALT, icon="plus", size_hint_y=None, height=dp(42))
            more.bind(on_release=lambda *_: self._show_more_followers())
            screen.items.add_widget(more)

    def _show_more_followers(self):
        self.followers_limit += 40
        self.screen_dirty["followers"] = True
        self._render_screen("followers", force=True)

    def _render_predictions(self):
        self.tutorial_targets["prediction_strategy"] = None
        screen = self.screens["predictions"]
        screen.clear()
        screen.title("Настройки прогнозов", "Стратегия автоставок Twitch")
        settings = self.state.get("bet_settings") or {}
        card = self._card(screen, "Стратегия и лимиты", "Параметры будут применены при следующем запуске.")
        self.tutorial_targets["prediction_strategy"] = card
        strategy = Field(hint="Стратегия (SMART, MOST_VOTED…)", text=str(settings.get("strategy") or "SMART"))
        card.add_widget(strategy)
        percentage = Field(hint="Процент от баланса", text=str(settings.get("percentage", 5)), input_filter="int")
        card.add_widget(percentage)
        max_points = Field(hint="Максимум баллов", text=str(settings.get("max_points", 50000)), input_filter="int")
        card.add_widget(max_points)
        minimum = Field(hint="Минимум баллов", text=str(settings.get("minimum_points", 0)), input_filter="int")
        card.add_widget(minimum)
        delay = Field(hint="Задержка", text=str(settings.get("delay", 6)), input_filter="int")
        card.add_widget(delay)
        filter_enabled = FarmButton("Фильтр участников: " + ("ВКЛ" if settings.get("filter_enabled") else "ВЫКЛ"), color=GREEN if settings.get("filter_enabled") else SURFACE_ALT, icon="check" if settings.get("filter_enabled") else "power", size_hint_y=None, height=dp(42))
        card.add_widget(filter_enabled)

        def toggle_filter():
            enabled = not filter_enabled.text.endswith("ВКЛ")
            filter_enabled.set_label("Фильтр участников: " + ("ВКЛ" if enabled else "ВЫКЛ"))
            filter_enabled.set_icon("check" if enabled else "power")

        filter_enabled.bind(on_release=lambda *_: toggle_filter())

        def save():
            values = dict(settings)
            values.update({
                "strategy": strategy.text.strip().upper() or "SMART",
                "percentage": int(percentage.text or 5),
                "max_points": int(max_points.text or 50000),
                "minimum_points": int(minimum.text or 0),
                "delay": int(delay.text or 6),
                "filter_enabled": filter_enabled.text.endswith("ВКЛ"),
            })
            self.rpc_async("apply_bet_settings", [values, self.state.get("selected_preset") or "Оптимальный"])

        card.add_widget(FarmButton("Применить параметры", color=PURPLE, icon="save", size_hint_y=None, height=dp(46), on_release=lambda *_: save()))

    def _render_events(self):
        screen = self.screens["events"]
        screen.clear()
        screen.title("События", "Последние события Twitch Farming")
        clear = FarmButton("Очистить журнал", color=SURFACE_ALT, icon="trash", size_hint_y=None, height=dp(40))
        clear.bind(on_release=lambda *_: self.rpc_async("clear_logs"))
        screen.items.add_widget(clear)
        events = list(self.state.get("events") or [])
        logs = list(self.log_cache)
        rows = [str(item.get("message") or "") if isinstance(item, dict) else str(item) for item in events[-20:]]
        rows.extend(str(item.get("line") or item.get("message") or "") if isinstance(item, dict) else str(item) for item in logs[-25:])
        text_value = "\n\n".join(reversed(rows[-35:])) or "Пока нет событий."
        card = self._card(screen, "Журнал", str(len(rows)) + " последних записей")
        label = AppText(text=text_value, size=10, color=MUTED, size_hint_y=None)
        label.text_size = (Window.width - dp(64), None)
        label.bind(texture_size=lambda widget, value: setattr(widget, "height", max(dp(42), value[1] + dp(8))))
        card.add_widget(label)

    def _render_settings(self):
        self.tutorial_targets["settings_options"] = None
        screen = self.screens["settings"]
        screen.clear()
        screen.title("Настройки", "Управление сбором и приложением")
        authenticated = bool(self.auth.get("authenticated"))
        username = str(self.auth.get("username") or self.state.get("authenticated_username") or "")
        account = self._card(screen, "Аккаунт Twitch", ("Подключён: @" + username) if authenticated else "Аккаунт не подключён")
        account.add_widget(FarmButton("Выйти из аккаунта" if authenticated else "Войти через Twitch", color=RED if authenticated else PURPLE, icon="logout" if authenticated else "login", size_hint_y=None, height=dp(44), on_release=lambda *_: self.confirm_logout() if authenticated else self.open_login_popup(self.state.get("username") or "")))
        options = self.state.get("options") or {}
        card = self._card(screen, "Действия фарма", "Включённые возможности будут обрабатываться движком.")
        self.tutorial_targets["settings_options"] = card
        names = [
            ("claim_drops", "Twitch Drops"),
            ("watch_streak", "Серии просмотров"),
            ("claim_moments", "Моменты"),
            ("follow_raid", "Рейды"),
            ("make_predictions", "Прогнозы"),
            ("chat", "IRC-чат"),
        ]
        for key, title in names:
            enabled = bool(options.get(key, True))
            button = FarmButton(title + ("  ВКЛ" if enabled else "  ВЫКЛ"), color=PURPLE_SOFT if enabled else SURFACE_ALT, icon="check" if enabled else "power", size_hint_y=None, height=dp(40))
            button.bind(on_release=lambda _b, k=key: self._toggle_local_option(k))
            card.add_widget(button)
        app_card = self._card(screen, "Приложение", "Фоновый сервис держит фарм активным при сворачивании.")
        app_card.add_widget(AppText(text="Для MIUI включи автозапуск и режим батареи «Без ограничений».", size=11, color=MUTED, size_hint_y=None, height=dp(44)))
        app_settings = self.state.get("app_settings") or {}
        auto_start = bool(app_settings.get("auto_start_farm", False))
        app_card.add_widget(FarmButton("Автозапуск фарма: " + ("ВКЛ" if auto_start else "ВЫКЛ"), color=GREEN if auto_start else SURFACE_ALT, icon="power", size_hint_y=None, height=dp(42), on_release=lambda *_: self._toggle_app_option("auto_start_farm")))
        app_card.add_widget(FarmButton("Язык: " + ("Русский" if self.language == "ru" else "English"), color=SURFACE_ALT, icon="settings", size_hint_y=None, height=dp(42), on_release=lambda *_: self.toggle_language()))
        app_card.add_widget(FarmButton("Открыть GitHub Releases", color=SURFACE_ALT, icon="github", size_hint_y=None, height=dp(42), on_release=lambda *_: self.open_external(GITHUB_RELEASES)))
        app_card.add_widget(FarmButton("Настройки батареи MIUI", color=SURFACE_ALT, icon="settings", size_hint_y=None, height=dp(42), on_release=lambda *_: self.open_battery_settings()))
        update_state = self.update_state if isinstance(self.update_state, dict) else {}
        update_card = self._card(screen, "Обновления", "Проверка актуальной версии через GitHub Releases.")
        current_version = str(update_state.get("current_version") or "—")
        latest_version = str(update_state.get("latest_version") or "")
        update_status = str(update_state.get("status") or "Проверка ещё не запускалась.")
        if update_state.get("error"):
            update_status += "\\n" + str(update_state.get("error"))
        version_text = "Установлена версия: " + current_version
        if latest_version:
            version_text += "\\nПоследняя версия: " + latest_version
        update_card.add_widget(AppText(text=version_text, size=10, color=MUTED, size_hint_y=None, height=dp(34)))
        update_card.add_widget(AppText(text=update_status, size=10, color=GREEN if update_state.get("phase") == "up_to_date" else AMBER if update_state.get("phase") in ("checking", "available") else MUTED, size_hint_y=None, height=dp(42)))
        checking = update_state.get("phase") == "checking"
        check_button = FarmButton("Проверка..." if checking else "Проверить обновления", color=SURFACE_ALT if checking else PURPLE, icon="refresh", size_hint_y=None, height=dp(44))
        check_button.disabled = bool(checking)
        check_button.bind(on_release=lambda *_: self.check_for_updates())
        update_card.add_widget(check_button)
        if update_state.get("update_available"):
            release_url = str(update_state.get("release_url") or GITHUB_RELEASES)
            update_card.add_widget(FarmButton("Открыть релиз и скачать APK", color=GREEN, icon="external", size_hint_y=None, height=dp(44), on_release=lambda *_: self.open_external(release_url)))
        diagnostics = self._card(screen, "Диагностика", "Журнал сохраняется отдельно для интерфейса и фонового сервиса.")
        diagnostics.add_widget(AppText(text="Файл: " + get_log_path(), size=9, color=MUTED, size_hint_y=None, height=dp(48)))
        diagnostics.add_widget(FarmButton("Скопировать журнал", color=SURFACE_ALT, icon="copy", size_hint_y=None, height=dp(42), on_release=lambda *_: self.copy_diagnostics()))
        diagnostics.add_widget(FarmButton("Поделиться журналом", color=PURPLE, icon="external", size_hint_y=None, height=dp(42), on_release=lambda *_: self.share_diagnostics()))

    def check_for_updates(self):
        self.rpc_async(
            "check_for_updates",
            callback=lambda result: self.show_toast("Проверка GitHub Releases запущена." if isinstance(result, dict) and result.get("ok") else str((result or {}).get("message") or "Не удалось запустить проверку.")),
            timeout=10
        )

    def toggle_language(self):
        self.language = "en" if self.language == "ru" else "ru"
        set_language(self.language)
        self.refresh_screen_content(force=True)
        self.show_toast("Language: English" if self.language == "en" else "Язык: Русский")

    def _save_full_settings(self, options=None, app_settings=None):
        payload = {
            "username": self.state.get("username") or "",
            "channels": self.state.get("channels") or [],
            "settings": options if options is not None else (self.state.get("options") or {}),
            "app_settings": app_settings if app_settings is not None else (self.state.get("app_settings") or {}),
            "bet_settings": self.state.get("bet_settings") or {},
            "custom_presets": self.state.get("custom_presets") or {},
            "selected_preset": self.state.get("selected_preset") or "Оптимальный"
        }
        self.rpc_async("save_settings", [payload])

    def _toggle_local_option(self, key):
        options = dict(self.state.get("options") or {})
        options[key] = not bool(options.get(key, True))
        self._save_full_settings(options=options)

    def _toggle_app_option(self, key):
        settings = dict(self.state.get("app_settings") or {})
        settings[key] = not bool(settings.get(key, False))
        self._save_full_settings(app_settings=settings)

    def open_add_channel(self):
        content = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        field = Field(hint="Логины или ссылки Twitch (через запятую)")
        content.add_widget(field)
        row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(46))
        popup = Popup(title="Добавить каналы", content=content, size_hint=(0.92, None), height=dp(220), background_color=SURFACE)
        row.add_widget(FarmButton("Отмена", color=SURFACE_ALT, icon="close", on_release=lambda *_: popup.dismiss()))

        def add():
            value = field.text.strip()
            popup.dismiss()
            if value:
                self.rpc_async("save_channel_editor", ["add", "", value])

        row.add_widget(FarmButton("Добавить", color=PURPLE, icon="plus", on_release=lambda *_: add()))
        content.add_widget(row)
        popup.open()
        field.focus = True

    def open_external(self, url):
        if platform == "android":
            self._open_twitch_web_url(str(url))
            return
        self.show_toast(str(url))

    def open_battery_settings(self):
        if platform == "android":
            try:
                from jnius import autoclass
                Intent = autoclass("android.content.Intent")
                Settings = autoclass("android.provider.Settings")
                Uri = autoclass("android.net.Uri")
                activity = autoclass("org.kivy.android.PythonActivity").mActivity
                intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                intent.setData(Uri.parse("package:" + activity.getPackageName()))
                activity.startActivity(intent)
            except Exception as error:
                self.show_toast(str(error)[:140])

    def copy_diagnostics(self):
        try:
            text = get_log_text()
            Clipboard.copy(text)
            self.show_toast("Журнал скопирован. Вставь его в сообщение.")
        except BaseException as error:
            log_exception("COPY_DIAGNOSTICS_FAILED", error)
            self.show_toast("Не удалось скопировать журнал.")

    def share_diagnostics(self):
        try:
            text = get_log_text()
            if platform == "android":
                from jnius import autoclass
                Intent = autoclass("android.content.Intent")
                activity = autoclass("org.kivy.android.PythonActivity").mActivity
                intent = Intent(Intent.ACTION_SEND)
                intent.setType("text/plain")
                intent.putExtra(Intent.EXTRA_SUBJECT, "Twitch Farming Android log")
                intent.putExtra(Intent.EXTRA_TEXT, text)
                JavaString = autoclass("java.lang.String")
                activity.startActivity(Intent.createChooser(intent, JavaString("Отправить журнал")))
                return
            Clipboard.copy(text)
            self.show_toast("Журнал скопирован.")
        except BaseException as error:
            log_exception("SHARE_DIAGNOSTICS_FAILED", error)
            try:
                Clipboard.copy(get_log_text())
                self.show_toast("Не удалось открыть отправку. Журнал скопирован.")
            except Exception:
                self.show_toast("Не удалось подготовить журнал.")

    def show_toast(self, message):
        self.toast.text = str(message or "")
        self.toast.opacity = 1
        if self.toast_event:
            self.toast_event.cancel()
        Animation(opacity=0, duration=0.35, t="out_quad").start(self.toast)
        self.toast.opacity = 1
        self.toast_event = Clock.schedule_once(lambda _dt: setattr(self.toast, "opacity", 0), 3.2)

    def preview_snapshot(self):
        return {
            "ok": True,
            "state": {
                "username": "demo_account",
                "authenticated_username": "demo_account",
                "authenticated": True,
                "channels": ["the_drop_creator", "bratishkinoff", "segall", "offline_channel"],
                "channel_profiles": {
                    "the_drop_creator": {"display_name": "TheDropCreator", "is_live": True, "viewer_count": 8430},
                    "bratishkinoff": {"display_name": "bratishkinOFF", "is_live": True, "viewer_count": 6077},
                    "segall": {"display_name": "segall", "is_live": False},
                    "offline_channel": {"display_name": "offline_channel", "is_live": False}
                },
                "channel_balances": {"the_drop_creator": {"points": 12940}, "bratishkinoff": {"points": 1770}, "segall": {"points": 1110}},
                "online_channels": ["the_drop_creator", "bratishkinoff"],
                "watch_slots": ["the_drop_creator", "bratishkinoff"],
                "session_running": True,
                "gained_total": 240,
                "status_text": "● ЗАПУЩЕН",
                "status_color": "#42D6A4",
                "options": {"claim_drops": True, "watch_streak": True, "claim_moments": True, "follow_raid": True, "make_predictions": True, "chat": True},
                "app_settings": {},
                "bet_settings": {"strategy": "SMART", "percentage": 5, "max_points": 50000, "minimum_points": 0, "delay": 6, "filter_enabled": True},
                "events": [{"message": "Drops: награда получена · Marvel Rivals"}, {"message": "Баланс обновлён · bratishkinOFF"}],
                "logs": [{"line": "Сессия восстановлена."}, {"line": "Twitch PubSub WebSocket connected."}]
            },
            "auth": {"authenticated": True},
            "followers": {"channels": [
                {"login": "bratishkinoff", "display_name": "bratishkinOFF", "is_live": True, "viewer_count": 6077},
                {"login": "segall", "display_name": "segall", "is_live": False}
            ]},
            "drops": {"campaigns": [
                {"id": "campaign-1", "name": "Marvel Rivals", "game": "Marvel Rivals", "rewards": [
                    {"name": "Devil Dinosaur Nameplate", "progress": 120, "required_minutes": 120},
                    {"name": "Spray", "progress": 60, "required_minutes": 120}
                ]}
            ], "focus": {}}
        }

    def capture_preview(self, _dt):
        output = Path(os.environ.get("TWITCH_MOBILE_SCREENSHOT_DIR", "screenshots"))
        output.mkdir(parents=True, exist_ok=True)
        names = ("channels", "drops", "followers", "predictions", "events", "settings")
        self.screen_manager.transition = NoTransition()

        def capture(name):
            self.navigate(name)
            self.refresh_screen_content(force=True)
            if self.screen_manager.current != name:
                raise RuntimeError("The preview could not navigate to " + name)
            Clock.schedule_once(lambda _event: Window.screenshot(name=str(output / (name + ".png"))), 0.45)

        for index, name in enumerate(names):
            Clock.schedule_once(lambda _event, screen=name: capture(screen), 0.25 + index * 1.0)
        Clock.schedule_once(lambda _event: self.stop(), 0.25 + len(names) * 1.0 + 0.9)


if __name__ == "__main__":
    log_event("UI_ENTRYPOINT", "TwitchMobileApp.run starting")
    try:
        TwitchMobileApp().run()
    except BaseException as error:
        log_exception("UI_ENTRYPOINT_CRASH", error)
        raise
    finally:
        log_event("UI_ENTRYPOINT_FINISHED", "TwitchMobileApp.run returned")
