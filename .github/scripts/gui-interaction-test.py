import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "Twitch Farming"
OUTPUT = ROOT / "artifacts" / "gui-smoke"
OUTPUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(APP))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QTextEdit

import qt_app


class FakeApi:
    def __init__(self):
        self.update_install_callback = None
        self.calls = []

    def get_update_state(self):
        return {"phase": "idle", "current_version": "GUI-TEST"}

    def check_for_updates(self):
        self.calls.append("check_for_updates")
        return {"ok": True, "message": "Проверка GitHub Releases завершена. Обновлений нет."}

    def start_farm(self, payload):
        self.calls.append("start_farm")
        return {"ok": True, "message": "Тестовый запуск фарма выполнен."}

    def stop_farm(self):
        self.calls.append("stop_farm")
        return {"ok": True, "message": "Тестовая остановка фарма выполнена."}

    def save_channel_editor(self, *args):
        self.calls.append("save_channel_editor")
        return {"ok": True, "message": "Тестовые каналы сохранены."}

    def refresh_followed_channels(self, *args):
        self.calls.append("refresh_followed_channels")
        return {"ok": True, "message": "Тестовый список фолловеров обновлён."}

    def clear_logs(self):
        self.calls.append("clear_logs")
        return {"ok": True, "message": "Тестовый журнал очищен.", "last_log_id": 100}

    def shutdown(self):
        return {"ok": True}


def run_task_sync(window, key, function, args=(), callback=None):
    try:
        result = function(*args)
        error = None
    except Exception as exc:
        result = None
        error = str(exc)
    if callback:
        callback(result, error)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def process(app, rounds=5):
    for _ in range(rounds):
        app.processEvents()
        QTest.qWait(20)


def screenshot(window, name):
    process(QApplication.instance())
    path = OUTPUT / name
    image = window.grab()
    check(not image.isNull(), "Screenshot is empty: " + name)
    check(image.save(str(path), "PNG"), "Screenshot could not be saved: " + name)
    return path


def click(button):
    check(button is not None and button.isEnabled(), "A tested button is missing or disabled.")
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    process(QApplication.instance())


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    qt_app.TwitchFarmingApi = FakeApi
    qt_app.MainWindow._setup_timers = lambda self: None
    qt_app.MainWindow._setup_tray = lambda self: setattr(self, "tray", None)
    qt_app.MainWindow._run_task = run_task_sync

    window = qt_app.MainWindow()
    window.latest_state = {
        "status_text": "● НЕ ЗАПУЩЕН",
        "status_color": "#9BA3B4",
        "session_running": False,
        "username": "gui_test_user",
        "channels": ["alpha_live", "beta_live", "offline_channel"],
        "channel_profiles": {
            "alpha_live": {"display_name": "Alpha Live", "is_live": True, "viewer_count": 1234},
            "beta_live": {"display_name": "Beta Live", "is_live": True, "viewer_count": 87},
            "offline_channel": {"display_name": "Offline Channel", "is_live": False}
        },
        "channel_balances": {"alpha_live": 12450, "beta_live": 783, "offline_channel": 1500},
        "channel_currency": {},
        "online_channels": ["alpha_live", "beta_live"],
        "watch_slots": {},
        "logs": [
            {"id": 1, "level": "INFO", "line": "GUI TEST · Приложение запущено"},
            {"id": 2, "level": "WARNING", "line": "GUI TEST · Проверка уведомлений"}
        ],
        "app_settings": {},
        "options": {}
    }
    window.drops_state = {"loaded": True, "campaigns": []}
    window.followed_state = {"loaded": True, "channels": []}
    window.show()
    process(app)
    window._render_channels()
    window._render_logs()
    screenshot(window, "01-channels.png")

    channel_cards = [
        window.channels_grid.itemAt(i).widget()
        for i in range(window.channels_grid.count())
        if isinstance(window.channels_grid.itemAt(i).widget(), qt_app.ChannelCard)
    ]
    check(len(channel_cards) == 3, "The channel grid did not render all sample cards.")
    heights = {card.login: card.height() for card in channel_cards}
    check(heights["offline_channel"] < heights["alpha_live"], "Offline channel card is not shorter than a live card.")

    click(window.add_channel_button)
    modal = window._modal_overlay
    check(modal is not None and modal.isVisible() and isinstance(modal.editor, QTextEdit), "The Add Channels dialog did not open.")
    modal.editor.setPlainText("gui_test_channel")
    screenshot(window, "02-add-channels-dialog.png")
    add_button = next((button for button in modal.findChildren(QPushButton) if button.text() == "Добавить"), None)
    click(add_button)
    check("save_channel_editor" in window.api.calls, "The Add Channels dialog did not invoke the save action.")

    click(window.nav_buttons[2])
    refresh_button = next((button for button in window.stack.widget(2).findChildren(QPushButton) if button.text() == "Обновить"), None)
    click(refresh_button)
    check("refresh_followed_channels" in window.api.calls, "The Followers refresh button did not invoke its action.")
    screenshot(window, "03-followers.png")

    click(window.nav_buttons[3])
    screenshot(window, "04-settings.png")
    click(window.update_check_button)
    check("check_for_updates" in window.api.calls, "The update-check button did not invoke its action.")
    expected = "Проверка GitHub Releases завершена. Обновлений нет."
    check(window.toast.isVisible() and window.toast.text() == expected, "The update result notification was not shown in full.")
    check(window.toast.textFormat() == Qt.TextFormat.PlainText, "Notifications must be rendered as plain text.")

    first = "Первое уведомление должно остаться полностью видимым."
    second = "Второе уведомление содержит символы <tag> & кавычки и не должно обрезаться."
    window._notify(first)
    window._notify(second)
    check(window.toast.text() == expected and len(window._toast_queue) >= 2, "Concurrent notices were overwritten instead of queued.")
    window.toast_timer.stop()
    window._show_next_toast()
    check(window.toast.text() == first, "The notification queue did not preserve FIFO order.")
    window.toast_timer.stop()
    window._show_next_toast()
    check(window.toast.text() == second, "The second queued notification was not displayed in full.")
    window.toast_timer.stop()
    window._toast_queue.clear()
    window.toast.hide()
    window._toast_current_text = ""

    long_notice = "Ошибка проверки обновлений: " + ("Подробное описание ошибки должно полностью переноситься на следующую строку. " * 7) + "<response> & данные"
    window._notify(long_notice)
    process(app)
    check(window.toast.isVisible(), "A toast shown after being hidden remained invisible.")
    check(window.toast.text() == long_notice, "The long notification text was truncated or interpreted as markup.")
    check(window.toast.width() <= 620 and window.toast.height() > window.toast.fontMetrics().lineSpacing(), "The long notification did not wrap into multiple visible lines.")
    parent = window.centralWidget()
    expected_x = (parent.width() - window.toast.width()) // 2
    expected_y = parent.height() - window.toast.height() - 20
    check(abs(window.toast.x() - expected_x) <= 2 and abs(window.toast.y() - expected_y) <= 2, "The notification is not centered at the bottom of the window.")
    screenshot(window, "05-long-notification.png")

    window.toast_timer.stop()
    window.toast.hide()
    window._toast_queue.clear()
    window._toast_current_text = ""
    click(window.nav_buttons[0])
    click(window.farm_button)
    check("start_farm" in window.api.calls, "The Start Farm button did not invoke its action.")
    check(window.toast.text() == "Тестовый запуск фарма выполнен.", "Farm feedback was not shown as a complete notification.")
    screenshot(window, "06-farm-feedback.png")

    click(window.nav_buttons[5])
    screenshot(window, "07-events.png")
    clear_button = next((button for button in window.stack.widget(5).findChildren(QPushButton) if button.text() == "Очистить журнал"), None)
    click(clear_button)
    check("clear_logs" in window.api.calls, "The Clear Log button did not invoke its action.")

    window.hide()
    window.api = None
    print("GUI interaction tests passed. Screenshots: " + str(OUTPUT))


if __name__ == "__main__":
    main()
