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

from TwitchChannelPointsMiner import TwitchChannelPointsMiner
from TwitchChannelPointsMiner.classes.Chat import ChatPresence
from TwitchChannelPointsMiner.classes.Settings import Priority
from TwitchChannelPointsMiner.classes.entities.Streamer import StreamerSettings
from TwitchChannelPointsMiner.classes.entities.Bet import BetSettings, Condition, DelayMode, FilterCondition, OutcomeKeys, Strategy
from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.logger import LoggerSettings
from TwitchChannelPointsMiner.utils import get_user_agent

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Twitch Farming"
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
        if record.levelno == logging.DEBUG and not record.getMessage().startswith("[WATCH]"):
            return
        try:
            self.app.on_log(record.getMessage(), record.levelname, record.created)
        except Exception:
            pass


class AuthWorker(threading.Thread):
    def __init__(self, app, username, restoring):
        super().__init__(name="TwitchAuth", daemon=True)
        self.app = app
        self.username = username
        self.restoring = restoring
        self.client = None

    def run(self):
        try:
            self.client = Twitch(self.username, get_user_agent("CHROME"))
            success = bool(self.client.login())
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
            self.client.running = True
            self.client.twitch_login.cancelled = False
            if self.stop_requested:
                miner.request_stop()
                return
            miner.mine(streamers=self.channels, followers=False)
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
                        self.app.update_channel_profile(login, {"loaded": True, "is_live": False, "error": message})
        finally:
            self.app.profile_worker_finished(self)


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
        self.rewards = []
        self.window = None
        self.window_maximized = False
        self.online_channels = set()
        self.watch_status = {}
        self.profile_worker = None
        self.pending_profile_logins = set()
        self.restore_started = False
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
        self._load_config()
        self.root_logger = logging.getLogger()
        self.root_logger.setLevel(logging.INFO)
        self.log_handler = AppLogHandler(self)
        self.root_logger.addHandler(self.log_handler)
        self.log_writer = threading.Thread(target=self._log_writer_loop, name="LogWriter", daemon=True)
        self.log_writer.start()
        self.startup_worker = threading.Thread(target=self._startup_tasks, name="StartupTasks", daemon=True)
        self.startup_worker.start()

    def _startup_tasks(self):
        try:
            self._sanitize_existing_log()
        except Exception:
            logging.getLogger("twitch_farming.startup").exception("Startup data preparation failed")

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
        stored_options = data.get("settings", {})
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
            "settings": dict(self.options),
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
            if self.session_running:
                return {"ok": False, "message": "Сначала останови сбор."}
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
            self.bet_settings = {**DEFAULT_BET_SETTINGS, **bets}
            self.custom_presets = presets
            selected = str(payload.get("selected_preset", self.selected_preset))
            self.selected_preset = selected if selected == "Оптимальный" or selected in presets else "Оптимальный"
        ok = self._save_config()
        return {"ok": ok, "message": "Настройки сохранены." if ok else "Не удалось сохранить настройки."}

    def save_channel_editor(self, mode, old_channel, raw_value):
        with self.lock:
            if self.session_running:
                return {"ok": False, "message": "Нельзя менять каналы во время сбора."}
            channel = self.normalize_channel(raw_value)
            if not channel:
                return {"ok": False, "message": "Укажи корректный логин или ссылку Twitch."}
            old = self.normalize_channel(old_channel)
            if mode == "edit":
                if old not in self.channels:
                    return {"ok": False, "message": "Канал больше не найден."}
                if channel != old and channel in self.channels:
                    return {"ok": False, "message": "Этот канал уже есть в списке."}
                self.channels[self.channels.index(old)] = channel
                self.online_channels.discard(old)
                self.channel_profiles.pop(old, None)
            else:
                if channel in self.channels:
                    return {"ok": False, "message": "Этот канал уже добавлен."}
                self.channels.append(channel)
        self._save_config()
        self.refresh_channel_profiles([channel])
        return {"ok": True, "message": "Канал сохранён."}

    def remove_channel(self, channel):
        channel = self.normalize_channel(channel)
        with self.lock:
            if self.session_running:
                return {"ok": False, "message": "Нельзя менять каналы во время сбора."}
            if channel in self.channels:
                self.channels.remove(channel)
                self.online_channels.discard(channel)
                self.channel_profiles.pop(channel, None)
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
            self.auth_worker = AuthWorker(self, username, False)
            worker = self.auth_worker
        self._save_config()
        worker.start()
        return {"ok": True, "message": "Подключение к Twitch…" }

    def _restore_session(self):
        with self.lock:
            username = self.username.strip().lower()
            if not username or (self.auth_worker is not None and self.auth_worker.is_alive()):
                return
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

    def get_auth_state(self):
        with self.lock:
            worker_alive = bool(self.auth_worker is not None and self.auth_worker.is_alive())
            elapsed = max(0, int(time.time() - self.auth_started_at)) if self.auth_started_at else 0
            if (
                not self.authenticated
                and not worker_alive
                and self.auth_worker_state in ("running", "restoring")
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
        worker.start()

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
        next_worker.start()

    def start_farm(self, payload):
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
        line = stamp + "  [" + str(level) + "]  " + self._redact(message)
        with self.lock:
            log_id = self.next_log_id
            self.next_log_id += 1
            self.log_records.append({"id": log_id, "line": line})
            self.log_records = self.log_records[-3000:]
            self.pending_log_lines.append(line)

    def _flush_logs(self):
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
                self.channel_balances[login] = {
                    "login": login,
                    "points": points,
                    "display_name": str(profile.get("display_name") or login),
                    "avatar_url": str(profile.get("avatar_url") or ""),
                }
        valid = set(self.channels)
        self.channel_balances = {
            login: item for login, item in self.channel_balances.items()
            if login in valid
        }
        ranked = sorted(
            self.channel_balances.values(),
            key=lambda item: (int(item.get("points", 0)), item.get("login", "")),
            reverse=True,
        )
        return ranked[:3], {
            login: int(item.get("points", 0))
            for login, item in self.channel_balances.items()
        }

    def get_state(self, after_log_id=0):
        try:
            after = int(after_log_id)
        except (TypeError, ValueError):
            after = 0
        with self.lock:
            top_channels, channel_balances = self._refresh_channel_balances_locked()
            logs = [dict(item) for item in self.log_records if item["id"] > after]
            return {
                "username": self.username,
                "channels": list(self.channels),
                "online_channels": sorted(self.online_channels),
                "watch_status": {key: dict(value) for key, value in self.watch_status.items()},
                "options": dict(self.options),
                "bet_settings": dict(self.bet_settings),
                "custom_presets": dict(self.custom_presets),
                "selected_preset": self.selected_preset,
                "tutorial_done": self.tutorial_done,
                "account_profile": dict(self.account_profile),
                "channel_profiles": {key: dict(value) for key, value in self.channel_profiles.items()},
                "channel_balances": channel_balances,
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
            }

    def get_log_snapshot(self):
        with self.lock:
            pending = list(self.pending_log_lines)
            self.pending_log_lines.clear()
            snapshot_id = self.next_log_id - 1
            if pending:
                try:
                    LOG_DIR.mkdir(parents=True, exist_ok=True)
                    with LOG_FILE.open("a", encoding="utf-8") as file:
                        file.write("\n".join(pending) + "\n")
                except OSError:
                    self.pending_log_lines[0:0] = pending
            try:
                if LOG_FILE.is_file():
                    with LOG_FILE.open("rb") as file:
                        file.seek(0, os.SEEK_END)
                        size = file.tell()
                        file.seek(max(0, size - 18000))
                        content = file.read(18000).decode("utf-8", errors="replace")
                    if size > 18000:
                        content = content.split("\n", 1)[-1]
                    lines = content.splitlines()[-250:]
                else:
                    lines = [item["line"] for item in self.log_records[-250:]]
            except OSError:
                lines = [item["line"] for item in self.log_records[-250:]]
            return {"lines": lines, "last_log_id": snapshot_id}

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

    def close_window(self):
        return self._schedule_window_action("destroy", "WindowClose")

    def sync_window_maximized(self, maximized):
        self.window_maximized = bool(maximized)
        try:
            self.window.evaluate_js("window.setWindowMaximized(" + ("true" if self.window_maximized else "false") + ")")
        except Exception:
            pass

    def on_loaded(self):
        with self.lock:
            if self.restore_started:
                return
            self.restore_started = True
        threading.Thread(target=self._restore_session, name="SessionRestore", daemon=True).start()

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

    def shutdown(self):
        self.on_closing()
        for worker in (self.auth_worker, self.engine_worker, self.profile_worker):
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
    APP_DIR.mkdir(parents=True, exist_ok=True)
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
        easy_drag=False,
        shadow=True,
        background_color="#0B0B12",
        text_select=True,
    )
    api.window = window
    window.events.loaded += api.on_loaded
    window.events.closing += api.on_closing
    window.events.maximized += lambda: api.sync_window_maximized(True)
    window.events.restored += lambda: api.sync_window_maximized(False)
    try:
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(APP_DIR / "webview"), icon=str(ICON_FILE) if ICON_FILE.is_file() else None, debug=False)
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
