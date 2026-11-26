import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "Twitch Farming"
sys.path.insert(0, str(APP))

from qt_app import _campaign_game_art_url, eligible_twitch_campaigns, eligible_watch_campaigns, format_duration, log_entry_matches_level


def check(condition, message):
    if not condition:
        raise AssertionError(message)


check(_campaign_game_art_url({"game": "Fortnite", "game_id": "33214"}) == "https://static-cdn.jtvnw.net/ttv-boxart/33214-144x192.jpg", "Flattened campaign data must derive game cover from game_id when backend replaces the nested game object.")
check(_campaign_game_art_url({"game": {"boxArtURL": "https://static-cdn.jtvnw.net/ttv-boxart/123-{width}x{height}.jpg", "id": "123"}}) == "https://static-cdn.jtvnw.net/ttv-boxart/123-144x192.jpg", "Nested Twitch game boxArtURL templates must render at 144x192.")
check(_campaign_game_art_url({"game_box_art_url": {"url": "//static-cdn.jtvnw.net/ttv-boxart/456-{width}x{height}.jpg"}}) == "https://static-cdn.jtvnw.net/ttv-boxart/456-144x192.jpg", "Protocol-relative nested game art URLs must be normalized.")
check(_campaign_game_art_url({"game": "Unknown", "game_id": ""}) == "", "The GAME placeholder must remain only when no game art URL or game ID is available.")


sample = [
    {
        "id": "campaign-1",
        "status": "ACTIVE",
        "started": True,
        "name": "Twitch profile rewards",
        "game": "Test Game",
        "drops": [
            {"id": "badge", "name": "Badge", "category": "twitch", "is_twitch_view": True, "image_url": "https://example.test/badge.png", "claimed": False},
            {"id": "emote", "name": "Emote", "category": "twitch", "is_twitch_view": True, "image_url": "https://example.test/emote.png", "claimed": False},
            {"id": "skin", "name": "Game skin", "category": "game", "is_twitch_view": False, "image_url": "https://example.test/skin.png", "claimed": False},
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
        "game": "Test Game",
        "drops": [
            {"id": "free-string-flags", "type": "WATCH", "claimed": "false", "paid": "false", "is_subscription": "false", "image_url": "https://example.test/free.png"},
            {"id": "subscription", "type": "SUBSCRIPTION", "claimed": False},
            {"id": "paid", "type": "WATCH", "claimed": False, "paid": "true"}
        ]
    }
])
check(len(edge_case_campaigns) == 1, "Available campaigns outside ACTIVE must still be listed.")
check([drop["id"] for drop in edge_case_campaigns[0]["visible_drops"]] == ["free-string-flags"], "String false flags must not hide free drops; subscription rewards must stay hidden.")
relaxed_campaigns = eligible_watch_campaigns([
    {"id": "upcoming", "status": "UPCOMING", "game": "Upcoming Game", "drops": [{"id": "box-art", "boxArtURL": "https://example.test/box.png"}]},
    {"id": "empty-status", "status": "", "game": "Game With Empty Status", "drops": [{"id": "image-url", "imageURL": "https://example.test/reward.png"}]},
    {"id": "unknown-no-game", "status": "UNKNOWN", "game": None, "drops": [{"id": "junk", "image_url": "https://example.test/junk.png"}]},
])
check([item["id"] for item in relaxed_campaigns] == ["upcoming", "empty-status"], "Upcoming and empty-status campaigns with a game and time-based rewards must stay visible; unknown campaigns without a game must be hidden.")
relaxed_drops = {drop["id"]: drop for campaign in relaxed_campaigns for drop in campaign["visible_drops"]}
check(relaxed_drops["box-art"]["image_url"] == "https://example.test/box.png", "boxArtURL must be accepted as a reward image.")
check(relaxed_drops["image-url"]["image_url"] == "https://example.test/reward.png", "imageURL must be accepted as a reward image.")
check(not eligible_watch_campaigns([{"id": "paid-goal", "status": "ACTIVE", "game": "Paid Game", "drops": [{"id": "subs-goal", "source_type": "SUBSGOAL"}]}]), "Subscription goals must remain excluded.")
check(log_entry_matches_level("2026 [iNfO] +100 points", "INFO"), "INFO filter must detect a case-insensitive level marker.")
check(log_entry_matches_level("2026 [WARNING] reconnect", "INFO / Важные"), "Important logs must include warnings.")
check(log_entry_matches_level("2026 [ERROR] failed", "INFO / Важные"), "Important logs must include errors.")
check(not log_entry_matches_level("2026 [DEBUG] trace", "INFO / Важные"), "Important logs must hide only DEBUG records.")
check(log_entry_matches_level("2026 [dEbUg] trace", "DEBUG"), "DEBUG filter must detect a case-insensitive level marker.")
check(log_entry_matches_level("2026 +50 → channel - Reason: WATCH", "INFO / Важные"), "Unmarked point accrual lines must remain visible in important logs.")
iconless_campaigns = eligible_watch_campaigns([{"id": "iconless", "status": "ACTIVE", "game": "Image Missing Game", "drops": [{"id": "placeholder", "category": "game", "image_url": ""}]}])
check(len(iconless_campaigns) == 1 and iconless_campaigns[0]["visible_drops"][0]["image_url"] == "", "Campaigns and rewards without an image must remain visible for the gift fallback.")
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
check("self.channels_scroll = QScrollArea()" in qt and "self.channels_scroll.setWidget(self.channels_wrap)" in qt and "layout.addWidget(self.channels_scroll, 1)" in qt and "self.stack.addWidget(page)" in qt and "scroll.setWidget(page)" not in qt, "Only the channel cards must scroll while the header, farm slots and bottom feeds stay fixed.")
check("feeds_row.setFixedHeight(135)" in qt and "self.rewards_feed = self._feed_panel" in qt and "self.events_feed = self._feed_panel" in qt, "Rewards and events feeds must stay pinned at the bottom of the Channels page.")
check("setWindowIcon(QIcon(str(APP_ICON_FILE)))" in qt, "The main window app icon is missing.")
check("set_gained_total(state.get(\"gained_total\", 0))" in qt and "def set_gained_total(self, value)" in qt, "The session points counter must use the backend session total.")
check("QLabel#sessionPoints" in qt, "The session points style is missing.")
check("QLabel#rewardCurrencyIcon { background:transparent; border:0;" in qt and "QLabel#rewardAmount" in qt, "Reward icons and amounts must use the compact style.")
check("details.setSpacing(2)" in qt and "details.setAlignment(Qt.AlignmentFlag.AlignVCenter)" in qt, "Reward text must be vertically compact and centered.")
check("QLabel#sidebarAccount { color:#E8E2F2; font-size:14px; font-weight:700;" in qt, "The sidebar username must be larger.")
check("def _animate_actions(self, visible)" in qt and "self.actions_opacity_animation" in qt, "Channel card actions must fade in and out on hover.")
check("QDesktopServices.openUrl(QUrl(\"https://www.twitch.tv/\" + value))" in qt and "Открыть на Twitch" in qt, "Channel cards must open their Twitch page in the system browser.")
check('QLabel("МЕНЮ")' not in qt, "The redundant sidebar menu heading must be removed.")
check("self.sidebar.setFixedWidth(238)" in qt, "The redesigned sidebar must fit all navigation labels.")
check("self.farm_status.hide()" in qt and "self.session_label.hide()" in qt, "The duplicate channel-page session status must be hidden.")
check('setContentsMargins(10 if title == "Последние награды" else 0, 0, 4, 0)' in qt, "The rewards column left inset is missing.")
check('QLabel#slotAvatar, QLabel#miniAvatar { background:transparent; border:0;' in qt, "Slot and follower avatars must not have square frames.")
check("in configured" in qt and 'QFrame#followerCard[added="true"]' in qt, "Added followers must be sorted and highlighted.")
check("followerAddedButton" in qt and "followerAddButton" in qt, "Added and not-added follower buttons must be visually distinct.")
check("def log_entry_matches_level" in qt and "INFO / Важные" in qt, "Log filters must parse severity markers and default to INFO / Important.")
check("def _set_header_status_notice" in qt and '_show_dialog("Обновление"' not in qt, "Update status must stay in the main titlebar.")

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
    "channelSlotBadge",
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

twitch_source = (APP / "engine" / "TwitchChannelPointsMiner" / "classes" / "Twitch.py").read_text(encoding="utf-8")
check("if campaigns is None:" in twitch_source and 'logger.debug("ViewerDropsDashboard:' in twitch_source and 'self._warn_drops_response("ViewerDropsDashboard", "отсутствует массив dropCampaigns", response)' in twitch_source, "A normal None dropCampaigns response must be debug-level while malformed dashboard data remains a warning.")
check('"is_followed" not in item or _flag_enabled(item.get("is_followed"))' in qt, "Campaign streamer spoiler must only include followed channels.")
check('QFrame[frameShape="4"], QFrame[frameShape="5"]' in qt, "Horizontal and vertical frame separators must use the neutral one-pixel style.")
check("self.setMinimumWidth(190)" in qt and "self.setMinimumHeight(185)" in qt and "QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed" in qt, "Channel cards must preserve their minimum height and expand horizontally.")
check("self.channels_grid.setColumnStretch(c, 1 if c < columns else 0)" in qt and "for c in range(10):" in qt and "setRowMinimumHeight(row, card_height)" in qt and "required_height = rows_count * card_height" in qt and "self.channels_wrap.setMinimumHeight(max(required_height, self.channels_grid.sizeHint().height() + 8))" in qt, "The channel grid must reset stretch and explicitly reserve full fixed-height rows inside its scroll area.")
check("self.channels_scroll.viewport().width()" in qt and "for c in range(10):" in qt, "The channel grid must recalculate columns from its actual viewport and clear obsolete columns.")
check('campaign.get("game.boxArtURL")' in qt and '.replace("%7Bwidth%7D", "144")' in qt, "Encoded and dotted campaign game box-art URLs must be supported.")
slot_card_source = qt[qt.index("class ActiveSlotCard(QFrame):"):qt.index("def _log_ui_exception", qt.index("class ActiveSlotCard(QFrame):"))]
check("class ActiveSlotCard(QFrame):" in qt and "self.select_button" not in slot_card_source and "self.remove_button" not in slot_card_source and 'QPushButton("Выбрать")' not in slot_card_source and 'QPushButton("Снять")' not in slot_card_source, "Active farm slot cards must not contain select or clear buttons.")
check("def _choose_slot_channel" in qt and "def _clear_slot_priority" in qt and "if has_active" not in qt, "Existing slot priority handlers must remain syntactically valid.")
check("def _campaign_game_art_url(campaign)" in qt and "_campaign_game_art_url(campaign)" in qt and "game_id" in qt and "ttv-boxart/" in qt and '"game_art"' in qt, "Campaign game cover URLs must be resolved from raw art fields or the flattened game_id, then loaded through the async image cache.")
check('self.drops_filter_index = 0' in qt and '("Все Twitch-награды", "gift")' in qt and '("Начатые (Инвентарь)", "inventory")' in qt and "campaigns = twitch_campaigns_all" in qt, "The Drops inventory must display only Twitch profile rewards, with a separate started-inventory filter.")
campaign_card_source = qt[qt.index("class CampaignCard(QFrame):"):qt.index("class ModalOverlay", qt.index("class CampaignCard(QFrame):"))]
check("self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)" in campaign_card_source and "layout.setSpacing(10)" in campaign_card_source, "Drops campaign cards must preserve their natural content height and keep spacing between rewards.")
check("reward_layout.setContentsMargins(10, 10, 10, 10)" in qt and "reward_layout.setSpacing(6)" in qt and "reward_row.setMinimumHeight(96)" in qt and "reward_name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)" in qt and "status.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)" in qt and "Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop" in qt and "footer.setContentsMargins(0, 6, 0, 0)" in qt, "Reward titles must wrap, status badges must stay at the right, and reward rows must have enough height for wrapped titles.")
check("filters.layout().setAlignment(Qt.AlignmentFlag.AlignTop)" in qt, "Prediction participant filter contents must align to the top.")
check('QComboBox::down-arrow { image: url("@COMBO_ARROW@");' in qt and (APP / "assets" / "combo_chevron.svg").is_file(), "Combo boxes must use the bundled SVG chevron.")
check("combo_chevron.svg;assets" in workflow and "combo_chevron.svg" in workflow, "The combo chevron SVG must be bundled and verified.")


print("Native Qt smoke tests passed.")
