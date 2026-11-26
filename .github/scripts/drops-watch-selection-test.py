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
        if len(dispatched) == len(dispatch_streamers):
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

assert dispatched == [streamer.username for streamer in dispatch_streamers]
assert worker_counts == [len(dispatch_streamers)]
assert any(
    f"Отправка minute-watched для {len(dispatch_streamers)} онлайн-каналов:" in message
    and all(streamer.username in message for streamer in dispatch_streamers)
    for message in dispatch_logs
), "The dispatch log must list every online streamer before sending."

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

print("Multi-channel watch and Twitch Drops HLS tests passed.")
