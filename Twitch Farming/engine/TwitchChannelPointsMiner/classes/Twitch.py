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
import validators
# import json

from pathlib import Path
from secrets import choice, token_hex
from typing import Dict, Any
# from urllib.parse import quote
# from base64 import urlsafe_b64decode
# from datetime import datetime

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
    Priority,
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
        event_properties = {
            "channel_id": streamer.channel_id,
            "broadcast_id": streamer.stream.broadcast_id,
            "player": "site",
            "user_id": self.twitch_login.get_user_id(),
            "live": True,
            "channel": streamer.username,
        }
        if streamer.stream.game_name() is not None and streamer.stream.game_id() is not None and streamer.settings.claim_drops is True:
            event_properties["game"] = streamer.stream.game_name()
            event_properties["game_id"] = streamer.stream.game_id()
            try:
                streamer.stream.campaigns_ids = self.__get_campaign_ids_from_streamer(streamer)
            except Exception as error:
                logger.warning(f"Не удалось обновить список Twitch Drops для @{streamer.username}: {error}")
        streamer.stream.payload = [{"event": "minute-watched", "properties": event_properties}]

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
        while internet_connection_available() is False:
            random_sleep = random.randint(1, 3)
            logger.warning(
                f"No internet connection available! Retry after {random_sleep}m"
            )
            self.__chuncked_sleep(random_sleep * 60, chunk_size=chunk_size)

    def post_gql_request(self, json_data):
        try:
            token = self.twitch_login.get_auth_token()
            response = requests.post(
                GQLOperations.url,
                json=json_data,
                headers={
                    "Authorization": f"OAuth {token}",
                    "Client-Id": CLIENT_ID,
                    "Client-Session-Id": self.client_session,
                    "Client-Version": self.update_client_version(),
                    "User-Agent": self.user_agent,
                    "X-Device-Id": self.device_id,
                },
                timeout=7,
            )
            if response.status_code == 401 and self._reauthenticate_after_expiry(token):
                token = self.twitch_login.get_auth_token()
                response = requests.post(
                    GQLOperations.url,
                    json=json_data,
                    headers={
                        "Authorization": f"OAuth {token}",
                        "Client-Id": CLIENT_ID,
                        "Client-Session-Id": self.client_session,
                        "Client-Version": self.update_client_version(),
                        "User-Agent": self.user_agent,
                        "X-Device-Id": self.device_id,
                    },
                    timeout=7,
                )
            operation = json_data.get("operationName", "batch") if isinstance(json_data, dict) else "batch"
            logger.debug(f"GQLOperations {operation} · HTTP {response.status_code}")
            return response.json()
        except requests.exceptions.RequestException as error:
            operation = json_data.get("operationName", "batch") if isinstance(json_data, dict) else "batch"
            logger.error(f"Error with GQLOperations ({operation}): {error}")
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

    def send_minute_watched_events(self, streamers, priority, chunk_size=3):
        while self.running:
            try:
                streamers_index = [
                    i
                    for i in range(0, len(streamers))
                    if streamers[i].is_online is True
                    and (
                        streamers[i].online_at == 0
                        or (time.time() - streamers[i].online_at) > 30
                    )
                ]

                for index in streamers_index:
                    if (streamers[index].stream.update_elapsed() / 60) > 10:
                        # Why this user It's currently online but the last updated was more than 10minutes ago?
                        # Please perform a manually update and check if the user it's online
                        self.check_streamer_online(streamers[index])

                """
                Twitch has a limit - you can't watch more than 2 channels at one time.
                We'll take the first two streamers from the final list as they have the highest priority.
                """
                max_watch_amount = 2
                streamers_watching = set()

                def remaining_watch_amount():
                    return max_watch_amount - len(streamers_watching)

                for prior in priority:
                    if remaining_watch_amount() <= 0:
                        break

                    if prior == Priority.ORDER:
                        # Get the first 2 items, they are already in order
                        streamers_watching.update(streamers_index[:remaining_watch_amount()])

                    elif prior in [Priority.POINTS_ASCENDING, Priority.POINTS_DESCENDING]:
                        items = [
                            {
                                "points": streamers[index].channel_points,
                                "index": index
                            }
                            for index in streamers_index
                        ]
                        items = sorted(
                            items,
                            key=lambda x: x["points"],
                            reverse=(
                                True if prior == Priority.POINTS_DESCENDING else False
                            ),
                        )
                        streamers_watching.update([item["index"] for item in items][:remaining_watch_amount()])

                    elif prior == Priority.STREAK:
                        """
                        Check if we need need to change priority based on watch streak
                        Viewers receive points for returning for x consecutive streams.
                        Each stream must be at least 10 minutes long and it must have been at least 30 minutes since the last stream ended.
                        Watch at least 6m for get the +10
                        """
                        for index in streamers_index:
                            if (
                                streamers[index].settings.watch_streak is True
                                and streamers[index].stream.watch_streak_missing is True
                                and (
                                    streamers[index].offline_at == 0
                                    or (
                                        (time.time() -
                                         streamers[index].offline_at)
                                        // 60
                                    )
                                    > 30
                                )
                                # fix #425
                                and streamers[index].stream.minute_watched < 7
                            ):
                                streamers_watching.add(index)
                                if remaining_watch_amount() <= 0:
                                    break

                    elif prior == Priority.DROPS:
                        for index in streamers_index:
                            if streamers[index].drops_condition() is True:
                                streamers_watching.add(index)
                                if remaining_watch_amount() <= 0:
                                    break

                    elif prior == Priority.SUBSCRIBED:
                        streamers_with_multiplier = [
                            index
                            for index in streamers_index
                            if streamers[index].viewer_has_points_multiplier()
                        ]
                        streamers_with_multiplier = sorted(
                            streamers_with_multiplier,
                            key=lambda x: streamers[x].total_points_multiplier(
                            ),
                            reverse=True,
                        )
                        streamers_watching.update(streamers_with_multiplier[:remaining_watch_amount()])

                selected_streamers = set(streamers_watching)
                for index in streamers_index:
                    if index not in selected_streamers:
                        username = streamers[index].username
                        logger.info(f"WATCH_STATUS: {username}|waiting|Ожидает очереди. Одновременно доступны два канала.")

                streamers_watching = list(streamers_watching)[:max_watch_amount]

                for index in streamers_watching:
                    username = streamers[index].username
                    logger.info(f"WATCH_STATUS: {username}|selected|Канал выбран для просмотра минут.")
                    next_iteration = time.time() + 20 / len(streamers_watching)

                    try:
                        logger.info(f"WATCH_STATUS: {username}|request|Получение токена просмотра.")
                        logger.debug(f"[WATCH] @{username}: запрос PlaybackAccessToken.")
                        ####################################
                        # Start of fix for 2024/5 API Change
                        # Create the JSON data for the GraphQL request
                        json_data = copy.deepcopy(
                            GQLOperations.PlaybackAccessToken)
                        json_data["variables"] = {
                            "login": streamers[index].username,
                            "isLive": True,
                            "isVod": False,
                            "vodID": "",
                            "playerType": "site",
                            # "playerType": "picture-by-picture",
                            "platform": "web"
                        }

                        # Get signature and value using the post_gql_request method
                        try:
                            responsePlaybackAccessToken = self.post_gql_request(
                                json_data)
                            logger.debug(f"[WATCH] @{username}: ответ PlaybackAccessToken получен.")

                            if 'data' not in responsePlaybackAccessToken:
                                logger.warning(f"WATCH_STATUS: {username}|error|Twitch не вернул данные PlaybackAccessToken.")
                                logger.error(f"Invalid PlaybackAccessToken response for @{username}.")
                                continue

                            streamPlaybackAccessToken = responsePlaybackAccessToken["data"].get(
                                'streamPlaybackAccessToken', {})
                            signature = streamPlaybackAccessToken.get(
                                "signature")
                            value = streamPlaybackAccessToken.get("value")

                            if not signature or not value:
                                logger.warning(f"WATCH_STATUS: {username}|error|В ответе Twitch отсутствует PlaybackAccessToken.")
                                logger.error(f"Missing PlaybackAccessToken signature or value for @{username}.")
                                continue

                        except Exception as e:
                            logger.warning(f"WATCH_STATUS: {username}|error|Не удалось получить токен просмотра.")
                            logger.error(f"PlaybackAccessToken request failed for @{username}: {type(e).__name__}: {e}")
                            continue

                        # encoded_value = quote(json.dumps(value))

                        # Construct the URL for the broadcast qualities
                        RequestBroadcastQualitiesURL = f"https://usher.ttvnw.net/api/channel/hls/{streamers[index].username}.m3u8?sig={signature}&token={value}"

                        # Get list of video qualities
                        responseBroadcastQualities = requests.get(
                            RequestBroadcastQualitiesURL,
                            headers={"User-Agent": self.user_agent},
                            timeout=20,
                        )
                        logger.debug(f"[WATCH] @{username}: HLS playlist HTTP {responseBroadcastQualities.status_code}.")
                        if responseBroadcastQualities.status_code != 200:
                            logger.warning(f"WATCH_STATUS: {username}|error|HLS playlist вернул HTTP {responseBroadcastQualities.status_code}.")
                            continue
                        BroadcastQualities = responseBroadcastQualities.text

                        # Just takes the last line, which should be the URL for the lowest quality
                        BroadcastLowestQualityURL = BroadcastQualities.split("\n")[-1]
                        if not validators.url(BroadcastLowestQualityURL):
                            logger.warning(f"WATCH_STATUS: {username}|error|Twitch не вернул корректный URL HLS-потока.")
                            continue
                        logger.debug(f"[WATCH] @{username}: HLS playlist URL проверен.")

                        # Get list of video URLs
                        responseStreamURLList = requests.get(
                            BroadcastLowestQualityURL,
                            headers={"User-Agent": self.user_agent},
                            timeout=20,
                        )
                        logger.debug(f"[WATCH] @{username}: stream playlist HTTP {responseStreamURLList.status_code}.")
                        if responseStreamURLList.status_code != 200:
                            logger.warning(f"WATCH_STATUS: {username}|error|Список видеопотоков вернул HTTP {responseStreamURLList.status_code}.")
                            continue
                        StreamURLList = responseStreamURLList.text

                        # Just takes the last line, which should be the URL for the lowest quality
                        StreamLowestQualityURL = StreamURLList.split("\n")[-2]
                        if not validators.url(StreamLowestQualityURL):
                            logger.warning(f"WATCH_STATUS: {username}|error|Не найден корректный URL видеопотока.")
                            continue
                        logger.debug(f"[WATCH] @{username}: URL видеопотока проверен.")

                        # Perform a HEAD request to simulate watching the stream
                        responseStreamLowestQualityURL = requests.head(
                            StreamLowestQualityURL,
                            headers={"User-Agent": self.user_agent},
                            timeout=20,
                        )
                        logger.debug(f"[WATCH] @{username}: video HEAD HTTP {responseStreamLowestQualityURL.status_code}.")
                        if responseStreamLowestQualityURL.status_code != 200:
                            logger.warning(f"WATCH_STATUS: {username}|error|Видеопоток не ответил на HEAD: HTTP {responseStreamLowestQualityURL.status_code}.")
                            continue
                        # End of fix for 2024/5 API Change
                        ##################################
                        logger.debug(f"[WATCH] @{username}: отправка minute-watched.")
                        response = requests.post(
                            streamers[index].stream.spade_url,
                            data=streamers[index].stream.encode_payload(),
                            headers={"User-Agent": self.user_agent},
                            timeout=20,
                        )
                        logger.debug(f"[WATCH] @{username}: minute-watched HTTP {response.status_code}.")
                        if response.status_code == 204:
                            streamers[index].stream.update_minute_watched()
                            watched = streamers[index].stream.minute_watched
                            logger.info(f"WATCH_STATUS: {username}|accepted|Twitch принял minute-watched (HTTP 204). Накоплено минут: {watched:.2f}.")

                            """
                            Remember, you can only earn progress towards a time-based Drop on one participating channel at a time.  [ ! ! ! ]
                            You can also check your progress towards Drops within a campaign anytime by viewing the Drops Inventory.
                            For time-based Drops, if you are unable to claim the Drop in time, you will be able to claim it from the inventory page until the Drops campaign ends.
                            """

                            for campaign in streamers[index].stream.campaigns:
                                for drop in campaign.drops:
                                    # We could add .has_preconditions_met condition inside is_printable
                                    if (
                                        drop.has_preconditions_met is not False
                                        and drop.is_printable is True
                                    ):
                                        drop_messages = [
                                            f"{streamers[index]} is streaming {streamers[index].stream}",
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
                                                    "skip_gotify": True
                                                },
                                            )

                                        if Settings.logger.telegram is not None:
                                            Settings.logger.telegram.send(
                                                "\n".join(drop_messages),
                                                Events.DROP_STATUS,
                                            )

                                        if Settings.logger.discord is not None:
                                            Settings.logger.discord.send(
                                                "\n".join(drop_messages),
                                                Events.DROP_STATUS,
                                            )
                                        if Settings.logger.webhook is not None:
                                            Settings.logger.webhook.send(
                                                "\n".join(drop_messages),
                                                Events.DROP_STATUS,
                                            )
                                        if Settings.logger.gotify is not None:
                                            Settings.logger.gotify.send(
                                                "\n".join(drop_messages),
                                                Events.DROP_STATUS,
                                            )
                        else:
                            logger.warning(f"WATCH_STATUS: {username}|error|minute-watched отклонён: HTTP {response.status_code}.")

                    except requests.exceptions.ConnectionError as e:
                        logger.warning(f"WATCH_STATUS: {username}|error|Потеряно сетевое соединение при отправке minute-watched.")
                        logger.error(f"Error while trying to send minute watched for @{username}: {e}")
                        self.__check_connection_handler(chunk_size)
                    except requests.exceptions.Timeout as e:
                        logger.warning(f"WATCH_STATUS: {username}|error|Тайм-аут запроса minute-watched.")
                        logger.error(f"Timeout while sending minute watched for @{username}: {e}")
                    except Exception as e:
                        logger.warning(f"WATCH_STATUS: {username}|error|Ошибка обработки запроса просмотра.")
                        logger.error(f"Unexpected minute-watched error for @{username}: {type(e).__name__}: {e}", exc_info=True)

                    self.__chuncked_sleep(
                        next_iteration - time.time(), chunk_size=chunk_size
                    )

                if streamers_watching == []:
                    # self.__chuncked_sleep(60, chunk_size=chunk_size)
                    self.__chuncked_sleep(20, chunk_size=chunk_size)
            except Exception:
                logger.error(
                    "Exception raised in send minute watched", exc_info=True)

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

    def __get_inventory(self):
        response = self.post_gql_request(GQLOperations.Inventory)
        try:
            return (
                response["data"]["currentUser"]["inventory"] if response != {} else {}
            )
        except (ValueError, KeyError, TypeError):
            return {}

    def __get_drops_dashboard(self, status=None):
        response = self.post_gql_request(GQLOperations.ViewerDropsDashboard)
        campaigns = (
            response.get("data", {})
            .get("currentUser", {})
            .get("dropCampaigns", [])
            or []
        )

        if status is not None:
            campaigns = (
                list(filter(lambda x: x["status"] == status.upper(), campaigns)) or []
            )

        return campaigns

    def __get_campaigns_details(self, campaigns):
        result = []
        chunks = create_chunks(campaigns, 20)
        for chunk in chunks:
            json_data = []
            for campaign in chunk:
                json_data.append(copy.deepcopy(
                    GQLOperations.DropCampaignDetails))
                json_data[-1]["variables"] = {
                    "dropID": campaign["id"],
                    "channelLogin": f"{self.twitch_login.get_user_id()}",
                }

            response = self.post_gql_request(json_data)
            if not isinstance(response, list):
                logger.debug("Unexpected campaigns response format, skipping chunk")
                continue
            for r in response:
                drop_campaign = (
                    r.get("data", {}).get("user", {}).get("dropCampaign", None)
                )
                if drop_campaign is not None:
                    result.append(drop_campaign)
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
        dashboard = self.__get_drops_dashboard(status="ACTIVE")
        details = self.__get_campaigns_details(dashboard)
        progress_campaigns = inventory.get("dropCampaignsInProgress") or []
        progress_by_campaign = {
            str(item.get("id")): item
            for item in progress_campaigns
            if isinstance(item, dict) and item.get("id") is not None
        }
        result = []
        drop_sources = (
            ("watch", ("timeBasedDrops", "time_based_drops")),
            ("subscription", ("subscriptionDrops", "subscriptionBasedDrops", "subscriptionDropRewards", "subDrops")),
        )
        image_keys = ("imageAssetURL", "imageUrl", "imageURL", "image_url", "url")
        def safe_int(value):
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError):
                return 0
        for campaign in details:
            if not isinstance(campaign, dict):
                continue
            campaign_id = str(campaign.get("id") or "")
            game = campaign.get("game") or {}
            if not isinstance(game, dict):
                game = {}
            active_progress = progress_by_campaign.get(campaign_id, {})
            progress_by_drop = {}
            for source_type, source_keys in drop_sources:
                for source_key in source_keys:
                    progress_items = active_progress.get(source_key) or []
                    for item in progress_items:
                        if isinstance(item, dict) and item.get("id") is not None:
                            progress_by_drop[str(item.get("id"))] = item.get("self") or item
            drops = []
            seen_drop_ids = set()
            for source_type, source_keys in drop_sources:
                source_items = []
                for source_key in source_keys:
                    value = campaign.get(source_key) or []
                    if isinstance(value, list):
                        source_items.extend(value)
                for drop in source_items:
                    if not isinstance(drop, dict):
                        continue
                    drop_id = str(drop.get("id") or "")
                    if drop_id and drop_id in seen_drop_ids:
                        continue
                    if drop_id:
                        seen_drop_ids.add(drop_id)
                    progress = progress_by_drop.get(drop_id, {}) or drop.get("self") or {}
                    required = safe_int(drop.get("requiredMinutesWatched") or drop.get("required_minutes"))
                    watched = safe_int(progress.get("currentMinutesWatched") or progress.get("current_minutes_watched"))
                    claimed = bool(progress.get("isClaimed") or drop.get("isClaimed") or drop.get("claimed"))
                    benefit_edges = drop.get("benefitEdges") or []
                    benefit_names = []
                    image_url = ""
                    for edge in benefit_edges:
                        benefit = (edge or {}).get("benefit") or {}
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
                            candidate = str(drop.get(key) or "")
                            if candidate.startswith("https://"):
                                image_url = candidate
                                break
                    subscription_count = 0
                    for key in ("requiredSubscriptions", "requiredSubscriptionsCount", "requiredSubs", "subscriptionCount"):
                        subscription_count = safe_int(drop.get(key))
                        if subscription_count:
                            break
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
                    is_subscription = (
                        source_type == "subscription"
                        or subscription_count > 0
                        or str(drop.get("dropType") or drop.get("type") or "").upper() in ("SUBSCRIPTION", "SUB")
                    )
                    drops.append({
                        "id": drop_id,
                        "name": str(drop.get("name") or "Награда"),
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
                        "claimable": bool(progress.get("dropInstanceID") or progress.get("drop_instance_id")) and not claimed,
                        "required_subscriptions": subscription_count,
                        "current_subscriptions": current_subscriptions,
                        "start_at": str(drop.get("startAt") or campaign.get("startAt") or ""),
                        "end_at": str(drop.get("endAt") or campaign.get("endAt") or ""),
                    })
            allow = campaign.get("allow")
            allowed_channels = allow.get("channels") if isinstance(allow, dict) else None
            game_image = ""
            for key in ("boxArtURL", "boxArtUrl", "imageURL", "imageUrl"):
                candidate = str(game.get(key) or "")
                if candidate.startswith("https://"):
                    game_image = candidate
                    break
            result.append({
                "id": campaign_id,
                "name": str(campaign.get("name") or "Drops"),
                "game": str(game.get("displayName") or game.get("name") or "Игра"),
                "game_id": str(game.get("id") or ""),
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
