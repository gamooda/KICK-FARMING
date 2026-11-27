import base64
import json
import requests
import sys
from collections import OrderedDict
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Twitch Farming" / "engine"))

from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.classes.entities.Stream import Stream
from TwitchChannelPointsMiner.classes.Settings import Settings
from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner
from TwitchChannelPointsMiner.classes.WebSocketsPool import WebSocketsPool
from TwitchChannelPointsMiner.classes.entities.PubsubTopic import PubsubTopic
import threading
from TwitchChannelPointsMiner.constants import GQLOperations

sys.path.insert(0, str(ROOT / "Twitch Farming"))
from backend import select_drops_streamers, _drops_campaign_completed, _drops_reward_received

Settings.logger = SimpleNamespace(less=True)


class FakeResponse:
    def __init__(self, status_code=200, text="", url=""):
        self.status_code = status_code
        self.text = text
        self.url = url


class FakeHelixResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": [{"id": "12345"}]}


helix_client = Twitch.__new__(Twitch)
helix_client.twitch_login = SimpleNamespace(token="test-token", client_id="test-client")
with (
    patch(
        "TwitchChannelPointsMiner.classes.Twitch.requests.get",
        side_effect=[requests.exceptions.ReadTimeout("temporary timeout"), FakeHelixResponse()],
    ) as helix_request,
    patch("TwitchChannelPointsMiner.classes.Twitch.time.sleep", lambda seconds: None),
):
    helix_payload = helix_client.helix_get_payload("users", {"login": "test"})
assert helix_payload == {"data": [{"id": "12345"}]}
assert helix_request.call_count == 2, "A transient Helix timeout must be retried once."

game_info_client = Twitch.__new__(Twitch)
game_lookup_calls = []

def fake_game_lookup(self, endpoint, params):
    game_lookup_calls.append((endpoint, params))
    if params.get("id") == "4242":
        return [{"id": "4242", "name": "Example Game", "display_name": "Example Game", "slug": "example-game"}]
    if params.get("name") == "Example Game":
        return [{"id": "4242", "name": "Example Game", "display_name": "Example Game", "slug": "example-game"}]
    return []

with patch.object(Twitch, "helix_get", fake_game_lookup):
    game_by_id = game_info_client.get_game_info("4242", "Wrong fallback slug")
    game_by_name = game_info_client.get_game_info("", "Example Game")
assert game_by_id["slug"] == "example-game", "Drops searches must use Twitch's canonical game slug, not a slug guessed from the display name."
assert game_by_name["id"] == "4242", "A campaign without a game ID must resolve it from its exact Twitch game name."
assert game_lookup_calls == [("games", {"id": "4242"}), ("games", {"name": "Example Game"})]

selected_drops_streamers = select_drops_streamers(
    [
        {"login": "follow_one", "is_followed": True},
        {"login": "follow_two", "is_followed": True},
    ],
    [
        {"login": "global_top", "is_followed": False, "viewer_count": 12000},
        {"login": "follow_one", "is_followed": True, "viewer_count": 15000},
        {"login": "global_second", "is_followed": False, "viewer_count": 9000},
    ],
    3,
)
assert [item["login"] for item in selected_drops_streamers] == ["follow_one", "follow_two", "global_top"], "Followed live Drops channels must be kept first and the general Twitch catalog must fill only the remaining slots without duplicates."
assert len(select_drops_streamers([], [{"login": "one"}, {"login": "two"}, {"login": "three"}, {"login": "four"}])) == 3, "The campaign streamer list must be capped at three entries."


def make_streamer(username, online=True):
    stream = Stream()
    stream.update(
        "24680",
        "Example Stream",
        {"id": "13579", "name": "Example Game", "displayName": "Example Game"},
        [],
        100,
    )
    return SimpleNamespace(
        username=username,
        channel_id="67890",
        is_online=online,
        stream=stream,
    )


streamers = [make_streamer(f"channel_{index}", index % 4 != 0) for index in range(15)]
online_indices = Twitch._get_online_streamer_indices(streamers)
assert online_indices == [index for index in range(15) if index % 4 != 0]
assert len(online_indices) > 2, "Every online channel must be included without a two-channel cap."


dispatch_streamers = [
    SimpleNamespace(
        username=f"dispatch_{index}",
        is_online=True,
        stream=SimpleNamespace(update_required=lambda: False),
    )
    for index in range(10)
]
dispatch_watcher = Twitch.__new__(Twitch)
dispatch_watcher.running = True
dispatch_watcher._watch_rotation_cursor = 0
dispatch_watcher._watch_rotation_started_at = 0.0
dispatch_watcher._watch_rotation_signature = None
dispatch_watcher._watch_online_signature = ()
dispatch_watcher.active_watch_channels = []
dispatched = []
worker_counts = []


class RecordingExecutor:
    def __init__(self, max_workers, thread_name_prefix):
        worker_counts.append(max_workers)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def submit(self, function, streamer):
        future = Future()
        function(streamer)
        future.set_result(None)
        dispatched.append(streamer.username)
        if len(dispatched) == 2:
            dispatch_watcher.running = False
        return future


dispatch_logs = []
with (
    patch.object(
        Twitch,
        "_Twitch__chuncked_sleep",
        lambda self, *args, **kwargs: None,
    ),
    patch.object(
        Twitch,
        "_send_minute_watched_for_streamer",
        lambda self, streamer: None,
    ),
    patch(
        "TwitchChannelPointsMiner.classes.Twitch.ThreadPoolExecutor",
        RecordingExecutor,
    ),
    patch(
        "TwitchChannelPointsMiner.classes.Twitch.logger.info",
        side_effect=lambda message, *args, **kwargs: dispatch_logs.append(str(message)),
    ),
):
    dispatch_watcher.send_minute_watched_events(dispatch_streamers, None)

assert dispatched == [streamer.username for streamer in dispatch_streamers[:2]]
assert worker_counts == [2]
assert any(
    "Отправка minute-watched: 2 слота из 10 онлайн-каналов" in message
    and all(f"@dispatch_{index}" in message for index in range(10))
    and "@dispatch_0" in message
    and "@dispatch_1" in message
    for message in dispatch_logs
)

def make_rotation_streamer(username, active_drop=False):
    campaign = SimpleNamespace(
        in_inventory=True,
        drops=[SimpleNamespace(is_claimed=False)] if active_drop else [],
    )
    return SimpleNamespace(
        username=username,
        is_online=True,
        settings=SimpleNamespace(claim_drops=True),
        stream=SimpleNamespace(campaigns=[campaign] if active_drop else []),
    )


rotation_watcher = Twitch.__new__(Twitch)
rotation_watcher._watch_rotation_cursor = 0
rotation_watcher._watch_rotation_started_at = 0.0
rotation_watcher._watch_rotation_signature = None
rotation_watcher._watch_online_signature = ()
rotation_watcher.active_watch_channels = []
rotation_channels = [
    make_rotation_streamer(f"round_{index}")
    for index in range(1, 6)
]
assert [
    streamer.username
    for streamer in rotation_watcher._select_minute_watched_streamers(rotation_channels, 100)
] == ["round_1", "round_2"]
assert [
    streamer.username
    for streamer in rotation_watcher._select_minute_watched_streamers(rotation_channels, 429)
] == ["round_1", "round_2"]
assert [
    streamer.username
    for streamer in rotation_watcher._select_minute_watched_streamers(rotation_channels, 430)
] == ["round_3", "round_4"]
assert [
    streamer.username
    for streamer in rotation_watcher._select_minute_watched_streamers(rotation_channels, 760)
] == ["round_5", "round_1"]

priority_watcher = Twitch.__new__(Twitch)
priority_watcher._watch_rotation_cursor = 0
priority_watcher._watch_rotation_started_at = 0.0
priority_watcher._watch_rotation_signature = None
priority_watcher._watch_online_signature = ()
priority_watcher.active_watch_channels = []
priority_streamer = make_rotation_streamer("drops_priority", True)
priority_drop = priority_streamer.stream.campaigns[0].drops[0]
priority_channels = [
    make_rotation_streamer("first"),
    make_rotation_streamer("second"),
    priority_streamer,
    make_rotation_streamer("third"),
    make_rotation_streamer("fourth"),
]
assert [
    streamer.username
    for streamer in priority_watcher._select_minute_watched_streamers(priority_channels, 100)
] == ["drops_priority", "first"]
assert [
    streamer.username
    for streamer in priority_watcher._select_minute_watched_streamers(priority_channels, 430)
] == ["drops_priority", "second"]
assert [
    streamer.username
    for streamer in priority_watcher._select_minute_watched_streamers(priority_channels, 760)
] == ["drops_priority", "third"]
priority_drop.is_claimed = True
assert [
    streamer.username
    for streamer in priority_watcher._select_minute_watched_streamers(priority_channels, 761)
] == ["first", "second"]



unlisten_topic = PubsubTopic("video-playback-by-id", streamer=SimpleNamespace(channel_id="topic-channel"))
unlisten_socket = SimpleNamespace(
    topics=[unlisten_topic],
    pending_topics=[],
    is_opened=True,
    send=lambda payload: unlisten_messages.append(payload),
)
unlisten_messages = []
unlisten_pool = WebSocketsPool.__new__(WebSocketsPool)
unlisten_pool.ws = [unlisten_socket]
unlisten_pool.stop_event = threading.Event()
unlisten_pool.unsubscribe(unlisten_topic)
assert unlisten_socket.topics == [] and len(unlisten_messages) == 1
assert unlisten_messages[0].get("type") == "UNLISTEN"
assert unlisten_messages[0].get("data", {}).get("topics") == ["video-playback-by-id.topic-channel"]

class FakeChat:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


removed_chat = FakeChat()
remove_miner = TwitchChannelPointsMiner.__new__(TwitchChannelPointsMiner)
remove_miner.streamers_lock = threading.RLock()
remove_miner.streamers = [
    SimpleNamespace(username="remove_me", irc_chat=removed_chat, settings=SimpleNamespace(
        follow_raid=False, make_predictions=False, claim_moments=False, community_goals=False
    )),
    SimpleNamespace(username="keep_me", irc_chat=None, settings=SimpleNamespace(
        follow_raid=False, make_predictions=False, claim_moments=False, community_goals=False
    )),
]
remove_miner.original_streamers = [100, 200]
remove_miner.twitch = SimpleNamespace(active_watch_channels=["remove_me", "keep_me"], drops_focus_login="remove_me")
remove_miner.ws_pool = None
remove_miner.streamer_topics_initialized = False
removed = remove_miner.remove_streamer("remove_me")
assert removed.get("ok") and [item.username for item in remove_miner.streamers] == ["keep_me"]
assert remove_miner.original_streamers == [200]
assert removed_chat.stopped
assert remove_miner.twitch.active_watch_channels == ["keep_me"]
assert remove_miner.twitch.drops_focus_login == ""

focus_watcher = Twitch.__new__(Twitch)
focus_watcher._watch_rotation_cursor = 0
focus_watcher._watch_rotation_started_at = 0.0
focus_watcher._watch_rotation_signature = None
focus_watcher._watch_online_signature = ()
focus_watcher.active_watch_channels = []
focus_watcher.drops_focus_login = "focus_target"
focus_channels = [
    make_rotation_streamer("focus_target"),
    make_rotation_streamer("other_drop", True),
    make_rotation_streamer("ordinary"),
]
assert [
    streamer.username
    for streamer in focus_watcher._select_minute_watched_streamers(focus_channels, 100)
] == ["focus_target", "other_drop"], "A selected campaign focus must reserve one watch slot ahead of other active Drops."
focus_watcher.drops_focus_login = "offline_focus"
assert [
    streamer.username
    for streamer in focus_watcher._select_minute_watched_streamers(focus_channels, 101)
] == ["other_drop", "focus_target"], "An offline focus login must not block the normal Drops watch-slot selection."

assert _drops_reward_received({"claimed": True})
assert _drops_reward_received({"percentage": 100})
assert _drops_reward_received({"watched_minutes": 60, "required_minutes": 60})
assert not _drops_reward_received({"percentage": 99, "watched_minutes": 59, "required_minutes": 60})
assert _drops_campaign_completed({"drops": [{"claimed": True}, {"percentage": 100}]})
assert not _drops_campaign_completed({"drops": [{"claimed": True}, {"percentage": 99}]})
assert not _drops_campaign_completed({"drops": []})



streamer = make_streamer("example_channel")
event = Twitch._build_minute_watched_event(streamer, "12345")
assert event is not None and len(event) == 1
streamer.stream.payload = event
encoded = streamer.stream.encode_payload()["data"]
properties = json.loads(base64.b64decode(encoded).decode("utf-8"))[0]["properties"]
for key in ("channel_id", "broadcast_id", "game", "game_id", "player", "user_id"):
    assert properties.get(key), f"minute-watched payload is missing {key}"
assert properties["channel_id"] == "67890"
assert properties["broadcast_id"] == "24680"
assert properties["game"] == "Example Game"
assert properties["game_id"] == "13579"
assert properties["player"] == "site"
assert properties["user_id"] == 12345

missing_metadata = make_streamer("missing_metadata")
missing_metadata.stream.game = {}
assert Twitch._build_minute_watched_event(missing_metadata, "12345") is None

master_playlist = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=64000\n"
    "low/index.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=128000\n"
    "high/index.m3u8\n"
)
variants = Twitch._parse_hls_playlist_urls(
    master_playlist,
    "https://video.example/live/master.m3u8",
    master=True,
)
assert variants == [
    "https://video.example/live/low/index.m3u8",
    "https://video.example/live/high/index.m3u8",
]

media_playlist = (
    "#EXTM3U\n"
    "#EXT-X-MEDIA-SEQUENCE:10\n"
    "#EXTINF:2.0,\n"
    "segments/one.ts\n"
    "#EXTINF:2.0,\n"
    "../two.ts?part=2\n"
)
segments = Twitch._parse_hls_playlist_urls(
    media_playlist,
    "https://video.example/live/quality/index.m3u8",
)
assert segments == [
    "https://video.example/live/quality/segments/one.ts",
    "https://video.example/live/two.ts?part=2",
]
assert Twitch._parse_hls_playlist_urls(
    "#EXTM3U\n#EXTINF:2.0,\nfile:///private/segment.ts\n",
    "https://video.example/live/index.m3u8",
) == []

watcher = Twitch.__new__(Twitch)
watcher.running = True
watcher.user_agent = "DropsSelectionTest/1.0"
playlist_url = "https://video.example/live/quality/index.m3u8"
streamer.stream.hls_url = playlist_url
playlist_response = FakeResponse(
    text=media_playlist,
    url=playlist_url,
)
with (
    patch("TwitchChannelPointsMiner.classes.Twitch.requests.get", return_value=playlist_response),
    patch("TwitchChannelPointsMiner.classes.Twitch.requests.head", return_value=FakeResponse()) as head,
):
    assert watcher._watch_stream_playlist(streamer) is True
    assert watcher._watch_stream_playlist(streamer) is True
assert [call.args[0] for call in head.call_args_list] == segments
assert len(streamer.stream.watched_segments) == 2

stream = streamer.stream
stream.hls_url = playlist_url
stream.watched_segments = OrderedDict({segments[0]: None})
stream.last_minute_watched_sent = 123.0
stream.update(
    "24680",
    "Updated title",
    {"id": "13579", "name": "Example Game", "displayName": "Example Game"},
    [],
    120,
)
assert stream.hls_url == playlist_url
assert list(stream.watched_segments) == [segments[0]]
assert stream.last_minute_watched_sent == 123.0
stream.update(
    "24681",
    "New broadcast",
    {"id": "13579", "name": "Example Game", "displayName": "Example Game"},
    [],
    120,
)
assert stream.hls_url is None
assert not stream.watched_segments
assert stream.last_minute_watched_sent == 0.0


inventory_reader = Twitch.__new__(Twitch)
inventory_response = {
    "data": {
        "currentUser": {
            "inventory": {
                "dropCampaignsInProgress": {
                    "edges": [{"node": {"id": "inventory-edge-campaign"}}]
                }
            }
        }
    }
}
with (
    patch.object(Twitch, "post_gql_request", lambda self, request: inventory_response),
    patch.object(Twitch, "_warn_drops_response", lambda self, *args: None),
):
    inventory_result = inventory_reader._Twitch__get_inventory()
assert inventory_result["dropCampaignsInProgress"] == [{"id": "inventory-edge-campaign"}]

snapshot_client = Twitch.__new__(Twitch)
snapshot_details_requested = []

def return_campaign_details(self, candidates):
    snapshot_details_requested.extend(candidates)
    return candidates

snapshot_campaign_one = {
    "id": "campaign-one",
    "status": "ACTIVE",
    "name": "Campaign One",
    "game": {"id": "10", "displayName": "Game One", "slug": "game-one"},
    "timeBasedDrops": [{
        "id": "badge-drop",
        "name": "Chat Badge",
        "requiredMinutesWatched": 60,
        "self": {"currentMinutesWatched": 15},
        "benefitEdges": {
            "edges": [{
                "node": {
                    "benefit": {
                        "name": "Chat Badge",
                        "distributionType": "BADGE",
                        "imageAssetURL": "https://example.test/badge.png",
                    }
                }
            }]
        },
    }],
    "subscriptionDrops": [{"id": "paid-drop", "name": "Paid reward", "dropType": "SUBSCRIPTION"}],
}
snapshot_campaign_two = {
    "id": "campaign-two",
    "status": "ACTIVE",
    "name": "Campaign Two",
    "game": {"id": "20", "displayName": "Game Two", "slug": "game-two"},
    "timeBasedDrops": {
        "edges": [{
            "node": {
                "id": "game-drop",
                "name": "Game Item",
                "requiredMinutesWatched": 30,
                "benefitEdges": [{
                    "benefit": {"name": "Weapon Skin", "distributionType": "DIRECT_ENTITLEMENT", "imageAssetURL": "https://example.test/weapon.png"}
                }],
            }
        }, {
            "node": {
                "id": "paid-subs-goal",
                "name": "Paid subscription goal",
                "dropType": "SUBSGOAL",
                "subsGoal": {"required": 1},
                "benefitEdges": [{"benefit": {"name": "Subscription", "benefitType": "SUBSGOAL"}}],
            }
        }]
    },
}
snapshot_campaign_in_progress = {
    "id": "campaign-in-progress",
    "status": "UNKNOWN",
    "name": "GTAVI Launch Celebration",
    "game": None,
    "timeBasedDrops": [{
        "id": "available-emote-drop",
        "name": "GTAVI Twitch Emote",
        "requiredMinutesWatched": 20,
        "self": {"isClaimed": "false", "currentMinutesWatched": "7"},
        "benefitEdges": [{
            "benefit": {"name": "GTAVI Emote", "distributionType": "EMOTE", "imageAssetURL": "https://example.test/gtavi-emote.png"}
        }],
    }],
}
snapshot_campaign_no_game = {
    "id": "pokemon-evolutions",
    "status": "ACTIVE",
    "name": "Pokemon Evolutions",
    "game": None,
    "timeBasedDrops": [{
        "id": "pokemon-drop",
        "name": "Pokemon reward",
        "benefitEdges": [{
            "benefit": {"name": "Pokemon reward", "distributionType": "DIRECT_ENTITLEMENT", "imageAssetURL": "https://example.test/pokemon.png"}
        }],
    }],
}
snapshot_campaign_no_time_drops = {
    "id": "campaign-empty-timebased",
    "status": "ACTIVE",
    "name": "Empty rewards",
    "game": {"id": "60", "displayName": "Empty Game"},
    "timeBasedDrops": [],
}
snapshot_campaign_no_image = {
    "id": "campaign-no-image",
    "status": "ACTIVE",
    "name": "Campaign without reward image",
    "game": {"id": "61", "displayName": "Image Missing Game"},
    "timeBasedDrops": [{
        "id": "drop-no-image",
        "name": "Unrenderable reward",
        "benefitEdges": [{"benefit": {"name": "Reward", "distributionType": "DIRECT_ENTITLEMENT"}}],
    }],
}
snapshot_campaign_marvel = {
    "id": "marvel-rivals",
    "status": "ACTIVE",
    "name": "Marvel Rivals",
    "game": {"id": "62", "displayName": "Marvel Rivals", "slug": "marvel-rivals"},
    "timeBasedDrops": [{
        "id": "marvel-emote-drop",
        "name": "Marvel Rivals Twitch Emote",
        "requiredMinutesWatched": 45,
        "self": {"isClaimed": "false", "currentMinutesWatched": "12"},
        "benefitEdges": [{
            "benefit": {"name": "Marvel Rivals Emote", "distributionType": "EMOTE", "imageAssetURL": "https://example.test/marvel-emote.png"}
        }],
    }],
}
snapshot_inventory = {
    "dropCampaignsInProgress": {"edges": [{"node": {"id": "campaign-one"}}]},
    "gameEventDrops": [{
        "campaignID": "campaign-three",
        "name": "Campaign Three",
        "game": {"id": "30", "displayName": "Game Three", "slug": "game-three"},
        "drops": [{
            "id": "emote-drop",
            "name": "Twitch Emote",
            "requiredMinutesWatched": 45,
            "benefitEdges": {
                "edges": [{
                    "node": {
                        "benefit": {"name": "Twitch Emote", "distributionType": "EMOTE"}
                    }
                }]
            },
        }],
    }],
}
snapshot_dashboard = {
    "data": {
        "currentUser": {"dropCampaigns": {"edges": [{"node": snapshot_campaign_one}]}},
        "rewardCampaignsAvailableToUser": {
            "edges": [
                {"node": {"campaign": snapshot_campaign_two}},
                {"node": {"campaign": snapshot_campaign_in_progress}},
                {"node": {"campaign": snapshot_campaign_no_game}},
                {"node": {"campaign": snapshot_campaign_no_time_drops}},
                {"node": {"campaign": snapshot_campaign_no_image}},
                {"node": {"campaign": snapshot_campaign_marvel}},
                {"node": {"campaign": {
                    "id": "global-twitch-rewards",
                    "status": "ACTIVE",
                    "name": "Twitch Global Rewards",
                    "game": None,
                    "timeBasedDrops": [{
                        "id": "global-badge-drop",
                        "name": "Global Twitch Badge",
                        "requiredMinutesWatched": 30,
                        "benefitEdges": [{"benefit": {"name": "Global Badge", "distributionType": "BADGE"}}],
                    }],
                }}},
                {"node": {"campaign": {
                    "id": "campaign-paid-subs-goal",
                    "status": "ACTIVE",
                    "name": "Paid subs goal campaign",
                    "game": {"id": "63", "displayName": "Paid Game"},
                    "timeBasedDrops": [{"id": "only-subs-goal", "name": "Paid goal", "dropType": "SUBSGOAL", "subsGoal": {"required": 2}}],
                }}},
            ],
        },
        "extraCampaigns": {
            "rewardCampaignsAvailableToUser": {
                "nodes": [
                    {"campaign": {
                        "id": "campaign-upcoming",
                        "status": "UPCOMING",
                        "name": "Upcoming campaign",
                        "game": {"id": "40", "displayName": "Upcoming Game"},
                        "timeBasedDrops": [{"id": "upcoming-drop", "name": "Upcoming", "boxArtURL": "https://example.test/upcoming-box.png"}],
                    }},
                    {"campaign": {
                        "id": "campaign-empty-status",
                        "name": "Campaign with empty status",
                        "game": {"id": "41", "displayName": "Empty Status Game"},
                        "timeBasedDrops": [{"id": "empty-status-drop", "name": "Empty Status Reward", "imageURL": "https://example.test/empty-status.png"}],
                    }},
                ],
            },
        },
    }
}
with (
    patch.object(Twitch, "_Twitch__get_inventory", lambda self: snapshot_inventory),
    patch.object(Twitch, "post_gql_request", lambda self, request: snapshot_dashboard),
    patch.object(Twitch, "_Twitch__get_campaigns_details", return_campaign_details),
    patch.object(Twitch, "_warn_drops_response", lambda self, *args: None),
):
    snapshot = snapshot_client.get_drop_inventory_snapshot()
assert {campaign["id"] for campaign in snapshot} == {"campaign-one", "campaign-two", "campaign-no-image", "marvel-rivals", "campaign-upcoming", "campaign-empty-status", "campaign-in-progress", "pokemon-evolutions", "global-twitch-rewards"}
campaigns_by_id = {campaign["id"]: campaign for campaign in snapshot}
assert {"campaign-one", "campaign-two", "marvel-rivals", "campaign-in-progress"}.issubset({str(item.get("id") or item.get("campaignID") or item.get("campaignId") or "") for item in snapshot_details_requested}), "Details must be requested for available campaigns before they are in inventory."
assert campaigns_by_id["campaign-one"]["started"] is True
assert campaigns_by_id["campaign-one"]["in_inventory"] is True
assert campaigns_by_id["campaign-two"]["started"] is False
assert campaigns_by_id["marvel-rivals"]["name"] == "Marvel Rivals"
assert campaigns_by_id["marvel-rivals"]["status"] == "ACTIVE"
assert campaigns_by_id["campaign-upcoming"]["status"] == "UPCOMING"
assert campaigns_by_id["campaign-empty-status"]["status"] == "ACTIVE"
assert campaigns_by_id["campaign-in-progress"]["game"] == "Twitch"
assert campaigns_by_id["pokemon-evolutions"]["game"] == "Twitch"
assert campaigns_by_id["global-twitch-rewards"]["game"] == "Twitch"
for excluded in ("campaign-three", "campaign-empty-timebased", "campaign-paid-subs-goal"):
    assert excluded not in campaigns_by_id, f"Invalid Drops campaign must be hidden: {excluded}"
snapshot_drops = {
    drop["id"]: drop
    for campaign in snapshot
    for drop in campaign["drops"]
}
assert set(snapshot_drops) == {"badge-drop", "game-drop", "drop-no-image", "marvel-emote-drop", "upcoming-drop", "empty-status-drop", "available-emote-drop", "pokemon-drop", "global-badge-drop"}
assert snapshot_drops["badge-drop"]["category"] == "twitch"
assert snapshot_drops["marvel-emote-drop"]["category"] == "twitch"
assert snapshot_drops["game-drop"]["category"] == "game"
assert snapshot_drops["game-drop"]["benefit_type"] == "DIRECT_ENTITLEMENT"
assert all(drop["is_time_based"] for drop in snapshot_drops.values())
assert snapshot_drops["drop-no-image"]["image_url"] == ""
assert snapshot_drops["upcoming-drop"]["image_url"] == "https://example.test/upcoming-box.png"
assert snapshot_drops["empty-status-drop"]["image_url"] == "https://example.test/empty-status.png"
assert snapshot_drops["global-badge-drop"]["benefit"] == "Global Badge"
assert snapshot_drops["badge-drop"]["is_twitch_view"] is True
assert snapshot_drops["game-drop"]["is_twitch_view"] is False
assert snapshot_drops["marvel-emote-drop"]["claimed"] is False
assert snapshot_drops["marvel-emote-drop"]["watched_minutes"] == 12
assert snapshot_drops["marvel-emote-drop"]["is_twitch_view"] is True
assert all(drop["type"] == "watch" for drop in snapshot_drops.values())
assert "paid-drop" not in snapshot_drops
assert "paid-subs-goal" not in snapshot_drops
assert "only-subs-goal" not in snapshot_drops

assert GQLOperations.Inventory["variables"]["fetchRewardCampaigns"] is False
assert GQLOperations.ViewerDropsDashboard["variables"]["fetchRewardCampaigns"] is False
assert GQLOperations.Inventory["extensions"]["persistedQuery"]["sha256Hash"] == "8337eb8541b314040b0edde0c09c5c7a2783ba1960aa9edfbf3bac16d0fec404"
assert GQLOperations.ViewerDropsDashboard["extensions"]["persistedQuery"]["sha256Hash"] == "c16bb890cc8ce7647a96ee69cd313d423a378a3dedadf630a1017cde18975feb"
assert GQLOperations.DropCampaignDetails["extensions"]["persistedQuery"]["sha256Hash"] == "039277bf98f3130929262cc7c6efd9c141ca3749cb6dca442fc8ead9a53f77c1"
assert GQLOperations.DropsHighlightService_AvailableDrops["extensions"]["persistedQuery"]["sha256Hash"] == "782dad0f032942260171d2d80a654f88bdd0c5a9dddc392e9bc92218a0f42d20"
available_campaign = {
    "id": "available-channel-campaign",
    "name": "Available Channel Campaign",
    "detailsURL": "https://www.twitch.tv/drops/campaigns/available-channel-campaign",
    "imageURL": "https://example.test/campaign.png",
    "endAt": "2099-12-31T23:59:59Z",
    "game": {"id": "4242", "name": "Example Game"},
    "timeBasedDrops": [{
        "id": "available-channel-drop",
        "name": "Exclusive Twitch Emote",
        "startAt": "2026-01-01T00:00:00Z",
        "endAt": "2099-12-31T23:59:59Z",
        "requiredMinutesWatched": 45,
        "benefitEdges": [{
            "benefit": {
                "id": "available-benefit",
                "name": "Exclusive Twitch Emote",
                "imageAssetURL": "https://example.test/emote.png",
                "game": None,
            }
        }],
    }],
}
available_client = Twitch.__new__(Twitch)
available_query_payloads = [
    {"data": {"channel": {"viewerDropCampaigns": [available_campaign]}}},
    {"data": {"channel": {"viewerDropCampaigns": []}}},
]
with patch.object(Twitch, "post_gql_request", return_value=available_query_payloads) as available_query:
    available_by_login = available_client.get_drop_campaigns_for_channels([
        {"login": "live_one", "channel_id": "1001"},
        {"login": "live_two", "channel_id": "1002"},
    ])
assert available_by_login["live_one"][0]["timeBasedDrops"][0]["id"] == "available-channel-drop"
assert available_by_login["live_one"][0]["game"]["id"] == "4242"
assert available_by_login["live_two"] == []
assert len(available_query.call_args.args[0]) == 2

available_snapshot_client = Twitch.__new__(Twitch)
available_details_requested = []
with (
    patch.object(Twitch, "_Twitch__get_inventory", lambda self: {"dropCampaignsInProgress": [], "gameEventDrops": []}),
    patch.object(Twitch, "post_gql_request", lambda self, request: {"data": {"currentUser": {"dropCampaigns": None}}}),
    patch.object(
        Twitch,
        "_Twitch__get_campaigns_details",
        lambda self, candidates: available_details_requested.extend(candidates) or [],
    ),
    patch.object(Twitch, "_warn_drops_response", lambda self, *args: None),
):
    available_snapshot = available_snapshot_client.get_drop_inventory_snapshot({
        "live_one": [available_campaign],
    })
assert len(available_snapshot) == 1, "AvailableDrops campaign summaries must create catalog entries without ViewerDropsDashboard."
assert available_snapshot[0]["id"] == "available-channel-campaign"
assert available_snapshot[0]["game"] == "Example Game"
assert available_snapshot[0]["game_id"] == "4242"
assert available_snapshot[0]["drops"][0]["id"] == "available-channel-drop"
assert available_snapshot[0]["drops"][0]["category"] == "twitch"
assert available_snapshot[0]["drops"][0]["image_url"] == "https://example.test/emote.png"
assert available_details_requested == [], "Complete AvailableDrops summaries must not depend on DropCampaignDetails."

fallback_client = Twitch.__new__(Twitch)
fallback_client.twitch_login = SimpleNamespace(get_user_id=lambda: 1346146321, username="dv4roka")
detail_requests = []

def fake_campaign_details_request(self, payload):
    detail_requests.append(payload)
    if len(detail_requests) == 1:
        return [{"data": {"user": {"dropCampaign": None}}}]
    return [{"data": {"user": {"dropCampaign": {"id": "fallback-campaign", "name": "Fallback Campaign"}}}}]

with patch.object(Twitch, "post_gql_request", fake_campaign_details_request):
    fallback_details = fallback_client._Twitch__get_campaigns_details([{"id": "fallback-campaign"}])
assert [item["id"] for item in fallback_details] == ["fallback-campaign"]
assert detail_requests[0][0]["variables"]["channelLogin"] == "1346146321"
assert detail_requests[1][0]["variables"]["channelLogin"] == "dv4roka"

directory_client = Twitch.__new__(Twitch)
directory_request = []
directory_response = {
    "data": {
        "game": {
            "streams": {
                "edges": [{
                    "node": {
                        "viewersCount": 4321,
                        "title": "Drops enabled stream",
                        "game": {"id": "4242", "displayName": "Example Game"},
                        "broadcaster": {"id": "9001", "login": "drops_live", "displayName": "Drops Live"},
                    }
                }]
            }
        }
    }
}

def fake_directory_request(self, payload):
    directory_request.append(payload)
    return directory_response

with patch.object(Twitch, "post_gql_request", fake_directory_request):
    directory_streams = directory_client.get_drops_streams_for_game("example-game", "4242")
assert len(directory_streams) == 1
assert directory_streams[0]["user_login"] == "drops_live"
assert directory_streams[0]["user_id"] == "9001"
assert directory_streams[0]["game_id"] == "4242"
assert directory_streams[0]["viewer_count"] == 4321
assert directory_streams[0]["drops_enabled"] is True
assert directory_request[0]["operationName"] == "DirectoryPage_Game"
assert directory_request[0]["variables"]["options"]["systemFilters"] == ["DROPS_ENABLED"]

claimed_campaign = {
    "id": "claimed-merge-campaign",
    "status": "ACTIVE",
    "name": "Claimed Merge Campaign",
    "game": {"id": "77", "displayName": "Claim Test Game"},
    "timeBasedDrops": [{
        "id": "claimed-merge-drop",
        "name": "Already claimed reward",
        "requiredMinutesWatched": 30,
        "self": {"isClaimed": "true", "currentMinutesWatched": "30"},
        "benefitEdges": [{"benefit": {"name": "Claimed Reward", "distributionType": "EMOTE"}}],
    }],
}
stale_channel_campaign = {
    "id": "claimed-merge-campaign",
    "status": "ACTIVE",
    "name": "Claimed Merge Campaign",
    "game": {"id": "77", "displayName": "Claim Test Game"},
    "timeBasedDrops": [{
        "id": "claimed-merge-drop",
        "name": "Already claimed reward",
        "requiredMinutesWatched": 30,
        "self": {"isClaimed": "false", "currentMinutesWatched": "0"},
        "benefitEdges": [{"benefit": {"name": "Claimed Reward", "distributionType": "EMOTE"}}],
    }],
}
claimed_merge_client = Twitch.__new__(Twitch)
claimed_dashboard = {
    "data": {
        "currentUser": {
            "dropCampaigns": {
                "edges": [{"node": claimed_campaign}],
            },
        },
    },
}
with (
    patch.object(Twitch, "_Twitch__get_inventory", lambda self: {"dropCampaignsInProgress": [], "gameEventDrops": []}),
    patch.object(Twitch, "post_gql_request", lambda self, request: claimed_dashboard),
    patch.object(Twitch, "_Twitch__get_campaigns_details", lambda self, candidates: []),
    patch.object(Twitch, "_warn_drops_response", lambda self, *args: None),
):
    claimed_snapshot = claimed_merge_client.get_drop_inventory_snapshot({
        "stale_channel": [stale_channel_campaign],
    })
claimed_drops = {
    drop["id"]: drop
    for campaign in claimed_snapshot
    for drop in campaign["drops"]
}
assert claimed_drops["claimed-merge-drop"]["claimed"] is True, "A stale channel campaign must not overwrite an already claimed inventory reward."

engine_source = (ROOT / "Twitch Farming" / "engine" / "TwitchChannelPointsMiner" / "TwitchChannelPointsMiner.py").read_text(encoding="utf-8")
assert "except requests.exceptions.RequestException as error:" in engine_source, "A single Twitch API timeout must not terminate the Engine worker."
backend_source = (ROOT / "Twitch Farming" / "backend.py").read_text(encoding="utf-8")
assert "get_drop_campaigns_for_channels(live_channels)" in backend_source, "Drops catalog must request full AvailableDrops campaign objects for all live farm/followed channels."
assert "configured_logins = list(dict.fromkeys(" in backend_source and 'self.client.helix_get(\n                        "streams",' in backend_source, "Configured farm channels must be checked for live streams even when they are not followed."
assert "get_drop_inventory_snapshot(" in backend_source and "available_campaigns_by_login\n            )" in backend_source, "AvailableDrops campaign summaries must be passed into catalog normalization."
assert "get_drop_inventory_snapshot(" in backend_source and "helix_get_payload(\"streams\", helix_params)" in backend_source, "Drops streamer search must follow Helix pagination instead of stopping at the first 100 live channels."
assert "get_drops_streams_for_game(" in backend_source, "Drops streamer search must also use Twitch's GameDirectory DROPS_ENABLED filter."
assert "self.client.get_game_info(game_id, game_name)" in backend_source and "campaign_stream_keys" in backend_source, "Drops campaign search must resolve Twitch's canonical game ID and slug before finding live streams."
assert "select_drops_streamers(" in backend_source and 'campaign["streamers"] = select_drops_streamers(' in backend_source, "Drops cards must keep followed matches first and fill the remaining slots from the general catalog."
assert 'max(0, int(stream.get("viewer_count") or 0))' in backend_source, "The general Twitch catalog must rank live streams by viewer count before selecting the top three."

assert '"game_id": str(stream.get("game_id") or game_id)' in backend_source, "GameDirectory streams must preserve their actual game ID when the campaign metadata is incomplete."
assert 'if not item.get("drops_enabled")' in backend_source and 'and not row.get("drops_enabled")' in backend_source, "GameDirectory streams with DROPS_ENABLED must not be rejected by an extra per-channel campaign ID lookup."
twitch_source = (ROOT / "Twitch Farming" / "engine" / "TwitchChannelPointsMiner" / "classes" / "Twitch.py").read_text(encoding="utf-8")
assert "for attempt in range(2):" in twitch_source and "requests.exceptions.Timeout" in twitch_source, "Helix requests must retry transient timeouts."
assert "campaigns_update = 0" in twitch_source and "requests.exceptions.RequestException" in twitch_source, "Drops sync must retry after transient request failures."
assert "def get_drops_streams_for_game(" in twitch_source and '"systemFilters": ["DROPS_ENABLED"]' in twitch_source, "Twitch GameDirectory must be queried with the DROPS_ENABLED filter."
assert claimed_drops["claimed-merge-drop"]["claimed"] is True, "Claimed drops must remain filtered after data-source merging."
assert "def _drop_is_claimed(drop)" in (ROOT / "Twitch Farming" / "qt_app.py").read_text(encoding="utf-8"), "The Drops UI must detect claimed flags from all Twitch response shapes."
assert "def set_drops_campaign_focus(" in backend_source and "def _find_drops_focus_candidate(" in backend_source and "def _validate_drops_focus_channel(" in backend_source, "The backend must provide a continuous campaign focus manager with live, category and Drops validation."
assert "additional_followed" in backend_source and "followed_logins[offset:offset + 100]" in backend_source, "The focus manager must check the full current live follower pool before using the public Twitch catalog."
assert "self.app._remove_drops_focus_channel(campaign_id)" in backend_source and "allowed_channel_ids" in backend_source, "The old temporary channel must be released immediately when it no longer matches the focused game or Drops eligibility."
assert "def remove_streamer(self, username):" in engine_source and "drops_focus_login" in twitch_source, "The active miner must support removing replaced temporary channels and pinning the focus streamer into one watch slot."
qt_source = (ROOT / "Twitch Farming" / "qt_app.py").read_text(encoding="utf-8")
assert "Фармить кампанию" in qt_source and "Фармится · Снять фокус" in qt_source and "drops_focus_banner" in qt_source, "Each Drops campaign card and the top inventory banner must expose selected campaign focus."

print("Drops inventory merging, already-claimed reward filtering, GameDirectory category lookup, Helix pagination, campaign detail fallback and HLS tests passed.")
