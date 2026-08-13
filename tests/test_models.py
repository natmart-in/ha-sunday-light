"""Standalone tests for the tolerant API parsers.

Runs with plain Python (no pytest, no Home Assistant):

    python tests/test_models.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(
    0,
    str(
        pathlib.Path(__file__).resolve().parents[1]
        / "custom_components"
        / "sunday_light"
    ),
)

import models  # noqa: E402


def test_lamp_state_current_shape() -> None:
    payload = {
        "lamp_id": "80B54EE51980",
        "home_id": "home_1",
        "online": True,
        "display_state": "Online",
        "state": {
            "desired": {"is_on": True, "brightness": 0.5, "colourTemperatureK": 5800},
            "reported": {"brightness": 0.28, "colourTemperatureK": 3456.4},
        },
    }
    state = models.parse_lamp_state(payload)
    assert state is not None
    assert state.lamp_id == "80B54EE51980"
    assert state.is_on is True  # desired wins for power
    assert state.brightness == 0.28  # reported wins for levels
    assert state.color_temp_k == 3456  # float rounded
    assert state.online is True


def test_lamp_state_legacy_envelopes_and_keys() -> None:
    for envelope in ("lamp", "data"):
        payload = {
            envelope: {
                "device_id": "ABC123",
                "state": {"reported": {"is_on": "true", "cct": 4000, "brightness": 42}},
            }
        }
        state = models.parse_lamp_state(payload)
        assert state is not None
        assert state.lamp_id == "ABC123"
        assert state.is_on is True  # string bool tolerated
        assert state.brightness == 0.42  # legacy 0-100 scale normalised
        assert state.color_temp_k == 4000  # legacy cct key
        assert state.online is True  # inferred from reported presence


def test_lamp_state_offline_and_missing() -> None:
    state = models.parse_lamp_state(
        {"lamp_id": "X", "display_state": "Offline", "state": {"desired": {}}}
    )
    assert state is not None
    assert state.online is False
    assert state.brightness is None
    assert state.color_temp_k is None
    assert models.parse_lamp_state({"no_id": True}) is None
    assert models.parse_lamp_state("not a dict") is None


def test_room_state_shapes() -> None:
    lamp = {"lamp_id": "L1", "state": {"reported": {"is_on": True}}}
    for payload in ({"lamps": [lamp]}, {"data": {"lamps": [lamp]}}, [lamp]):
        states = models.parse_room_state(payload)
        assert len(states) == 1
        assert states[0].lamp_id == "L1"


def test_homes_rooms_lamps() -> None:
    homes = models.parse_homes({"homes": [{"home_id": "h1", "name": "Studio"}]})
    assert homes == [models.SundayHome(home_id="h1", name="Studio", role="unknown")]
    homes = models.parse_homes({"data": {"homes": [{"id": "h2", "role": "owner"}]}})
    assert homes[0].home_id == "h2"
    assert homes[0].role == "owner"

    rooms = models.parse_rooms({"groups": [{"group_id": "r1", "name": "Kitchen"}]}, "h1")
    assert rooms[0].room_id == "r1"
    assert rooms[0].name == "Kitchen"

    lamps = models.parse_home_lamps(
        {
            "lamps": [
                {"lamp_id": "L1", "friendly_name": "Big Light", "room_id": "r1"},
                {"device_id": "L2", "name": "Spare", "group_id": None},
            ]
        },
        "h1",
    )
    assert lamps[0] == models.SundayLampInfo(
        lamp_id="L1", home_id="h1", name="Big Light", room_id="r1"
    )
    assert lamps[1].room_id is None
    assert lamps[1].name == "Spare"


def test_brightness_clamping() -> None:
    assert models._as_brightness(0.0) == 0.01
    assert models._as_brightness(1.5) == 0.015  # >1 treated as 0-100 scale
    assert models._as_brightness(100) == 1.0
    assert models._as_brightness(True) is None
    assert models._as_brightness("0.5") is None


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} tests passed")


if __name__ == "__main__":
    main()
