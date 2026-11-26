import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Twitch Farming" / "engine"))

from TwitchChannelPointsMiner.classes.Settings import Priority
from TwitchChannelPointsMiner.classes.Twitch import Twitch


class FakeStreamer:
    def __init__(self, drops_active=False, synchronized=True, pending=False):
        self._drops_active = drops_active
        self.stream = SimpleNamespace(
            drops_campaigns_synced=synchronized,
            campaigns_ids=["active-campaign"] if drops_active else [],
            campaigns=[
                SimpleNamespace(drops=[SimpleNamespace(is_claimed=False)])
            ] if pending else []
        )

    def drops_condition(self):
        return self._drops_active


def select(streamers):
    return Twitch._select_minute_watch_indices(
        streamers,
        list(range(len(streamers))),
        [Priority.ORDER]
    )


watching, drops_mode = select([
    FakeStreamer(drops_active=True, synchronized=True, pending=True),
    FakeStreamer()
])
assert watching == [0] and drops_mode is True, "An active Drops channel must occupy the only watch slot."

watching, drops_mode = select([
    FakeStreamer(),
    FakeStreamer(drops_active=True, synchronized=True, pending=True)
])
assert watching == [1] and drops_mode is True, "A Drops channel in the second slot must be selected alone."

watching, drops_mode = select([
    FakeStreamer(drops_active=True, synchronized=True, pending=True),
    FakeStreamer(drops_active=True, synchronized=True, pending=True)
])
assert watching == [0] and drops_mode is True, "Only one of multiple active Drops channels may be selected."

watching, drops_mode = select([
    FakeStreamer(drops_active=True, synchronized=True, pending=False),
    FakeStreamer()
])
assert watching == [0, 1] and drops_mode is False, "Normal two-slot viewing must resume after Drops are completed."

watching, drops_mode = select([
    FakeStreamer(drops_active=True, synchronized=False, pending=False),
    FakeStreamer()
])
assert watching == [0] and drops_mode is True, "An unsynchronized active Drops campaign must reserve one slot."

print("Drops minute-watched selection tests passed.")
