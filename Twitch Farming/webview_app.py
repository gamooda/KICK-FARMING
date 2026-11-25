import json
import logging
import os
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import webview

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "engine"))


APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Twitch Farming"
LOCAL_APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Twitch Farming"
WEBVIEW_DIR = LOCAL_APP_DIR / "WebView2"
CONFIG_FILE = APP_DIR / "config.json"
LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "TwitchFarming.log"
ACTIVATION_URL = "https://www.twitch.tv/activate"
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
UI_DIR = RESOURCE_DIR / "ui"
ICON_FILE = RESOURCE_DIR / "assets" / "twitch.ico"

DEFAULT_BET_SETTINGS = {
    "strategy": "SMART",
    "percentage": 5,
    "percentage_gap": 20,
    "max_points": 50000,
    "minimum_points": 0,
    "stealth_mode": False,
    "delay": 6,
    "delay_mode": "FROM_END",
    "filter_enabled": False,
    "filter_by": "TOTAL_USERS",
    "filter_condition": "GT",
    "filter_value": 200,
}
DEFAULT_OPTIONS = {
    "claim_drops": True,
    "watch_streak": True,
    "claim_moments": True,
    "follow_raid": True,
    "make_predictions": True,
    "chat": True,
}
DEFAULT_APP_SETTINGS = {
    "minimize_to_tray": False,
    "auto_start_farm": False,
}
OPTION_NAMES = [
    ("claim_drops", "Twitch Drops"),
    ("watch_streak", "Серии просмотров"),
    ("claim_moments", "Моменты"),
    ("follow_raid", "Рейды"),
    ("make_predictions", "Прогнозы"),
    ("chat", "IRC-чат"),
]
STRATEGIES = ["SMART", "MOST_VOTED", "HIGH_ODDS", "PERCENTAGE", "SMART_MONEY", "NUMBER_1", "NUMBER_2", "NUMBER_3", "NUMBER_4", "NUMBER_5", "NUMBER_6", "NUMBER_7", "NUMBER_8"]
FILTER_KEYS = ["TOTAL_USERS", "DECISION_USERS", "TOTAL_POINTS", "DECISION_POINTS", "ODDS", "ODDS_PERCENTAGE", "PERCENTAGE_USERS", "TOP_POINTS"]


class AppLogHandler(logging.Handler):
    def __init__(self, app):
        super().__init__(logging.DEBUG)
        self.app = app

    def emit(self, record):
        if record.name.startswith(("pywebview", "webview", "clr")):
            return
        try:
            self.app.on_log(record.getMessage(), record.levelname, record.created)
        except Exception:
            pass


class AuthWorker(threading.Thread):
    def __init__(self, app, username, restoring, force_reauth=False):
        super().__init__(name="TwitchAuth", daemon=True)
        self.app = app
        self.username = username
        self.restoring = restoring
        self.force_reauth = bool(force_reauth)
        self.client = None

    def run(self):
        try:
            logger = logging.getLogger("twitch_farming.startup" if self.restoring else "twitch_farming.auth")
            import_started = time.monotonic()
            if self.restoring:
                logger.info("[STARTUP] Загрузка модулей Twitch...")
            from TwitchChannelPointsMiner.classes.Twitch import Twitch
            from TwitchChannelPointsMiner.utils import get_user_agent
            if self.restoring:
                logger.info("[STARTUP] Модули Twitch загружены за %.2f сек.", time.monotonic() - import_started)
            client_started = time.monotonic()
            self.client = Twitch(self.username, get_user_agent("CHROME"))
            if self.restoring:
                logger.info("[STARTUP] Клиент Twitch создан за %.2f сек.", time.monotonic() - client_started)
                logger.info("[STARTUP] Проверка сохранённой сессии Twitch...")
                restore_started = time.monotonic()
                success = bool(self.client.restore_session())
                logger.info("[STARTUP] Проверка сессии завершена за %.2f сек. Результат: %s.", time.monotonic() - restore_started, "успех" if success else "неудача")
            else:
                success = bool(self.client.login(force_reauth=self.force_reauth))
            self.app.auth_result(
                self.username,
                self.client if success else None,
                success,
                self.restoring,
                "" if success else "Twitch не подтвердил авторизацию.",
            )
        except BaseException as error:
            logging.getLogger("twitch_farming.auth").exception("Authentication worker failed")
            try:
                self.app.auth_result(self.username, None, False, self.restoring, str(error))
            except BaseException:
                logging.getLogger("twitch_farming.auth").exception("Authentication failure callback failed")
            if self.client is not None:
                try:
                    self.client.twitch_login.session.close()
                except Exception:
                    pass


class EngineWorker(threading.Thread):
    def __init__(self, app, username, channels, options, client):
        super().__init__(name="TwitchFarmEngine", daemon=True)
        self.app = app
        self.username = username
        self.channels = list(channels)
        self.options = dict(options)
        self.client = client
        self.miner = None
        self.stop_requested = False

    def add_streamer(self, login, channel_id=None):
        miner = self.miner
        deadline = time.monotonic() + 25
        while miner is not None and self.is_alive() and not self.stop_requested:
            try:
                ready = bool(miner.running and miner.twitch.twitch_login.get_auth_token())
            except Exception:
                ready = False
            if ready:
                return miner.add_streamer(login, channel_id)
            if time.monotonic() >= deadline:
                break
            time.sleep(0.15)
        return {"ok": False, "message": "Фарм ещё подключается к Twitch. Повтори добавление через несколько секунд."}

    def request_stop(self):
        self.stop_requested = True
        if self.miner is not None:
            try:
                self.miner.request_stop()
            except Exception:
                pass

    def run(self):
        miner = None
        try:
            from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner
            from TwitchChannelPointsMiner.classes.Chat import ChatPresence
            from TwitchChannelPointsMiner.classes.Settings import Priority
            from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer, StreamerSettings
            from TwitchChannelPointsMiner.classes.entities.Bet import BetSettings, Condition, DelayMode, FilterCondition, OutcomeKeys, Strategy
            from TwitchChannelPointsMiner.logger import LoggerSettings
            data = self.options.get("bet_settings", dict(DEFAULT_BET_SETTINGS))
            condition = None
            if data.get("filter_enabled"):
                condition = FilterCondition(
                    by=getattr(OutcomeKeys, data["filter_by"]),
                    where=getattr(Condition, data["filter_condition"]),
                    value=float(data["filter_value"]),
                )
            bet = BetSettings(
                strategy=getattr(Strategy, data["strategy"]),
                percentage=float(data["percentage"]),
                percentage_gap=float(data["percentage_gap"]),
                max_points=int(data["max_points"]),
                minimum_points=int(data["minimum_points"]),
                stealth_mode=bool(data["stealth_mode"]),
                filter_condition=condition,
                delay=float(data["delay"]),
                delay_mode=getattr(DelayMode, data["delay_mode"]),
            )
            settings = StreamerSettings(
                make_predictions=self.options["make_predictions"],
                follow_raid=self.options["follow_raid"],
                claim_drops=self.options["claim_drops"],
                claim_moments=self.options["claim_moments"],
                watch_streak=self.options["watch_streak"],
                community_goals=False,
                bet=bet,
                chat=ChatPresence.ONLINE if self.options["chat"] else ChatPresence.NEVER,
            )
            if self.client is None or not self.client.twitch_login.get_auth_token():
                raise RuntimeError("Авторизация Twitch недоступна. Подключи аккаунт заново.")
            miner = TwitchChannelPointsMiner(
                username=self.username,
                claim_drops_startup=False,
                enable_analytics=False,
                priority=[Priority.STREAK, Priority.DROPS, Priority.ORDER],
                logger_settings=LoggerSettings(save=False, less=True, console_level=logging.CRITICAL, file_level=logging.CRITICAL, emoji=False, colored=False),
                streamer_settings=settings,
                twitch_client=self.client,
                check_for_updates=False,
            )
            self.miner = miner
            with self.app.lock:
                branding_cache = {key: dict(value) for key, value in self.app.channel_points_branding_cache.items()}
            prepared_streamers = []
            for login in self.channels:
                streamer = Streamer(login)
                cached_branding = branding_cache.get(login)
                if isinstance(cached_branding, dict):
                    streamer.channel_points_name = str(cached_branding.get("name") or "")
                    streamer.channel_points_image = str(cached_branding.get("icon_url") or "")
                    streamer.channel_points_branding_loaded = True
                prepared_streamers.append(streamer)
            self.client.running = True
            self.client.twitch_login.cancelled = False
            if self.stop_requested:
                miner.request_stop()
                return
            miner.mine(streamers=prepared_streamers, followers=False)
        except BaseException as error:
            self.app.engine_error(str(error) or error.__class__.__name__)
        finally:
            if miner is not None:
                try:
                    miner.stop()
                except Exception as error:
                    self.app.engine_error(str(error))
            self.miner = None
            self.app.engine_finished(self)


class ChannelProfileWorker(threading.Thread):
    def __init__(self, app, client, logins):
        super().__init__(name="TwitchChannelProfiles", daemon=True)
        self.app = app
        self.client = client
        self.logins = list(logins)

    def run(self):
        try:
            for offset in range(0, len(self.logins), 100):
                batch = self.logins[offset:offset + 100]
                try:
                    users = self.client.helix_get("users", [("login", login) for login in batch])
                    streams = self.client.helix_get("streams", [("user_login", login) for login in batch])
                    users_by_login = {str(user.get("login", "")).lower(): user for user in users}
                    streams_by_login = {str(stream.get("user_login", "")).lower(): stream for stream in streams}
                    for login in batch:
                        user = users_by_login.get(login)
                        if not user:
                            self.app.update_channel_profile(login, {"loaded": True, "is_live": False, "error": "Канал не найден"})
                            continue
                        profile = {
                            "loaded": True,
                            "is_live": False,
                            "display_name": user.get("display_name") or login,
                            "error": "",
                        }
                        avatar = str(user.get("profile_image_url") or "")
                        if avatar.startswith("https://"):
                            profile["avatar_url"] = avatar
                        stream = streams_by_login.get(login)
                        if stream:
                            profile["is_live"] = True
                            profile["stream_title"] = str(stream.get("title") or "")
                            profile["viewer_count"] = int(stream.get("viewer_count") or 0)
                        self.app.update_channel_profile(login, profile)
                except Exception as error:
                    message = str(error)[:180]
                    for login in batch:
                        self.app.update_channel_profile(login, {"loaded": True, "error": message})
        finally:
            self.app.profile_worker_finished(self)


class FollowedChannelsWorker(threading.Thread):
    def __init__(self, app, client, request_id):
        super().__init__(name="TwitchFollowedChannels", daemon=True)
        self.app = app
        self.client = client
        self.request_id = request_id

    def run(self):
        channels = []
        error = ""
        requires_reauth = False
        try:
            scopes = set(getattr(self.client.twitch_login, "scopes", ()) or ())
            if "user:read:follows" not in scopes:
                raise PermissionError("MISSING_SCOPE:user:read:follows")
            user_id = self.client.twitch_login.get_user_id()
            if not user_id:
                raise RuntimeError("Не удалось определить ID авторизованного аккаунта Twitch.")
            raw_channels = []
            cursor = ""
            seen_cursors = set()
            while True:
                params = {"user_id": str(user_id), "first": 100}
                if cursor:
                    params["after"] = cursor
                payload = self.client.helix_get_payload("channels/followed", params)
                raw_channels.extend(payload.get("data", []) or [])
                self.app.update_followed_channels_progress(self, self.request_id, len(raw_channels))
                next_cursor = str((payload.get("pagination") or {}).get("cursor") or "")
                if not next_cursor or next_cursor in seen_cursors:
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            for offset in range(0, len(raw_channels), 100):
                batch = raw_channels[offset:offset + 100]
                ids = [str(item.get("broadcaster_id") or "") for item in batch if item.get("broadcaster_id")]
                users_by_id = {}
                streams_by_id = {}
                if ids:
                    try:
                        users = self.client.helix_get("users", [("id", item) for item in ids])
                        users_by_id = {str(item.get("id") or ""): item for item in users}
                    except Exception:
                        users_by_id = {}
                    try:
                        streams = self.client.helix_get("streams", [("user_id", item) for item in ids])
                        streams_by_id = {str(item.get("user_id") or ""): item for item in streams}
                    except Exception:
                        streams_by_id = {}
                for item in batch:
                    login = str(item.get("broadcaster_login") or "").strip().lower()
                    if not login:
                        continue
                    broadcaster_id = str(item.get("broadcaster_id") or "")
                    user = users_by_id.get(broadcaster_id, {})
                    stream = streams_by_id.get(broadcaster_id, {})
                    avatar = str(user.get("profile_image_url") or "")
                    channels.append({
                        "login": login,
                        "channel_id": broadcaster_id,
                        "display_name": str(user.get("display_name") or item.get("broadcaster_name") or login)[:80],
                        "avatar_url": avatar if avatar.startswith("https://") else "",
                        "game_name": str(stream.get("game_name") or ""),
                        "game_id": str(stream.get("game_id") or ""),
                        "is_live": bool(stream),
                        "viewer_count": max(0, int(stream.get("viewer_count") or 0)),
                        "followed_at": str(item.get("followed_at") or ""),
                    })
            self.app.finish_followed_channels_worker(self, self.request_id, channels)
            return
        except Exception as exception:
            message = str(exception)
            status_code = getattr(getattr(exception, "response", None), "status_code", 0)
            if message.startswith("MISSING_SCOPE:") or status_code in (401, 403):
                error = "Для просмотра подписок требуется разрешение Twitch user:read:follows. Нажми «Переподключить Twitch» и подтверди новые разрешения."
                requires_reauth = True
            else:
                error = message[:500] or exception.__class__.__name__
            self.app.finish_followed_channels_worker(self, self.request_id, [], error, requires_reauth)




class DropsInventoryWorker(threading.Thread):
    def __init__(self, app, client, request_id):
        super().__init__(name="TwitchDropsInventory", daemon=True)
        self.app = app
        self.client = client
        self.request_id = request_id

    def run(self):
        campaigns = []
        error = ""
        followers_error = ""
        try:
            with self.app.lock:
                refresh_followers = (
                    not self.app.followed_channels_loaded
                    or time.time() - self.app.followed_channels_updated_at > 180
                )
            if refresh_followers:
                self.app.refresh_followed_channels(force=True)
                self.app.followed_channels_event.wait(45)
            with self.app.lock:
                followed = [dict(item) for item in self.app.followed_channels]
                followers_error = self.app.followed_channels_error
            raw_campaigns = self.client.get_drop_inventory_snapshot()
            live_followers = [
                item for item in followed
                if item.get("is_live") and item.get("channel_id") and item.get("login")
            ]
            campaign_ids_by_login = (
                self.client.get_drop_campaign_ids_for_channels(live_followers)
                if live_followers else {}
            )
            for campaign in raw_campaigns:
                campaign_id = str(campaign.get("id") or "")
                allowed = set(str(item) for item in campaign.get("allowed_channel_ids") or [])
                candidates = []
                for streamer in live_followers:
                    login = str(streamer.get("login") or "").lower()
                    channel_id = str(streamer.get("channel_id") or "")
                    if campaign_id not in set(campaign_ids_by_login.get(login, [])):
                        continue
                    if allowed and channel_id not in allowed:
                        continue
                    candidates.append({
                        "login": login,
                        "display_name": str(streamer.get("display_name") or login),
                        "avatar_url": str(streamer.get("avatar_url") or ""),
                        "viewer_count": max(0, int(streamer.get("viewer_count") or 0)),
                        "game_name": str(streamer.get("game_name") or ""),
                    })
                item = dict(campaign)
                item["streamers"] = candidates
                item["watch_drops"] = [drop for drop in item.get("drops", []) if drop.get("type") == "watch"]
                item["subscription_drops"] = [drop for drop in item.get("drops", []) if drop.get("type") == "subscription"]
                campaigns.append(item)
        except Exception as exception:
            error = str(exception)[:500] or exception.__class__.__name__
        self.app.finish_drops_worker(self, self.request_id, campaigns, error, followers_error)


class TwitchFarmingApi:
    def __init__(self):
        self.lock = threading.RLock()
        self.username = ""
        self.channels = []
        self.options = dict(DEFAULT_OPTIONS)
        self.bet_settings = dict(DEFAULT_BET_SETTINGS)
        self.custom_presets = {}
        self.selected_preset = "Оптимальный"
        self.tutorial_done = False
        self.account_profile = {}
        self.channel_profiles = {}
        self.channel_balances = {}
        self.channel_points_branding_cache = {}
        self.channel_points_branding_worker = None
        self.rewards = []
        self.window = None
        self.window_maximized = False
        self.online_channels = set()
        self.watch_status = {}
        self.profile_worker = None
        self.pending_profile_logins = set()
        self.last_profile_refresh_at = 0.0
        self.restore_started = False
        self.startup_worker = None
        self.startup_error = ""
        self.startup_complete = False
        self.startup_phase = "Подготовка запуска..."
        self.startup_started_at = time.monotonic()
        self.run_id = datetime.now().strftime("%H%M%S") + "-" + str(os.getpid())
        self.webview_start_requested_at = 0.0
        self.webview_before_show_at = 0.0
        self.webview_before_load_at = 0.0
        self.webview_loaded_at = 0.0
        self.webview_shown_at = 0.0
        self.frontend_timing_logged = False
        self.frontend_ready_event = threading.Event()
        self.restore_pending = False
        self.followed_channels = []
        self.followed_channels_loading = False
        self.followed_channels_loaded = False
        self.followed_channels_error = ""
        self.followed_channels_requires_reauth = False
        self.followed_channels_updated_at = 0.0
        self.followed_channels_found_count = 0
        self.followed_channels_worker = None
        self.followed_channels_request_id = 0
        self.followed_channels_event = threading.Event()
        self.drops_campaigns = []
        self.drops_loading = False
        self.drops_loaded = False
        self.drops_error = ""
        self.drops_followers_error = ""
        self.drops_updated_at = 0.0
        self.drops_worker = None
        self.drops_request_id = 0
        self.drops_event = threading.Event()
        self.app_settings = dict(DEFAULT_APP_SETTINGS)
        self.tray_icon = None
        self.authenticated = False
        self.authenticated_username = ""
        self.auth_code = ""
        self.auth_error = ""
        self.auth_worker_state = "idle"
        self.auth_started_at = 0.0
        self.auth_url = ACTIVATION_URL
        self.auth_opened = False
        self.auth_status = "АККАУНТ НЕ ПОДКЛЮЧЁН"
        self.auth_worker = None
        self.engine_worker = None
        self.api_client = None
        self.session_running = False
        self.status_text = "●  НЕ ЗАПУЩЕН"
        self.status_color = "#A9B2C3"
        self.gained_total = 0
        self.event_total = 0
        self.events = []
        self.reward = None
        self.log_records = []
        self.next_log_id = 1
        self.pending_log_lines = []
        self.log_stop = threading.Event()
        self.root_logger = logging.getLogger()
        self.root_logger.setLevel(logging.DEBUG)
        self.log_file_lock = threading.Lock()
        self.log_handler = AppLogHandler(self)
        self.root_logger.addHandler(self.log_handler)
        try:
            self._sanitize_existing_log()
        except Exception:
            pass
        self.log_writer = threading.Thread(target=self._log_writer_loop, name="LogWriter", daemon=True)
        self.log_writer.start()
    def _startup_tasks(self):
        logger = logging.getLogger("twitch_farming.startup")
        started = time.monotonic()
        try:
            with self.lock:
                self.startup_phase = "Подготовка папки данных..."
            APP_DIR.mkdir(parents=True, exist_ok=True)
            with self.lock:
                self.startup_phase = "Чтение локальной конфигурации..."
            logger.info("[STARTUP] Чтение config.json...")
            self._load_config()
        except Exception as error:
            self.startup_error = str(error) or error.__class__.__name__
            logger.exception("Startup configuration failed")
        finally:
            with self.lock:
                self.restore_pending = False
                self.startup_complete = True
                self.startup_phase = "Готово"
                should_restore = bool(self.username)
            logger.info("[STARTUP] Конфигурация готова за %.2f сек.", time.monotonic() - started)
            if should_restore:
                self._restore_session()
    def begin_session_restore(self):
        with self.lock:
            pending = bool(self.restore_pending)
        if pending:
            self._restore_session()
        return {"ok": True}

    def _sanitize_startup_log(self):
        try:
            self._sanitize_existing_log()
        except Exception:
            logging.getLogger("twitch_farming.startup").exception("Existing log preparation failed")

    def get_startup_state(self):
        with self.lock:
            return {
                "ready": bool(self.startup_complete),
                "error": self.startup_error,
                "phase": self.startup_phase,
                "elapsed": round(max(0.0, time.monotonic() - self.startup_started_at), 1),
                "restore_pending": bool(self.restore_pending),
            }

    def _mark_webview_checkpoint(self, attribute, label):
        now = time.monotonic()
        with self.lock:
            if getattr(self, attribute):
                return
            setattr(self, attribute, now)
            webview_started = self.webview_start_requested_at or self.startup_started_at
            api_started = self.startup_started_at
        logging.getLogger("twitch_farming.startup").info(
            "[STARTUP] %s: %.2f сек. от запуска WebView2, %.2f сек. от создания API.",
            label,
            now - webview_started,
            now - api_started,
        )

    def on_webview_before_show(self, *args):
        self._mark_webview_checkpoint("webview_before_show_at", "WebView2: окно готово к показу")

    def on_webview_before_load(self, *args):
        self._mark_webview_checkpoint("webview_before_load_at", "WebView2: начинается загрузка HTML")

    def on_webview_shown(self, *args):
        self._mark_webview_checkpoint("webview_shown_at", "WebView2: окно показано")

    def record_frontend_timing(self, timing):
        if not isinstance(timing, dict):
            return {"ok": False}
        def number(key):
            try:
                return max(0.0, min(120000.0, float(timing.get(key) or 0)))
            except (TypeError, ValueError):
                return 0.0
        with self.lock:
            if self.frontend_timing_logged:
                return {"ok": True}
            self.frontend_timing_logged = True
        logging.getLogger("twitch_farming.startup").info(
            "[STARTUP] Браузер: DOMContentLoaded %.0f мс, window.load %.0f мс, PyWebView API готов %.0f мс.",
            number("dom_ready_ms"),
            number("load_ms"),
            number("bridge_ready_ms"),
        )
        return {"ok": True}

    def mark_frontend_ready(self):
        self.frontend_ready_event.set()
        return {"ok": True}

    def restart_app(self):
        try:
            self.shutdown()
            if getattr(sys, "frozen", False):
                os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
            os.execv(sys.executable, [sys.executable, *sys.argv])
        except Exception as error:
            logging.getLogger("twitch_farming.startup").exception("Application restart failed")
            return {"ok": False, "message": str(error) or error.__class__.__name__}

    def watch_webview_startup(self):
        if self.frontend_ready_event.wait(60):
            return
        if os.name != "nt":
            return
        logging.getLogger("twitch_farming.startup").error("[STARTUP] WebView2 не передал управление интерфейсу за 60 секунд.")
        try:
            import ctypes
            answer = ctypes.windll.user32.MessageBoxW(
                0,
                "Встроенный браузер не завершил запуск за 60 секунд.\n\nНажми «Да», чтобы полностью перезапустить приложение, или «Нет», чтобы продолжить ожидание.",
                "Twitch Farming",
                0x00000004 | 0x00000030 | 0x00010000,
            )
            if answer == 6:
                self.restart_app()
        except Exception:
            logging.getLogger("twitch_farming.startup").exception("WebView2 startup recovery dialog failed")

    def _load_config(self):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        self.username = str(data.get("username", "")).strip()
        channels = data.get("channels", [])
        if isinstance(channels, str):
            channels = re.split(r"[\n,;]+", channels)
        self.channels = list(dict.fromkeys(self.normalize_channel(x) for x in channels if self.normalize_channel(x)))
        stored_branding = data.get("channel_points_branding", {})
        self.channel_points_branding_cache = {}
        if isinstance(stored_branding, dict):
            for login, item in stored_branding.items():
                normalized_login = self.normalize_channel(login)
                if normalized_login not in self.channels or not isinstance(item, dict):
                    continue
                icon_url = str(item.get("icon_url") or "")
                entry = {
                    "name": str(item.get("name") or "").strip()[:80],
                    "icon_url": icon_url if icon_url.startswith("https://") else "",
                }
                try:
                    points = max(0, int(item.get("points")))
                except (TypeError, ValueError):
                    points = None
                if points is not None:
                    entry["points"] = points
                    self.channel_balances[normalized_login] = {
                        "login": normalized_login,
                        "points": points,
                        "display_name": normalized_login,
                        "avatar_url": "",
                        "points_name": entry["name"],
                        "points_icon_url": entry["icon_url"],
                    }
                self.channel_points_branding_cache[normalized_login] = entry
        stored_options = data.get("settings", {})
        stored_app_settings = data.get("app_settings", {})
        if isinstance(stored_app_settings, dict):
            self.app_settings = {key: bool(stored_app_settings.get(key, default)) for key, default in DEFAULT_APP_SETTINGS.items()}
        else:
            self.app_settings = dict(DEFAULT_APP_SETTINGS)
        config_version = int(data.get("ui_config_version", 0) or 0)
        self.options = {key: bool(stored_options.get(key, default)) for key, default in DEFAULT_OPTIONS.items()}
        if config_version < 2:
            self.options = dict(DEFAULT_OPTIONS)
        self.tutorial_done = bool(data.get("tutorial_done", False))
        stored_bets = data.get("bet_settings", {})
        if isinstance(stored_bets, dict):
            self.bet_settings.update(stored_bets)
        presets = data.get("custom_presets", {})
        self.custom_presets = presets if isinstance(presets, dict) else {}
        self.selected_preset = str(data.get("selected_preset", "Оптимальный"))

    def _config_data(self):
        return {
            "username": self.username,
            "channels": list(self.channels),
            "channel_points_branding": {key: dict(value) for key, value in self.channel_points_branding_cache.items() if key in self.channels},
            "settings": dict(self.options),
            "app_settings": dict(self.app_settings),
            "bet_settings": dict(self.bet_settings),
            "custom_presets": self.custom_presets,
            "selected_preset": self.selected_preset,
            "tutorial_done": self.tutorial_done,
            "ui_config_version": 2,
        }

    def _save_config(self):
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(self._config_data(), ensure_ascii=False, indent=2), encoding="utf-8")
            self._append_event("Настройки сохранены")
            return True
        except OSError as error:
            self._append_event("Не удалось сохранить настройки · " + str(error)[:180])
            return False

    def normalize_channel(self, value):
        value = str(value or "").strip()
        value = re.sub(r"^(?:https?://)?(?:www\.)?twitch\.tv/", "", value, flags=re.IGNORECASE)
        value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        value = value.lstrip("@").strip().lower()
        return value if re.fullmatch(r"[a-z0-9_]{1,25}", value) else ""

    def save_settings(self, payload):
        payload = payload if isinstance(payload, dict) else {}
        with self.lock:
            username = str(payload.get("username", self.username)).strip().lower()
            channels = payload.get("channels", self.channels)
            if isinstance(channels, str):
                channels = re.split(r"[\n,;]+", channels)
            normalized = []
            for item in channels if isinstance(channels, list) else []:
                channel = self.normalize_channel(item)
                if channel and channel not in normalized:
                    normalized.append(channel)
            settings = payload.get("settings", self.options)
            if not isinstance(settings, dict):
                settings = self.options
            bets = payload.get("bet_settings", self.bet_settings)
            if not isinstance(bets, dict):
                bets = self.bet_settings
            presets = payload.get("custom_presets", self.custom_presets)
            if not isinstance(presets, dict):
                presets = self.custom_presets
            self.username = username
            self.channels = normalized
            self.options = {key: bool(settings.get(key, DEFAULT_OPTIONS[key])) for key in DEFAULT_OPTIONS}
            stored_app = payload.get("app_settings", self.app_settings)
            self.app_settings = {
                key: bool(stored_app.get(key, self.app_settings.get(key, default)))
                for key, default in DEFAULT_APP_SETTINGS.items()
            } if isinstance(stored_app, dict) else dict(DEFAULT_APP_SETTINGS)
            self.bet_settings = {**DEFAULT_BET_SETTINGS, **bets}
            self.custom_presets = presets
            selected = str(payload.get("selected_preset", self.selected_preset))
            self.selected_preset = selected if selected == "Оптимальный" or selected in presets else "Оптимальный"
        ok = self._save_config()
        return {"ok": ok, "message": "Настройки сохранены." if ok else "Не удалось сохранить настройки."}

    def save_channel_editor(self, mode, old_channel, raw_value, preview_profiles=None):
        prepared_profiles = {}
        for item in (preview_profiles if isinstance(preview_profiles, list) else []):
            if not isinstance(item, dict):
                continue
            login = self.normalize_channel(item.get("login"))
            if not login:
                continue
            avatar_url = str(item.get("avatar_url") or "")
            try:
                viewer_count = max(0, int(item.get("viewer_count") or 0))
            except (TypeError, ValueError):
                viewer_count = 0
            prepared_profiles[login] = {
                "loaded": bool(item.get("loaded")),
                "display_name": str(item.get("display_name") or login)[:80],
                "avatar_url": avatar_url if avatar_url.startswith("https://") else "",
                "is_live": bool(item.get("is_live")),
                "viewer_count": viewer_count,
                "error": str(item.get("error") or "")[:160],
            }
        with self.lock:
            if self.session_running:
                return {"ok": False, "message": "Нельзя менять каналы во время сбора."}
            old = self.normalize_channel(old_channel)
            if mode == "edit":
                channel = self.normalize_channel(raw_value)
                if not channel:
                    return {"ok": False, "message": "Укажи корректный логин или ссылку Twitch."}
                if old not in self.channels:
                    return {"ok": False, "message": "Канал больше не найден."}
                if channel != old and channel in self.channels:
                    return {"ok": False, "message": "Этот канал уже есть в списке."}
                if channel == old:
                    return {"ok": True, "message": "Канал не изменён.", "added": [], "unchanged": True}
                self.channels[self.channels.index(old)] = channel
                self.online_channels.discard(old)
                self.channel_profiles.pop(old, None)
                self.channel_balances.pop(old, None)
                self.channel_points_branding_cache.pop(old, None)
                added = [channel]
            else:
                tokens = re.split(r"[\n,;]+", str(raw_value or ""))
                normalized = list(dict.fromkeys(self.normalize_channel(item) for item in tokens))
                normalized = [item for item in normalized if item]
                added = [item for item in normalized if item not in self.channels]
                if not added:
                    if normalized:
                        return {"ok": False, "message": "Все указанные каналы уже добавлены."}
                    return {"ok": False, "message": "Укажи корректные логины или ссылки Twitch."}
                self.channels.extend(added)
                for login in added:
                    profile = prepared_profiles.get(login)
                    if profile and profile.get("loaded") and not profile.get("error"):
                        self.channel_profiles[login] = dict(profile)
            refresh_targets = [
                login for login in added
                if not bool(self.channel_profiles.get(login, {}).get("loaded"))
                or bool(self.channel_profiles.get(login, {}).get("error"))
            ]
        if not self._save_config():
            return {"ok": False, "message": "Не удалось сохранить список каналов."}
        if refresh_targets:
            self.refresh_channel_profiles(refresh_targets)
        if mode == "add":
            return {"ok": True, "message": "Добавлено каналов: " + str(len(added)) + ".", "added": added}
        return {"ok": True, "message": "Канал сохранён.", "added": added}

    def remove_channel(self, channel):
        channel = self.normalize_channel(channel)
        with self.lock:
            if self.session_running:
                return {"ok": False, "message": "Нельзя менять каналы во время сбора."}
            if channel in self.channels:
                self.channels.remove(channel)
                self.online_channels.discard(channel)
                self.channel_profiles.pop(channel, None)
                self.channel_balances.pop(channel, None)
                self.channel_points_branding_cache.pop(channel, None)
        self._save_config()
        return {"ok": True}

    def save_preset(self, name, values):
        name = str(name or "").strip()
        if not name:
            return {"ok": False, "message": "Введи название пресета."}
        if name == "Оптимальный":
            return {"ok": False, "message": "Название «Оптимальный» зарезервировано."}
        if not isinstance(values, dict):
            return {"ok": False, "message": "Некорректные параметры."}
        with self.lock:
            self.custom_presets[name] = {**DEFAULT_BET_SETTINGS, **values}
            self.bet_settings = dict(self.custom_presets[name])
            self.selected_preset = name
        self._save_config()
        return {"ok": True, "message": "Пресет «" + name + "» сохранён."}

    def delete_preset(self, name):
        name = str(name or "")
        with self.lock:
            if name == "Оптимальный" or name not in self.custom_presets:
                return {"ok": False, "message": "Выбери пользовательский пресет."}
            self.custom_presets.pop(name, None)
            self.bet_settings = dict(DEFAULT_BET_SETTINGS)
            self.selected_preset = "Оптимальный"
        self._save_config()
        return {"ok": True, "message": "Пресет удалён."}

    def apply_bet_settings(self, values, selected):
        if not isinstance(values, dict):
            return {"ok": False, "message": "Некорректные параметры."}
        with self.lock:
            self.bet_settings = {**DEFAULT_BET_SETTINGS, **values}
            selected = str(selected or "Оптимальный")
            self.selected_preset = selected if selected == "Оптимальный" or selected in self.custom_presets else "Оптимальный"
            if self.selected_preset in self.custom_presets:
                self.custom_presets[self.selected_preset] = dict(self.bet_settings)
        self._save_config()
        return {"ok": True, "message": "Параметры применены для следующего запуска."}

    def finish_tutorial(self):
        with self.lock:
            self.tutorial_done = True
        self._save_config()
        return {"ok": True}

    def login(self, username):
        username = str(username or "").strip().lower()
        if not username:
            self._append_event("Укажи логин Twitch перед подключением.")
            return {"ok": False, "message": "Укажи логин Twitch."}
        with self.lock:
            if self.auth_worker is not None and self.auth_worker.is_alive():
                return {"ok": False, "message": "Уже выполняется авторизация."}
            if self.engine_worker is not None and self.engine_worker.is_alive():
                return {"ok": False, "message": "Останови сбор перед сменой аккаунта."}
            self.username = username
            self.authenticated = False
            self.authenticated_username = ""
            self.auth_code = ""
            self.auth_error = ""
            self.auth_worker_state = "running"
            self.auth_started_at = time.time()
            self.auth_opened = False
            self.auth_url = ACTIVATION_URL
            self.auth_status = "ПОДКЛЮЧЕНИЕ К TWITCH…"
            self._set_status_locked("●  АВТОРИЗАЦИЯ", "#F0B65A")
            self.auth_worker = AuthWorker(self, username, False, force_reauth=True)
            worker = self.auth_worker
        self._save_config()
        worker.start()
        return {"ok": True, "message": "Подключение к Twitch…" }

    def _restore_session(self):
        with self.lock:
            username = self.username.strip().lower()
            if not username or (self.auth_worker is not None and self.auth_worker.is_alive()):
                self.restore_pending = False
                return
            self.restore_pending = False
            self.auth_status = "ПРОВЕРКА СОХРАНЁННОЙ СЕССИИ…"
            self.auth_error = ""
            self.auth_worker_state = "restoring"
            self.auth_started_at = time.time()
            self._set_status_locked("●  АВТОРИЗАЦИЯ", "#F0B65A")
            self.auth_worker = AuthWorker(self, username, True)
            worker = self.auth_worker
        worker.start()

    def auth_result(self, username, client, success, restored, error):
        with self.lock:
            if success and client is not None and self.authenticated and self.api_client is client and self.auth_worker_state == "completed":
                return
            if success and client is not None:
                self.api_client = client
                self.authenticated = True
                self.authenticated_username = username
                self.username = username
                self.auth_status = "АККАУНТ ПОДКЛЮЧЁН · " + username
                self.auth_code = "Сессия восстановлена" if restored else "подтверждён"
                self.auth_error = ""
                self.auth_worker_state = "completed"
                self._set_status_locked("●  АККАУНТ ПОДКЛЮЧЁН", "#42D6A4")
                self._append_event_locked(("Сессия Twitch восстановлена · " if restored else "Аккаунт Twitch подключён · ") + username)
            else:
                self.api_client = None
                self.authenticated = False
                self.authenticated_username = ""
                self.auth_status = "АВТОРИЗАЦИЯ НЕ УДАЛАСЬ"
                self.auth_error = str(error)[:500]
                self.auth_worker_state = "failed"
                self._append_event_locked("Вход не выполнен · " + self.auth_error[:220])
                self._set_status_locked("●  ОШИБКА АВТОРИЗАЦИИ", "#FF6B78")
        try:
            self._save_config()
        except BaseException:
            logging.getLogger("twitch_farming.auth").exception("Could not persist authentication state")
        if success and client is not None:
            try:
                self.refresh_channel_profiles()
            except BaseException:
                logging.getLogger("twitch_farming.auth").exception("Could not start channel profile loader")
            if restored:
                with self.lock:
                    auto_start = bool(self.app_settings.get("auto_start_farm")) and bool(self.channels)
                if auto_start:
                    threading.Thread(target=self._auto_start_after_restore, name="AutoStartFarm", daemon=True).start()

    def _auto_start_after_restore(self):
        time.sleep(1.0)
        result = self.start_farm({})
        if not result.get("ok"):
            logging.getLogger("twitch_farming.startup").warning("Автозапуск фарма не выполнен: %s", result.get("message", ""))

    def get_auth_state(self):
        with self.lock:
            worker_alive = bool(self.auth_worker is not None and self.auth_worker.is_alive())
            elapsed = max(0, int(time.time() - self.auth_started_at)) if self.auth_started_at else 0
            if (
                not self.authenticated
                and not worker_alive
                and self.auth_worker_state in ("running", "restoring")
                and not self.restore_pending
                and elapsed > 8
            ):
                self.auth_worker_state = "failed"
                self.auth_status = "АВТОРИЗАЦИЯ НЕ УДАЛАСЬ"
                self.auth_error = "Поток авторизации завершился без результата. Повтори вход."
                self._set_status_locked("●  ОШИБКА АВТОРИЗАЦИИ", "#FF6B78")
                self._append_event_locked(self.auth_error)
            return {
                "authenticated": bool(self.authenticated and self.api_client is not None),
                "username": self.authenticated_username or self.username,
                "auth_status": self.auth_status,
                "auth_code": self.auth_code,
                "auth_error": self.auth_error,
                "worker_alive": worker_alive,
                "worker_state": self.auth_worker_state,
                "restore_pending": bool(self.restore_pending),
                "elapsed": elapsed if worker_alive else 0,
            }

    def refresh_channel_profiles(self, logins=None):
        with self.lock:
            if not self.authenticated or self.api_client is None:
                return
            targets = list(logins if logins is not None else self.channels)
            account_login = self.authenticated_username or self.username
            self.pending_profile_logins.update(x for x in targets if x in self.channels)
            if account_login and account_login not in self.pending_profile_logins:
                self.pending_profile_logins.add(account_login)
            if self.profile_worker is not None and self.profile_worker.is_alive():
                return
            batch = sorted(self.pending_profile_logins)
            self.pending_profile_logins.clear()
            if not batch:
                return
            worker = ChannelProfileWorker(self, self.api_client, batch)
            self.profile_worker = worker
            self.last_profile_refresh_at = time.monotonic()
        worker.start()

    def refresh_followed_channels(self, force=False):
        with self.lock:
            if not self.authenticated or self.api_client is None:
                return {"ok": False, "message": "Сначала подключи аккаунт Twitch."}
            if self.followed_channels_worker is not None and self.followed_channels_worker.is_alive():
                return {"ok": True, "loading": True}
            if (
                not force
                and self.followed_channels_loaded
                and not self.followed_channels_error
                and time.time() - self.followed_channels_updated_at < 300
            ):
                return {"ok": True, "cached": True}
            self.followed_channels_loading = True
            self.followed_channels_event.clear()
            self.followed_channels_error = ""
            self.followed_channels_requires_reauth = False
            self.followed_channels_found_count = 0
            self.followed_channels_request_id += 1
            request_id = self.followed_channels_request_id
            worker = FollowedChannelsWorker(self, self.api_client, request_id)
            self.followed_channels_worker = worker
        try:
            worker.start()
        except Exception as error:
            self.finish_followed_channels_worker(worker, request_id, [], str(error), False)
            return {"ok": False, "message": str(error)}
        return {"ok": True, "loading": True}

    def get_followed_channels_state(self):
        with self.lock:
            return {
                "channels": [dict(item) for item in self.followed_channels],
                "loading": bool(self.followed_channels_loading),
                "loaded": bool(self.followed_channels_loaded),
                "error": self.followed_channels_error,
                "requires_reauth": bool(self.followed_channels_requires_reauth),
                "updated_at": self.followed_channels_updated_at,
                "found_count": self.followed_channels_found_count,
            }

    def refresh_drops_inventory(self, force=False):
        with self.lock:
            if not self.authenticated or self.api_client is None:
                return {"ok": False, "message": "Сначала подключи аккаунт Twitch."}
            if self.drops_worker is not None and self.drops_worker.is_alive():
                return {"ok": True, "loading": True}
            if (
                not force
                and self.drops_loaded
                and not self.drops_error
                and time.time() - self.drops_updated_at < 150
            ):
                return {"ok": True, "cached": True}
            self.drops_loading = True
            self.drops_event.clear()
            self.drops_error = ""
            self.drops_followers_error = ""
            self.drops_request_id += 1
            request_id = self.drops_request_id
            worker = DropsInventoryWorker(self, self.api_client, request_id)
            self.drops_worker = worker
        try:
            worker.start()
        except Exception as error:
            self.finish_drops_worker(worker, request_id, [], str(error))
            return {"ok": False, "message": str(error)}
        return {"ok": True, "loading": True}

    def get_drops_inventory_state(self):
        with self.lock:
            return {
                "campaigns": [dict(item) for item in self.drops_campaigns],
                "loading": bool(self.drops_loading),
                "loaded": bool(self.drops_loaded),
                "error": self.drops_error,
                "followers_error": self.drops_followers_error,
                "updated_at": self.drops_updated_at,
                "active_campaigns": len(self.drops_campaigns),
            }

    def finish_drops_worker(self, worker, request_id, campaigns, error="", followers_error=""):
        with self.lock:
            if self.drops_worker is not worker or self.drops_request_id != request_id:
                return
            self.drops_worker = None
            self.drops_loading = False
            self.drops_loaded = True
            self.drops_error = str(error or "")[:500]
            self.drops_followers_error = str(followers_error or "")[:400]
            if not error:
                self.drops_campaigns = [dict(item) for item in campaigns]
                self.drops_updated_at = time.time()
            self.drops_event.set()

    def add_drop_channel_to_farm(self, raw_login):
        login = self.normalize_channel(raw_login)
        if not login:
            return {"ok": False, "message": "Некорректный логин Twitch."}
        with self.lock:
            follow = next((dict(item) for item in self.followed_channels if item.get("login") == login), None)
            if follow is None:
                return {"ok": False, "message": "Стример больше не находится в списке фолловеров. Обнови Drops."}
            already_configured = login in self.channels
            running = self.session_running
            worker = self.engine_worker
        if running:
            if worker is None or not worker.is_alive():
                return {"ok": False, "message": "Фарм ещё запускается. Повтори добавление через несколько секунд."}
            result = worker.add_streamer(login, follow.get("channel_id"))
            if not result.get("ok"):
                return result
            if not already_configured:
                profile = {
                    "loaded": True,
                    "display_name": str(follow.get("display_name") or login),
                    "avatar_url": str(follow.get("avatar_url") or ""),
                    "is_live": bool(follow.get("is_live")),
                    "viewer_count": max(0, int(follow.get("viewer_count") or 0)),
                    "error": "",
                }
                with self.lock:
                    if login not in self.channels:
                        self.channels.append(login)
                    self.channel_profiles[login] = profile
                    if profile["is_live"]:
                        self.online_channels.add(login)
                self._save_config()
            return {"ok": True, "message": "Канал добавлен в работающий фарм."}
        if not already_configured:
            profile = {
                "login": login,
                "loaded": True,
                "display_name": str(follow.get("display_name") or login),
                "avatar_url": str(follow.get("avatar_url") or ""),
                "is_live": bool(follow.get("is_live")),
                "viewer_count": max(0, int(follow.get("viewer_count") or 0)),
                "error": "",
            }
            result = self.save_channel_editor("add", "", login, [profile])
            if not result.get("ok"):
                return result
        result = self.start_farm({})
        if result.get("ok"):
            return {"ok": True, "message": "Канал добавлен. Автоматический сбор запущен."}
        return result

    def clear_avatar_cache(self):
        with self.lock:
            cleared = 0
            for profile in self.channel_profiles.values():
                if profile.pop("avatar_url", ""):
                    cleared += 1
            if self.account_profile.pop("avatar_url", ""):
                cleared += 1
            for item in self.followed_channels:
                if item.pop("avatar_url", ""):
                    cleared += 1
        return {"ok": True, "cleared": cleared, "message": "Кэш аватарок очищен."}

    def clear_currency_cache(self):
        with self.lock:
            cleared = sum(1 for item in self.channel_points_branding_cache.values() if item.get("icon_url"))
            self.channel_points_branding_cache.clear()
            for item in self.channel_balances.values():
                item["points_name"] = ""
                item["points_icon_url"] = ""
            miner = getattr(self.engine_worker, "miner", None) if self.engine_worker is not None else None
            streamers = list(getattr(miner, "streamers", []) or [])
            for streamer in streamers:
                streamer.channel_points_name = ""
                streamer.channel_points_image = ""
                streamer.channel_points_branding_loaded = False
                streamer.channel_points_branding_loading = False
        self._save_config()
        return {"ok": True, "cleared": cleared, "message": "Кэш оформления Channel Points очищен."}

    def logout(self):
        with self.lock:
            if self.session_running:
                return {"ok": False, "message": "Сначала останови фарм."}
            username = self.authenticated_username or self.username
            client = self.api_client
        if username:
            cookies_file = Path(os.environ.get("APPDATA", str(Path.home()))) / "Twitch Farming" / "cookies" / (username + ".dat")
            try:
                cookies_file.unlink(missing_ok=True)
            except OSError:
                return {"ok": False, "message": "Не удалось удалить локальную сессию Twitch."}
        with self.lock:
            self.api_client = None
            self.authenticated = False
            self.authenticated_username = ""
            self.username = ""
            self.auth_code = ""
            self.auth_error = ""
            self.auth_worker_state = "idle"
            self.auth_status = "АККАУНТ НЕ ПОДКЛЮЧЁН"
            self.account_profile = {}
            self.followed_channels = []
            self.followed_channels_loaded = False
            self.followed_channels_error = ""
            self.drops_campaigns = []
            self.drops_loaded = False
            self.drops_error = ""
            self._set_status_locked("●  НЕ ЗАПУЩЕН", "#A9B2C3")
        if client is not None:
            try:
                client.twitch_login.session.close()
            except Exception:
                pass
        self._save_config()
        return {"ok": True, "message": "Аккаунт Twitch отключён."}

    def update_followed_channels_progress(self, worker, request_id, found_count):
        with self.lock:
            if self.followed_channels_worker is worker and self.followed_channels_request_id == request_id:
                self.followed_channels_found_count = max(0, int(found_count))

    def finish_followed_channels_worker(self, worker, request_id, channels, error="", requires_reauth=False):
        with self.lock:
            if self.followed_channels_worker is not worker or self.followed_channels_request_id != request_id:
                return
            self.followed_channels_worker = None
            self.followed_channels_loading = False
            self.followed_channels_loaded = True
            self.followed_channels_error = str(error or "")[:500]
            self.followed_channels_requires_reauth = bool(requires_reauth)
            self.followed_channels_event.set()
            if not error:
                self.followed_channels = [dict(item) for item in channels]
                self.followed_channels_updated_at = time.time()
                self.followed_channels_found_count = len(self.followed_channels)

    def add_followed_channel(self, raw_login):
        login = self.normalize_channel(raw_login)
        if not login:
            return {"ok": False, "message": "Некорректный логин Twitch."}
        with self.lock:
            if login in self.channels:
                return {"ok": True, "message": "Канал уже добавлен.", "unchanged": True}
            item = next((dict(row) for row in self.followed_channels if row.get("login") == login), None)
            if item is None:
                return {"ok": False, "message": "Канал не найден в списке фолловеров. Обнови список."}
        profile = {
            "login": login,
            "loaded": True,
            "display_name": item.get("display_name") or login,
            "avatar_url": item.get("avatar_url") or "",
            "is_live": bool(item.get("is_live")),
            "viewer_count": int(item.get("viewer_count") or 0),
            "error": "",
        }
        return self.save_channel_editor("add", "", login, [profile])

    def update_channel_profile(self, login, profile):
        with self.lock:
            account_login = (self.authenticated_username or self.username).lower()
            if login == account_login:
                self.account_profile = {
                    key: value for key, value in profile.items()
                    if key in ("loaded", "display_name", "avatar_url", "error")
                }
            if login not in self.channels:
                return
            current = dict(self.channel_profiles.get(login, {}))
            current.update(profile)
            self.channel_profiles[login] = current
            if current.get("is_live"):
                self.online_channels.add(login)
            elif current.get("loaded"):
                self.online_channels.discard(login)

    def profile_worker_finished(self, worker):
        with self.lock:
            if self.profile_worker is not worker:
                return
            self.profile_worker = None
            batch = sorted(self.pending_profile_logins)
            self.pending_profile_logins.clear()
            if not batch or not self.authenticated or self.api_client is None:
                return
            next_worker = ChannelProfileWorker(self, self.api_client, batch)
            self.profile_worker = next_worker
            self.last_profile_refresh_at = time.monotonic()
        next_worker.start()

    def preview_channels(self, raw_value):
        tokens = re.split(r"[\n,;]+", str(raw_value or ""))
        logins = list(dict.fromkeys(self.normalize_channel(item) for item in tokens))
        logins = [login for login in logins if login][:100]
        if not logins:
            return {"channels": []}
        with self.lock:
            client = self.api_client if self.authenticated else None
            cached_profiles = {login: dict(self.channel_profiles.get(login, {})) for login in logins}
        profiles = {
            login: {
                "login": login,
                "display_name": cached_profiles[login].get("display_name") or login,
                "avatar_url": cached_profiles[login].get("avatar_url") or "",
                "is_live": bool(cached_profiles[login].get("is_live")),
                "loaded": bool(cached_profiles[login].get("loaded")),
            }
            for login in logins
        }
        if client is not None:
            try:
                users = client.helix_get("users", [("login", login) for login in logins])
                streams = client.helix_get("streams", [("user_login", login) for login in logins])
                users_by_login = {str(user.get("login", "")).lower(): user for user in users}
                streams_by_login = {str(stream.get("user_login", "")).lower(): stream for stream in streams}
                for login in logins:
                    user = users_by_login.get(login)
                    if not user:
                        profiles[login].update({"loaded": True, "error": "Канал не найден"})
                        continue
                    stream = streams_by_login.get(login)
                    avatar = str(user.get("profile_image_url") or "")
                    profiles[login].update({
                        "loaded": True,
                        "display_name": str(user.get("display_name") or login),
                        "avatar_url": avatar if avatar.startswith("https://") else "",
                        "is_live": bool(stream),
                        "viewer_count": int((stream or {}).get("viewer_count") or 0),
                        "error": "",
                    })
            except Exception as error:
                message = str(error)[:160]
                for login in logins:
                    profiles[login]["error"] = message
        return {"channels": list(profiles.values())}

    def refresh_channel_points_branding(self):
        with self.lock:
            if not self.authenticated or self.api_client is None:
                return {"ok": False, "message": "Сначала подключи аккаунт Twitch."}
            worker = self.channel_points_branding_worker
            if worker is not None and worker.is_alive():
                return {"ok": False, "message": "Проверка оформления уже выполняется."}
            client = self.api_client
            engine = self.engine_worker
            miner = getattr(engine, "miner", None) if engine is not None else None
            streamers = {
                str(getattr(item, "username", "") or "").lower(): item
                for item in list(getattr(miner, "streamers", []) or [])
            }
            logins = list(self.channels)

            def refresh():
                try:
                    from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer
                    changed = 0
                    for login in logins:
                        streamer = streamers.get(login) or Streamer(login)
                        streamer.channel_points_branding_loaded = False
                        streamer.channel_points_branding_loading = True
                        try:
                            client._load_channel_points_branding(streamer)
                        except Exception:
                            logging.getLogger("twitch_farming.branding").exception("Channel Points branding refresh failed for @%s", login)
                        if streamer.channel_points_branding_loaded:
                            with self.lock:
                                previous = self.channel_points_branding_cache.get(login, {})
                                entry = {
                                    "name": str(streamer.channel_points_name or "").strip()[:80],
                                    "icon_url": str(streamer.channel_points_image or "") if str(streamer.channel_points_image or "").startswith("https://") else "",
                                }
                                if "points" in previous:
                                    entry["points"] = previous["points"]
                                if entry != previous:
                                    self.channel_points_branding_cache[login] = entry
                                    changed += 1
                    if changed:
                        self._save_config()
                    with self.lock:
                        self._append_event_locked("Проверено оформление Channel Points · обновлено каналов: " + str(changed))
                finally:
                    with self.lock:
                        self.channel_points_branding_worker = None

            worker = threading.Thread(target=refresh, name="ChannelPointsBrandingRefresh", daemon=True)
            self.channel_points_branding_worker = worker
        worker.start()
        return {"ok": True, "message": "Проверка названий и значков Channel Points запущена."}

    def start_farm(self, payload):
        with self.lock:
            if self.engine_worker is not None and self.engine_worker.is_alive():
                return {"ok": False, "message": "Сбор уже запущен."}
        saved = self.save_settings(payload)
        if not saved.get("ok"):
            return saved
        with self.lock:
            username = self.username
            if not username:
                return {"ok": False, "message": "Укажи логин Twitch."}
            if self.auth_worker is not None and self.auth_worker.is_alive():
                return {"ok": False, "message": "Ожидается подтверждение Twitch."}
            if not self.authenticated or self.authenticated_username != username or self.api_client is None:
                start_auth = True
            else:
                start_auth = False
            if self.engine_worker is not None and self.engine_worker.is_alive():
                return {"ok": False, "message": "Сбор уже запущен."}
            if not self.channels:
                return {"ok": False, "message": "Добавь хотя бы один канал."}
            if start_auth:
                pass
            else:
                self.session_running = True
                self.online_channels.clear()
                self.gained_total = 0
                self.event_total = 0
                self.events = []
                options = dict(self.options)
                options["bet_settings"] = dict(self.bet_settings)
                self._set_status_locked("●  ЗАПУСК", "#F0B65A")
                self.engine_worker = EngineWorker(self, username, self.channels, options, self.api_client)
                worker = self.engine_worker
        if start_auth:
            self.login(username)
            return {"ok": True, "message": "Сначала подтверди вход в Twitch, затем нажми «Запустить» ещё раз."}
        worker.start()
        return {"ok": True, "message": "Фарм запущен."}

    def stop_farm(self):
        with self.lock:
            worker = self.engine_worker
            if worker is None or not worker.is_alive():
                return {"ok": False, "message": "Сбор не запущен."}
            self._set_status_locked("●  ОСТАНОВКА", "#F0B65A")
        worker.request_stop()
        return {"ok": True, "message": "Остановка запрошена."}

    def engine_error(self, error):
        with self.lock:
            self._append_event_locked("Ошибка · " + str(error).replace("\n", " ")[:220])
            self._set_status_locked("●  ОШИБКА", "#FF6B78")

    def engine_finished(self, worker):
        with self.lock:
            if self.engine_worker is worker:
                self.session_running = False
                self.engine_worker = None
                self._set_status_locked("●  НЕ ЗАПУЩЕН", "#A9B2C3")

    def _set_status_locked(self, text, color="#A9B2C3"):
        self.status_text = text
        self.status_color = color

    def _append_event_locked(self, message):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.events.append(stamp + "  " + str(message))
        self.events = self.events[-100:]

    def _append_reward_locked(self, title, description, kind, created):
        self.rewards.append({
            "title": str(title)[:100],
            "description": str(description)[:140],
            "kind": str(kind),
            "timestamp": float(created),
        })
        self.rewards = self.rewards[-60:]

    def _append_event(self, message):
        with self.lock:
            self._append_event_locked(message)

    def on_log(self, message, level, created):
        raw = str(message)
        self._append_log(raw, level, created)
        safe = raw if "and enter this code:" in raw else self._redact(raw)
        self._handle_log(safe, level, created)

    def _handle_log(self, message, level, created):
        watch_status = re.match(r"WATCH_STATUS:\s*([a-zA-Z0-9_]{1,25})\|([a-z_]+)\|?(.*)", message)
        if watch_status:
            channel, state, detail = watch_status.groups()
            with self.lock:
                self.watch_status[channel.lower()] = {
                    "state": state,
                    "detail": detail.strip()[:220],
                    "updated_at": float(created),
                }
            return
        if message in ("Twitch session saved securely.", "Restored the saved Twitch session."):
            with self.lock:
                worker = self.auth_worker
                client = worker.client if worker is not None else None
                username = worker.username if worker is not None else self.username
                restored = bool(worker.restoring) if worker is not None else False
            if client is not None:
                try:
                    if client.twitch_login.get_auth_token():
                        self.auth_result(username, client, True, restored, "")
                except Exception:
                    logging.getLogger("twitch_farming.auth").exception("Could not confirm Twitch session from the completion event")
            return
        if message.startswith("DEVICE_AUTH_URL:"):
            with self.lock:
                self.auth_url = message.split(":", 1)[1].strip()
            return
        if "and enter this code:" in message:
            code = message.split("and enter this code:", 1)[1].strip().split()[0]
            with self.lock:
                changed = code != self.auth_code
                self.auth_code = code
                self.auth_status = "ОЖИДАЕТСЯ ПОДТВЕРЖДЕНИЕ В TWITCH"
                self._append_event_locked("Код входа готов · " + code)
                url = self.auth_url
                if "device-code=" not in url:
                    url = ACTIVATION_URL + "?public=true&device-code=" + quote(code)
                should_open = changed or not self.auth_opened
                self.auth_opened = True
            if should_open:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
            return
        if "Twitch authorization confirmed." in message:
            with self.lock:
                self._set_status_locked("●  ПОДГОТОВКА КАНАЛОВ", "#F0B65A")
            return
        progress = re.search(r"Loading channel (\d+)/(\d+): (.+)", message)
        if progress:
            current, total, channel = progress.groups()
            with self.lock:
                self._set_status_locked("●  ЗАГРУЗКА КАНАЛОВ " + current + "/" + total, "#F0B65A")
                self._append_event_locked("Загружаю канал · @" + channel)
            return
        if "Synchronizing Drops inventory and dashboard." in message:
            with self.lock:
                self._set_status_locked("●  СИНХРОНИЗАЦИЯ DROPS", "#F0B65A")
            return
        if "Monitoring started for " in message:
            with self.lock:
                self._set_status_locked("●  РАБОТАЕТ", "#42D6A4")
                self._append_event_locked(message)
            return
        if "No valid Twitch channels were loaded." in message:
            with self.lock:
                self._set_status_locked("●  НЕТ ДОСТУПНЫХ КАНАЛОВ", "#FF6B78")
                self._append_event_locked(message)
            return
        if "Join IRC Chat:" in message or "Leave IRC Chat:" in message:
            return
        stamp = datetime.fromtimestamp(created).strftime("%H:%M:%S")
        online = re.match(r"^([a-zA-Z0-9_]{1,25})(?:\s+\([^)]*\))?\s+is Online!$", message.strip(), re.IGNORECASE)
        offline = re.match(r"^([a-zA-Z0-9_]{1,25})(?:\s+\([^)]*\))?\s+is Offline!$", message.strip(), re.IGNORECASE)
        if online:
            name = online.group(1).lower()
            with self.lock:
                self.online_channels.add(name)
                profile = dict(self.channel_profiles.get(name, {}))
                profile.update(loaded=True, is_live=True)
                self.channel_profiles[name] = profile
                self._append_event_locked(name + " · В эфире")
                self._set_status_locked("●  РАБОТАЕТ", "#42D6A4")
            return
        if offline:
            name = offline.group(1).lower()
            with self.lock:
                self.online_channels.discard(name)
                profile = dict(self.channel_profiles.get(name, {}))
                profile.update(loaded=True, is_live=False)
                self.channel_profiles[name] = profile
                self._append_event_locked(name + " · Не в эфире")
            return
        gain = re.search(r"\+([\d,]+)\s*(?:→|-->)\s*(.*?)\s*-\s*Reason:\s*([A-Z_]+)", message)
        if gain:
            amount = int(gain.group(1).replace(",", ""))
            channel = gain.group(2).split(" (", 1)[0].strip()
            reason = gain.group(3)
            label = {"WATCH":"Просмотр","CLAIM":"Бонус","WATCH_STREAK":"Серия просмотров","RAID":"Рейд","PREDICTION":"Прогноз","REFUND":"Возврат"}.get(reason, reason)
            with self.lock:
                self.gained_total += amount
                self.event_total += 1
                self._append_event_locked("+" + str(amount) + "  ·  " + channel + "  ·  " + label)
                self._append_reward_locked(channel, "+" + str(amount) + " Channel Points", "gem", created)
                self.reward = {"text": "@" + channel + "  ·  +" + str(amount) + " очков  ·  " + label, "until": time.time() + 5}
            return
        if "Twitch Drop received:" in message:
            name = message.split("Twitch Drop received:", 1)[1].strip()[:100]
            with self.lock:
                self.event_total += 1
                self._append_event_locked("Twitch Drop · " + name)
                self._append_reward_locked(name, "Twitch Drop", "gift", created)
                self.reward = {"text": "Twitch Drop · " + name, "until": time.time() + 5}
            return
        if "Twitch Moment claimed:" in message:
            name = message.split("Twitch Moment claimed:", 1)[1].strip()[:100]
            with self.lock:
                self._append_event_locked("Момент · " + name)
                self._append_reward_locked(name, "Момент получен", "star", created)
                self.reward = {"text": "Момент · " + name, "until": time.time() + 5}
            return
        if "Joining raid from" in message:
            self._append_event("Рейд · " + message.split("Joining raid from", 1)[1].strip()[:140])
            return
        if "Place " in message and "channel points on:" in message:
            self._append_event("Прогноз · " + message[:150])
            return
        if level in ("ERROR", "CRITICAL"):
            self._append_event("Ошибка · " + message[:180])
        elif level == "WARNING" and any(word in message.lower() for word in ("failed", "unable", "invalid", "expired", "ошибка")):
            self._append_event("Предупреждение · " + message[:180])

    def _redact(self, value):
        text = str(value).replace("\r", " ").replace("\n", " ")
        pairs = [
            (r"""(?i)(["']?(?:access[_-]?token|refresh[_-]?token|auth[_-]?token|auth-token|client[_-]?secret|device[_-]?code|user[_-]?code|authorization|token)["']?\s*[:=]\s*["'])(.*?)(["'])""", r"\1[REDACTED]\3"),
            (r"(?i)([?&](?:token|sig|auth_token|access_token|refresh_token|dna)=)[^&\s]+", r"\1[REDACTED]"),
            (r"(?i)(\b(?:access[_-]?token|refresh[_-]?token|auth[_-]?token|auth-token|client[_-]?secret|device[_-]?code|user[_-]?code|token)=)[^&,\s]+", r"\1[REDACTED]"),
            (r"(?i)(enter this code:\s*)[A-Z0-9-]+", r"\1[REDACTED]"),
            (r"""(?i)(["']user_ip["']\s*:\s*["'])[^"']+(["'])""", r"\1[REDACTED]\2"),
            (r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]"),
            (r"(?i)(\bOAuth\s+)[A-Za-z0-9._~+/-]+=*", r"\1[REDACTED]"),
        ]
        for pattern, replacement in pairs:
            text = re.sub(pattern, replacement, text)
        text = re.sub(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", "[IP REDACTED]", text)
        return text[:1200]

    def _sanitize_existing_log(self):
        try:
            if not LOG_FILE.is_file():
                return
            with LOG_FILE.open("r+b") as file:
                file.seek(0, os.SEEK_END)
                size = file.tell()
                start = max(0, size - 262144)
                if start:
                    file.seek(start)
                    file.readline()
                    start = file.tell()
                file.seek(start)
                original = file.read().decode("utf-8", errors="replace")
                lines = original.splitlines()
                clean = "\n".join(self._redact(line) for line in lines)
                if original.endswith("\n"):
                    clean += "\n"
                if clean != original:
                    file.seek(start)
                    file.write(clean.encode("utf-8"))
                    file.truncate()
        except OSError:
            pass

    def _append_log(self, message, level, created):
        stamp = datetime.fromtimestamp(created).strftime("%d.%m.%Y %H:%M:%S")
        line = stamp + "  [" + str(level) + "]  [RUN " + self.run_id + "] " + self._redact(message)
        with self.lock:
            log_id = self.next_log_id
            self.next_log_id += 1
            self.log_records.append({"id": log_id, "line": line})
            self.log_records = self.log_records[-500:]
            self.pending_log_lines.append(line)

    def _flush_logs(self):
        with self.log_file_lock:
            with self.lock:
                if not self.pending_log_lines:
                    return
                data = "\n".join(self.pending_log_lines) + "\n"
                self.pending_log_lines.clear()
            try:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                with LOG_FILE.open("a", encoding="utf-8") as file:
                    file.write(data)
            except OSError:
                with self.lock:
                    self.pending_log_lines.insert(0, data.rstrip("\n"))

    def _log_writer_loop(self):
        while not self.log_stop.wait(0.4):
            self._flush_logs()

    def _refresh_channel_balances_locked(self):
        worker = self.engine_worker
        miner = getattr(worker, "miner", None) if worker is not None else None
        branding_changed = False
        if miner is not None:
            for streamer in list(getattr(miner, "streamers", []) or []):
                login = str(getattr(streamer, "username", "") or "").lower()
                if not login:
                    continue
                try:
                    points = max(0, int(getattr(streamer, "channel_points", 0) or 0))
                except (TypeError, ValueError):
                    points = 0
                profile = self.channel_profiles.get(login, {})
                points_name = str(getattr(streamer, "channel_points_name", "") or "").strip()
                points_icon_url = str(getattr(streamer, "channel_points_image", "") or "")
                self.channel_balances[login] = {
                    "login": login,
                    "points": points,
                    "display_name": str(profile.get("display_name") or login),
                    "avatar_url": str(profile.get("avatar_url") or ""),
                    "points_name": points_name,
                    "points_icon_url": points_icon_url,
                }
                if bool(getattr(streamer, "channel_points_branding_loaded", False)):
                    previous = self.channel_points_branding_cache.get(login, {})
                    entry = {
                        "name": points_name[:80],
                        "icon_url": points_icon_url if points_icon_url.startswith("https://") else "",
                        "points": points,
                    }
                    if entry != previous:
                        self.channel_points_branding_cache[login] = entry
                        branding_changed = True
        valid = set(self.channels)
        self.channel_balances = {
            login: item for login, item in self.channel_balances.items()
            if login in valid
        }
        if branding_changed:
            self._save_config()
        ranked = sorted(
            self.channel_balances.values(),
            key=lambda item: (int(item.get("points", 0)), item.get("login", "")),
            reverse=True,
        )
        balances = {
            login: int(item.get("points", 0))
            for login, item in self.channel_balances.items()
        }
        currency = {}
        for login in self.channels:
            cached = self.channel_points_branding_cache.get(login, {})
            currency[login] = {
                "name": str(cached.get("name") or ""),
                "icon_url": str(cached.get("icon_url") or ""),
            }
            if "points" in cached and login not in balances:
                try:
                    balances[login] = max(0, int(cached.get("points")))
                except (TypeError, ValueError):
                    pass
        for login, item in self.channel_balances.items():
            currency[login] = {
                "name": str(item.get("points_name") or currency.get(login, {}).get("name") or ""),
                "icon_url": str(item.get("points_icon_url") or currency.get(login, {}).get("icon_url") or ""),
            }
        return ranked[:3], balances, currency

    def get_state(self, after_log_id=0):
        try:
            after = int(after_log_id)
        except (TypeError, ValueError):
            after = 0
        with self.lock:
            profile_worker_alive = bool(self.profile_worker is not None and self.profile_worker.is_alive())
            if (
                self.authenticated
                and self.api_client is not None
                and self.channels
                and not profile_worker_alive
                and time.monotonic() - self.last_profile_refresh_at >= 45
            ):
                self.refresh_channel_profiles()
            top_channels, channel_balances, channel_currency = self._refresh_channel_balances_locked()
            logs = [dict(item) for item in self.log_records if item["id"] > after]
            return {
                "username": self.username,
                "channels": list(self.channels),
                "online_channels": sorted(self.online_channels),
                "watch_status": {key: dict(value) for key, value in self.watch_status.items()},
                "options": dict(self.options),
                "app_settings": dict(self.app_settings),
                "cache_stats": {
                    "avatars": sum(1 for item in self.channel_profiles.values() if item.get("avatar_url")) + int(bool(self.account_profile.get("avatar_url"))),
                    "currency_icons": sum(1 for item in self.channel_points_branding_cache.values() if item.get("icon_url")),
                },
                "bet_settings": dict(self.bet_settings),
                "custom_presets": dict(self.custom_presets),
                "selected_preset": self.selected_preset,
                "tutorial_done": self.tutorial_done,
                "account_profile": dict(self.account_profile),
                "channel_profiles": {key: dict(value) for key, value in self.channel_profiles.items()},
                "channel_balances": channel_balances,
                "channel_currency": channel_currency,
                "top_channels": top_channels,
                "authenticated": self.authenticated,
                "authenticated_username": self.authenticated_username,
                "auth_status": self.auth_status,
                "auth_code": self.auth_code,
                "auth_error": self.auth_error,
                "auth_worker_state": self.auth_worker_state,
                "session_running": self.session_running,
                "status_text": self.status_text,
                "status_color": self.status_color,
                "gained_total": self.gained_total,
                "event_total": self.event_total,
                "events": list(self.events[-100:]),
                "rewards": [dict(item) for item in self.rewards[-60:]],
                "reward": dict(self.reward) if self.reward else None,
                "logs": logs[-100:],
                "next_log_id": self.next_log_id,
                "drops_active_campaigns": len(self.drops_campaigns),
            }

    def get_log_snapshot(self):
        self._flush_logs()
        with self.lock:
            snapshot_id = self.next_log_id - 1
            fallback = [item["line"] for item in self.log_records[-500:]]
        try:
            with self.log_file_lock:
                if LOG_FILE.is_file():
                    with LOG_FILE.open("rb") as file:
                        file.seek(0, os.SEEK_END)
                        size = file.tell()
                        file.seek(max(0, size - 1000000))
                        content = file.read(1000000).decode("utf-8", errors="replace")
                    if size > 1000000:
                        content = content.split("\n", 1)[-1]
                    lines = content.splitlines()[-500:]
                else:
                    lines = fallback
        except OSError:
            lines = fallback
        return {"lines": lines, "last_log_id": snapshot_id}

    def clear_logs(self):
        try:
            with self.log_file_lock:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                with self.lock:
                    self.log_records.clear()
                    self.pending_log_lines.clear()
                    snapshot_id = self.next_log_id - 1
                LOG_FILE.write_text("", encoding="utf-8")
            return {"ok": True, "last_log_id": snapshot_id, "message": "Журнал очищен."}
        except OSError as error:
            return {"ok": False, "message": "Не удалось очистить журнал: " + str(error)[:180]}

    def open_app_folder(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(APP_DIR))
            return {"ok": True}
        except Exception as error:
            return {"ok": False, "message": str(error)}

    def open_url(self, url):
        value = str(url or "")
        if value.startswith(("https://", "http://")):
            webbrowser.open(value)
            return {"ok": True}
        return {"ok": False}


    def _schedule_window_action(self, action, name):
        window = self.window
        if window is None:
            return {"ok": False, "message": "Окно ещё не готово."}

        def run():
            try:
                getattr(window, action)()
            except Exception:
                logging.getLogger("twitch_farming.window").exception("Window action failed: %s", action)

        threading.Thread(target=run, name=name, daemon=True).start()
        return {"ok": True}

    def minimize_window(self):
        return self._schedule_window_action("minimize", "WindowMinimize")

    def toggle_maximize_window(self):
        with self.lock:
            self.window_maximized = not self.window_maximized
            maximized = self.window_maximized
        result = self._schedule_window_action("maximize" if maximized else "restore", "WindowMaximize")
        result["maximized"] = maximized
        return result

    def _ensure_tray_icon(self):
        if self.tray_icon is not None:
            return
        import pystray
        from PIL import Image
        image = Image.open(ICON_FILE).convert("RGBA") if ICON_FILE.is_file() else Image.new("RGBA", (64, 64), "#7439c3")
        self.tray_icon = pystray.Icon(
            "Twitch Farming",
            image,
            "Twitch Farming",
            menu=pystray.Menu(
                pystray.MenuItem("Открыть Twitch Farming", self._tray_show, default=True),
                pystray.MenuItem("Выход", self._tray_exit),
            ),
        )
        self.tray_icon.run_detached()

    def _tray_show(self, icon=None, item=None):
        self._schedule_window_action("show", "WindowTrayShow")

    def _tray_exit(self, icon=None, item=None):
        self.exit_app()

    def exit_app(self):
        icon = self.tray_icon
        self.tray_icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
        return self._schedule_window_action("destroy", "WindowExit")

    def close_window(self):
        with self.lock:
            minimize_to_tray = bool(self.app_settings.get("minimize_to_tray"))
        if minimize_to_tray:
            try:
                self._ensure_tray_icon()
                return self._schedule_window_action("hide", "WindowHideToTray")
            except Exception as error:
                logging.getLogger("twitch_farming.window").exception("Could not create tray icon")
                return {"ok": False, "message": "Не удалось свернуть в трей: " + (str(error) or error.__class__.__name__)[:180]}
        return self._schedule_window_action("destroy", "WindowClose")

    def sync_window_maximized(self, maximized):
        self.window_maximized = bool(maximized)
        try:
            self.window.evaluate_js("window.setWindowMaximized(" + ("true" if self.window_maximized else "false") + ")")
        except Exception:
            pass

    def start_startup_worker(self):
        with self.lock:
            if self.startup_worker is not None:
                return
            self.restore_started = True
            self.startup_worker = threading.Thread(target=self._startup_tasks, name="StartupTasks", daemon=True)
            worker = self.startup_worker
        try:
            worker.start()
        except Exception as error:
            with self.lock:
                self.startup_error = str(error) or error.__class__.__name__
                self.startup_phase = "Не удалось запустить подготовку"
                self.startup_complete = True
            logging.getLogger("twitch_farming.startup").exception("Could not start startup worker")

    def on_loaded(self, *args):
        self._mark_webview_checkpoint("webview_loaded_at", "WebView2: DOM загружен")
        self.start_startup_worker()

    def on_closing(self):
        self.stop_farm()
        with self.lock:
            worker = self.auth_worker
        if worker is not None and worker.is_alive() and worker.client is not None:
            try:
                worker.client.twitch_login.cancel()
            except Exception:
                pass
        self.log_stop.set()
        self._flush_logs()
        icon = self.tray_icon
        self.tray_icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass

    def shutdown(self):
        self.on_closing()
        for worker in (self.auth_worker, self.engine_worker, self.profile_worker, self.followed_channels_worker, self.drops_worker):
            if worker is not None and worker.is_alive():
                worker.join(timeout=5)
        self._flush_logs()
        try:
            self.root_logger.removeHandler(self.log_handler)
        except Exception:
            pass
        if self.api_client is not None:
            try:
                self.api_client.twitch_login.session.close()
            except Exception:
                pass


def main():
    webview.settings['DRAG_REGION_DIRECT_TARGET_ONLY'] = True
    api = TwitchFarmingApi()
    window = webview.create_window(
        "Twitch Farming",
        url=str(UI_DIR / "index.html"),
        js_api=api,
        width=1180,
        height=800,
        min_size=(960, 660),
        resizable=True,
        frameless=True,
        transparent=False,
        easy_drag=False,
        shadow=True,
        background_color="#0B0B12",
        text_select=True,
    )
    api.window = window

    window.events.before_show += lambda *args: api.on_webview_before_show()
    window.events.before_load += lambda *args: api.on_webview_before_load()
    window.events.shown += lambda *args: api.on_webview_shown()
    window.events.loaded += lambda *args: api.on_loaded()
    window.events.closing += api.on_closing
    window.events.maximized += lambda: api.sync_window_maximized(True)
    window.events.restored += lambda: api.sync_window_maximized(False)
    api.start_startup_worker()
    threading.Thread(target=api.watch_webview_startup, name="WebViewStartupWatchdog", daemon=True).start()
    WEBVIEW_DIR.mkdir(parents=True, exist_ok=True)
    logging.getLogger("twitch_farming.startup").info("[STARTUP] Запуск WebView2. Профиль браузера: %s", WEBVIEW_DIR)
    api.webview_start_requested_at = time.monotonic()
    try:
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(WEBVIEW_DIR), icon=str(ICON_FILE) if ICON_FILE.is_file() else None, debug=False)
    except Exception as error:
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "Не удалось запустить WebView2. Установи Microsoft Edge WebView2 Runtime и запусти приложение снова.\n\n" + str(error)[:700],
                    "Twitch Farming",
                    0x10,
                )
                webbrowser.open("https://developer.microsoft.com/en-us/microsoft-edge/webview2/")
            except Exception:
                pass
        raise
    finally:
        api.shutdown()


if __name__ == "__main__":
    main()
