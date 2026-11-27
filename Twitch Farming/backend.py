import faulthandler
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "engine"))


APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Twitch Farming"
CONFIG_FILE = APP_DIR / "config.json"
LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "TwitchFarming.log"
ACTIVATION_URL = "https://www.twitch.tv/activate"
try:
    from version_info import APP_VERSION
except ImportError:
    APP_VERSION = "1.0.0-dev"
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ICON_FILE = RESOURCE_DIR / "assets" / "twitch.ico"
BOOT_LOG_LOCK = threading.Lock()


def stream_uptime_seconds(value):
    try:
        started = datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
        return max(0, int(time.time() - started))
    except (TypeError, ValueError, OverflowError):
        return 0


def select_drops_streamers(followed_streamers, catalog_streamers, limit=3):
    try:
        maximum = max(0, int(limit))
    except (TypeError, ValueError):
        maximum = 3
    if maximum == 0:
        return []
    selected = []
    seen = set()
    for source in (followed_streamers, catalog_streamers):
        if not isinstance(source, (list, tuple)):
            continue
        for item in source:
            if not isinstance(item, dict):
                continue
            login = str(item.get("login") or "").strip().lower()
            if not login or login in seen:
                continue
            seen.add(login)
            selected.append(dict(item))
            if len(selected) >= maximum:
                return selected
    return selected


def write_boot_log(level, message):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        line = f"{stamp}  [{str(level).upper()}]  [RUN {os.getpid()}]  [PYTHON STARTUP] {str(message)}\n"
        with BOOT_LOG_LOCK:
            with LOG_FILE.open("a", encoding="utf-8") as file:
                file.write(line)
    except BaseException:
        pass


_ORIGINAL_SYS_EXCEPTHOOK = sys.excepthook
_ORIGINAL_THREAD_EXCEPTHOOK = threading.excepthook
_ORIGINAL_UNRAISABLEHOOK = sys.unraisablehook
_CRASH_LOG_LOCK = threading.RLock()
_CRASH_LOG_HANDLE = None
CRASH_LOG_FILE = APP_DIR / "crash.log"


def write_crash_log(level, message):
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    payload = f"{stamp}  [{str(level).upper()}]  [PID {os.getpid()}]  {str(message).rstrip()}\n"
    try:
        with _CRASH_LOG_LOCK:
            if _CRASH_LOG_HANDLE is not None:
                _CRASH_LOG_HANDLE.write(payload.encode("utf-8", errors="replace"))
                _CRASH_LOG_HANDLE.flush()
            else:
                APP_DIR.mkdir(parents=True, exist_ok=True)
                with CRASH_LOG_FILE.open("a", encoding="utf-8") as file:
                    file.write(payload)
                    file.flush()
    except BaseException:
        pass


def _startup_sys_excepthook(exc_type, exc_value, exc_traceback):
    details = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)).rstrip()
    write_crash_log("FATAL", "Необработанное исключение главного потока\n" + details)
    write_boot_log("ERROR", details)
    try:
        _ORIGINAL_SYS_EXCEPTHOOK(exc_type, exc_value, exc_traceback)
    except BaseException:
        pass


def _startup_thread_excepthook(args):
    details = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)).rstrip()
    write_crash_log("THREAD", "Необработанное исключение потока " + str(args.thread.name if args.thread else "unknown") + "\n" + details)
    write_boot_log("ERROR", "THREAD " + str(args.thread.name if args.thread else "unknown") + "\n" + details)
    try:
        _ORIGINAL_THREAD_EXCEPTHOOK(args)
    except BaseException:
        pass


def _startup_unraisablehook(args):
    details = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)).rstrip()
    write_crash_log("UNRAISABLE", str(args.err_msg or "Необрабатываемое исключение") + "\n" + details)
    try:
        _ORIGINAL_UNRAISABLEHOOK(args)
    except BaseException:
        pass


def install_crash_handlers():
    global _CRASH_LOG_HANDLE
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        if _CRASH_LOG_HANDLE is None:
            _CRASH_LOG_HANDLE = CRASH_LOG_FILE.open("ab", buffering=0)
        faulthandler.enable(file=_CRASH_LOG_HANDLE, all_threads=True)
    except BaseException as error:
        write_boot_log("ERROR", "Не удалось включить faulthandler: " + str(error))
    sys.excepthook = _startup_sys_excepthook
    threading.excepthook = _startup_thread_excepthook
    sys.unraisablehook = _startup_unraisablehook


install_crash_handlers()


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
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
            self.app.on_log(message, record.levelname, record.created)
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
                self.app.startup_note("INFO", "Проверка сессии: загрузка модулей Twitch")
            from TwitchChannelPointsMiner.classes.Twitch import Twitch
            from TwitchChannelPointsMiner.utils import get_user_agent
            if self.restoring:
                self.app.startup_note("INFO", "Модули Twitch загружены за %.2f сек." % (time.monotonic() - import_started))
            client_started = time.monotonic()
            self.client = Twitch(self.username, get_user_agent("CHROME"))
            if self.restoring:
                self.app.startup_note("INFO", "Клиент Twitch создан за %.2f сек." % (time.monotonic() - client_started))
                self.app.startup_note("INFO", "Проверка сохранённой сессии Twitch начата")
                restore_started = time.monotonic()
                success = bool(self.client.restore_session())
                self.app.startup_note("INFO", "Проверка сессии завершена за %.2f сек. Результат: %s." % (time.monotonic() - restore_started, "успех" if success else "неудача"))
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
            if self.restoring:
                self.app.startup_note("ERROR", "Ошибка проверки сохранённой сессии: " + str(error) + "\n" + traceback.format_exc())
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
        self.stop_event = threading.Event()
        self.miner_lock = threading.RLock()

    def add_streamer(self, login, channel_id=None):
        deadline = time.monotonic() + 35
        while self.is_alive() and not self.stop_event.is_set():
            miner = self.miner
            if miner is not None:
                try:
                    ready = bool(miner.running and miner.twitch.twitch_login.get_auth_token() and miner.streamer_topics_initialized)
                except Exception:
                    ready = False
                if ready:
                    return miner.add_streamer(login, channel_id)
            if time.monotonic() >= deadline:
                break
            time.sleep(0.15)
        return {"ok": False, "message": "Фарм ещё подключается к Twitch. Повтори добавление через несколько секунд."}

    def remove_streamer(self, login):
        deadline = time.monotonic() + 35
        while self.is_alive() and not self.stop_event.is_set():
            miner = self.miner
            if miner is not None:
                try:
                    ready = bool(miner.running and miner.streamer_topics_initialized)
                except Exception:
                    ready = False
                if ready:
                    return miner.remove_streamer(login)
            if time.monotonic() >= deadline:
                break
            time.sleep(0.15)
        return {"ok": False, "message": "Майнер ещё запускается или уже останавливается."}

    def request_stop(self):
        self.stop_event.set()
        with self.miner_lock:
            miner = self.miner
        if miner is not None:
            try:
                miner.request_stop()
            except Exception:
                logging.getLogger("twitch_farming.engine").exception("Could not stop the active miner")

    def run(self):
        miner = None
        session_client = None
        try:
            from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner
            from TwitchChannelPointsMiner.classes.Twitch import Twitch
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
            if self.stop_event.is_set():
                return
            session_client = Twitch(self.username, self.client.user_agent)
            if self.stop_event.is_set():
                try:
                    session_client.twitch_login.session.close()
                except Exception:
                    pass
                return
            miner = TwitchChannelPointsMiner(
                username=self.username,
                claim_drops_startup=False,
                enable_analytics=False,
                priority=[Priority.ORDER],
                logger_settings=LoggerSettings(save=False, less=True, console_level=logging.CRITICAL, file_level=logging.CRITICAL, emoji=False, colored=False),
                streamer_settings=settings,
                twitch_client=session_client,
                check_for_updates=False,
            )
            with self.miner_lock:
                self.miner = miner
                stop_requested = self.stop_event.is_set()
            if stop_requested:
                miner.request_stop()
                return
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
            if self.stop_event.is_set():
                miner.request_stop()
                return
            miner.mine(streamers=prepared_streamers, followers=False)
        except BaseException as error:
            details = traceback.format_exc()
            write_crash_log("ENGINE", "Ошибка EngineWorker: " + str(error) + "\n" + details)
            logging.getLogger("twitch_farming.engine").exception("Engine worker failed")
            self.app.enqueue_ui_state("engine_error", message=str(error) or error.__class__.__name__)
        finally:
            if miner is not None:
                try:
                    miner.stop()
                except BaseException as error:
                    details = traceback.format_exc()
                    write_crash_log("ENGINE_STOP", "Ошибка остановки майнера: " + str(error) + "\n" + details)
                    self.app.enqueue_ui_state("engine_error", message=str(error))
            if session_client is not None:
                try:
                    session_client.twitch_login.session.close()
                except Exception:
                    logging.getLogger("twitch_farming.engine").exception("Could not close the farming Twitch session")
            with self.miner_lock:
                self.miner = None
            self.app.enqueue_ui_state("engine_finished", worker=self)


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
                        "started_at": str(stream.get("started_at") or ""),
                        "uptime_seconds": stream_uptime_seconds(stream.get("started_at")),
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
        catalog_streamers = {}
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
                configured_logins = list(dict.fromkeys(
                    str(login).strip().lower()
                    for login in self.app.channels
                    if str(login).strip()
                ))
            live_channels_by_login = {
                str(item.get("login") or "").lower(): dict(item)
                for item in followed
                if item.get("is_live") and item.get("channel_id") and item.get("login")
            }
            missing_configured = [
                login for login in configured_logins
                if login not in live_channels_by_login
            ]
            for offset in range(0, len(missing_configured), 100):
                batch = missing_configured[offset:offset + 100]
                try:
                    streams = self.client.helix_get(
                        "streams",
                        [("user_login", login) for login in batch],
                    )
                    for stream in streams:
                        if not isinstance(stream, dict):
                            continue
                        login = str(stream.get("user_login") or "").strip().lower()
                        channel_id = str(stream.get("user_id") or "")
                        if not login or not channel_id:
                            continue
                        live_channels_by_login[login] = {
                            "login": login,
                            "channel_id": channel_id,
                            "display_name": str(stream.get("user_name") or login),
                            "avatar_url": "",
                            "game_name": str(stream.get("game_name") or ""),
                            "game_id": str(stream.get("game_id") or ""),
                            "is_live": True,
                            "viewer_count": max(0, int(stream.get("viewer_count") or 0)),
                            "started_at": str(stream.get("started_at") or ""),
                            "uptime_seconds": stream_uptime_seconds(stream.get("started_at")),
                        }
                except Exception as stream_error:
                    logging.getLogger("twitch_farming.drops").warning(
                        "Не удалось получить эфиры каналов из списка фарма: %s",
                        stream_error,
                    )
            live_channels = list(live_channels_by_login.values())
            available_campaigns_by_login = (
                self.client.get_drop_campaigns_for_channels(live_channels)
                if live_channels else {}
            )
            campaign_ids_by_login = {
                login: [
                    str(campaign["id"])
                    for campaign in campaigns
                    if isinstance(campaign, dict) and campaign.get("id")
                ]
                for login, campaigns in available_campaigns_by_login.items()
            }
            discovered_campaign_ids = {
                campaign_id
                for campaign_ids in campaign_ids_by_login.values()
                for campaign_id in campaign_ids
            }
            logging.getLogger("twitch_farming.drops").info(
                "AvailableDrops: найдено %s кампаний на %s живых каналах.",
                len(discovered_campaign_ids),
                len(live_channels),
            )
            raw_campaigns = self.client.get_drop_inventory_snapshot(
                available_campaigns_by_login
            )
            campaigns_by_id = {}
            catalog_targets = []
            followed_logins = {str(item.get("login") or "").lower() for item in followed}
            for raw_campaign in raw_campaigns if isinstance(raw_campaigns, list) else []:
                if not isinstance(raw_campaign, dict):
                    continue
                campaign = dict(raw_campaign)
                campaign_id = str(campaign.get("id") or "")
                if not campaign_id:
                    continue
                game = campaign.get("game")
                game_name = str(
                    (game.get("displayName") or game.get("name") or "")
                    if isinstance(game, dict) else (game or campaign.get("gameName") or campaign.get("gameDisplayName") or "")
                ).strip()
                if not game_name:
                    game_name = "Twitch"
                campaign["game"] = game_name
                campaign_status = str(campaign.get("status") or "").strip().upper()
                if campaign_status in ("EXPIRED", "ENDED", "COMPLETED", "CANCELLED", "CANCELED", "INACTIVE", "SCHEDULED"):
                    continue
                paid_types = ("SUBSCRIPTION", "SUB", "SUBSGOAL", "SUBS_GOAL", "SUBSCRIPTION_GOAL")
                image_keys = ("image_url", "imageURL", "imageUrl", "imageAssetURL", "boxArtURL", "boxArtUrl", "url")
                normalized_drops = []
                raw_drops = campaign.get("drops", [])
                for raw_drop in raw_drops if isinstance(raw_drops, list) else []:
                    if not isinstance(raw_drop, dict):
                        continue
                    source_type = str(raw_drop.get("source_type") or raw_drop.get("drop_type") or raw_drop.get("dropType") or raw_drop.get("type") or "").strip().upper()
                    category = str(raw_drop.get("category") or "").strip().upper()
                    benefit_types = raw_drop.get("benefit_types") or [raw_drop.get("benefit_type") or ""]
                    if not isinstance(benefit_types, (list, tuple, set)):
                        benefit_types = [benefit_types]
                    paid_fields = any(
                        raw_drop.get(key) not in (None, "", False, 0)
                        for key in ("requiredSubscriptionTier", "subscriptionTier", "subsGoal", "subs_goal", "subscriptionGoal", "subscription_goal")
                    )
                    paid_flags = any(
                        str(raw_drop.get(key) or "").strip().lower() in ("1", "true", "yes", "on")
                        for key in ("paid", "is_subscription", "isSubscription")
                    )
                    paid_benefit = any(str(value or "").strip().upper() in paid_types for value in benefit_types)
                    if source_type in paid_types or category in ("SUBSCRIPTION", "PAID", "SUBSCRIBER") or paid_fields or paid_flags or paid_benefit:
                        continue
                    if "is_time_based" in raw_drop and not raw_drop.get("is_time_based"):
                        continue
                    normalized_drop = dict(raw_drop)
                    image_url = ""
                    for key in image_keys:
                        candidate = str(raw_drop.get(key) or "").strip()
                        if candidate.startswith("https://"):
                            image_url = candidate
                            break
                    normalized_drop["image_url"] = image_url
                    normalized_drops.append(normalized_drop)
                if not normalized_drops:
                    continue
                campaign["drops"] = normalized_drops
                allowed = set(str(value) for value in campaign.get("allowed_channel_ids") or [])
                game_id = str(campaign.get("game_id") or "")
                candidates = []
                for streamer in live_channels:
                    login = str(streamer.get("login") or "").lower()
                    channel_id = str(streamer.get("channel_id") or "")
                    streamer_game_id = str(streamer.get("game_id") or "")
                    if campaign_id not in set(str(value) for value in campaign_ids_by_login.get(login, [])):
                        continue
                    if game_id and streamer_game_id and game_id != streamer_game_id:
                        continue
                    if allowed and channel_id not in allowed:
                        continue
                    candidates.append({
                        "login": login,
                        "channel_id": channel_id,
                        "display_name": str(streamer.get("display_name") or login),
                        "avatar_url": str(streamer.get("avatar_url") or ""),
                        "viewer_count": max(0, int(streamer.get("viewer_count") or 0)),
                        "started_at": str(streamer.get("started_at") or ""),
                        "uptime_seconds": stream_uptime_seconds(streamer.get("started_at")),
                        "game_name": str(streamer.get("game_name") or ""),
                        "is_followed": True,
                    })
                candidates.sort(key=lambda item: (item["uptime_seconds"], item["viewer_count"]), reverse=True)
                candidates = candidates[:3]
                item = dict(campaign)
                item["watch_drops"] = list(normalized_drops)
                item["streamers"] = candidates
                item["streamer_source"] = "followers" if candidates else ""
                campaigns_by_id[campaign_id] = item
                game_slug = str(campaign.get("game_slug") or "").strip().lower()
                if len(candidates) < 3 and (game_id or game_slug):
                    catalog_targets.append(item)
            game_info_cache = {}
            for campaign in campaigns_by_id.values():
                game_id = str(campaign.get("game_id") or "")
                game_name = str(campaign.get("game") or "")
                cache_key = "id:" + game_id if game_id else "name:" + game_name.casefold()
                if cache_key not in game_info_cache:
                    try:
                        game_info_cache[cache_key] = self.client.get_game_info(game_id, game_name)
                    except Exception as game_error:
                        game_info_cache[cache_key] = {}
                        logging.getLogger("twitch_farming.drops").warning(
                            "Не удалось уточнить категорию Drops game_id=%s game=%s: %s",
                            game_id,
                            game_name,
                            game_error,
                        )
                game_info = game_info_cache.get(cache_key) or {}
                resolved_id = str(game_info.get("id") or "")
                resolved_slug = str(game_info.get("slug") or "").strip().lower()
                if resolved_id:
                    campaign["game_id"] = resolved_id
                if resolved_slug:
                    campaign["game_slug"] = resolved_slug
                if not campaign.get("game_id"):
                    logging.getLogger("twitch_farming.drops").warning(
                        "Не удалось определить game_id для Drops кампании %s (%s); поиск продолжится по slug=%s.",
                        campaign.get("id"),
                        game_name,
                        campaign.get("game_slug") or "",
                    )
            streams_by_game = {}
            campaign_stream_keys = {}
            game_search_stats = {}
            candidate_rows = {}
            for campaign in catalog_targets:
                game_id = str(campaign.get("game_id") or "")
                game_slug = str(campaign.get("game_slug") or "").strip().lower()
                stream_key = game_id or ("slug:" + game_slug if game_slug else "")
                if not stream_key:
                    continue
                campaign_stream_keys[str(campaign.get("id") or "")] = stream_key
                if stream_key not in streams_by_game:
                    streams_by_login = {}
                    helix_count = 0
                    directory_count = 0
                    if game_id:
                        helix_params = {"game_id": game_id, "first": 100}
                        try:
                            for page in range(5):
                                payload = self.client.helix_get_payload("streams", helix_params)
                                page_streams = payload.get("data", []) if isinstance(payload, dict) else []
                                for stream in page_streams if isinstance(page_streams, list) else []:
                                    if not isinstance(stream, dict):
                                        continue
                                    login = str(stream.get("user_login") or "").strip().lower()
                                    if login:
                                        streams_by_login[login] = dict(stream)
                                cursor = str((payload.get("pagination") or {}).get("cursor") or "") if isinstance(payload, dict) else ""
                                if not cursor:
                                    break
                                helix_params["after"] = cursor
                            helix_count = len(streams_by_login)
                        except Exception as catalog_error:
                            logging.getLogger("twitch_farming.drops").warning(
                                "Не удалось получить страницы Twitch Helix для game_id=%s: %s",
                                game_id,
                                catalog_error,
                            )
                    if game_slug:
                        try:
                            directory_streams = self.client.get_drops_streams_for_game(
                                game_slug,
                                game_id,
                                limit=30,
                            )
                            for stream in directory_streams:
                                if not isinstance(stream, dict):
                                    continue
                                login = str(stream.get("user_login") or "").strip().lower()
                                if not login:
                                    continue
                                directory_count += 1
                                merged_stream = dict(streams_by_login.get(login) or {})
                                merged_stream.update({
                                    key: value
                                    for key, value in stream.items()
                                    if value not in (None, "", [], {})
                                })
                                streams_by_login[login] = merged_stream
                        except Exception as catalog_error:
                            logging.getLogger("twitch_farming.drops").warning(
                                "Не удалось получить Twitch GameDirectory для game=%s (%s): %s",
                                campaign.get("game") or game_slug,
                                game_id,
                                catalog_error,
                            )
                    streams_by_game[stream_key] = sorted(
                        list(streams_by_login.values()),
                        key=lambda stream: (
                            max(0, int(stream.get("viewer_count") or 0)),
                            stream_uptime_seconds(stream.get("started_at")),
                        ),
                        reverse=True,
                    )
                    game_search_stats[stream_key] = {
                        "helix": helix_count,
                        "directory": directory_count,
                        "combined": len(streams_by_login),
                    }
                for stream in streams_by_game[stream_key]:
                    login = str(stream.get("user_login") or "").strip().lower()
                    channel_id = str(stream.get("user_id") or "")
                    if not login or not channel_id:
                        continue
                    candidate_rows[login] = {
                        "login": login,
                        "channel_id": channel_id,
                        "viewer_count": max(0, int(stream.get("viewer_count") or 0)),
                        "started_at": str(stream.get("started_at") or ""),
                        "uptime_seconds": stream_uptime_seconds(stream.get("started_at")),
                        "game_name": str(stream.get("game_name") or campaign.get("game") or ""),
                        "game_id": str(stream.get("game_id") or game_id),
                        "is_live": True,
                        "is_followed": login in followed_logins,
                        "drops_enabled": bool(stream.get("drops_enabled")),
                    }
            campaign_lookup_rows = [
                item for item in candidate_rows.values()
                if not item.get("drops_enabled")
            ]
            catalog_ids_by_login = (
                self.client.get_drop_campaign_ids_for_channels(campaign_lookup_rows)
                if campaign_lookup_rows else {}
            )
            matched_logins = set()
            campaign_rows = {}
            for campaign in catalog_targets:
                campaign_id = str(campaign.get("id") or "")
                game_id = str(campaign.get("game_id") or "")
                stream_key = campaign_stream_keys.get(campaign_id, game_id)
                allowed = set(str(value) for value in campaign.get("allowed_channel_ids") or [])
                matched = []
                for stream in streams_by_game.get(stream_key, []):
                    login = str(stream.get("user_login") or "").strip().lower()
                    row = candidate_rows.get(login)
                    if row is None:
                        continue
                    if (
                        campaign_id not in set(str(value) for value in catalog_ids_by_login.get(login, []))
                        and not row.get("drops_enabled")
                    ):
                        continue
                    if allowed and str(row.get("channel_id") or "") not in allowed:
                        continue
                    matched.append(row)
                    matched_logins.add(login)
                    if len(matched) >= 3:
                        break
                campaign_rows[campaign_id] = matched
                stats = game_search_stats.get(stream_key, {})
                logging.getLogger("twitch_farming.drops").info(
                    "Drops stream search campaign=%s game=%s game_id=%s slug=%s: Helix=%s, DROPS_ENABLED=%s, merged=%s, matched=%s.",
                    campaign_id,
                    campaign.get("game") or "",
                    game_id,
                    campaign.get("game_slug") or "",
                    stats.get("helix", 0),
                    stats.get("directory", 0),
                    stats.get("combined", 0),
                    len(matched),
                )
            users_by_id = {}
            matched_rows = [candidate_rows[login] for login in matched_logins if login in candidate_rows]
            for offset in range(0, len(matched_rows), 100):
                batch = matched_rows[offset:offset + 100]
                try:
                    users = self.client.helix_get("users", [("id", str(item["channel_id"])) for item in batch])
                    for user in users:
                        if isinstance(user, dict):
                            users_by_id[str(user.get("id") or "")] = user
                except Exception as catalog_error:
                    logging.getLogger("twitch_farming.drops").warning(
                        "Не удалось загрузить профили каталога Drops: %s",
                        catalog_error,
                    )
            for campaign in catalog_targets:
                campaign_id = str(campaign.get("id") or "")
                suggestions = []
                for row in campaign_rows.get(campaign_id, []):
                    user = users_by_id.get(str(row.get("channel_id") or ""), {})
                    login = str(row.get("login") or "").lower()
                    avatar = str(user.get("profile_image_url") or "")
                    profile = {
                        "login": login,
                        "channel_id": str(row.get("channel_id") or ""),
                        "display_name": str(user.get("display_name") or login),
                        "avatar_url": avatar if avatar.startswith("https://") else "",
                        "viewer_count": max(0, int(row.get("viewer_count") or 0)),
                        "started_at": str(row.get("started_at") or ""),
                        "uptime_seconds": max(0, int(row.get("uptime_seconds") or 0)),
                        "game_name": str(row.get("game_name") or ""),
                        "game_id": str(row.get("game_id") or ""),
                        "is_live": True,
                        "is_followed": bool(row.get("is_followed")),
                    }
                    catalog_streamers[login] = profile
                    suggestions.append(profile)
                followed_suggestions = [
                    item for item in campaign.get("streamers", [])
                    if isinstance(item, dict) and item.get("login")
                ]
                campaign["streamers"] = select_drops_streamers(
                    followed_suggestions,
                    suggestions,
                    3,
                )
                campaign["streamer_source"] = "followers+catalog" if campaign.get("streamers") and suggestions else ("followers" if campaign.get("streamers") else "")
            campaigns = list(campaigns_by_id.values())
        except Exception as exception:
            error = str(exception)[:500] or exception.__class__.__name__
        self.app.finish_drops_worker(
            self,
            self.request_id,
            campaigns,
            error,
            followers_error,
            catalog_streamers,
        )


def _drops_reward_received(drop):
    if not isinstance(drop, dict):
        return False
    for key in ("claimed", "is_claimed", "isClaimed", "isRewardClaimed", "rewardClaimed"):
        value = drop.get(key)
        if isinstance(value, str):
            if value.strip().lower() in ("1", "true", "yes", "on", "claimed", "success", "completed"):
                return True
        elif bool(value):
            return True
    for key in ("claimedAt", "claimed_at", "claimedDate", "claimed_date", "claimDate", "claim_date"):
        if str(drop.get(key) or "").strip().lower() not in ("", "0", "false", "none", "null"):
            return True
    status = str(drop.get("status") or drop.get("claimStatus") or drop.get("claim_status") or "").strip().lower()
    if status in ("claimed", "received", "success", "completed", "already_claimed"):
        return True
    try:
        percentage = drop.get("percentage")
        if percentage is not None and float(percentage) >= 100:
            return True
    except (TypeError, ValueError):
        pass
    watched = drop.get("watched_minutes", drop.get("current_minutes", drop.get("currentMinutes", 0)))
    required = drop.get("required_minutes", drop.get("requiredMinutes", drop.get("duration_minutes", 0)))
    try:
        return int(float(required or 0)) > 0 and float(watched or 0) >= float(required or 0)
    except (TypeError, ValueError):
        return False


def _drops_campaign_completed(campaign):
    if not isinstance(campaign, dict):
        return False
    drops = campaign.get("drops") or campaign.get("timeBasedDrops") or []
    if isinstance(drops, dict):
        drops = drops.get("nodes") or drops.get("edges") or []
        drops = [item.get("node", item) if isinstance(item, dict) else item for item in drops]
    drops = [item for item in drops if isinstance(item, dict)]
    return bool(drops) and all(_drops_reward_received(item) for item in drops)


class DropsFocusWorker(threading.Thread):
    def __init__(self, app):
        super().__init__(name="TwitchDropsCampaignFocus", daemon=True)
        self.app = app
        self.stop_event = threading.Event()

    def run(self):
        next_inventory_refresh = 0.0
        missing_refresh_at = 0.0
        missing_refresh_count = 0
        last_inventory_update = 0.0
        while not self.stop_event.is_set():
            with self.app.lock:
                campaign_id = str(self.app.drops_focus_campaign_id or "")
                current_login = str(self.app.drops_focus_login or "")
                campaign = next(
                    (
                        dict(item) for item in self.app.drops_campaigns
                        if str(item.get("id") or "") == campaign_id
                    ),
                    None,
                ) if campaign_id else None
                updated_at = float(self.app.drops_updated_at or 0)
                drops_loading = bool(self.app.drops_loading)
            if not campaign_id:
                break
            self.app._sync_drops_focus_slot(campaign_id, current_login)
            if campaign is None:
                if updated_at > last_inventory_update and not drops_loading:
                    last_inventory_update = updated_at
                    missing_refresh_count += 1
                if missing_refresh_count >= 2:
                    self.app._finish_drops_campaign_focus(campaign_id, "Кампания больше не активна")
                    break
            else:
                missing_refresh_count = 0
                if _drops_campaign_completed(campaign):
                    self.app._finish_drops_campaign_focus(campaign_id, "Все награды получены")
                    break
                if updated_at > last_inventory_update:
                    last_inventory_update = updated_at
            now = time.monotonic()
            if now >= next_inventory_refresh and not drops_loading:
                self.app.refresh_drops_inventory(True)
                next_inventory_refresh = now + 90
            if self.stop_event.is_set():
                break
            if current_login and campaign is not None:
                valid = self.app._validate_drops_focus_channel(campaign, current_login)
                if valid is True:
                    self.app._set_drops_focus_status(campaign_id, "Фармится")
                    self.stop_event.wait(20)
                    continue
                if valid is False:
                    self.app._remove_drops_focus_channel(campaign_id, stop_if_empty=False)
                    current_login = ""
                if valid is None:
                    self.stop_event.wait(10)
                    continue
            if self.stop_event.is_set():
                break
            if campaign is not None:
                candidate = self.app._find_drops_focus_candidate(campaign)
                if self.stop_event.is_set():
                    break
                if candidate:
                    result = self.app._assign_drops_focus_channel(campaign_id, candidate)
                    if result.get("ok"):
                        self.stop_event.wait(15)
                        continue
            self.app._set_drops_focus_status(campaign_id, "Ищем живого стримера с Drops…")
            self.stop_event.wait(20)




class TwitchFarmingApi:
    def __init__(self):
        self.lock = threading.RLock()
        self.ui_state_queue = queue.SimpleQueue()
        self.username = ""
        self.channels = []
        self.temporary_channels = {}
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
        self.startup_events = []
        self.startup_event_id = 0
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
        self.drops_catalog_streamers = {}
        self.drops_focus_campaign_id = ""
        self.drops_focus_campaign_name = ""
        self.drops_focus_login = ""
        self.drops_focus_status = ""
        self.drops_focus_worker = None
        self.drops_loading = False
        self.drops_loaded = False
        self.drops_error = ""
        self.drops_followers_error = ""
        self.drops_updated_at = 0.0
        self.drops_worker = None
        self.drops_request_id = 0
        self.drops_event = threading.Event()
        self.app_settings = dict(DEFAULT_APP_SETTINGS)
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
        self.session_started_at = 0.0
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
        self.root_logger = None
        self.log_file_lock = threading.Lock()
        self.log_handler = None
        self.log_history_worker = None
        self.log_writer = None
        self.logging_initialized = False
        self.pending_startup_debug = []
        self.update_lock = threading.RLock()
        self.update_worker = None
        self.update_install_callback = None
        self.update_state = {
            "phase": "idle",
            "current_version": APP_VERSION,
            "latest_version": "",
            "latest_tag": "",
            "release_url": "",
            "asset_url": "",
            "update_available": False,
            "percent": 0.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "speed_bps": 0.0,
            "status": "Нажмите кнопку, чтобы проверить наличие новой версии на GitHub",
            "error": "",
        }

    def _initialize_logging(self):
        if self.logging_initialized:
            return
        self.root_logger = logging.getLogger()
        self.root_logger.setLevel(logging.DEBUG)
        self.log_handler = AppLogHandler(self)
        self.root_logger.addHandler(self.log_handler)
        self.log_writer = threading.Thread(
            target=self._log_writer_loop,
            name="LogWriter",
            daemon=True,
        )
        self.log_writer.start()
        self.logging_initialized = True
        with self.lock:
            pending = list(self.pending_startup_debug)
            self.pending_startup_debug.clear()
        startup_logger = logging.getLogger("twitch_farming.startup.js")
        for severity, message in pending:
            startup_logger.log(getattr(logging, severity, logging.INFO), "[JS STARTUP] %s", message)

    def startup_note(self, level, message):
        severity = str(level or "INFO").upper()
        text = str(message or "")[:6000]
        with self.lock:
            self.startup_event_id += 1
            event = {
                "id": self.startup_event_id,
                "level": severity,
                "message": text,
                "elapsed": round(max(0.0, time.monotonic() - self.startup_started_at), 3),
            }
            self.startup_events.append(event)
            self.startup_events = self.startup_events[-250:]
        if self.logging_initialized:
            numeric_level = getattr(logging, severity, logging.INFO)
            logging.getLogger("twitch_farming.startup").log(numeric_level, "[STARTUP] %s", text)
        return event

    def _startup_tasks(self):
        started = time.monotonic()
        try:
            self._initialize_logging()
            logger = logging.getLogger("twitch_farming.startup")
            self.startup_note("INFO", "Поток инициализации Python запущен")
            with self.lock:
                self.startup_phase = "Подготовка папки данных..."
            APP_DIR.mkdir(parents=True, exist_ok=True)
            self.startup_note("INFO", "Папка AppData подготовлена")
            with self.lock:
                self.startup_phase = "Чтение локальной конфигурации..."
            self.startup_note("INFO", "Чтение config.json начато")
            config_started = time.monotonic()
            self._load_config()
            self.startup_note("INFO", "Конфигурация прочитана за %.3f сек.; каналов: %s" % (time.monotonic() - config_started, len(self.channels)))
        except BaseException as error:
            self.startup_error = str(error) or error.__class__.__name__
            self.startup_note("ERROR", "Ошибка подготовки Python: " + self.startup_error + "\n" + traceback.format_exc())
        finally:
            with self.lock:
                self.restore_pending = bool(self.username)
                self.startup_complete = True
                self.startup_phase = "Готово"
                if self.log_history_worker is None:
                    self.log_history_worker = threading.Thread(
                        target=self._load_startup_log_history,
                        name="LogHistoryLoader",
                        daemon=True,
                    )
                log_worker = self.log_history_worker
            self.startup_note("INFO", "Конфигурация готова за %.2f сек." % (time.monotonic() - started))
            if log_worker is not None and not log_worker.is_alive():
                try:
                    log_worker.start()
                except RuntimeError:
                    pass
    def begin_session_restore(self):
        with self.lock:
            pending = bool(self.restore_pending)
        if pending:
            self.startup_note("INFO", "Проверка сохранённой сессии запущена в фоновом потоке")
            self._restore_session()
        else:
            self.startup_note("INFO", "Проверка сессии пропущена: сохранённый аккаунт не задан")
        return {"ok": True}

    def _sanitize_startup_log(self):
        try:
            self._sanitize_existing_log()
        except Exception:
            logging.getLogger("twitch_farming.startup").exception("Existing log preparation failed")

    def get_startup_status(self, after_event_id=0):
        try:
            after_id = max(0, int(after_event_id or 0))
        except (TypeError, ValueError):
            after_id = 0
        events = self.startup_events
        return {
            "ready": bool(self.startup_complete),
            "error": self.startup_error,
            "phase": self.startup_phase,
            "elapsed": round(max(0.0, time.monotonic() - self.startup_started_at), 1),
            "restore_pending": bool(self.restore_pending),
            "diagnostics": [dict(item) for item in events if item["id"] > after_id],
        }












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
        permanent_channels = [login for login in self.channels if login not in self.temporary_channels]
        return {
            "username": self.username,
            "channels": permanent_channels,
            "channel_points_branding": {key: dict(value) for key, value in self.channel_points_branding_cache.items() if key in permanent_channels},
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
            if self.session_running:
                normalized = [login for login in self.channels if login not in self.temporary_channels]
            else:
                channels = payload.get("channels", self.channels)
                if isinstance(channels, str):
                    channels = re.split(r"[\n,;]+", channels)
                normalized = []
                for item in channels if isinstance(channels, list) else []:
                    channel = self.normalize_channel(item)
                    if channel and channel not in normalized and channel not in self.temporary_channels:
                        normalized.append(channel)
            normalized.extend(login for login in self.temporary_channels if login not in normalized)
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

    def reorder_channels(self, order):
        if not isinstance(order, list):
            return {"ok": False, "message": "Некорректный порядок каналов."}
        with self.lock:
            if self.session_running:
                return {"ok": False, "message": "Останови сбор перед изменением приоритета каналов."}
            normalized = []
            for item in order:
                login = self.normalize_channel(item)
                if not login or login in normalized:
                    return {"ok": False, "message": "Список каналов содержит некорректные или повторяющиеся значения."}
                normalized.append(login)
            if len(normalized) != len(self.channels) or set(normalized) != set(self.channels):
                return {"ok": False, "message": "Список каналов изменился. Обнови страницу и повтори сортировку."}
            self.channels = normalized
        saved = self._save_config()
        return {"ok": bool(saved), "message": "Порядок каналов сохранён." if saved else "Не удалось записать порядок каналов в конфиг."}

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
                self.watch_status.pop(channel, None)
                self.temporary_channels.pop(channel, None)
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
        if restored:
            level = "INFO" if success else "WARNING"
            message = "Сохранённая сессия Twitch восстановлена для @" + str(username) if success else "Сохранённая сессия Twitch не восстановлена: " + str(error or "Twitch отклонил авторизацию")
            self.startup_note(level, message)
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
                "focus": {
                    "campaign_id": self.drops_focus_campaign_id,
                    "campaign_name": self.drops_focus_campaign_name,
                    "login": self.drops_focus_login,
                    "status": self.drops_focus_status,
                } if self.drops_focus_campaign_id else {},
            }

    def finish_drops_worker(self, worker, request_id, campaigns, error="", followers_error="", catalog_streamers=None):
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
                self.drops_catalog_streamers = {
                    str(login).lower(): dict(profile)
                    for login, profile in (catalog_streamers or {}).items()
                    if isinstance(profile, dict)
                }
                self.drops_updated_at = time.time()
            self.drops_event.set()

    def set_drops_campaign_focus(self, raw_campaign_id):
        campaign_id = str(raw_campaign_id or "").strip()
        if not campaign_id:
            return {"ok": False, "message": "Кампания не найдена."}
        with self.lock:
            if self.drops_focus_campaign_id == campaign_id:
                return self.clear_drops_campaign_focus()
            if not self.authenticated or self.api_client is None:
                return {"ok": False, "message": "Сначала подключи аккаунт Twitch."}
            campaign = next(
                (dict(item) for item in self.drops_campaigns if str(item.get("id") or "") == campaign_id),
                None,
            )
            if campaign is None:
                return {"ok": False, "message": "Кампания больше не найдена. Обнови инвентарь Drops."}
            self.drops_focus_campaign_id = campaign_id
            self.drops_focus_campaign_name = str(campaign.get("name") or campaign.get("game") or "Drops")
            self.drops_focus_status = "Ищем живого стримера с Drops…"
            worker = self.drops_focus_worker
            if worker is None or not worker.is_alive() or worker.stop_event.is_set():
                worker = DropsFocusWorker(self)
                self.drops_focus_worker = worker
                start_worker = True
            else:
                start_worker = False
        if start_worker:
            worker.start()
        self._append_event("Целевой Drops-фарм: " + self.drops_focus_campaign_name)
        return {"ok": True, "message": "Целевой фарм кампании запущен."}

    def clear_drops_campaign_focus(self):
        with self.lock:
            campaign_id = self.drops_focus_campaign_id
            if not campaign_id:
                return {"ok": True, "message": "Целевой фарм уже выключен."}
            self.drops_focus_campaign_id = ""
            self.drops_focus_campaign_name = ""
            self.drops_focus_status = ""
            worker = self.drops_focus_worker
            self.drops_focus_worker = None
            if worker is not None:
                worker.stop_event.set()
        self._remove_drops_focus_channel(campaign_id)
        self._append_event("Целевой Drops-фокус снят.")
        return {"ok": True, "message": "Фокус кампании снят."}

    def _sync_drops_focus_slot(self, campaign_id, login):
        login = str(login or "").strip().lower()
        if not login:
            return
        with self.lock:
            if self.drops_focus_campaign_id != str(campaign_id or "") or self.drops_focus_login != login:
                return
            engine = self.engine_worker
        miner = getattr(engine, "miner", None) if engine is not None else None
        twitch_client = getattr(miner, "twitch", None) if miner is not None else None
        if twitch_client is not None:
            twitch_client.drops_focus_login = login

    def _set_drops_focus_status(self, campaign_id, status):
        with self.lock:
            if self.drops_focus_campaign_id == str(campaign_id or ""):
                self.drops_focus_status = str(status or "")

    def _finish_drops_campaign_focus(self, campaign_id, reason):
        with self.lock:
            if self.drops_focus_campaign_id != str(campaign_id or ""):
                return
            self.drops_focus_campaign_id = ""
            self.drops_focus_campaign_name = ""
            self.drops_focus_status = ""
            worker = self.drops_focus_worker
            self.drops_focus_worker = None
            if worker is not None:
                worker.stop_event.set()
        self._remove_drops_focus_channel(campaign_id)
        self._append_event("Целевой Drops-фокус завершён · " + str(reason))

    def _remove_drops_focus_channel(self, campaign_id="", stop_if_empty=True):
        with self.lock:
            login = str(self.drops_focus_login or "")
            self.drops_focus_login = ""
            temporary_info = dict(self.temporary_channels.get(login) or {}) if login else {}
            remove_from_list = bool(login and temporary_info.get("drops_focus"))
            if remove_from_list:
                self.temporary_channels.pop(login, None)
                if login in self.channels:
                    self.channels.remove(login)
                self.online_channels.discard(login)
                self.channel_profiles.pop(login, None)
                self.channel_balances.pop(login, None)
                self.channel_points_branding_cache.pop(login, None)
                self.watch_status.pop(login, None)
            engine = self.engine_worker
            should_stop = bool(
                stop_if_empty
                and remove_from_list
                and self.session_running
                and not self.channels
                and engine is not None
                and engine.is_alive()
            )
        if remove_from_list and engine is not None and engine.is_alive() and login:
            threading.Thread(
                target=engine.remove_streamer,
                args=(login,),
                name="RemoveDropsFocusStreamer",
                daemon=True,
            ).start()
        miner = getattr(engine, "miner", None) if engine is not None else None
        twitch_client = getattr(miner, "twitch", None) if miner is not None else None
        if twitch_client is not None:
            twitch_client.drops_focus_login = ""
        if should_stop:
            engine.request_stop()

    def _validate_drops_focus_channel(self, campaign, login):
        with self.lock:
            client = self.api_client if self.authenticated else None
        if client is None:
            return None
        campaign_id = str(campaign.get("id") or "")
        game_id = str(campaign.get("game_id") or "")
        game_slug = str(campaign.get("game_slug") or "").strip().lower()
        if not game_id or not game_slug:
            try:
                game_info = client.get_game_info(game_id, str(campaign.get("game") or ""))
                game_id = str(game_info.get("id") or game_id)
                game_slug = str(game_info.get("slug") or game_slug)
            except Exception:
                return None
        if not game_slug:
            return None
        try:
            stream = client.get_stream_by_login(login)
        except Exception:
            return None
        if not isinstance(stream, dict):
            return False
        if game_id and str(stream.get("game_id") or "") != game_id:
            return False
        allowed = {str(value) for value in campaign.get("allowed_channel_ids") or []}
        channel_id = str(stream.get("user_id") or "")
        if allowed and channel_id not in allowed:
            return False
        try:
            directory = client.get_drops_streams_for_game(game_slug, game_id, limit=30)
            if any(
                str(item.get("user_login") or "").lower() == str(login).lower()
                and (not game_id or str(item.get("game_id") or game_id) == game_id)
                for item in directory if isinstance(item, dict)
            ):
                return True
            direct = client.get_drop_campaign_ids_for_channels([{
                "login": login,
                "channel_id": str(stream.get("user_id") or ""),
            }])
            return campaign_id in set(str(value) for value in direct.get(str(login).lower(), []))
        except Exception:
            return None

    def _find_drops_focus_candidate(self, campaign):
        with self.lock:
            client = self.api_client if self.authenticated else None
            followed = [dict(item) for item in self.followed_channels if isinstance(item, dict)]
        if client is None:
            return None
        campaign_id = str(campaign.get("id") or "")
        game_id = str(campaign.get("game_id") or "")
        game_slug = str(campaign.get("game_slug") or "").strip().lower()
        game_name = str(campaign.get("game") or "")
        if not game_id or not game_slug:
            try:
                game_info = client.get_game_info(game_id, game_name)
                game_id = str(game_info.get("id") or game_id)
                game_slug = str(game_info.get("slug") or game_slug)
            except Exception:
                pass
        if not game_slug:
            return None
        allowed = {str(value) for value in campaign.get("allowed_channel_ids") or []}
        followed_profiles = [
            dict(item) for item in campaign.get("streamers", [])
            if isinstance(item, dict) and item.get("is_followed") and item.get("login")
        ]
        followed_by_login = {
            str(item.get("login") or "").lower(): item
            for item in followed_profiles
        }
        additional_followed = []
        for item in followed:
            login = str(item.get("login") or "").strip().lower()
            channel_id = str(item.get("channel_id") or "")
            followed_game_id = str(item.get("game_id") or "")
            if not login or login in followed_by_login or not item.get("is_live") or not channel_id:
                continue
            if game_id and followed_game_id != game_id:
                continue
            if allowed and channel_id not in allowed:
                continue
            additional_followed.append(dict(item))
        additional_followed.sort(
            key=lambda item: max(0, int(item.get("viewer_count") or 0)),
            reverse=True,
        )
        for item in additional_followed:
            followed_by_login[str(item.get("login") or "").lower()] = item
        live_followed = []
        followed_logins = list(followed_by_login)
        for offset in range(0, len(followed_logins), 100):
            batch = followed_logins[offset:offset + 100]
            try:
                live_followed.extend(
                    client.helix_get("streams", [("user_login", login) for login in batch])
                )
            except Exception:
                continue
        followed_rows = []
        for stream in live_followed if isinstance(live_followed, list) else []:
            if not isinstance(stream, dict):
                continue
            login = str(stream.get("user_login") or "").lower()
            channel_id = str(stream.get("user_id") or "")
            if not login or not channel_id:
                continue
            if game_id and str(stream.get("game_id") or "") != game_id:
                continue
            if allowed and channel_id not in allowed:
                continue
            followed_rows.append({
                "login": login,
                "channel_id": channel_id,
                "game_id": str(stream.get("game_id") or ""),
                "game_name": str(stream.get("game_name") or game_name),
                "viewer_count": max(0, int(stream.get("viewer_count") or 0)),
                "started_at": str(stream.get("started_at") or ""),
            })
        active_followed_ids = {}
        if followed_rows:
            try:
                active_followed_ids = client.get_drop_campaign_ids_for_channels(followed_rows)
            except Exception:
                active_followed_ids = {}
        directory_by_login = {}
        try:
            directory = client.get_drops_streams_for_game(game_slug, game_id, limit=30)
            for stream in directory if isinstance(directory, list) else []:
                if not isinstance(stream, dict):
                    continue
                login = str(stream.get("user_login") or "").lower()
                channel_id = str(stream.get("user_id") or "")
                stream_game_id = str(stream.get("game_id") or game_id)
                if not login or not channel_id or (game_id and stream_game_id != game_id):
                    continue
                if allowed and channel_id not in allowed:
                    continue
                directory_by_login[login] = dict(stream)
        except Exception:
            directory_by_login = {}
        followed_rows_by_login = {item["login"]: item for item in followed_rows}
        for login, profile in followed_by_login.items():
            row = followed_rows_by_login.get(login)
            if row is None:
                continue
            active = campaign_id in set(str(value) for value in active_followed_ids.get(login, []))
            if not active and login not in directory_by_login:
                continue
            selected = dict(profile)
            selected.update({
                "login": login,
                "channel_id": row["channel_id"],
                "game_id": row["game_id"],
                "game_name": row["game_name"],
                "viewer_count": row["viewer_count"],
                "is_live": True,
                "is_followed": True,
            })
            return selected
        catalog_rows = sorted(
            directory_by_login.values(),
            key=lambda item: max(0, int(item.get("viewer_count") or 0)),
            reverse=True,
        )
        if not catalog_rows:
            return None
        selected = catalog_rows[0]
        login = str(selected.get("user_login") or "").lower()
        channel_id = str(selected.get("user_id") or "")
        profile = {}
        try:
            users = client.helix_get("users", {"id": channel_id})
            profile = users[0] if users and isinstance(users[0], dict) else {}
        except Exception:
            profile = {}
        avatar = str(profile.get("profile_image_url") or "")
        return {
            "login": login,
            "channel_id": channel_id,
            "display_name": str(profile.get("display_name") or selected.get("user_name") or login),
            "avatar_url": avatar if avatar.startswith("https://") else "",
            "viewer_count": max(0, int(selected.get("viewer_count") or 0)),
            "game_id": str(selected.get("game_id") or game_id),
            "game_name": str(selected.get("game_name") or game_name),
            "is_live": True,
            "is_followed": False,
        }

    def _assign_drops_focus_channel(self, campaign_id, profile):
        login = self.normalize_channel(profile.get("login"))
        channel_id = str(profile.get("channel_id") or "")
        if not login or not channel_id:
            return {"ok": False, "message": "Twitch не вернул данные стримера."}
        with self.lock:
            if self.drops_focus_campaign_id != str(campaign_id or ""):
                return {"ok": False, "message": "Фокус кампании уже изменён."}
            old_login = str(self.drops_focus_login or "")
            if old_login == login:
                self.drops_focus_status = "Фармится"
                return {"ok": True, "unchanged": True}
            previous_focus_info = dict(self.temporary_channels.get(old_login) or {}) if old_login else {}
            already_configured = login in self.channels and login not in self.temporary_channels
            if login not in self.channels:
                self.channels.append(login)
            if not already_configured:
                temp_info = dict(self.temporary_channels.get(login) or {})
                was_temporary = bool(temp_info)
                temp_info.update({
                    "campaign_id": str(campaign_id),
                    "created_at": float(temp_info.get("created_at") or time.time()),
                    "drops_focus": bool(temp_info.get("drops_focus")) or not was_temporary,
                })
                self.temporary_channels[login] = temp_info
            self.channel_profiles[login] = {
                "loaded": True,
                "display_name": str(profile.get("display_name") or login),
                "avatar_url": str(profile.get("avatar_url") or ""),
                "is_live": True,
                "viewer_count": max(0, int(profile.get("viewer_count") or 0)),
                "error": "",
            }
            self.online_channels.add(login)
            engine = self.engine_worker
            session_running = bool(self.session_running and engine is not None and engine.is_alive())
        if session_running:
            result = engine.add_streamer(login, channel_id)
        else:
            result = self.start_farm({})
        if not result.get("ok"):
            with self.lock:
                info = self.temporary_channels.get(login) or {}
                if info.get("drops_focus") and self.drops_focus_login != login:
                    self.temporary_channels.pop(login, None)
                    if login in self.channels:
                        self.channels.remove(login)
                    self.online_channels.discard(login)
                    self.channel_profiles.pop(login, None)
            return result
        if not session_running:
            with self.lock:
                engine = self.engine_worker
        with self.lock:
            if self.drops_focus_campaign_id != str(campaign_id or ""):
                rollback = True
            else:
                rollback = False
                self.drops_focus_login = login
                self.drops_focus_status = "Фармится"
                previous_focus_info = dict(self.temporary_channels.get(old_login) or {}) if old_login else {}
                if old_login and old_login != login and previous_focus_info.get("drops_focus"):
                    self.temporary_channels.pop(old_login, None)
                    if old_login in self.channels:
                        self.channels.remove(old_login)
                    self.online_channels.discard(old_login)
                    self.channel_profiles.pop(old_login, None)
                    self.channel_balances.pop(old_login, None)
                    self.watch_status.pop(old_login, None)
        miner = getattr(engine, "miner", None) if engine is not None else None
        twitch_client = getattr(miner, "twitch", None) if miner is not None else None
        if twitch_client is not None and not rollback:
            twitch_client.drops_focus_login = login
        if old_login and old_login != login and previous_focus_info.get("drops_focus") and engine is not None:
            threading.Thread(
                target=engine.remove_streamer,
                args=(old_login,),
                name="ReplaceDropsFocusStreamer",
                daemon=True,
            ).start()
        if rollback:
            with self.lock:
                rollback_info = dict(self.temporary_channels.get(login) or {})
                rollback_remove = bool(rollback_info.get("drops_focus") and self.drops_focus_login != login)
                if rollback_remove:
                    self.temporary_channels.pop(login, None)
                    if login in self.channels:
                        self.channels.remove(login)
                    self.online_channels.discard(login)
                    self.channel_profiles.pop(login, None)
                    self.channel_balances.pop(login, None)
                    self.watch_status.pop(login, None)
            if rollback_remove and engine is not None:
                threading.Thread(target=engine.remove_streamer, args=(login,), name="RollbackDropsFocusStreamer", daemon=True).start()
            return {"ok": False, "message": "Фокус кампании уже снят."}
        return {"ok": True, "message": "Целевой Drops-канал: @" + login}



    def add_drop_channel_to_farm(self, raw_login, campaign_id=""):
        login = self.normalize_channel(raw_login)
        if not login:
            return {"ok": False, "message": "Некорректный логин Twitch."}
        with self.lock:
            if self.session_running or (self.engine_worker is not None and self.engine_worker.is_alive()):
                return {"ok": False, "message": "Остановите фарм, чтобы изменить список каналов."}
            follow = next((dict(item) for item in self.followed_channels if item.get("login") == login), None)
            catalog_profile = dict(self.drops_catalog_streamers.get(login) or {})
            profile_source = dict(follow or catalog_profile)
            if not profile_source:
                return {"ok": False, "message": "Стример больше не найден в актуальном каталоге Drops. Обнови инвентарь."}
            already_configured = login in self.channels and login not in self.temporary_channels
            if not already_configured:
                if login not in self.channels:
                    self.channels.append(login)
                self.temporary_channels[login] = {
                    "campaign_id": str(campaign_id or ""),
                    "created_at": time.time(),
                }
                self.channel_profiles[login] = {
                    "loaded": True,
                    "display_name": str(profile_source.get("display_name") or login),
                    "avatar_url": str(profile_source.get("avatar_url") or ""),
                    "is_live": bool(profile_source.get("is_live")),
                    "viewer_count": max(0, int(profile_source.get("viewer_count") or 0)),
                    "error": "",
                }
                if profile_source.get("is_live"):
                    self.online_channels.add(login)
        result = self.start_farm({})
        if result.get("ok"):
            return {
                "ok": True,
                "temporary": not already_configured,
                "message": "Канал добавлен во временный Drops-фарм." if not already_configured else "Канал уже находится в постоянном списке. Автоматический сбор запущен.",
            }
        if not already_configured:
            with self.lock:
                if not self.session_running and login in self.temporary_channels:
                    self.temporary_channels.pop(login, None)
                    if login in self.channels:
                        self.channels.remove(login)
                    self.online_channels.discard(login)
                    self.channel_profiles.pop(login, None)
                    self.channel_balances.pop(login, None)
                    self.channel_points_branding_cache.pop(login, None)
                    self.watch_status.pop(login, None)
        return result

    def clear_avatar_cache(self):
        import shutil
        cache_dir = APP_DIR / "image_cache" / "avatars"
        try:
            shutil.rmtree(cache_dir, ignore_errors=True)
        except OSError:
            pass
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
        self.refresh_channel_profiles()
        self.refresh_followed_channels(force=True)
        return {"ok": True, "cleared": cleared, "message": "Кэш аватарок очищен. Профили обновляются."}

    def clear_currency_cache(self):
        import shutil
        cache_dir = APP_DIR / "image_cache" / "currency"
        try:
            shutil.rmtree(cache_dir, ignore_errors=True)
        except OSError:
            pass
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
            focus_active = bool(self.drops_focus_campaign_id)
        if focus_active:
            self.clear_drops_campaign_focus()
        with self.lock:
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
            self.drops_catalog_streamers = {}
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
            self._apply_ui_state_queue_locked()
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
                self.session_started_at = time.time()
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
            self.session_started_at = 0.0
            focus_worker = self.drops_focus_worker
            if focus_worker is not None:
                focus_worker.stop_event.set()
            self.drops_focus_worker = None
            self.drops_focus_campaign_id = ""
            self.drops_focus_campaign_name = ""
            self.drops_focus_login = ""
            self.drops_focus_status = ""
        worker.request_stop()
        return {"ok": True, "message": "Остановка запрошена."}

    def enqueue_ui_state(self, event_type, **payload):
        try:
            self.ui_state_queue.put_nowait((str(event_type), dict(payload)))
            return True
        except BaseException as error:
            write_crash_log("QUEUE", "Не удалось поставить событие UI в очередь: " + str(error))
            return False

    def _apply_ui_state_queue_locked(self):
        published = []
        while True:
            try:
                event_type, payload = self.ui_state_queue.get_nowait()
            except queue.Empty:
                break
            if event_type == "engine_error":
                message = str(payload.get("message") or "Неизвестная ошибка фарма")
                self._append_event_locked("Ошибка · " + message.replace("\\n", " ")[:220])
                self._set_status_locked("●  ОШИБКА", "#FF6B78")
                published.append({"type": event_type, "message": message[:220]})
            elif event_type == "engine_finished":
                self._finish_engine_worker_locked(payload.get("worker"))
                published.append({"type": event_type})
        return published

    def engine_error(self, error):
        self.enqueue_ui_state("engine_error", message=str(error))

    def _finish_engine_worker_locked(self, worker):
        if self.engine_worker is not worker:
            return
        self.session_running = False
        self.session_started_at = 0.0
        self.engine_worker = None
        focus_worker = self.drops_focus_worker
        if focus_worker is not None:
            focus_worker.stop_event.set()
        self.drops_focus_worker = None
        self.drops_focus_campaign_id = ""
        self.drops_focus_campaign_name = ""
        self.drops_focus_login = ""
        self.drops_focus_status = ""
        temporary = list(self.temporary_channels)
        for login in temporary:
            if login in self.channels:
                self.channels.remove(login)
            self.online_channels.discard(login)
            self.channel_profiles.pop(login, None)
            self.channel_balances.pop(login, None)
            self.channel_points_branding_cache.pop(login, None)
            self.watch_status.pop(login, None)
        self.temporary_channels.clear()
        if temporary:
            self._append_event_locked("Временные Drops-каналы удалены после завершения фарма: " + str(len(temporary)))
        self._set_status_locked("●  НЕ ЗАПУЩЕН", "#A9B2C3")

    def engine_finished(self, worker):
        self.enqueue_ui_state("engine_finished", worker=worker)

    def _set_status_locked(self, text, color="#A9B2C3"):
        self.status_text = text
        self.status_color = color

    def _append_event_locked(self, message):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.events.append(stamp + "  " + str(message))
        self.events = self.events[-100:]

    def _resolve_channel_login_locked(self, value):
        raw = str(value or "").strip().lower().lstrip("@")
        raw = re.sub(r"\s*\([^)]*\)$", "", raw).strip()
        if raw in self.channels or raw in self.channel_points_branding_cache:
            return raw
        for login, profile in self.channel_profiles.items():
            display_name = str(profile.get("display_name") or "").strip().lower()
            if display_name and display_name == raw:
                return str(login).strip().lower()
        return raw

    def _append_reward_locked(self, title, description, kind, created, channel_login=""):
        login = self._resolve_channel_login_locked(channel_login or title)
        branding = self.channel_points_branding_cache.get(login, {}) if login else {}
        self.rewards.append({
            "title": str(title)[:100],
            "description": str(description)[:140],
            "kind": str(kind),
            "timestamp": float(created),
            "channel_login": login,
            "currency_name": str(branding.get("name") or "")[:80],
            "currency_icon": str(branding.get("icon_url") or "") if str(branding.get("icon_url") or "").startswith("https://") else "",
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
                channel_login = channel.lower()
                branding = self.channel_points_branding_cache.get(channel_login, {})
                currency_name = str(branding.get("name") or "").strip()
                reward_description = "+" + str(amount) + " " + (currency_name or "Channel Points")
                self._append_reward_locked(channel, reward_description, "gem", created, channel_login)
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

    def _load_existing_log_records(self):
        try:
            self._flush_logs()
            with self.log_file_lock:
                if not LOG_FILE.is_file():
                    content = ""
                else:
                    with LOG_FILE.open("rb") as file:
                        file.seek(0, os.SEEK_END)
                        size = file.tell()
                        file.seek(max(0, size - 1000000))
                        content = file.read(1000000).decode("utf-8", errors="replace")
                    if size > 1000000:
                        content = content.split("\n", 1)[-1]
            history_lines = content.splitlines()[-500:]
            with self.lock:
                remaining = {}
                for line in history_lines:
                    remaining[line] = remaining.get(line, 0) + 1
                live_lines = []
                for item in self.log_records:
                    line = str(item.get("line") or "")
                    if remaining.get(line, 0):
                        remaining[line] -= 1
                    else:
                        live_lines.append(line)
                lines = (history_lines + live_lines)[-500:]
                self.log_records = [
                    {"id": index + 1, "line": line}
                    for index, line in enumerate(lines)
                ]
                self.next_log_id = len(self.log_records) + 1
        except OSError:
            return

    def _sanitize_existing_log(self):
        try:
            with self.log_file_lock:
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
            streamers_lock = getattr(miner, "streamers_lock", None)
            if streamers_lock is None:
                streamers = list(getattr(miner, "streamers", []) or [])
            else:
                with streamers_lock:
                    streamers = list(getattr(miner, "streamers", []) or [])
            for streamer in streamers:
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
        for reward in self.rewards:
            login = str(reward.get("channel_login") or "").strip().lower()
            cached = currency.get(login) or {}
            icon_url = str(cached.get("icon_url") or "")
            currency_name = str(cached.get("name") or "")
            if icon_url.startswith("https://"):
                reward["currency_icon"] = icon_url
            if currency_name:
                reward["currency_name"] = currency_name
        return ranked[:3], balances, currency

    def get_state(self, after_log_id=0):
        try:
            after = int(after_log_id)
        except (TypeError, ValueError):
            after = 0
        with self.lock:
            ui_events = self._apply_ui_state_queue_locked()
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
            watch_slots = []
            if self.session_running:
                engine = self.engine_worker
                miner = getattr(engine, "miner", None) if engine is not None else None
                twitch_client = getattr(miner, "twitch", None) if miner is not None else None
                if twitch_client is not None and callable(getattr(twitch_client, "get_active_watch_channels", None)):
                    watch_slots = twitch_client.get_active_watch_channels()
                elif twitch_client is not None:
                    watch_slots = list(getattr(twitch_client, "active_watch_channels", []) or [])
            logs = [dict(item) for item in self.log_records if item["id"] > after]
            avatar_cache_logins = {
                login for login, profile in self.channel_profiles.items()
                if profile.get("avatar_url")
            }
            avatar_cache_logins.update(
                str(item.get("login") or "").lower()
                for item in self.followed_channels
                if item.get("avatar_url") and item.get("login")
            )
            avatar_cache_count = len(avatar_cache_logins) + int(bool(self.account_profile.get("avatar_url")))
            return {
                "username": self.username,
                "channels": list(self.channels),
                "temporary_channels": list(self.temporary_channels),
                "temporary_channel_info": {key: dict(value) for key, value in self.temporary_channels.items()},
                "online_channels": sorted(self.online_channels),
                "watch_status": {key: dict(value) for key, value in self.watch_status.items()},
                "watch_slots": watch_slots,
                "ui_events": ui_events,
                "options": dict(self.options),
                "app_settings": dict(self.app_settings),
                "cache_stats": {
                    "avatars": avatar_cache_count,
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
                "session_started_at": float(self.session_started_at or 0),
                "app_version": APP_VERSION,
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
                "drops_focus_login": self.drops_focus_login,
                "drops_focus_campaign_id": self.drops_focus_campaign_id,
            }

    def get_log_snapshot(self):
        self._flush_logs()
        with self.lock:
            rows = [dict(item) for item in self.log_records[-500:]]
            snapshot_id = self.next_log_id - 1
        return {
            "rows": rows,
            "lines": [item["line"] for item in rows],
            "last_log_id": snapshot_id,
        }

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

    def get_app_version(self):
        return APP_VERSION

    def _set_update_state(self, **values):
        with self.update_lock:
            self.update_state.update(values)
            self.update_state["current_version"] = APP_VERSION

    def get_update_state(self):
        with self.update_lock:
            return dict(self.update_state)

    @staticmethod
    def _version_tuple(value):
        numbers = re.findall(r"\d+", str(value or ""))
        return tuple(int(item) for item in numbers[:4]) or (0,)

    @staticmethod
    def _release_version(tag):
        value = str(tag or "").strip()
        build = re.search(r"(?i)twitch-farming-build-(\d+)$", value)
        if build:
            return "1.0." + build.group(1)
        semantic = re.search(r"(?i)v?(\d+\.\d+(?:\.\d+)?)$", value)
        return semantic.group(1) if semantic else value

    def check_for_updates(self):
        with self.update_lock:
            if self.update_worker is not None and self.update_worker.is_alive():
                return {"ok": True, "checking": True}
            self.update_state = {
                "phase": "checking",
                "current_version": APP_VERSION,
                "latest_version": "",
                "latest_tag": "",
                "release_url": "",
                "asset_url": "",
                "update_available": False,
                "percent": 0.0,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "speed_bps": 0.0,
                "status": "Проверяю GitHub Releases…",
                "error": "",
            }
            worker = threading.Thread(
                target=self._check_updates_worker,
                name="GitHubUpdateCheck",
                daemon=True,
            )
            self.update_worker = worker
        try:
            worker.start()
        except Exception as error:
            self._set_update_state(
                phase="error",
                error=str(error) or error.__class__.__name__,
                status="Не удалось запустить проверку обновлений.",
            )
            return {"ok": False, "message": str(error)}
        return {"ok": True, "checking": True}

    def _check_updates_worker(self):
        try:
            import urllib.request

            request = urllib.request.Request(
                "https://api.github.com/repos/gamooda/KICK-FARMING/releases/latest",
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Twitch-Farming-Updater/" + APP_VERSION,
                },
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                release = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
            if not isinstance(release, dict):
                raise RuntimeError("GitHub вернул неожиданный ответ Releases API.")
            tag = str(release.get("tag_name") or "")
            latest_version = self._release_version(tag)
            current_tuple = self._version_tuple(APP_VERSION)
            latest_tuple = self._version_tuple(latest_version)
            assets = release.get("assets") if isinstance(release.get("assets"), list) else []
            asset = next(
                (
                    item for item in assets
                    if isinstance(item, dict)
                    and str(item.get("name") or "").lower() == "twitch.farming.zip"
                    and str(item.get("browser_download_url") or "").startswith("https://")
                ),
                None,
            )
            if latest_tuple > current_tuple:
                if asset is None:
                    self._set_update_state(
                        phase="error",
                        latest_version=latest_version,
                        latest_tag=tag,
                        release_url=str(release.get("html_url") or ""),
                        update_available=False,
                        error="В последнем релизе GitHub отсутствует архив Twitch.Farming.zip.",
                        status="Новая версия найдена, но установочный архив не опубликован.",
                    )
                    return
                self._set_update_state(
                    phase="available",
                    latest_version=latest_version,
                    latest_tag=tag,
                    release_url=str(release.get("html_url") or ""),
                    asset_url=str(asset["browser_download_url"]),
                    update_available=True,
                    status="Доступна новая версия v" + latest_version + ".",
                    error="",
                )
            else:
                self._set_update_state(
                    phase="up_to_date",
                    latest_version=latest_version,
                    latest_tag=tag,
                    release_url=str(release.get("html_url") or ""),
                    asset_url="",
                    update_available=False,
                    status="Установлена последняя версия v" + APP_VERSION + ".",
                    error="",
                )
        except Exception as error:
            self._set_update_state(
                phase="error",
                update_available=False,
                error=str(error) or error.__class__.__name__,
                status="Не удалось проверить GitHub Releases.",
            )

    def download_update(self):
        with self.update_lock:
            if self.update_worker is not None and self.update_worker.is_alive():
                return {"ok": False, "message": "Операция обновления уже выполняется."}
            asset_url = str(self.update_state.get("asset_url") or "")
            if not self.update_state.get("update_available") or not asset_url:
                return {"ok": False, "message": "Сначала проверь наличие новой версии."}
        with self.lock:
            if self.session_running or (self.engine_worker is not None and self.engine_worker.is_alive()):
                return {"ok": False, "message": "Остановите фарм перед установкой обновления."}
        if not getattr(sys, "frozen", False):
            return {"ok": False, "message": "Автоматическое обновление доступно в собранной версии приложения."}
        worker = threading.Thread(
            target=self._download_update_worker,
            args=(asset_url,),
            name="GitHubUpdateDownload",
            daemon=True,
        )
        with self.update_lock:
            self.update_worker = worker
        try:
            worker.start()
        except Exception as error:
            self._set_update_state(
                phase="error",
                error=str(error) or error.__class__.__name__,
                status="Не удалось начать скачивание обновления.",
            )
            return {"ok": False, "message": str(error)}
        return {"ok": True, "downloading": True}

    def _download_update_worker(self, asset_url):
        import shutil
        import subprocess
        import tempfile
        import urllib.request
        import zipfile

        archive_path = None
        staging_dir = None
        try:
            executable = Path(sys.executable).resolve()
            install_dir = executable.parent
            install_parent = install_dir.parent
            process_id = os.getpid()
            safe_folder = re.sub(r"[^A-Za-z0-9_.-]+", "_", install_dir.name)
            staging_dir = install_parent / ("." + safe_folder + ".update-" + str(process_id))
            backup_dir = install_parent / ("." + safe_folder + ".backup-" + str(process_id))
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            staging_dir.mkdir(parents=True, exist_ok=True)
            archive_path = Path(tempfile.gettempdir()) / ("TwitchFarming-update-" + str(process_id) + ".zip")
            request = urllib.request.Request(
                asset_url,
                headers={"User-Agent": "Twitch-Farming-Updater/" + APP_VERSION},
            )
            self._set_update_state(
                phase="downloading",
                percent=0.0,
                downloaded_bytes=0,
                total_bytes=0,
                speed_bps=0.0,
                status="Подключение к GitHub…",
                error="",
            )
            started = time.monotonic()
            downloaded = 0
            with urllib.request.urlopen(request, timeout=35) as response:
                total = int(response.headers.get("Content-Length") or 0)
                self._set_update_state(total_bytes=total)
                with archive_path.open("wb") as output:
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        elapsed = max(0.001, time.monotonic() - started)
                        speed = downloaded / elapsed
                        percent = min(100.0, downloaded * 100.0 / total) if total else 0.0
                        self._set_update_state(
                            downloaded_bytes=downloaded,
                            total_bytes=total,
                            speed_bps=speed,
                            percent=percent,
                            status="Скачивание обновления…",
                        )
            if downloaded <= 0:
                raise RuntimeError("GitHub вернул пустой архив обновления.")
            self._set_update_state(
                phase="extracting",
                percent=100.0,
                downloaded_bytes=downloaded,
                status="Проверка и подготовка файлов обновления…",
            )
            staging_root = staging_dir.resolve()
            with zipfile.ZipFile(archive_path, "r") as archive:
                for item in archive.infolist():
                    name = str(item.filename or "").replace("\\", "/")
                    relative = Path(name)
                    if not name or relative.is_absolute() or ".." in relative.parts or ":" in name.split("/", 1)[0]:
                        raise RuntimeError("Архив содержит недопустимый путь: " + name[:160])
                    destination = (staging_dir / relative).resolve()
                    if os.path.commonpath((str(staging_root), str(destination))) != str(staging_root):
                        raise RuntimeError("Путь внутри архива выходит за пределы папки обновления.")
                    if (item.external_attr >> 16) & 0o170000 == 0o120000:
                        raise RuntimeError("Архив содержит символическую ссылку.")
                    if item.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(item, "r") as source, destination.open("wb") as target:
                            shutil.copyfileobj(source, target)
            if not (staging_dir / executable.name).is_file():
                raise RuntimeError("В архиве не найден " + executable.name + ".")
            if not (staging_dir / "_internal").is_dir():
                raise RuntimeError("В архиве отсутствует папка зависимостей _internal.")
            quote_path = lambda value: str(value).replace('"', "")
            target_value = quote_path(install_dir)
            stage_value = quote_path(staging_dir)
            backup_value = quote_path(backup_dir)
            exe_name = quote_path(executable.name)
            script = (
                "@echo off\r\n"
                "chcp 65001 >nul\r\n"
                "set \"PID=" + str(process_id) + "\"\r\n"
                "set \"TARGET=" + target_value + "\"\r\n"
                "set \"STAGE=" + stage_value + "\"\r\n"
                "set \"BACKUP=" + backup_value + "\"\r\n"
                "set \"UPDATE_BATCH=%~f0\"\r\n"
                ":wait_for_app\r\n"
                "tasklist /FI \"PID eq %PID%\" 2>NUL | find \"%PID%\" >NUL\r\n"
                "if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait_for_app)\r\n"
                "if exist \"%BACKUP%\" rmdir /s /q \"%BACKUP%\"\r\n"
                "move \"%TARGET%\" \"%BACKUP%\" >nul\r\n"
                "if errorlevel 1 goto update_failed\r\n"
                "move \"%STAGE%\" \"%TARGET%\" >nul\r\n"
                "if errorlevel 1 (move \"%BACKUP%\" \"%TARGET%\" >nul & goto update_failed)\r\n"
                "start \"\" \"%TARGET%\\" + exe_name + "\"\r\n"
                "timeout /t 3 /nobreak >nul\r\n"
                "rmdir /s /q \"%BACKUP%\"\r\n"
                "start \"\" /b powershell.exe -NoProfile -WindowStyle Hidden -Command \"Start-Sleep -Seconds 5; Remove-Item -LiteralPath $env:UPDATE_BATCH -Force -ErrorAction SilentlyContinue\"\r\n"
                "exit /b 0\r\n"
                ":update_failed\r\n"
                "start \"\" /b powershell.exe -NoProfile -WindowStyle Hidden -Command \"Start-Sleep -Seconds 2; Remove-Item -LiteralPath $env:UPDATE_BATCH -Force -ErrorAction SilentlyContinue\"\r\n"
                "exit /b 1\r\n"
            )
            batch_fd, batch_name = tempfile.mkstemp(
                prefix="TwitchFarming-update-" + str(process_id) + "-",
                suffix=".bat",
                dir=tempfile.gettempdir(),
            )
            batch_path = Path(batch_name)
            with os.fdopen(batch_fd, "w", encoding="utf-8", newline="") as batch_file:
                batch_file.write(script)
            creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", "/min", str(batch_path)],
                cwd=str(install_parent),
                creationflags=creation_flags,
                close_fds=True,
            )
            self._set_update_state(
                phase="installing",
                percent=100.0,
                status="Скачивание завершено. Перезапуск и замена файлов…",
            )
            callback = self.update_install_callback
            if callable(callback):
                callback()
        except Exception as error:
            if staging_dir is not None:
                shutil.rmtree(staging_dir, ignore_errors=True)
            self._set_update_state(
                phase="error",
                error=str(error) or error.__class__.__name__,
                status="Не удалось установить обновление.",
            )
        finally:
            try:
                if archive_path is not None:
                    archive_path.unlink(missing_ok=True)
            except OSError:
                pass

    def open_url(self, url):
        value = str(url or "")
        if value.startswith(("https://", "http://")):
            webbrowser.open(value)
            return {"ok": True}
        return {"ok": False}











    def _load_startup_log_history(self):
        started = time.monotonic()
        self.startup_note("INFO", "Загрузка истории журнала начата в фоновом потоке")
        try:
            self._sanitize_existing_log()
            self._load_existing_log_records()
            self.startup_note("INFO", "История журнала загружена за %.2f сек." % (time.monotonic() - started))
        except BaseException as error:
            self.startup_note("ERROR", "Ошибка загрузки истории журнала: " + str(error) + "\n" + traceback.format_exc())



    def shutdown(self):
        self.stop_farm()
        with self.lock:
            auth_worker = self.auth_worker
        if auth_worker is not None and auth_worker.is_alive() and auth_worker.client is not None:
            try:
                auth_worker.client.twitch_login.cancel()
            except Exception:
                pass
        self.log_stop.set()
        self._flush_logs()
        try:
            if self.root_logger is not None and self.log_handler is not None:
                self.root_logger.removeHandler(self.log_handler)
        except Exception:
            pass
        if self.api_client is not None:
            try:
                self.api_client.twitch_login.session.close()
            except Exception:
                pass
