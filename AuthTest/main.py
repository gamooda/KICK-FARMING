import json
import os
import time
import uuid
import webbrowser
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import platform

VERIFY_URL = "https://www.twitch.tv/activate"


def app_root(app):
    private = os.environ.get("ANDROID_PRIVATE")
    if private:
        return Path(private) / "auth_test"
    return Path(app.user_data_dir) / "auth_test"


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    os.replace(str(temporary), str(path))


class AuthTestApp(App):
    def build(self):
        self.title = "Twitch Auth Test"
        Window.clearcolor = (0.045, 0.045, 0.07, 1)
        Window.fullscreen = True
        self.data_dir = app_root(self)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.data_dir / "events.log"
        self.state_path = self.data_dir / "state.json"
        self.request_path = self.data_dir / "request.json"
        self.app_state_path = self.data_dir / "app_state.json"
        self.log_offset = 0
        self.last_code = ""
        self.last_log_text = ""
        self.request_pending = False
        self.request_submitted_at = 0.0

        self.root_layout = BoxLayout(orientation="vertical", spacing=dp(6), padding=[dp(10), dp(8)])
        self.top_panel = BoxLayout(orientation="vertical", size_hint_y=0.35, spacing=dp(6))
        self.start_button = self.make_button("Получить код авторизации", (0.42, 0.24, 0.78, 1), dp(44))
        self.start_button.bind(on_release=self.start_authorization)
        self.top_panel.add_widget(self.start_button)

        self.code_label = Label(
            text="КОД ПОКА НЕ ПОЛУЧЕН",
            size_hint_y=1,
            color=(0.96, 0.95, 1, 1),
            bold=True,
            font_size=sp(26),
            halign="center",
            valign="middle"
        )
        self.code_label.bind(size=lambda instance, value: setattr(instance, "text_size", value))
        self.top_panel.add_widget(self.code_label)

        actions = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(46), spacing=dp(6))
        self.copy_code_button = self.make_button("Скопировать код", (0.16, 0.16, 0.23, 1), dp(46))
        self.copy_code_button.disabled = True
        self.copy_code_button.bind(on_release=self.copy_code)
        self.open_button = self.make_button("Открыть twitch.tv/activate", (0.16, 0.16, 0.23, 1), dp(46))
        self.open_button.bind(on_release=self.open_activation)
        actions.add_widget(self.copy_code_button)
        actions.add_widget(self.open_button)
        self.top_panel.add_widget(actions)
        self.root_layout.add_widget(self.top_panel)

        self.log_panel = BoxLayout(orientation="vertical", size_hint_y=0.65, spacing=dp(5))
        log_toolbar = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(40), spacing=dp(6))
        log_title = Label(
            text="ЖИВОЙ ЖУРНАЛ",
            color=(0.77, 0.68, 1, 1),
            bold=True,
            font_size=sp(14),
            halign="left",
            valign="middle"
        )
        log_title.bind(size=lambda instance, value: setattr(instance, "text_size", value))
        log_toolbar.add_widget(log_title)
        self.copy_log_button = self.make_button("Копировать лог", (0.16, 0.16, 0.23, 1), dp(40))
        self.copy_log_button.bind(on_release=self.copy_log)
        self.clear_button = self.make_button("Очистить", (0.24, 0.13, 0.17, 1), dp(40))
        self.clear_button.bind(on_release=self.clear_log)
        log_toolbar.add_widget(self.copy_log_button)
        log_toolbar.add_widget(self.clear_button)
        self.log_panel.add_widget(log_toolbar)

        self.log_scroll = ScrollView(do_scroll_x=False, bar_width=dp(4), scroll_type=["bars", "content"])
        self.log_text = TextInput(
            text="",
            readonly=True,
            multiline=True,
            size_hint=(1, None),
            height=dp(300),
            font_size=sp(11),
            padding=[dp(9), dp(9)],
            background_color=(0.025, 0.025, 0.04, 1),
            foreground_color=(0.86, 0.87, 0.92, 1),
            cursor_color=(0.75, 0.58, 1, 1),
            selection_color=(0.45, 0.28, 0.7, 0.65)
        )
        self.log_text.bind(text=self.resize_log_text)
        self.log_scroll.add_widget(self.log_text)
        self.log_panel.add_widget(self.log_scroll)
        self.root_layout.add_widget(self.log_panel)

        Clock.schedule_interval(self.refresh_diagnostics, 0.25)
        self.write_app_state(True)
        self.append_local_note("UI started. Private data directory: " + str(self.data_dir))
        return self.root_layout

    def make_button(self, title, color, height):
        return Button(
            text=title,
            size_hint_y=None,
            height=height,
            background_normal="",
            background_down="",
            background_color=color,
            color=(1, 1, 1, 1),
            bold=True,
            font_size=sp(13)
        )

    def resize_log_text(self, *_):
        width = max(dp(120), self.log_text.width - dp(22))
        columns = max(18, int(width / max(dp(5), self.log_text.font_size * 0.58)))
        rows = 0
        for line in self.log_text.text.splitlines() or [""]:
            rows += max(1, (len(line) + columns - 1) // columns)
        target = max(self.log_scroll.height, dp(18) * rows + dp(20))
        self.log_text.height = target
        Clock.schedule_once(lambda _dt: setattr(self.log_scroll, "scroll_y", 0), 0)

    def append_local_note(self, message):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = "[%s] UI: %s\n" % (stamp, message)
        try:
            with self.log_path.open("a", encoding="utf-8") as output:
                output.write(line)
                output.flush()
        except OSError:
            pass

    def write_app_state(self, foreground):
        try:
            write_json(self.app_state_path, {"foreground": bool(foreground), "updated_at": time.time()})
        except Exception as error:
            self.append_local_note("Could not update foreground state: " + repr(error))

    def start_authorization(self, *_):
        request_id = uuid.uuid4().hex
        try:
            self.write_app_state(True)
            write_json(self.request_path, {"action": "start", "request_id": request_id, "created_at": time.time()})
            self.request_pending = True
            self.request_submitted_at = time.time()
            self.start_button.disabled = True
            self.start_button.text = "Запрос отправлен..."
            self.code_label.text = "ОЖИДАНИЕ КОДА TWITCH"
            self.last_code = ""
            self.copy_code_button.disabled = True
            self.append_local_note("Authorization request queued id=" + request_id)
        except Exception as error:
            self.append_local_note("Could not queue authorization request: " + repr(error))
            self.start_button.disabled = False
            self.start_button.text = "Получить код авторизации"

    def refresh_diagnostics(self, _dt):
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.is_file() else {}
            if self.request_pending and float(state.get("updated_at") or 0) >= self.request_submitted_at:
                self.request_pending = False
            if self.request_pending and time.time() - self.request_submitted_at > 25:
                self.request_pending = False
                self.start_button.disabled = False
                self.start_button.text = "Повторить запрос кода"
                self.append_local_note("No service response after 25 seconds. Check whether the Android foreground service started.")
            code = str(state.get("user_code") or "")
            if code and code != self.last_code:
                self.last_code = code
                self.code_label.text = code
                self.copy_code_button.disabled = False
            status = str(state.get("status") or "")
            if not self.request_pending and status.startswith("SUCCESS"):
                self.start_button.disabled = False
                self.start_button.text = "Получить новый код"
            elif not self.request_pending and any(word in status.lower() for word in ("failed", "error", "expired")):
                self.start_button.disabled = False
                self.start_button.text = "Повторить запрос кода"
            elif not self.request_pending and code:
                self.start_button.disabled = False
                self.start_button.text = "Получить новый код"
            self.read_new_log_data()
        except Exception as error:
            self.append_local_note("UI refresh exception: " + repr(error))

    def read_new_log_data(self):
        if not self.log_path.is_file():
            return
        size = self.log_path.stat().st_size
        if size < self.log_offset:
            self.log_offset = 0
        with self.log_path.open("r", encoding="utf-8", errors="replace") as source:
            source.seek(self.log_offset)
            chunk = source.read()
            self.log_offset = source.tell()
        if chunk:
            self.last_log_text += chunk
            if len(self.last_log_text) > 250000:
                self.last_log_text = self.last_log_text[-250000:]
            self.log_text.text = self.last_log_text
            self.resize_log_text()

    def copy_code(self, *_):
        if self.last_code:
            Clipboard.copy(self.last_code)

    def open_activation(self, *_):
        try:
            if platform == "android":
                from jnius import autoclass
                Intent = autoclass("android.content.Intent")
                Uri = autoclass("android.net.Uri")
                activity = autoclass("org.kivy.android.PythonActivity").mActivity
                intent = Intent(Intent.ACTION_VIEW)
                intent.setData(Uri.parse(VERIFY_URL))
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                activity.startActivity(intent)
            else:
                webbrowser.open(VERIFY_URL)
            self.append_local_note("Opened " + VERIFY_URL)
        except Exception:
            import traceback
            self.append_local_note("Browser launch failed:\n" + traceback.format_exc())

    def copy_log(self, *_):
        Clipboard.copy(self.last_log_text or self.log_text.text)

    def clear_log(self, *_):
        try:
            self.log_path.write_text("", encoding="utf-8")
            self.log_offset = 0
            self.last_log_text = ""
            self.log_text.text = ""
            self.append_local_note("Log cleared by user.")
        except Exception as error:
            self.append_local_note("Log clear failed: " + repr(error))

    def on_pause(self):
        self.write_app_state(False)
        self.append_local_note("APP PAUSED: UI backgrounded; diagnostic service should continue polling.")
        return True

    def on_resume(self):
        self.write_app_state(True)
        self.append_local_note("APP RESUMED: foreground UI restored.")
        return True

    def on_stop(self):
        self.write_app_state(False)
        self.append_local_note("APP STOPPED: UI process stopping; check foreground service log for polling state.")


if __name__ == "__main__":
    AuthTestApp().run()
