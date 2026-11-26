# For documentation on Twitch GraphQL API see:
# https://www.apollographql.com/docs/
# https://github.com/mauricew/twitch-graphql-api
# Full list of available methods: https://azr.ivr.fi/schema/query.doc.html (a bit outdated)


import copy
import logging
import os
import random
import re
import string
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from pathlib import Path
from secrets import choice, token_hex
from typing import Dict, Any

from TwitchChannelPointsMiner.classes.entities.Campaign import Campaign
from TwitchChannelPointsMiner.classes.entities.CommunityGoal import CommunityGoal
from TwitchChannelPointsMiner.classes.entities.Drop import Drop
from TwitchChannelPointsMiner.classes.Exceptions import (
    StreamerDoesNotExistException,
    StreamerIsOfflineException,
)
from TwitchChannelPointsMiner.classes.Settings import (
    Events,
    FollowersOrder,
    Settings,
)
from TwitchChannelPointsMiner.classes.TwitchLogin import TwitchLogin
from TwitchChannelPointsMiner.constants import (
    CLIENT_ID,
    CLIENT_VERSION,
    URL,
    GQLOperations,
)
from TwitchChannelPointsMiner.utils import (
    _millify,
    create_chunks,
    internet_connection_available,
)

logger = logging.getLogger(__name__)
JsonType = Dict[str, Any]


class Twitch(object):
    WATCH_SLOT_COUNT = 2
    WATCH_ROTATION_INTERVAL = 330.0

    __slots__ = [
        "cookies_file",
        "user_agent",
        "twitch_login",
        "running",
        "device_id",
        # "integrity",
        # "integrity_expire",
        "client_session",
        "client_version",
        "twilight_build_id_pattern",
        "auth_lock",
        "_watch_rotation_cursor",
        "_watch_rotation_started_at",
        "_watch_rotation_signature",
        "_watch_online_signature",
        "active_watch_channels",
    ]

    def __init__(self, username, user_agent, password=None):
        cookies_path = Path(os.environ.get("APPDATA", str(Path.home()))) / "Twitch Farming" / "cookies"
        cookies_path.mkdir(parents=True, exist_ok=True)
        self.cookies_file = str(cookies_path / f"{username}.dat")
        self.user_agent = user_agent
        self.device_id = "".join(
            choice(string.ascii_letters + string.digits) for _ in range(32)
        )
        self.twitch_login = TwitchLogin(
            CLIENT_ID, self.device_id, username, self.user_agent, password=password
        )
        self.running = True
        self.auth_lock = threading.Lock()
        self._watch_rotation_cursor = 0
        self._watch_rotation_started_at = 0.0
        self._watch_rotation_signature = None
        self._watch_online_signature = ()
        self.active_watch_channels = []
        # self.integrity = None
        # self.integrity_expire = 0
        self.client_session = token_hex(16)
        self.client_version = CLIENT_VERSION
        self.twilight_build_id_pattern = re.compile(
            r'window\.__twilightBuildID\s*=\s*"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"'
        )

    def restore_session(self):
        if not os.path.isfile(self.cookies_file):
            return False
        try:
            self.twitch_login.load_cookies(self.cookies_file)
            token = self.twitch_login.get_auth_token()
            if not token:
                return False
            self.twitch_login.set_token(token)
            if self.twitch_login.check_login():
                logger.info("Restored the saved Twitch session.")
                return True
            if self.twitch_login.refresh_token and self.twitch_login.refresh_access_token():
                self.twitch_login.save_cookies(self.cookies_file)
                return True
        except Exception as error:
            logger.warning(f"Saved Twitch session could not be restored: {error}")
        return False

    def login(self, force_reauth=False):
        if not force_reauth and self.restore_session():
            return True
        if self.twitch_login.login_flow():
            self.twitch_login.validate_token()
            self.twitch_login.save_cookies(self.cookies_file)
            logger.info("Twitch session saved securely.")
            return True
        return False

    def _reauthenticate_after_expiry(self, expired_token):
        with self.auth_lock:
            if self.twitch_login.token != expired_token and self.twitch_login.check_login():
                return True
            logger.warning("Twitch session expired. A new device confirmation is required.")
            self.twitch_login.cancelled = False
            if not self.twitch_login.login_flow():
                return False
            self.twitch_login.validate_token()
            self.twitch_login.save_cookies(self.cookies_file)
            logger.info("Twitch session renewed after device confirmation.")
            return True

    def helix_get_payload(self, endpoint, params=None):
        token = self.twitch_login.token
        if not token:
            raise RuntimeError("Сначала подключи аккаунт Twitch.")
        response = requests.get(
            f"https://api.twitch.tv/helix/{endpoint}",
            headers={
                "Client-ID": self.twitch_login.client_id,
                "Authorization": f"Bearer {token}",
            },
            params=params or {},
            timeout=7,
        )
        if response.status_code == 401 and self._reauthenticate_after_expiry(token):
            token = self.twitch_login.token
            response = requests.get(
                f"https://api.twitch.tv/helix/{endpoint}",
                headers={
                    "Client-ID": self.twitch_login.client_id,
                    "Authorization": f"Bearer {token}",
                },
                params=params or {},
                timeout=7,
            )
        response.raise_for_status()
        return response.json()

    def helix_get(self, endpoint, params=None):
        return self.helix_get_payload(endpoint, params).get("data", [])

    def get_user_info(self, login):
        users = self.helix_get("users", {"login": login})
        return users[0] if users else None

    def get_stream_by_login(self, login):
        streams = self.helix_get("streams", {"user_login": login})
        return streams[0] if streams else None

    # === STREAMER / STREAM / INFO === #
    def update_stream(self, streamer, stream_info=None):
        if stream_info is None:
            stream_info = self.get_stream_info(streamer)
        if stream_info is None:
            raise StreamerIsOfflineException
        game_id = stream_info.get("game_id") or ""
        game_name = stream_info.get("game_name") or ""
        game = {"id": game_id, "name": game_name, "displayName": game_name} if game_id or game_name else {}
        tags = [{"id": tag, "localizedName": tag} for tag in (stream_info.get("tags") or [])]
        streamer.stream.update(
            broadcast_id=stream_info.get("id"),
            title=stream_info.get("title") or "",
            game=game,
            tags=tags,
            viewers_count=int(stream_info.get("viewer_count") or 0),
        )
        if streamer.settings.claim_drops is True and streamer.stream.game_name() and streamer.stream.game_id():
            try:
                streamer.stream.campaigns_ids = self.__get_campaign_ids_from_streamer(streamer)
            except Exception as error:
                logger.warning(f"Не удалось обновить список Twitch Drops для @{streamer.username}: {error}")
        streamer.stream.payload = self._build_minute_watched_event(
            streamer,
            self.twitch_login.get_user_id(),
        )

    def get_spade_url(self, streamer):
        try:
            # fixes AttributeError: 'NoneType' object has no attribute 'group'
            # headers = {"User-Agent": self.user_agent}
            from TwitchChannelPointsMiner.constants import USER_AGENTS

            headers = {"User-Agent": USER_AGENTS["Linux"]["FIREFOX"]}

            main_page_request = requests.get(streamer.streamer_url, headers=headers, timeout=5)
            if main_page_request.status_code != 200:
                return
            regex_settings = "(https://static.twitchcdn.net/config/settings.*?js|https://assets.twitch.tv/config/settings.*?.js)"
            settings_match = re.search(regex_settings, main_page_request.text)
            if not settings_match:
                return
            settings_request = requests.get(settings_match.group(1), headers=headers, timeout=5)
            if settings_request.status_code != 200:
                return
            spade_match = re.search('"spade_url":"(.*?)"', settings_request.text)
            if spade_match:
                streamer.stream.spade_url = spade_match.group(1)
        except requests.exceptions.RequestException as error:
            logger.warning(f"Не удалось получить адрес Twitch Spade для @{streamer.username}: {error}")

    def get_broadcast_id(self, streamer):
        json_data = copy.deepcopy(GQLOperations.WithIsStreamLiveQuery)
        json_data["variables"] = {"id": streamer.channel_id}
        response = self.post_gql_request(json_data)
        if response != {}:
            stream = response["data"]["user"]["stream"]
            if stream is not None:
                return stream["id"]
            else:
                raise StreamerIsOfflineException

    def get_stream_info(self, streamer):
        return self.get_stream_by_login(streamer.username)

    def check_streamer_online(self, streamer):
        if not self.running:
            return
        if streamer.is_online is False and streamer.offline_at and time.time() < streamer.offline_at + 12:
            return
        try:
            stream_info = self.get_stream_info(streamer)
            if stream_info is None:
                if streamer.is_online:
                    streamer.set_offline()
                return
            self.update_stream(streamer, stream_info)
            if streamer.is_online is False and not streamer.stream.spade_url:
                self.get_spade_url(streamer)
            streamer.set_online()
        except StreamerIsOfflineException:
            if streamer.is_online:
                streamer.set_offline()
        except requests.RequestException as error:
            logger.warning(f"Не удалось проверить эфир @{streamer.username}: {error}")
        except Exception as error:
            logger.error(f"Ошибка проверки эфира @{streamer.username}: {error}")

    def get_channel_id(self, streamer_username):
        user = self.get_user_info(streamer_username)
        if not user or not user.get("id"):
            raise StreamerDoesNotExistException
        return user["id"]

    def get_followers(
        self, limit: int = 100, order: FollowersOrder = FollowersOrder.ASC
    ):
        json_data = copy.deepcopy(GQLOperations.ChannelFollows)
        json_data["variables"] = {"limit": limit, "order": str(order)}
        has_next = True
        last_cursor = ""
        follows = []
        while has_next is True:
            json_data["variables"]["cursor"] = last_cursor
            json_response = self.post_gql_request(json_data)
            try:
                follows_response = json_response["data"]["user"]["follows"]
                last_cursor = None
                for f in follows_response["edges"]:
                    follows.append(f["node"]["login"].lower())
                    last_cursor = f["cursor"]

                has_next = follows_response["pageInfo"]["hasNextPage"]
            except KeyError:
                return []
        return follows

    def update_raid(self, streamer, raid):
        if streamer.raid != raid:
            streamer.raid = raid
            json_data = copy.deepcopy(GQLOperations.JoinRaid)
            json_data["variables"] = {"input": {"raidID": raid.raid_id}}
            self.post_gql_request(json_data)

            logger.info(
                f"Joining raid from {streamer} to {raid.target_login}!",
                extra={"emoji": ":performing_arts:",
                       "event": Events.JOIN_RAID},
            )

    def viewer_is_mod(self, streamer):
        json_data = copy.deepcopy(GQLOperations.ModViewChannelQuery)
        json_data["variables"] = {"channelLogin": streamer.username}
        response = self.post_gql_request(json_data)
        try:
            streamer.viewer_is_mod = response["data"]["user"]["self"]["isModerator"]
        except (ValueError, KeyError):
            streamer.viewer_is_mod = False

    # === 'GLOBALS' METHODS === #
    # Create chunk of sleep of speed-up the break loop after CTRL+C
    def __chuncked_sleep(self, seconds, chunk_size=3):
        sleep_time = max(seconds, 0) / chunk_size
        for i in range(0, chunk_size):
            time.sleep(sleep_time)
            if self.running is False:
                break

    def __check_connection_handler(self, chunk_size):
        # The success rate It's very hight usually. Why we have failed?
        # Check internet connection ...
        while self.running and internet_connection_available() is False:
            random_sleep = random.randint(1, 3)
            logger.warning(
                f"No internet connection available! Retry after {random_sleep}m"
            )
            self.__chuncked_sleep(random_sleep * 60, chunk_size=chunk_size)

    def post_gql_request(self, json_data):
        request_items = json_data if isinstance(json_data, list) else [json_data]
        operation_names = [
            str(item.get("operationName") or "batch")
            for item in request_items
            if isinstance(item, dict)
        ]
        operation = ", ".join(dict.fromkeys(operation_names)) or "batch"
        is_drops_query = any(
            name in {"Inventory", "ViewerDropsDashboard", "DropCampaignDetails"}
            for name in operation_names
        )
        response = None
        try:
            token = self.twitch_login.get_auth_token()
            headers = {
                "Authorization": f"OAuth {token}",
                "Client-Id": CLIENT_ID,
                "Client-Session-Id": self.client_session,
                "Client-Version": self.update_client_version(),
                "User-Agent": self.user_agent,
                "X-Device-Id": self.device_id,
            }
            response = requests.post(
                GQLOperations.url,
                json=json_data,
                headers=headers,
                timeout=7,
            )
            if response.status_code == 401 and self._reauthenticate_after_expiry(token):
                token = self.twitch_login.get_auth_token()
                headers["Authorization"] = f"OAuth {token}"
                response = requests.post(
                    GQLOperations.url,
                    json=json_data,
                    headers=headers,
                    timeout=7,
                )
            logger.debug(f"GQLOperations {operation} · HTTP {response.status_code}")
            if is_drops_query and response.status_code >= 400:
                logger.warning(
                    f"GQLOperations {operation} · HTTP {response.status_code} · Ответ Twitch: {response.text[:2500]}"
                )
            payload = response.json()
            errors = []
            if isinstance(payload, dict):
                errors = payload.get("errors") or []
            elif isinstance(payload, list):
                errors = [
                    item.get("errors")
                    for item in payload
                    if isinstance(item, dict) and item.get("errors")
                ]
            if is_drops_query and errors and response.status_code < 400:
                logger.warning(
                    f"GQLOperations {operation} · Ошибка GraphQL · Ответ Twitch: {str(payload)[:2500]}"
                )
            return payload
        except requests.exceptions.RequestException as error:
            response_text = str(getattr(getattr(error, "response", None), "text", "") or "")[:2500]
            if is_drops_query:
                logger.warning(
                    f"GQLOperations {operation} · Ошибка запроса: {error} · Ответ Twitch: {response_text}"
                )
            else:
                logger.error(f"Error with GQLOperations ({operation}): {error}")
            return {}
        except ValueError as error:
            response_text = str(getattr(response, "text", "") or "")[:2500]
            if is_drops_query:
                logger.warning(
                    f"GQLOperations {operation} · Некорректный JSON: {error} · Ответ Twitch: {response_text}"
                )
            else:
                logger.error(f"Invalid JSON from GQLOperations ({operation}): {error}")
            return {}

    # Request for Integrity Token
    # Twitch needs Authorization, Client-Id, X-Device-Id to generate JWT which is used for authorize gql requests
    # Regenerate Integrity Token 5 minutes before expire
    """def post_integrity(self):
        if (
            self.integrity_expire - datetime.now().timestamp() * 1000 > 5 * 60 * 1000
            and self.integrity is not None
        ):
            return self.integrity
        try:
            response = requests.post(
                GQLOperations.integrity_url,
                json={},
                headers={
                    "Authorization": f"OAuth {self.twitch_login.get_auth_token()}",
                    "Client-Id": CLIENT_ID,
                    "Client-Session-Id": self.client_session,
                    "Client-Version": self.update_client_version(),
                    "User-Agent": self.user_agent,
                    "X-Device-Id": self.device_id,
                },
            )
            logger.debug(
                f"Data: [], Status code: {response.status_code}, Content: {response.text}"
            )
            self.integrity = response.json().get("token", None)
            # logger.info(f"integrity: {self.integrity}")

            if self.isBadBot(self.integrity) is True:
                logger.info(
                    "Uh-oh, Twitch has detected this miner as a \"Bad Bot\". Don't worry.")

            self.integrity_expire = response.json().get("expiration", 0)
            # logger.info(f"integrity_expire: {self.integrity_expire}")
            return self.integrity
        except requests.exceptions.RequestException as e:
            logger.error(f"Error with post_integrity: {e}")
            return self.integrity

    # verify the integrity token's contents for the "is_bad_bot" flag
    def isBadBot(self, integrity):
        stripped_token: str = self.integrity.split('.')[2] + "=="
        messy_json: str = urlsafe_b64decode(
            stripped_token.encode()).decode(errors="ignore")
        match = re.search(r'(.+)(?<="}).+$', messy_json)
        if match is None:
            # raise MinerException("Unable to parse the integrity token")
            logger.info("Unable to parse the integrity token. Don't worry.")
            return
        decoded_header = json.loads(match.group(1))
        # logger.info(f"decoded_header: {decoded_header}")
        if decoded_header.get("is_bad_bot", "false") != "false":
            return True
        else:
            return False"""

    def update_client_version(self):
        try:
            response = requests.get(URL, timeout=4)
            if response.status_code != 200:
                logger.debug(
                    f"Error with update_client_version: {response.status_code}"
                )
                return self.client_version
            matcher = re.search(self.twilight_build_id_pattern, response.text)
            if not matcher:
                logger.debug("Error with update_client_version: no match")
                return self.client_version
            self.client_version = matcher.group(1)
            logger.debug(f"Client version: {self.client_version}")
            return self.client_version
        except requests.exceptions.RequestException as e:
            logger.error(f"Error with update_client_version: {e}")
            return self.client_version

    @staticmethod
    def _get_online_streamer_indices(streamers):
        return [
            index
            for index, streamer in enumerate(streamers)
            if streamer.is_online is True
        ]

    @staticmethod
    def _build_minute_watched_event(streamer, user_id):
        stream = streamer.stream
        channel_id = str(streamer.channel_id or "").strip()
        broadcast_id = str(stream.broadcast_id or "").strip()
        game_name = str(stream.game_name() or "").strip()
        game_id = str(stream.game_id() or "").strip()
        user_id_text = str(user_id or "").strip()
        if not all((channel_id, broadcast_id, game_name, game_id, user_id_text.isdigit())):
            return None
        properties = {
            "channel_id": channel_id,
            "broadcast_id": broadcast_id,
            "channel": streamer.username,
            "client_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "game": game_name,
            "game_id": game_id,
            "hidden": False,
            "is_live": True,
            "live": True,
            "location": "channel",
            "logged_in": True,
            "minutes_logged": 1,
            "muted": False,
            "player": "site",
            "user_id": int(user_id_text),
        }
        return [{"event": "minute-watched", "properties": properties}]

    @staticmethod
    def _parse_hls_playlist_urls(playlist, base_url, master=False):
        if not isinstance(playlist, str) or not playlist.lstrip().startswith("#EXTM3U"):
            return []
        expected_tag = "#EXT-X-STREAM-INF:" if master else "#EXTINF:"
        urls = []
        pending = False
        try:
            for raw in playlist.splitlines():
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    if line.startswith(expected_tag):
                        pending = True
                    continue
                if not pending:
                    return []
                resolved = urljoin(base_url, line)
                parsed = urlparse(resolved)
                if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
                    return []
                if master and not parsed.path.lower().endswith(".m3u8"):
                    return []
                urls.append(resolved)
                pending = False
            return [] if pending else urls
        except (TypeError, ValueError):
            return []

    def _get_hls_playlist_url(self, streamer):
        stream = streamer.stream
        if stream.hls_url:
            return stream.hls_url
        json_data = copy.deepcopy(GQLOperations.PlaybackAccessToken)
        json_data["variables"] = {
            "login": streamer.username,
            "isLive": True,
            "isVod": False,
            "vodID": "",
            "playerType": "site",
            "platform": "web",
        }
        response = self.post_gql_request(json_data)
        data = response.get("data") if isinstance(response, dict) else None
        token_data = data.get("streamPlaybackAccessToken") if isinstance(data, dict) else None
        if not isinstance(token_data, dict):
            return None
        signature = token_data.get("signature")
        value = token_data.get("value")
        if not signature or not value:
            return None
        master_url = f"https://usher.ttvnw.net/api/channel/hls/{streamer.username}.m3u8"
        try:
            response = requests.get(
                master_url,
                params={"sig": signature, "token": value},
                headers={"User-Agent": self.user_agent},
                timeout=8,
            )
            if response.status_code != 200:
                return None
            variants = self._parse_hls_playlist_urls(
                response.text,
                response.url or master_url,
                master=True,
            )
            if not variants:
                return None
            stream.hls_url = variants[-1]
            return stream.hls_url
        except requests.exceptions.RequestException as error:
            logger.debug(f"HLS master playlist request failed for @{streamer.username}: {type(error).__name__}")
            return None

    def _watch_stream_playlist(self, streamer):
        stream = streamer.stream
        broadcast_id = str(stream.broadcast_id or "").strip()
        if not self.running or streamer.is_online is not True or not broadcast_id:
            return False
        playlist_url = self._get_hls_playlist_url(streamer)
        if not playlist_url or not self.running:
            return False
        headers = {"User-Agent": self.user_agent}
        try:
            response = requests.get(playlist_url, headers=headers, timeout=8)
            if response.status_code != 200:
                if response.status_code in (401, 403, 404):
                    stream.hls_url = None
                return False
            segments = self._parse_hls_playlist_urls(
                response.text,
                response.url or playlist_url,
            )
            if not segments:
                return False
            succeeded = True
            new_segments = 0
            for segment_url in segments:
                if (
                    not self.running
                    or streamer.is_online is not True
                    or str(stream.broadcast_id or "").strip() != broadcast_id
                ):
                    return False
                if segment_url in stream.watched_segments:
                    continue
                try:
                    segment_response = requests.head(
                        segment_url,
                        headers=headers,
                        timeout=5,
                    )
                    if segment_response.status_code == 200:
                        stream.watched_segments[segment_url] = None
                        new_segments += 1
                        if len(stream.watched_segments) > 256:
                            stream.watched_segments.popitem(last=False)
                    else:
                        succeeded = False
                        if segment_response.status_code in (401, 403, 404):
                            stream.hls_url = None
                except requests.exceptions.RequestException:
                    succeeded = False
            if new_segments:
                logger.debug(f"[WATCH] @{streamer.username}: HLS обработано новых сегментов: {new_segments}.")
            return succeeded
        except requests.exceptions.RequestException as error:
            logger.debug(f"HLS media playlist request failed for @{streamer.username}: {type(error).__name__}")
            return False

    def _log_drop_progress(self, streamer):
        for campaign in streamer.stream.campaigns:
            for drop in campaign.drops:
                if drop.has_preconditions_met is not False and drop.is_printable is True:
                    drop_messages = [
                        f"{streamer} is streaming {streamer.stream}",
                        f"Campaign: {campaign}",
                        f"Drop: {drop}",
                        f"{drop.progress_bar()}",
                    ]
                    for single_line in drop_messages:
                        logger.info(
                            single_line,
                            extra={
                                "event": Events.DROP_STATUS,
                                "skip_telegram": True,
                                "skip_discord": True,
                                "skip_webhook": True,
                                "skip_matrix": True,
                                "skip_gotify": True,
                            },
                        )
                    for service in ("telegram", "discord", "webhook", "gotify"):
                        notifier = getattr(Settings.logger, service, None)
                        if notifier is not None:
                            notifier.send("\n".join(drop_messages), Events.DROP_STATUS)

    def _send_minute_watched_for_streamer(self, streamer):
        if not self.running or streamer.is_online is not True:
            return
        self._watch_stream_playlist(streamer)
        if not self.running or streamer.is_online is not True:
            return
        stream = streamer.stream
        now = time.monotonic()
        if stream.last_minute_watched_sent and now - stream.last_minute_watched_sent < 59:
            return
        user_id = self.twitch_login.get_user_id()
        payload = self._build_minute_watched_event(streamer, user_id)
        if payload is None:
            logger.warning(
                f"WATCH_STATUS: {streamer.username}|error|Пропущен minute-watched: Twitch не предоставил channel_id, broadcast_id, game, game_id или user_id."
            )
            return
        if not stream.spade_url and now - stream.last_spade_url_attempt >= 60:
            stream.last_spade_url_attempt = now
            self.get_spade_url(streamer)
        if not stream.spade_url:
            return
        if not self.running or streamer.is_online is not True:
            return
        stream.payload = payload
        stream.last_minute_watched_sent = time.monotonic()
        try:
            response = requests.post(
                stream.spade_url,
                data=stream.encode_payload(),
                headers={"User-Agent": self.user_agent},
                timeout=8,
            )
            if response.status_code == 204:
                stream.update_minute_watched()
                logger.info(
                    f"WATCH_STATUS: {streamer.username}|accepted|Spade HTTP 204; локальный таймер: {stream.minute_watched:.2f} мин."
                )
                self._log_drop_progress(streamer)
            else:
                logger.warning(
                    f"WATCH_STATUS: {streamer.username}|error|minute-watched HTTP {response.status_code}."
                )
        except requests.exceptions.RequestException as error:
            logger.warning(
                f"WATCH_STATUS: {streamer.username}|error|Ошибка отправки minute-watched: {type(error).__name__}."
            )

    @staticmethod
    def _has_active_inventory_drop(streamer):
        settings = getattr(streamer, "settings", None)
        if getattr(settings, "claim_drops", False) is not True:
            return False
        stream = getattr(streamer, "stream", None)
        for campaign in getattr(stream, "campaigns", []) or []:
            if getattr(campaign, "in_inventory", False) is not True:
                continue
            if any(
                not getattr(drop, "is_claimed", False)
                for drop in (getattr(campaign, "drops", []) or [])
            ):
                return True
        return False

    def _select_minute_watched_streamers(self, online_streamers, now=None):
        current_time = time.monotonic() if now is None else float(now)
        online = list(online_streamers or [])
        if not online:
            self._watch_rotation_cursor = 0
            self._watch_rotation_started_at = current_time
            self._watch_rotation_signature = None
            return []

        priority_streamer = next(
            (
                streamer
                for streamer in online
                if self._has_active_inventory_drop(streamer)
            ),
            None,
        )
        rotating_streamers = [
            streamer for streamer in online if streamer is not priority_streamer
        ]
        rotating_slots = self.WATCH_SLOT_COUNT - int(priority_streamer is not None)
        signature = (
            str(priority_streamer.username).lower() if priority_streamer else "",
            tuple(str(streamer.username).lower() for streamer in rotating_streamers),
        )

        if signature != self._watch_rotation_signature:
            self._watch_rotation_signature = signature
            self._watch_rotation_cursor = 0
            self._watch_rotation_started_at = current_time
        elif (
            rotating_streamers
            and rotating_slots > 0
            and len(rotating_streamers) > rotating_slots
            and current_time - self._watch_rotation_started_at >= self.WATCH_ROTATION_INTERVAL
        ):
            self._watch_rotation_cursor = (
                self._watch_rotation_cursor + rotating_slots
            ) % len(rotating_streamers)
            self._watch_rotation_started_at = current_time

        selected = [priority_streamer] if priority_streamer is not None else []
        selected_count = min(rotating_slots, len(rotating_streamers))
        selected.extend(
            rotating_streamers[
                (self._watch_rotation_cursor + offset) % len(rotating_streamers)
            ]
            for offset in range(selected_count)
        )
        return selected

    def send_minute_watched_events(self, streamers, priority, chunk_size=3):
        watch_streak_counters = {}
        while self.running:
            cycle_started = time.monotonic()
            try:
                for index in self._get_online_streamer_indices(streamers):
                    if not self.running:
                        break
                    streamer = streamers[index]
                    if streamer.stream.update_required():
                        self.check_streamer_online(streamer)

                online_streamers = [
                    streamers[index]
                    for index in self._get_online_streamer_indices(streamers)
                ]
                previous_slots = set(self.active_watch_channels)
                current_streak_counters = {}
                for streamer in online_streamers:
                    history = getattr(streamer, "history", {}) or {}
                    streak = history.get("WATCH_STREAK", {}) if isinstance(history, dict) else {}
                    try:
                        current_streak_counters[str(streamer.username).lower()] = int(
                            streak.get("counter", 0)
                        )
                    except (AttributeError, TypeError, ValueError):
                        current_streak_counters[str(streamer.username).lower()] = 0

                streak_rewarded = any(
                    current_streak_counters.get(username, 0)
                    > watch_streak_counters.get(username, 0)
                    for username in previous_slots
                )
                watch_streak_counters = current_streak_counters
                if streak_rewarded:
                    self._watch_rotation_started_at = (
                        time.monotonic() - self.WATCH_ROTATION_INTERVAL
                    )
                    logger.info(
                        "Получен WATCH_STREAK на активном слоте; запускаю досрочную ротацию."
                    )

                selected_streamers = self._select_minute_watched_streamers(
                    online_streamers
                )
                selected_names = [
                    str(streamer.username).lower()
                    for streamer in selected_streamers
                ]
                online_signature = tuple(
                    str(streamer.username).lower()
                    for streamer in online_streamers
                )
                if (
                    selected_names != self.active_watch_channels
                    or online_signature != self._watch_online_signature
                ):
                    self.active_watch_channels = selected_names
                    self._watch_online_signature = online_signature
                    online_names = ", ".join(
                        f"@{streamer.username}" for streamer in online_streamers
                    ) or "нет"
                    slot_details = "; ".join(
                        f"{index}. @{streamer.username}"
                        + (
                            " [Drops]"
                            if self._has_active_inventory_drop(streamer)
                            else ""
                        )
                        for index, streamer in enumerate(selected_streamers, start=1)
                    ) or "нет активных слотов"
                    logger.info(
                        f"Отправка minute-watched: {len(selected_streamers)} слота из "
                        f"{len(online_streamers)} онлайн-каналов; пул: {online_names}; "
                        f"активно: {slot_details}"
                    )

                if selected_streamers and self.running:
                    with ThreadPoolExecutor(
                        max_workers=len(selected_streamers),
                        thread_name_prefix="twitch-watch",
                    ) as executor:
                        futures = {
                            executor.submit(
                                self._send_minute_watched_for_streamer,
                                streamer,
                            ): streamer
                            for streamer in selected_streamers
                        }
                        for future in as_completed(futures):
                            streamer = futures[future]
                            try:
                                future.result()
                            except Exception as error:
                                logger.error(
                                    f"Unexpected watch error for @{streamer.username}: "
                                    f"{type(error).__name__}: {error}",
                                    exc_info=True,
                                )

                self.__chuncked_sleep(
                    max(0, 10 - (time.monotonic() - cycle_started)),
                    chunk_size=chunk_size,
                )
            except Exception:
                logger.error("Exception raised in send minute watched", exc_info=True)
                self.__chuncked_sleep(2, chunk_size=chunk_size)
        self.active_watch_channels = []

    # === CHANNEL POINTS / PREDICTION === #
    # Load the amount of current points for a channel, check if a bonus is available
    def _load_channel_points_branding(self, streamer):
        try:
            request = {
                "operationName": "ChannelPointsBranding",
                "variables": {"channelName": streamer.username},
                "query": "query ChannelPointsBranding($channelName: String!) { channel(name: $channelName) { communityPointsSettings { isEnabled name image { url url2x url4x } defaultImage { url url2x url4x } } } }",
            }
            response = self.post_gql_request(request)
            errors = response.get("errors") if isinstance(response, dict) else None
            if errors:
                details = "; ".join(str(item.get("message") or item) for item in errors[:2])
                logger.warning(f"Twitch не вернул оформление Channel Points для @{streamer.username}: {details[:220]}")
                return
            data = response.get("data") if isinstance(response, dict) else None
            channel = data.get("channel") if isinstance(data, dict) else None
            settings = channel.get("communityPointsSettings") if isinstance(channel, dict) else None
            if not isinstance(settings, dict):
                logger.warning(f"Twitch не вернул настройки Channel Points для @{streamer.username}.")
                return
            image = settings.get("image") or settings.get("defaultImage") or {}
            image_url = image.get("url2x") or image.get("url") or image.get("url4x")
            image_url = str(image_url or "")
            streamer.channel_points_name = str(settings.get("name") or "").strip()
            streamer.channel_points_image = image_url if image_url.startswith("https://") else ""
            streamer.channel_points_branding_loaded = True
        except Exception as error:
            logger.warning(f"Не удалось загрузить оформление Channel Points для @{streamer.username}: {str(error)[:220]}")
        finally:
            streamer.channel_points_branding_loading = False

    def load_channel_points_context(self, streamer):
        json_data = copy.deepcopy(GQLOperations.ChannelPointsContext)
        json_data["variables"] = {"channelLogin": streamer.username}

        response = self.post_gql_request(json_data)
        if response != {}:
            if "data" not in response or response["data"] is None:
                logger.warning(f"Invalid response from load_channel_points_context for {streamer.username}: {response}")
                return
            if response["data"]["community"] is None:
                raise StreamerDoesNotExistException
            channel = response["data"]["community"]["channel"]
            community_points = channel["self"]["communityPoints"]
            streamer.channel_points = community_points["balance"]
            streamer.activeMultipliers = community_points["activeMultipliers"]
            if not streamer.channel_points_branding_loaded and not streamer.channel_points_branding_loading:
                streamer.channel_points_branding_loading = True
                try:
                    threading.Thread(
                        target=self._load_channel_points_branding,
                        args=(streamer,),
                        name=f"PointsBranding-{streamer.username}",
                        daemon=True,
                    ).start()
                except Exception:
                    streamer.channel_points_branding_loading = False

            if streamer.settings.community_goals is True:
                streamer.community_goals = {
                    goal["id"]: CommunityGoal.from_gql(goal)
                    for goal in channel["communityPointsSettings"]["goals"]
                }

            if community_points["availableClaim"] is not None:
                self.claim_bonus(
                    streamer, community_points["availableClaim"]["id"])

            if streamer.settings.community_goals is True:
                self.contribute_to_community_goals(streamer)

            if streamer.settings.community_goals is True:
                self.contribute_to_community_goals(streamer)

    def make_predictions(self, event):
        decision = event.bet.calculate(event.streamer.channel_points)
        # selector_index = 0 if decision["choice"] == "A" else 1

        logger.info(
            f"Going to complete bet for {event}",
            extra={
                "emoji": ":four_leaf_clover:",
                "event": Events.BET_GENERAL,
            },
        )
        if event.status == "ACTIVE":
            skip, compared_value = event.bet.skip()
            if skip is True:
                logger.info(
                    f"Skip betting for the event {event}",
                    extra={
                        "emoji": ":pushpin:",
                        "event": Events.BET_FILTERS,
                    },
                )
                logger.info(
                    f"Skip settings {event.bet.settings.filter_condition}, current value is: {compared_value}",
                    extra={
                        "emoji": ":pushpin:",
                        "event": Events.BET_FILTERS,
                    },
                )
            else:
                if decision["amount"] >= 10:
                    logger.info(
                        # f"Place {_millify(decision['amount'])} channel points on: {event.bet.get_outcome(selector_index)}",
                        f"Place {_millify(decision['amount'])} channel points on: {event.bet.get_outcome(decision['choice'])}",
                        extra={
                            "emoji": ":four_leaf_clover:",
                            "event": Events.BET_GENERAL,
                        },
                    )

                    json_data = copy.deepcopy(GQLOperations.MakePrediction)
                    json_data["variables"] = {
                        "input": {
                            "eventID": event.event_id,
                            "outcomeID": decision["id"],
                            "points": decision["amount"],
                            "transactionID": token_hex(16),
                        }
                    }
                    response = self.post_gql_request(json_data)
                    if (
                        "data" in response
                        and "makePrediction" in response["data"]
                        and "error" in response["data"]["makePrediction"]
                        and response["data"]["makePrediction"]["error"] is not None
                    ):
                        error_code = response["data"]["makePrediction"]["error"]["code"]
                        logger.error(
                            f"Failed to place bet, error: {error_code}",
                            extra={
                                "emoji": ":four_leaf_clover:",
                                "event": Events.BET_FAILED,
                            },
                        )
                else:
                    logger.info(
                        f"Bet won't be placed as the amount {_millify(decision['amount'])} is less than the minimum required 10",
                        extra={
                            "emoji": ":four_leaf_clover:",
                            "event": Events.BET_GENERAL,
                        },
                    )
        else:
            logger.info(
                f"Oh no! The event is not active anymore! Current status: {event.status}",
                extra={
                    "emoji": ":disappointed_relieved:",
                    "event": Events.BET_FAILED,
                },
            )

    def claim_bonus(self, streamer, claim_id):
        if Settings.logger.less is False:
            logger.info(
                f"Claiming the bonus for {streamer}!",
                extra={"emoji": ":gift:", "event": Events.BONUS_CLAIM},
            )

        json_data = copy.deepcopy(GQLOperations.ClaimCommunityPoints)
        json_data["variables"] = {
            "input": {"channelID": streamer.channel_id, "claimID": claim_id}
        }
        self.post_gql_request(json_data)

    # === MOMENTS === #
    def claim_moment(self, streamer, moment_id):
        if Settings.logger.less is False:
            logger.info(
                f"Claiming the moment for {streamer}!",
                extra={"emoji": ":video_camera:",
                       "event": Events.MOMENT_CLAIM},
            )

        json_data = copy.deepcopy(GQLOperations.CommunityMomentCallout_Claim)
        json_data["variables"] = {"input": {"momentID": moment_id}}
        response = self.post_gql_request(json_data)
        if response and not response.get("errors"):
            logger.info(f"Twitch Moment claimed: {streamer.username}")

    # === CAMPAIGNS / DROPS / INVENTORY === #
    def __get_campaign_ids_from_streamer(self, streamer):
        json_data = copy.deepcopy(
            GQLOperations.DropsHighlightService_AvailableDrops)
        json_data["variables"] = {"channelID": streamer.channel_id}
        response = self.post_gql_request(json_data)
        try:
            return (
                []
                if response["data"]["channel"]["viewerDropCampaigns"] is None
                else [
                    item["id"]
                    for item in response["data"]["channel"]["viewerDropCampaigns"]
                ]
            )
        except (ValueError, KeyError):
            return []

    def _warn_drops_response(self, operation, reason, response):
        logger.warning(
            f"{operation}: {reason}. Ответ Twitch: {str(response)[:2500]}"
        )

    def __get_inventory(self):
        json_data = copy.deepcopy(GQLOperations.Inventory)
        response = self.post_gql_request(json_data)
        if not isinstance(response, dict):
            self._warn_drops_response("Inventory", "неожиданный формат ответа", response)
            return {}
        if response.get("errors"):
            self._warn_drops_response("Inventory", "Twitch вернул GraphQL-ошибку", response)
        current_user = (response.get("data") or {}).get("currentUser")
        inventory = current_user.get("inventory") if isinstance(current_user, dict) else None
        if not isinstance(inventory, dict):
            self._warn_drops_response("Inventory", "в ответе отсутствует data.currentUser.inventory", response)
            return {}
        progress = inventory.get("dropCampaignsInProgress")
        if not isinstance(progress, list):
            self._warn_drops_response("Inventory", "отсутствует массив dropCampaignsInProgress", response)
            inventory["dropCampaignsInProgress"] = []
        elif not progress:
            self._warn_drops_response("Inventory", "массив dropCampaignsInProgress пуст", response)
        return inventory

    def __get_drops_dashboard(self, status=None):
        json_data = copy.deepcopy(GQLOperations.ViewerDropsDashboard)
        response = self.post_gql_request(json_data)
        if not isinstance(response, dict):
            self._warn_drops_response("ViewerDropsDashboard", "неожиданный формат ответа", response)
            return []
        if response.get("errors"):
            self._warn_drops_response("ViewerDropsDashboard", "Twitch вернул GraphQL-ошибку", response)
        current_user = (response.get("data") or {}).get("currentUser")
        if not isinstance(current_user, dict):
            self._warn_drops_response("ViewerDropsDashboard", "в ответе отсутствует data.currentUser", response)
            return []
        campaigns = current_user.get("dropCampaigns")
        if not isinstance(campaigns, list):
            self._warn_drops_response("ViewerDropsDashboard", "отсутствует массив dropCampaigns", response)
            return []
        if not campaigns:
            self._warn_drops_response("ViewerDropsDashboard", "массив dropCampaigns пуст", response)
            return []
        if status is not None:
            requested_status = str(status).upper()
            filtered = [
                item for item in campaigns
                if isinstance(item, dict) and str(item.get("status") or "").upper() == requested_status
            ]
            if not filtered:
                statuses = sorted({
                    str(item.get("status") or "UNKNOWN")
                    for item in campaigns
                    if isinstance(item, dict)
                })
                self._warn_drops_response(
                    "ViewerDropsDashboard",
                    f"нет кампаний со статусом {requested_status}; полученные статусы: {statuses}",
                    response,
                )
            campaigns = filtered
        return campaigns

    def __get_campaigns_details(self, campaigns):
        result = []
        chunks = create_chunks(campaigns, 20)
        last_response = None
        for chunk in chunks:
            json_data = []
            for campaign in chunk:
                json_data.append(copy.deepcopy(GQLOperations.DropCampaignDetails))
                json_data[-1]["variables"] = {
                    "dropID": campaign["id"],
                    "channelLogin": f"{self.twitch_login.get_user_id()}",
                }
            response = self.post_gql_request(json_data)
            last_response = response
            if not isinstance(response, list):
                self._warn_drops_response(
                    "DropCampaignDetails",
                    "Twitch вернул неожиданный формат списка деталей кампаний",
                    response,
                )
                continue
            for item in response:
                if not isinstance(item, dict):
                    continue
                if item.get("errors"):
                    self._warn_drops_response(
                        "DropCampaignDetails",
                        "ошибка при получении деталей кампании",
                        item,
                    )
                    continue
                drop_campaign = (
                    item.get("data", {}).get("user", {}).get("dropCampaign")
                )
                if drop_campaign is not None:
                    result.append(drop_campaign)
        if campaigns and len(result) < len(campaigns):
            self._warn_drops_response(
                "DropCampaignDetails",
                f"получены детали только для {len(result)} из {len(campaigns)} кампаний",
                last_response,
            )
        return result

    def __sync_campaigns(self, campaigns):
        # We need the inventory only for get the real updated value/progress
        # Get data from inventory and sync current status with streamers.campaigns
        inventory = self.__get_inventory()
        if inventory not in [None, {}] and inventory["dropCampaignsInProgress"] not in [
            None,
            {},
        ]:
            # Iterate all campaigns from dashboard (only active, with working drops)
            # In this array we have also the campaigns never started from us (not in nventory)
            for i in range(len(campaigns)):
                campaigns[i].clear_drops()  # Remove all the claimed drops
                # Iterate all campaigns currently in progress from out inventory
                for progress in inventory["dropCampaignsInProgress"]:
                    if progress["id"] == campaigns[i].id:
                        campaigns[i].in_inventory = True
                        campaigns[i].sync_drops(
                            progress["timeBasedDrops"], self.claim_drop
                        )
                        # Remove all the claimed drops
                        campaigns[i].clear_drops()
                        break
        return campaigns

    def get_drop_inventory_snapshot(self):
        inventory = self.__get_inventory() or {}
        dashboard_response = self.post_gql_request(copy.deepcopy(GQLOperations.ViewerDropsDashboard))
        dashboard_data = dashboard_response.get("data") or {} if isinstance(dashboard_response, dict) else {}
        current_user = dashboard_data.get("currentUser") or {}
        dashboard_campaigns = current_user.get("dropCampaigns") if isinstance(current_user, dict) else None
        available_campaigns = dashboard_data.get("rewardCampaignsAvailableToUser")
        if not isinstance(dashboard_campaigns, list):
            dashboard_campaigns = []
        if not isinstance(available_campaigns, list):
            available_campaigns = []
        if not dashboard_campaigns and available_campaigns:
            logger.info(
                f"ViewerDropsDashboard: использую data.rewardCampaignsAvailableToUser ({len(available_campaigns)} кампаний)."
            )
        if isinstance(dashboard_response, dict) and dashboard_response.get("errors"):
            self._warn_drops_response("ViewerDropsDashboard", "Twitch вернул GraphQL-ошибку", dashboard_response)
        if not dashboard_campaigns and not available_campaigns:
            self._warn_drops_response(
                "ViewerDropsDashboard",
                "dropCampaigns и rewardCampaignsAvailableToUser пусты",
                dashboard_response,
            )
        progress_campaigns = inventory.get("dropCampaignsInProgress") or []
        if not isinstance(progress_campaigns, list):
            progress_campaigns = []
        event_drops = inventory.get("gameEventDrops") or []
        if not isinstance(event_drops, list):
            event_drops = []
        progress_by_campaign = {
            str(item.get("id")): item
            for item in progress_campaigns
            if isinstance(item, dict) and item.get("id") is not None
        }
        campaigns_by_id = {}
        for campaign in dashboard_campaigns + available_campaigns + progress_campaigns:
            if not isinstance(campaign, dict):
                continue
            campaign_id = str(campaign.get("id") or "")
            if not campaign_id:
                continue
            status = str(campaign.get("status") or "").upper()
            if status and status not in ("ACTIVE", "UPCOMING") and campaign in dashboard_campaigns + available_campaigns:
                continue
            existing = campaigns_by_id.get(campaign_id, {})
            merged = dict(existing)
            merged.update(campaign)
            campaigns_by_id[campaign_id] = merged
        for event in event_drops:
            if not isinstance(event, dict):
                continue
            campaign_id = str(
                event.get("campaignID")
                or event.get("campaignId")
                or event.get("dropCampaignID")
                or event.get("id")
                or ""
            )
            if not campaign_id:
                continue
            existing = campaigns_by_id.get(campaign_id, {})
            merged = dict(existing)
            merged.setdefault("id", campaign_id)
            for key in ("name", "game", "gameName", "startAt", "endAt", "timeBasedDrops", "subscriptionDrops"):
                if key not in merged and key in event:
                    merged[key] = event[key]
            event_items = event.get("drops") or event.get("gameEventDrops")
            if isinstance(event_items, list):
                merged.setdefault("gameEventDrops", event_items)
            campaigns_by_id[campaign_id] = merged
        result = []
        drop_sources = (
            ("watch", ("timeBasedDrops", "time_based_drops")),
            ("subscription", ("subscriptionDrops", "subscriptionBasedDrops", "subscriptionDropRewards", "subDrops")),
            ("watch", ("gameEventDrops",)),
        )
        image_keys = ("imageAssetURL", "imageUrl", "imageURL", "image_url", "url")

        def safe_int(value):
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError):
                return 0

        def nested_items(value):
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                for key in ("edges", "nodes", "items"):
                    nested = value.get(key)
                    if isinstance(nested, list):
                        return nested
            return []

        for campaign_id, campaign in campaigns_by_id.items():
            game = campaign.get("game") or {}
            if not isinstance(game, dict):
                game = {}
            active_progress = progress_by_campaign.get(campaign_id, {})
            progress_by_drop = {}
            for source_keys in (
                ("timeBasedDrops", "time_based_drops"),
                ("subscriptionDrops", "subscriptionBasedDrops", "subscriptionDropRewards", "subDrops"),
                ("gameEventDrops",),
            ):
                for source_key in source_keys:
                    for item in nested_items(active_progress.get(source_key)):
                        if not isinstance(item, dict):
                            continue
                        drop = item.get("drop") if isinstance(item.get("drop"), dict) else item
                        drop_id = drop.get("id") or item.get("id")
                        if drop_id is not None:
                            progress_by_drop[str(drop_id)] = item.get("self") or item
            drops = []
            seen_drop_ids = set()
            for source_type, source_keys in drop_sources:
                source_items = []
                for source_key in source_keys:
                    source_items.extend(nested_items(campaign.get(source_key)))
                    source_items.extend(nested_items(active_progress.get(source_key)))
                if not source_items and source_type == "watch" and source_keys[0] == "timeBasedDrops":
                    if campaign.get("unlockRequirements") or active_progress.get("currentMinutesWatched") is not None:
                        source_items.append(campaign)
                for raw_drop in source_items:
                    if not isinstance(raw_drop, dict):
                        continue
                    drop = raw_drop.get("drop") if isinstance(raw_drop.get("drop"), dict) else raw_drop
                    drop_id = str(drop.get("id") or raw_drop.get("id") or "")
                    unique_id = drop_id or str(id(raw_drop))
                    if unique_id in seen_drop_ids:
                        continue
                    seen_drop_ids.add(unique_id)
                    progress = progress_by_drop.get(drop_id, {}) or raw_drop.get("self") or drop.get("self") or active_progress
                    unlock = drop.get("unlockRequirements") or {}
                    if not isinstance(unlock, dict):
                        unlock = {}
                    required = safe_int(
                        drop.get("requiredMinutesWatched")
                        or drop.get("required_minutes")
                        or unlock.get("minuteWatchedGoal")
                        or unlock.get("minutesWatched")
                    )
                    watched = safe_int(
                        progress.get("currentMinutesWatched")
                        or progress.get("current_minutes_watched")
                        or raw_drop.get("currentMinutesWatched")
                        or raw_drop.get("current_minutes_watched")
                    )
                    claimed = bool(
                        progress.get("isClaimed")
                        or raw_drop.get("isClaimed")
                        or drop.get("isClaimed")
                        or drop.get("claimed")
                        or progress.get("claimedAt")
                    )
                    benefit_edges = drop.get("benefitEdges") or raw_drop.get("benefitEdges") or []
                    if isinstance(benefit_edges, dict):
                        benefit_edges = benefit_edges.get("edges") or benefit_edges.get("nodes") or []
                    benefit_names = []
                    image_url = ""
                    for edge in benefit_edges if isinstance(benefit_edges, list) else []:
                        benefit = (edge or {}).get("benefit") or edge.get("node", {}).get("benefit") or {}
                        name = str(benefit.get("name") or "").strip()
                        if name and name not in benefit_names:
                            benefit_names.append(name)
                        if not image_url:
                            for key in image_keys:
                                candidate = str(benefit.get(key) or "")
                                if candidate.startswith("https://"):
                                    image_url = candidate
                                    break
                    if not image_url:
                        for key in image_keys:
                            candidate = str(drop.get(key) or raw_drop.get(key) or "")
                            if candidate.startswith("https://"):
                                image_url = candidate
                                break
                    if not benefit_names:
                        summary = drop.get("summary") or raw_drop.get("summary") or ""
                        if isinstance(summary, str) and summary.strip():
                            benefit_names.append(summary.strip())
                    subscription_count = 0
                    for key in ("requiredSubscriptions", "requiredSubscriptionsCount", "requiredSubs", "subscriptionCount", "subscriptionGoal"):
                        subscription_count = safe_int(drop.get(key) or unlock.get(key))
                        if subscription_count:
                            break
                    subscription_progress_keys = (
                        "currentSubscriptions",
                        "currentSubscriptionCount",
                        "currentSubs",
                        "subscriptionsCount",
                    )
                    subscriptions_progress_known = any(
                        key in progress and progress.get(key) is not None
                        for key in subscription_progress_keys
                    )
                    current_subscriptions = safe_int(
                        progress.get("currentSubscriptions")
                        or progress.get("currentSubscriptionCount")
                        or progress.get("currentSubs")
                        or progress.get("subscriptionsCount")
                    )
                    watch_percentage = min(100, int(watched * 100 / required)) if required else 100
                    subscription_percentage = (
                        min(100, int(current_subscriptions * 100 / subscription_count))
                        if subscription_count else 100
                    )
                    if required and subscription_count:
                        percentage_value = min(watch_percentage, subscription_percentage)
                    elif required:
                        percentage_value = watch_percentage
                    elif subscription_count:
                        percentage_value = subscription_percentage
                    else:
                        percentage_value = 100 if claimed else 0
                    if claimed:
                        watch_percentage = 100
                        subscription_percentage = 100
                        percentage_value = 100
                    is_subscription = (
                        source_type == "subscription"
                        or subscription_count > 0
                        or str(drop.get("dropType") or drop.get("type") or "").upper() in ("SUBSCRIPTION", "SUB")
                    )
                    drops.append({
                        "id": drop_id,
                        "name": str(drop.get("name") or drop.get("summary") or "Награда"),
                        "benefit": ", ".join(benefit_names),
                        "image_url": image_url,
                        "type": "subscription" if is_subscription else "watch",
                        "required_minutes": required,
                        "watched_minutes": watched,
                        "remaining_minutes": max(0, required - watched) if required else 0,
                        "percentage": percentage_value,
                        "watch_percentage": watch_percentage,
                        "subscription_percentage": subscription_percentage,
                        "claimed": claimed,
                        "claimable": bool(progress.get("dropInstanceID") or progress.get("drop_instance_id") or raw_drop.get("dropInstanceID")) and not claimed,
                        "required_subscriptions": subscription_count,
                        "current_subscriptions": current_subscriptions,
                        "subscriptions_progress_known": subscriptions_progress_known,
                        "start_at": str(drop.get("startAt") or campaign.get("startAt") or ""),
                        "end_at": str(drop.get("endAt") or campaign.get("endAt") or ""),
                    })
            allow = campaign.get("allow")
            allowed_channels = allow.get("channels") if isinstance(allow, dict) else None
            game_image = ""
            for key in ("boxArtURL", "boxArtUrl", "imageURL", "imageUrl"):
                candidate = str(game.get(key) or campaign.get(key) or "")
                if candidate.startswith("https://"):
                    game_image = candidate
                    break
            campaign_name = campaign.get("name") or campaign.get("summary") or "Drops"
            if not drops:
                self._warn_drops_response(
                    "Inventory",
                    f"кампания {campaign_id} ({campaign_name}) не содержит распознанных наград",
                    campaign,
                )
            result.append({
                "id": campaign_id,
                "name": str(campaign_name),
                "game": str(game.get("displayName") or game.get("name") or campaign.get("gameName") or "Игра"),
                "game_id": str(game.get("id") or campaign.get("gameID") or ""),
                "game_image": game_image,
                "start_at": str(campaign.get("startAt") or ""),
                "end_at": str(campaign.get("endAt") or ""),
                "allowed_channel_ids": [
                    str(item.get("id"))
                    for item in (allowed_channels or [])
                    if isinstance(item, dict) and item.get("id")
                ],
                "drops": drops,
            })
        if not result:
            self._warn_drops_response(
                "Inventory",
                "не найдено кампаний в dropCampaignsInProgress, gameEventDrops или rewardCampaignsAvailableToUser",
                inventory,
            )
        return result

    def get_drop_campaign_ids_for_channels(self, channels):
        rows = [item for item in (channels or []) if isinstance(item, dict) and item.get("channel_id")]
        result = {}
        for offset in range(0, len(rows), 20):
            batch = rows[offset:offset + 20]
            requests_data = []
            for item in batch:
                request_data = copy.deepcopy(GQLOperations.DropsHighlightService_AvailableDrops)
                request_data["variables"] = {"channelID": str(item["channel_id"])}
                requests_data.append(request_data)
            response = self.post_gql_request(requests_data)
            if not isinstance(response, list):
                continue
            for item, payload in zip(batch, response):
                try:
                    available = payload["data"]["channel"]["viewerDropCampaigns"] or []
                    result[str(item.get("login") or "").lower()] = [str(campaign["id"]) for campaign in available if campaign.get("id")]
                except (KeyError, TypeError):
                    result[str(item.get("login") or "").lower()] = []
        return result

    def claim_drop(self, drop):
        logger.info(
            f"Claim {drop}", extra={"emoji": ":package:", "event": Events.DROP_CLAIM}
        )

        json_data = copy.deepcopy(GQLOperations.DropsPage_ClaimDropRewards)
        json_data["variables"] = {
            "input": {"dropInstanceID": drop.drop_instance_id}}
        response = self.post_gql_request(json_data)
        try:
            # response["data"]["claimDropRewards"] can be null and respose["data"]["errors"] != []
            # or response["data"]["claimDropRewards"]["status"] === DROP_INSTANCE_ALREADY_CLAIMED
            if ("claimDropRewards" in response["data"]) and (
                response["data"]["claimDropRewards"] is None
            ):
                return False
            elif ("errors" in response["data"]) and (response["data"]["errors"] != []):
                return False
            elif ("claimDropRewards" in response["data"]) and (
                response["data"]["claimDropRewards"]["status"]
                in ["ELIGIBLE_FOR_ALL", "DROP_INSTANCE_ALREADY_CLAIMED"]
            ):
                logger.info(f"Twitch Drop received: {drop}")
                return True
            else:
                return False
        except (ValueError, KeyError):
            return False

    def claim_all_drops_from_inventory(self):
        inventory = self.__get_inventory()
        if inventory not in [None, {}]:
            if inventory["dropCampaignsInProgress"] not in [None, {}]:
                for campaign in inventory["dropCampaignsInProgress"]:
                    for drop_dict in campaign["timeBasedDrops"]:
                        drop = Drop(drop_dict)
                        drop.update(drop_dict["self"])
                        if drop.is_claimable is True:
                            drop.is_claimed = self.claim_drop(drop)
                            time.sleep(random.uniform(5, 10))

    def sync_campaigns(self, streamers, chunk_size=3):
        campaigns_update = 0
        campaigns = []
        while self.running:
            try:
                # Get update from dashboard each 60minutes
                if (
                    campaigns_update == 0
                    # or ((time.time() - campaigns_update) / 60) > 60
                    # TEMPORARY AUTO DROP CLAIMING FIX
                    # 30 minutes instead of 60 minutes
                    or ((time.time() - campaigns_update) / 30) > 30
                    #####################################
                ):
                    campaigns_update = time.time()

                    # TEMPORARY AUTO DROP CLAIMING FIX
                    self.claim_all_drops_from_inventory()
                    #####################################

                    # Get full details from current ACTIVE campaigns
                    # Use dashboard so we can explore new drops not currently active in our Inventory
                    campaigns_details = self.__get_campaigns_details(
                        self.__get_drops_dashboard(status="ACTIVE")
                    )
                    campaigns = []

                    # Going to clear array and structure. Remove all the timeBasedDrops expired or not started yet
                    for index in range(0, len(campaigns_details)):
                        if campaigns_details[index] is not None:
                            campaign = Campaign(campaigns_details[index])
                            if campaign.dt_match is True:
                                # Remove all the drops already claimed or with dt not matching
                                campaign.clear_drops()
                                if campaign.drops != []:
                                    campaigns.append(campaign)
                        else:
                            continue

                # Divide et impera :)
                campaigns = self.__sync_campaigns(campaigns)

                # Check if user It's currently streaming the same game present in campaigns_details
                for i in range(0, len(streamers)):
                    if streamers[i].drops_condition() is True:
                        # yes! The streamer[i] have the drops_tags enabled and we It's currently stream a game with campaign active!
                        # With 'campaigns_ids' we are also sure that this streamer have the campaign active.
                        # yes! The streamer[index] have the drops_tags enabled and we It's currently stream a game with campaign active!
                        streamers[i].stream.campaigns = list(
                            filter(
                                lambda x: x.drops != []
                                and x.game == streamers[i].stream.game
                                and x.id in streamers[i].stream.campaigns_ids,
                                campaigns,
                            )
                        )

            except (ValueError, KeyError, requests.exceptions.ConnectionError) as e:
                logger.error(f"Error while syncing inventory: {e}")
                campaigns = []
                self.__check_connection_handler(chunk_size)

            self.__chuncked_sleep(60, chunk_size=chunk_size)

    def contribute_to_community_goals(self, streamer):
        # Don't bother doing the request if no goal is currently started or in stock
        if any(
            goal.status == "STARTED" and goal.is_in_stock
            for goal in streamer.community_goals.values()
        ):
            json_data = copy.deepcopy(GQLOperations.UserPointsContribution)
            json_data["variables"] = {"channelLogin": streamer.username}
            response = self.post_gql_request(json_data)
            user_goal_contributions = response["data"]["user"]["channel"]["self"][
                "communityPoints"
            ]["goalContributions"]

            logger.debug(
                f"Found {len(user_goal_contributions)} community goals for the current stream"
            )

            for goal_contribution in user_goal_contributions:
                goal_id = goal_contribution["goal"]["id"]
                goal = streamer.community_goals[goal_id]
                if goal is None:
                    # TODO should this trigger a new load context request
                    logger.error(
                        f"Unable to find context data for community goal {goal_id}"
                    )
                else:
                    user_stream_contribution = goal_contribution[
                        "userPointsContributedThisStream"
                    ]
                    user_left_to_contribute = (
                        goal.per_stream_user_maximum_contribution
                        - user_stream_contribution
                    )
                    amount = min(
                        goal.amount_left(),
                        user_left_to_contribute,
                        streamer.channel_points,
                    )
                    if amount > 0:
                        self.contribute_to_community_goal(
                            streamer, goal_id, goal.title, amount
                        )
                    else:
                        logger.debug(
                            f"Not contributing to community goal {goal.title}, user channel points {streamer.channel_points}, user stream contribution {user_stream_contribution}, all users total contribution {goal.points_contributed}"
                        )

    def contribute_to_community_goal(self, streamer, goal_id, title, amount):
        json_data = copy.deepcopy(
            GQLOperations.ContributeCommunityPointsCommunityGoal)
        json_data["variables"] = {
            "input": {
                "amount": amount,
                "channelID": streamer.channel_id,
                "goalID": goal_id,
                "transactionID": token_hex(16),
            }
        }

        response = self.post_gql_request(json_data)

        error = response["data"]["contributeCommunityPointsCommunityGoal"]["error"]
        if error:
            logger.error(
                f"Unable to contribute channel points to community goal '{title}', reason '{error}'"
            )
        else:
            logger.info(
                f"Contributed {amount} channel points to community goal '{title}'"
            )
            streamer.channel_points -= amount
