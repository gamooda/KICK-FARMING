import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "Twitch Farming"
sys.path.insert(0, str(APP))

from qt_app import eligible_twitch_campaigns, eligible_watch_campaigns, format_duration


def check(condition, message):
    if not condition:
        raise AssertionError(message)


sample = [
    {
        "id": "campaign-1",
        "status": "ACTIVE",
        "started": True,
        "name": "Twitch profile rewards",
        "drops": [
            {"id": "badge", "name": "Badge", "category": "twitch", "is_twitch_view": True, "claimed": False},
            {"id": "emote", "name": "Emote", "category": "twitch", "is_twitch_view": True, "claimed": False},
            {"id": "skin", "name": "Game skin", "category": "game", "is_twitch_view": False, "claimed": False},
            {"id": "claimed", "name": "Claimed badge", "category": "twitch", "is_twitch_view": True, "claimed": True}
        ],
        "streamers": [
            {"login": "highview", "viewer_count": 4500, "uptime_seconds": 3600},
            {"login": "longstream", "viewer_count": 200, "uptime_seconds": 36000},
            {"login": "medium", "viewer_count": 900, "uptime_seconds": 7200},
            {"login": "excluded", "viewer_count": 99999, "uptime_seconds": 100}
        ]
    },
    {
        "id": "campaign-2",
        "status": "ACTIVE",
        "drops": [
            {"id": "all-claimed", "category": "twitch", "is_twitch_view": True, "claimed": True}
        ]
    },
    {
        "id": "campaign-3",
        "status": "EXPIRED",
        "drops": [
            {"id": "expired", "category": "twitch", "is_twitch_view": True, "claimed": False}
        ]
    }
]

result = eligible_twitch_campaigns(sample)
check(len(result) == 1, "Only active campaigns with unclaimed Twitch profile rewards must be listed.")
check([item["id"] for item in result[0]["visible_drops"]] == ["badge", "emote"], "Twitch profile filter must contain only unclaimed Twitch rewards.")
all_campaigns = eligible_watch_campaigns(sample)
check(len(all_campaigns) == 1, "All-campaign view must include active campaigns with free watch drops.")
check([item["id"] for item in all_campaigns[0]["visible_drops"]] == ["badge", "emote", "skin"], "All-campaign view must include free game rewards and Twitch profile rewards.")
edge_case_campaigns = eligible_watch_campaigns([
    {
        "id": "in-progress",
        "status": "IN_PROGRESS",
        "drops": [
            {"id": "free-string-flags", "type": "WATCH", "claimed": "false", "paid": "false", "is_subscription": "false"},
            {"id": "subscription", "type": "SUBSCRIPTION", "claimed": False},
            {"id": "paid", "type": "WATCH", "claimed": False, "paid": "true"}
        ]
    }
])
check(len(edge_case_campaigns) == 1, "Available campaigns outside ACTIVE must still be listed.")
check([drop["id"] for drop in edge_case_campaigns[0]["visible_drops"]] == ["free-string-flags"], "String false flags must not hide free drops; subscription rewards must stay hidden.")
check([item["login"] for item in result[0]["streamers"]] == ["longstream", "medium", "highview"], "Long-running streams must be recommended first.")
check(format_duration(3660) == "1 ч 1 мин", "Stream uptime must be formatted.")
check(format_duration(0) == "0 мин", "Zero uptime must be formatted.")

backend = (APP / "backend.py").read_text(encoding="utf-8")
qt = (APP / "qt_app.py").read_text(encoding="utf-8")
requirements = (APP / "requirements.txt").read_text(encoding="utf-8").lower()
workflow = (ROOT / ".github" / "workflows" / "build-twitch-exe.yml").read_text(encoding="utf-8").lower()
entry = (APP / "app.py").read_text(encoding="utf-8")
for required in (
    "class AuthWorker",
    "class EngineWorker",
    "class DropsInventoryWorker",
    "def get_drop_inventory_snapshot",
    "def _check_updates_worker",
    "def _download_update_worker",
    "def get_active_watch_channels",
):
    check(required in backend or required in (APP / "engine" / "TwitchChannelPointsMiner" / "classes" / "Twitch.py").read_text(encoding="utf-8"), "Backend feature missing: " + required)
check("webview" not in backend.lower() and "pystray" not in backend.lower() and "clr_loader" not in backend.lower(), "The native backend still contains a legacy WebView or PyStray reference.")
check("pyside6-essentials" in requirements, "PySide6-Essentials dependency is missing.")
check("QMessageBox" not in qt and "QInputDialog" not in qt, "Native system dialogs must be replaced with in-window overlays.")
check("events_output" not in qt, "The duplicate event-log text area must be removed.")
check("followers_list" not in qt, "Follower cards must use the compact grid layout.")
check("QPushButton:focus { outline:none; border:none; }" in qt, "Button focus outlines must be disabled.")
check("setFocusPolicy(Qt.FocusPolicy.NoFocus)" in qt, "Application buttons must not take keyboard focus.")
check("CreateMutexW" in qt and "Local\\\\TwitchFarmingQt6SingleInstance" in qt, "The Windows single-instance mutex is missing.")
check("self.channels_scroll = QScrollArea()" in qt and "frame.feed_scroll = scroll" in qt, "Independent main-screen scroll areas are missing.")
check("setWindowIcon(QIcon(str(APP_ICON_FILE)))" in qt, "The main window app icon is missing.")
check("events_output" not in qt, "The duplicate event-log text area must be removed.")
check("followers_list" not in qt, "Follower cards must use the compact grid layout.")
check("ui_icons.png" in qt and (APP / "assets" / "ui_icons.png").is_file(), "The generated Twitch icon atlas is missing.")
check((APP / "assets" / "app.ico").is_file(), "The generated app.ico file is missing.")
check("app.ico" in (APP / "build_icon.py").read_text(encoding="utf-8"), "The icon builder does not generate app.ico.")
check("pywebview" not in requirements and "pystray" not in requirements, "Legacy GUI dependencies are still installed.")
check("from qt_app import main" in entry, "The executable entry point must launch the native Qt interface.")
for marker in (
    "FramelessWindowHint",
    "class ModalOverlay",
    "eligible_watch_campaigns",
    "QScrollArea > QWidget > QWidget",
    "border-radius:0",
    "rgba(0, 0, 0, 185)",
    "QWidget#modalOverlay",
    "overlay.editor.setFocus()",
    "class SwitchCheckBox",
    "setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)",
    "QScrollBar:vertical { background:transparent; width:6px;",
    "self.followers_grid = QGridLayout()",
    "grid.addWidget(options, 1, 0)",
    "Автор и дизайн: Dv4roka",
    "AI-ассистенты: ChatGPT & Google Gemini",
    "Папка с данными",
    "self.login_button.setEnabled(not running)",
    "Основные",
    "html.escape(line)",
    "class QPushButton(QtPushButton)",
    "Qt.FocusPolicy.NoFocus",
    "QPushButton:focus { outline:none; border:none; }",
    "def hitButton(self, position)",
    "self.channels_scroll = QScrollArea()",
    "frame.feed_scroll = scroll",
    "self.account_avatar_sidebar",
    "self.account_avatar = QLabel",
    "self.api.refresh_channel_profiles, ([],)",
    "def acquire_single_instance",
    "CreateMutexW",
    "initializationOverlay",
    "APP_ICON_FILE.is_file()",
    "app.setWindowIcon",
    "Все кампании",
    "Начатые (Инвентарь)",
    "Награды Twitch (значки и эмоуты за просмотр)",
    "Редактировать",
    "Переместить",
    "channel_currency",
    "card.style().polish(card)",
    "class TitleBar",
    "class TwitchLogo",
    "class ChannelCard",
    "class CampaignCard",
    "def eligible_twitch_campaigns",
    "def _render_update",
    "def _render_active_slots",
    "def _run_task",
    "Signal",
    "QPropertyAnimation",
    "image_cache",
    "Скачать и обновить",
    "СМОТРИМ • СЛОТ",
):
    check(marker in qt, "Native Qt interface feature is missing: " + marker)
for excluded in ("QtWebEngine", "QtQml", "QtQuick", "QtSql", "Qt3D"):
    check(excluded not in qt, "A prohibited Qt module is referenced: " + excluded)
check("pyside6-essentials" in requirements and "pywebview" not in requirements and "pythonnet" not in requirements and "pystray" not in requirements, "Legacy WebView/pythonnet dependencies remain installed.")
check("--exclude-module pywebview" in workflow and "--exclude-module pythonnet" in workflow and "--exclude-module clr_loader" in workflow, "PyInstaller does not explicitly exclude legacy WebView/pythonnet modules.")
check("qt-smoke-test.py" in workflow, "Qt smoke tests are not included in CI.")
check(not (APP / "ui").exists(), "The legacy HTML/CSS/JS folder must be removed.")
readme = (ROOT / "README.md").read_text(encoding="utf-8")
app_readme = (APP / "README.md").read_text(encoding="utf-8")
check("webview" not in readme.lower() and "pywebview" not in readme.lower(), "The root README still contains legacy WebView references.")
check("Twitch Farming/ui/" not in readme, "The root README still lists the removed ui folder.")
check("webview" not in app_readme.lower() and "pywebview" not in app_readme.lower(), "The application README still contains legacy WebView references.")

print("Native Qt smoke tests passed.")
