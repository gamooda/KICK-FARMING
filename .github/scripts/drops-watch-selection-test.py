import base64
import json
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

Settings.logger = SimpleNamespace(less=True)


class FakeResponse:
    def __init__(self, status_code=200, text="", url=""):
        self.status_code = status_code
        self.text = text
        self.url = url


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


dispatch_watcher._send_minute_watched_for_streamer = lambda streamer: None
setattr(dispatch_watcher, "_Twitch__chuncked_sleep", lambda *args, **kwargs: None)
dispatch_logs = []
with (
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
playlist_url = "https://video.example/live/index.m3u8"
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
inventory_reader.post_gql_request = lambda request: {
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
inventory_reader._warn_drops_response = lambda *args: None
inventory_result = inventory_reader._Twitch__get_inventory()
assert inventory_result["dropCampaignsInProgress"] == [{"id": "inventory-edge-campaign"}]

snapshot_client = Twitch.__new__(Twitch)
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
                        "benefitType": "BADGE",
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
                    "benefit": {"name": "Weapon Skin", "benefitType": "GAME_ITEM"}
                }],
            }
        }]
    },
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
                        "benefit": {"name": "Twitch Emote", "benefitType": "EMOTE"}
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
            "edges": [{"node": {"campaign": snapshot_campaign_two}}],
        },
        "extraCampaigns": {
            "rewardCampaignsAvailableToUser": {
                "nodes": [{"campaign": {
                    "id": "campaign-upcoming",
                    "status": "UPCOMING",
                    "name": "Upcoming campaign",
                    "game": {"id": "40", "displayName": "Upcoming Game"},
                    "timeBasedDrops": [{"id": "upcoming-drop", "name": "Upcoming"}],
                }}],
            },
        },
    }
}
snapshot_client._Twitch__get_inventory = lambda: snapshot_inventory
snapshot_client.post_gql_request = lambda request: snapshot_dashboard
snapshot_client._warn_drops_response = lambda *args: None
snapshot = snapshot_client.get_drop_inventory_snapshot()
assert {campaign["id"] for campaign in snapshot} == {"campaign-one", "campaign-two", "campaign-three"}
campaigns_by_id = {campaign["id"]: campaign for campaign in snapshot}
assert campaigns_by_id["campaign-one"]["started"] is True
assert campaigns_by_id["campaign-one"]["in_inventory"] is True
assert campaigns_by_id["campaign-two"]["started"] is False
assert "campaign-upcoming" not in campaigns_by_id
snapshot_drops = {
    drop["id"]: drop
    for campaign in snapshot
    for drop in campaign["drops"]
}
assert set(snapshot_drops) == {"badge-drop", "game-drop", "emote-drop"}
assert snapshot_drops["badge-drop"]["category"] == "twitch"
assert snapshot_drops["emote-drop"]["category"] == "twitch"
assert snapshot_drops["game-drop"]["category"] == "game"
assert snapshot_drops["badge-drop"]["is_time_based"] is True
assert snapshot_drops["badge-drop"]["is_twitch_view"] is True
assert snapshot_drops["game-drop"]["is_twitch_view"] is False
assert snapshot_drops["emote-drop"]["is_time_based"] is False
assert snapshot_drops["emote-drop"]["is_twitch_view"] is False
assert all(drop["type"] == "watch" for drop in snapshot_drops.values())
assert "paid-drop" not in snapshot_drops

print("Multi-channel watch, full Drops inventory parsing, reward categories and Twitch Drops HLS tests passed.")
