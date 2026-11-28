import json
import os
import socket
import sys
import threading
from pathlib import Path

from kivy.app import App
from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.properties import StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import FadeTransition, NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.graphics import Color, RoundedRectangle, Line
from kivy.utils import platform

if platform == "android":
    try:
        from android.storage import app_storage_path
        os.environ.setdefault("APPDATA", app_storage_path())
    except Exception:
        pass

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
    def __init__(self, text="", color=PURPLE, text_color=TEXT, radius=dp(12), **kwargs):
        super().__init__(text=text, **kwargs)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self.text = translate_text(str(text))
        self.color = text_color
        self.bold = True
        self.font_size = sp(13)
        self.halign = "center"
        self.valign = "middle"
        self._button_color = color
        self._button_radius = radius
        self.bind(pos=self._draw_bg, size=self._draw_bg)
        with self.canvas.before:
            Color(*self._button_color)
            self._button_bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._button_radius])
        self._draw_bg()

    def _draw_bg(self, *_):
        self._button_bg.pos = self.pos
        self._button_bg.size = self.size


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
        with socket.create_connection((RPC_HOST, RPC_PORT), timeout=timeout) as connection:
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
            raise RuntimeError("Фоновый сервис ещё запускается.")
        envelope = json.loads(raw.decode("utf-8"))
        if not envelope.get("ok"):
            raise RuntimeError(envelope.get("error") or "Ошибка фонового сервиса.")
        return envelope.get("result")


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
        Window.clearcolor = BG
        Window.softinput_mode = "below_target"
        if "--preview" in sys.argv:
            Window.size = (420, 900)
        self.language = get_language()
        self.rpc = RPCClient()
        self.snapshot = {}
        self.state = {}
        self.auth = {}
        self.followers = {}
        self.drops = {}
        self.last_log_id = 0
        self.polling = False
        self.busy = False
        self.toast_event = None
        self.preview = "--preview" in sys.argv
        self.screens = {}
        self.root_box = BoxLayout(orientation="vertical", spacing=0)
        self.header = self._build_header()
        self.root_box.add_widget(self.header)
        self.screen_manager = ScreenManager(transition=FadeTransition(duration=0.12))
        for name in ("channels", "drops", "followers", "predictions", "events", "settings"):
            screen = MobileScreen(name=name)
            self.screens[name] = screen
            self.screen_manager.add_widget(screen)
        self.root_box.add_widget(self.screen_manager)
        self.nav = self._build_nav()
        self.root_box.add_widget(self.nav)
        self.toast = AppText(text="", size=12, color=TEXT, align="center", size_hint_y=None, height=dp(42))
        self.toast.opacity = 0
        self.root_box.add_widget(self.toast)
        self.refresh_screen_content()
        return self.root_box

    def _build_header(self):
        header = Panel(
            color=(0.075, 0.075, 0.115, 1),
            radius=0,
            orientation="horizontal",
            size_hint_y=None,
            height=dp(62),
            padding=[dp(14), dp(7)],
            spacing=dp(10)
        )
        mark = Panel(color=PURPLE, radius=dp(13), size_hint=(None, None), size=(dp(42), dp(42)), orientation="vertical")
        mark.add_widget(AppText(text="T", size=25, bold=True, align="center"))
        header.add_widget(mark)
        brand = BoxLayout(orientation="vertical", spacing=0)
        brand.add_widget(AppText(text="TWITCH FARMING", size=13, bold=True))
        self.header_user = AppText(text="Не подключён", size=10, color=MUTED)
        brand.add_widget(self.header_user)
        header.add_widget(brand)
        self.header_status = AppText(text="● НЕ ЗАПУЩЕН", size=9, color=MUTED, bold=True, align="right", size_hint_x=1.1)
        header.add_widget(self.header_status)
        return header

    def _build_nav(self):
        nav = Panel(color=(0.075, 0.075, 0.115, 1), radius=0, orientation="horizontal", size_hint_y=None, height=dp(60), padding=[dp(5), dp(5)], spacing=dp(3))
        self.nav_buttons = {}
        for key, title in (
            ("channels", "Каналы"),
            ("drops", "Drops"),
            ("followers", "Фолловеры"),
            ("predictions", "Ставки"),
            ("events", "События"),
            ("settings", "Ещё"),
        ):
            button = FarmButton(title, color=PURPLE_SOFT if key == "channels" else SURFACE, radius=dp(10), font_size=sp(10))
            button.bind(on_release=lambda _button, target=key: self.navigate(target))
            nav.add_widget(button)
            self.nav_buttons[key] = button
        return nav

    def navigate(self, name):
        self.screen_manager.current = name
        for key, button in self.nav_buttons.items():
            button._button_color = PURPLE_SOFT if key == name else SURFACE
            button._draw_bg()
        self.refresh_screen_content()

    def on_start(self):
        if not self.preview:
            self.start_android_service()
            Clock.schedule_once(lambda _dt: self.poll_state(), 0.8)
            Clock.schedule_interval(lambda _dt: self.poll_state(), 2.0)
        else:
            self.apply_snapshot(self.preview_snapshot())
            Clock.schedule_once(self.capture_preview, 1.0)

    def on_pause(self):
        return True

    def on_resume(self):
        if not self.preview:
            Clock.schedule_once(lambda _dt: self.poll_state(), 0.25)
        return True

    def start_android_service(self):
        if platform != "android":
            return
        try:
            from jnius import autoclass
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            service_class = autoclass(PACKAGE_NAME + ".ServiceFarm")
            service_class.start(activity, "ic_launcher", APP_NAME, "Фоновый сервис готов", "")
        except Exception as error:
            self.show_toast("Не удалось запустить фоновый сервис: " + str(error)[:100])

    def rpc_async(self, method, args=None, kwargs=None, callback=None, timeout=12):
        if self.busy and method != "snapshot":
            self.show_toast("Предыдущее действие ещё выполняется.")
            return
        if method != "snapshot":
            self.busy = True

        def worker():
            try:
                result = self.rpc.call(method, args, kwargs, timeout=timeout)
                error = ""
            except Exception as exc:
                result = None
                error = str(exc)

            def complete(_dt):
                if method != "snapshot":
                    self.busy = False
                if error:
                    self.show_toast(error)
                elif callback:
                    callback(result)
                elif isinstance(result, dict) and result.get("message"):
                    self.show_toast(result["message"])
                if method != "snapshot":
                    self.poll_state()
            Clock.schedule_once(complete, 0)

        threading.Thread(target=worker, name="MobileRPC-" + method, daemon=True).start()

    def poll_state(self):
        if self.preview or self.polling:
            return
        self.polling = True

        def worker():
            try:
                result = self.rpc.call("snapshot", kwargs={"after_log_id": self.last_log_id}, timeout=5)
                error = ""
            except Exception as exc:
                result = None
                error = str(exc)

            def complete(_dt):
                self.polling = False
                if result and result.get("ok"):
                    self.apply_snapshot(result)
                elif error and not self.state:
                    self.header_status.text = "● ПОДКЛЮЧЕНИЕ..."
            Clock.schedule_once(complete, 0)

        threading.Thread(target=worker, name="MobileStatePoll", daemon=True).start()

    def apply_snapshot(self, snapshot):
        self.snapshot = snapshot or {}
        self.state = self.snapshot.get("state") or {}
        self.auth = self.snapshot.get("auth") or {}
        self.followers = self.snapshot.get("followers") or {}
        self.drops = self.snapshot.get("drops") or {}
        next_id = self.state.get("next_log_id")
        if next_id:
            self.last_log_id = max(self.last_log_id, int(next_id) - 1)
        username = self.state.get("authenticated_username") or self.state.get("username") or ""
        self.header_user.text = ("@" + username) if self.state.get("authenticated") else "Не подключён"
        status = self.state.get("status_text") or "● НЕ ЗАПУЩЕН"
        self.header_status.text = status.replace("●  ", "● ")
        self.header_status.color = self._hex_color(self.state.get("status_color", "#A9B2C3"))
        self.refresh_screen_content()

    def _hex_color(self, value):
        try:
            value = str(value).lstrip("#")
            return tuple(int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)) + (1,)
        except Exception:
            return MUTED

    def refresh_screen_content(self):
        active = self.screen_manager.current
        self._render_channels()
        self._render_drops()
        self._render_followers()
        if active != "predictions" or not any(getattr(widget, "focus", False) for widget in self.screens["predictions"].walk()):
            self._render_predictions()
        self._render_events()
        self._render_settings()
        if not self.state.get("authenticated") and self.screen_manager.current == "channels" and self.state:
            self._ensure_login_prompt()

    def _ensure_login_prompt(self):
        if getattr(self, "_login_prompt_open", False):
            return
        self._login_prompt_open = True
        Clock.schedule_once(lambda _dt: self.open_login_popup(), 0.1)

    def _card(self, screen, title, subtitle=None, color=SURFACE):
        card = Panel(color=color, radius=dp(16), orientation="vertical", padding=[dp(14), dp(12)], spacing=dp(8), size_hint_y=None)
        card.bind(minimum_height=card.setter("height"))
        card.add_widget(AppText(text=title, size=15, bold=True, size_hint_y=None, height=dp(24)))
        if subtitle:
            card.add_widget(AppText(text=subtitle, size=11, color=MUTED, size_hint_y=None, height=dp(28)))
        screen.items.add_widget(card)
        return card

    def _render_channels(self):
        screen = self.screens["channels"]
        screen.clear()
        screen.title("Каналы", "Channel Points · Twitch Drops · автоматическая ротация")
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
        summary = AppText(text=f"{len(state.get('channels') or [])} каналов    ·    +{int(state.get('gained_total') or 0):,} баллов".replace(",", " "), size=11, color=GREEN if active else MUTED, size_hint_y=None, height=dp(22))
        slots_card.add_widget(summary)
        start = FarmButton("ОСТАНОВИТЬ ФАРМ" if active else "ЗАПУСТИТЬ ФАРМ", color=RED if active else PURPLE, size_hint_y=None, height=dp(48))
        start.bind(on_release=lambda *_: self._toggle_farm())
        slots_card.add_widget(start)
        buttons = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(42))
        add = FarmButton("+ Добавить канал", color=SURFACE_ALT)
        add.bind(on_release=lambda *_: self.open_add_channel())
        refresh = FarmButton("Обновить", color=SURFACE_ALT)
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
            empty.add_widget(FarmButton("Добавить канал", color=PURPLE, size_hint_y=None, height=dp(44), on_release=lambda *_: self.open_add_channel()))
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
        screen = self.screens["drops"]
        screen.clear()
        focus = self.drops.get("focus") or {}
        screen.title("Инвентарь Drops", "Только начатые кампании из инвентаря Twitch")
        refresh = FarmButton("Обновить инвентарь", color=SURFACE_ALT, size_hint_y=None, height=dp(42))
        refresh.bind(on_release=lambda *_: self.rpc_async("refresh_drops_inventory", kwargs={"force": True}))
        screen.items.add_widget(refresh)
        if focus:
            card = self._card(screen, "ЦЕЛЕВОЙ ФАРМ", str(focus.get("campaign_name") or "") + " · " + str(focus.get("status") or ""))
            card.add_widget(FarmButton("Снять фокус", color=RED, size_hint_y=None, height=dp(38), on_release=lambda *_: self.rpc_async("clear_drops_campaign_focus")))
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
            button = FarmButton(button_text, color=color, size_hint_y=None, height=dp(42))
            button.bind(on_release=lambda _b, cid=campaign_id: self.rpc_async("set_drops_campaign_focus", [cid]))
            card.add_widget(button)

    def _render_followers(self):
        screen = self.screens["followers"]
        screen.clear()
        screen.title("Фолловеры", "Онлайн-стримеры выделены отдельно")
        refresh = FarmButton("Обновить фолловеров", color=SURFACE_ALT, size_hint_y=None, height=dp(42))
        refresh.bind(on_release=lambda *_: self.rpc_async("refresh_followed_channels", kwargs={"force": True}))
        screen.items.add_widget(refresh)
        if self.followers.get("loading"):
            screen.items.add_widget(AppText(text="Загрузка списка…", color=MUTED, size_hint_y=None, height=dp(35)))
        added = set(self.state.get("channels") or [])
        for item in self.followers.get("channels", [])[:150]:
            login = item.get("login") or ""
            live = bool(item.get("is_live"))
            card = self._card(screen, str(item.get("display_name") or login), ("● В ЭФИРЕ" if live else "ОФЛАЙН") + "   ·   " + login)
            if live:
                card.add_widget(AppText(text=f"{int(item.get('viewer_count') or 0):,} зрителей".replace(",", " "), size=11, color=GREEN, size_hint_y=None, height=dp(20)))
            button = FarmButton("Добавлен" if login in added else "+ Добавить", color=SURFACE_ALT if login in added else PURPLE, size_hint_y=None, height=dp(38))
            button.disabled = login in added or bool(self.state.get("session_running"))
            button.bind(on_release=lambda _b, name=login: self.rpc_async("add_followed_channel", [name]))
            card.add_widget(button)

    def _render_predictions(self):
        screen = self.screens["predictions"]
        screen.clear()
        screen.title("Настройки прогнозов", "Стратегия автоставок Twitch")
        settings = self.state.get("bet_settings") or {}
        card = self._card(screen, "Стратегия и лимиты", "Параметры будут применены при следующем запуске.")
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
        filter_enabled = FarmButton("Фильтр участников: " + ("ВКЛ" if settings.get("filter_enabled") else "ВЫКЛ"), color=GREEN if settings.get("filter_enabled") else SURFACE_ALT, size_hint_y=None, height=dp(42))
        card.add_widget(filter_enabled)
        filter_enabled.bind(on_release=lambda *_: setattr(filter_enabled, "text", "Фильтр участников: " + ("ВЫКЛ" if filter_enabled.text.endswith("ВКЛ") else "ВКЛ")))

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

        card.add_widget(FarmButton("Применить параметры", color=PURPLE, size_hint_y=None, height=dp(46), on_release=lambda *_: save()))

    def _render_events(self):
        screen = self.screens["events"]
        screen.clear()
        screen.title("События", "Последние события Twitch Farming")
        clear = FarmButton("Очистить журнал", color=SURFACE_ALT, size_hint_y=None, height=dp(40))
        clear.bind(on_release=lambda *_: self.rpc_async("clear_logs"))
        screen.items.add_widget(clear)
        events = list(self.state.get("events") or [])
        logs = list(self.state.get("logs") or [])
        rows = [
            str(item.get("message") or "") if isinstance(item, dict) else str(item)
            for item in events[-30:]
        ]
        rows.extend(
            str(item.get("line") or item.get("message") or "") if isinstance(item, dict) else str(item)
            for item in logs[-70:]
        )
        for text_value in reversed(rows[-90:]):
            self._card(screen, text_value[:400])

    def _render_settings(self):
        screen = self.screens["settings"]
        screen.clear()
        screen.title("Настройки", "Управление сбором и приложением")
        options = self.state.get("options") or {}
        card = self._card(screen, "Действия фарма", "Включённые возможности будут обрабатываться движком.")
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
            button = FarmButton(title + ("  ВКЛ" if enabled else "  ВЫКЛ"), color=PURPLE_SOFT if enabled else SURFACE_ALT, size_hint_y=None, height=dp(40))
            button.bind(on_release=lambda _b, k=key: self._toggle_local_option(k))
            card.add_widget(button)
        app_card = self._card(screen, "Приложение", "Фоновый сервис держит фарм активным при сворачивании.")
        app_card.add_widget(AppText(text="Для MIUI включи автозапуск и режим батареи «Без ограничений».", size=11, color=MUTED, size_hint_y=None, height=dp(44)))
        app_settings = self.state.get("app_settings") or {}
        auto_start = bool(app_settings.get("auto_start_farm", False))
        app_card.add_widget(FarmButton("Автозапуск фарма: " + ("ВКЛ" if auto_start else "ВЫКЛ"), color=GREEN if auto_start else SURFACE_ALT, size_hint_y=None, height=dp(42), on_release=lambda *_: self._toggle_app_option("auto_start_farm")))
        app_card.add_widget(FarmButton("Язык: " + ("Русский" if self.language == "ru" else "English"), color=SURFACE_ALT, size_hint_y=None, height=dp(42), on_release=lambda *_: self.toggle_language()))
        app_card.add_widget(FarmButton("Открыть GitHub Releases", color=SURFACE_ALT, size_hint_y=None, height=dp(42), on_release=lambda *_: self.open_external(GITHUB_RELEASES)))
        app_card.add_widget(FarmButton("Настройки батареи MIUI", color=SURFACE_ALT, size_hint_y=None, height=dp(42), on_release=lambda *_: self.open_battery_settings()))

    def toggle_language(self):
        self.language = "en" if self.language == "ru" else "ru"
        set_language(self.language)
        self.refresh_screen_content()
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
        row.add_widget(FarmButton("Отмена", color=SURFACE_ALT, on_release=lambda *_: popup.dismiss()))

        def add():
            value = field.text.strip()
            popup.dismiss()
            if value:
                self.rpc_async("save_channel_editor", ["add", "", value])

        row.add_widget(FarmButton("Добавить", color=PURPLE, on_release=lambda *_: add()))
        content.add_widget(row)
        popup.open()
        field.focus = True

    def open_login_popup(self):
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(12))
        content.add_widget(AppText(text="Подключи аккаунт Twitch", size=18, bold=True, size_hint_y=None, height=dp(34)))
        content.add_widget(AppText(text="Введи логин, затем подтверди авторизацию на официальной странице Twitch.", size=12, color=MUTED, size_hint_y=None, height=dp(48)))
        field = Field(hint="Логин Twitch", text=self.state.get("username") or "")
        content.add_widget(field)
        popup = Popup(title="Twitch Farming", content=content, size_hint=(0.9, None), height=dp(265), background_color=SURFACE)
        row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(46))
        row.add_widget(FarmButton("Позже", color=SURFACE_ALT, on_release=lambda *_: popup.dismiss()))
        row.add_widget(FarmButton("Подключить", color=PURPLE, on_release=lambda *_: (popup.dismiss(), self.rpc_async("login", [field.text.strip()]))))
        content.add_widget(row)
        popup.open()
        field.focus = True

    def open_external(self, url):
        if platform == "android":
            try:
                from jnius import autoclass
                Intent = autoclass("android.content.Intent")
                Uri = autoclass("android.net.Uri")
                activity = autoclass("org.kivy.android.PythonActivity").mActivity
                activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                return
            except Exception:
                pass
        self.show_toast(url)

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
            self.refresh_screen_content()
            if self.screen_manager.current != name:
                raise RuntimeError("The preview could not navigate to " + name)
            Clock.schedule_once(lambda _event: Window.screenshot(name=str(output / (name + ".png"))), 0.45)

        for index, name in enumerate(names):
            Clock.schedule_once(lambda _event, screen=name: capture(screen), 0.25 + index * 1.0)
        Clock.schedule_once(lambda _event: self.stop(), 0.25 + len(names) * 1.0 + 0.9)


if __name__ == "__main__":
    TwitchMobileApp().run()
