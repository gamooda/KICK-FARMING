import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "Twitch Farming"
sys.path.insert(0, str(APP))

from qt_app import eligible_twitch_campaigns, format_duration


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
check([item["id"] for item in result[0]["visible_drops"]] == ["badge", "emote"], "Game drops and claimed Twitch rewards must be hidden.")
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
check("import webview" not in backend and "import pystray" not in backend, "The native backend must not depend on PyWebView or PyStray.")
check("PySide6-Essentials" in requirements, "PySide6-Essentials dependency is missing.")
check("pywebview" not in requirements and "pystray" not in requirements, "Legacy GUI dependencies are still installed.")
check("from qt_app import main" in entry, "The executable entry point must launch the native Qt interface.")
for marker in (
    "FramelessWindowHint",
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
check("pywebview" not in workflow and "pythonnet" not in workflow and "clr_loader" not in workflow, "Legacy WebView/pythonnet packages remain in the build.")
check("qt-smoke-test.py" in workflow, "Qt smoke tests are not included in CI.")
check(not (APP / "ui").exists(), "The legacy HTML/CSS/JS folder must be removed.")

print("Native Qt smoke tests passed.")
