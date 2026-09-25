"""Standalone tests for turn-on planning and sequencing (soft start).

Runs with plain Python (no pytest, no Home Assistant):

    python tests/test_soft_start.py
"""

from __future__ import annotations

import asyncio
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

import soft_start  # noqa: E402
from soft_start import (  # noqa: E402
    SOFT_START_BRIGHTNESS,
    SOFT_START_FADE_MS,
    SOFT_START_SETTLE_S,
    TurnOnPlan,
    async_run_turn_on,
    plan_turn_on,
)


def test_off_to_full_parks_low_then_fades_up() -> None:
    plan = plan_turn_on(
        is_on=False, target_brightness=1.0, saved_brightness=1.0, lerp_ms=250
    )
    assert plan == TurnOnPlan(
        pre_brightness=SOFT_START_BRIGHTNESS,
        power_on=True,
        brightness=1.0,
        lerp_ms=SOFT_START_FADE_MS,
    )


def test_off_without_target_restores_saved_level_softly() -> None:
    plan = plan_turn_on(
        is_on=False, target_brightness=None, saved_brightness=0.8, lerp_ms=250
    )
    assert plan.pre_brightness == SOFT_START_BRIGHTNESS
    assert plan.brightness == 0.8
    assert plan.lerp_ms == SOFT_START_FADE_MS


def test_unknown_saved_level_assumes_firmware_default() -> None:
    plan = plan_turn_on(
        is_on=None, target_brightness=None, saved_brightness=None, lerp_ms=250
    )
    assert plan.power_on is True
    assert plan.pre_brightness == SOFT_START_BRIGHTNESS
    assert plan.brightness == soft_start.FIRMWARE_DEFAULT_BRIGHTNESS


def test_low_target_powers_on_straight_to_it() -> None:
    plan = plan_turn_on(
        is_on=False, target_brightness=0.02, saved_brightness=1.0, lerp_ms=250
    )
    assert plan == TurnOnPlan(
        pre_brightness=0.02, power_on=True, brightness=None, lerp_ms=250
    )


def test_longer_requested_transition_is_kept() -> None:
    plan = plan_turn_on(
        is_on=False, target_brightness=1.0, saved_brightness=0.3, lerp_ms=10_000
    )
    assert plan.lerp_ms == 10_000


def test_already_on_is_a_plain_adjustment() -> None:
    plan = plan_turn_on(
        is_on=True, target_brightness=0.6, saved_brightness=1.0, lerp_ms=250
    )
    assert plan == TurnOnPlan(
        pre_brightness=None, power_on=False, brightness=0.6, lerp_ms=250
    )


def test_already_on_without_target_changes_nothing() -> None:
    plan = plan_turn_on(
        is_on=True, target_brightness=None, saved_brightness=1.0, lerp_ms=250
    )
    assert plan.pre_brightness is None
    assert plan.power_on is False
    assert plan.brightness is None


class FakeApi:
    """Records calls in order; can fail on a named call."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple] = []
        self.fail_on = fail_on

    async def async_set_power(self, lamp_id: str, is_on: bool) -> None:
        self.calls.append(("power", is_on))
        if self.fail_on == "power":
            raise RuntimeError("power failed")

    async def async_update_lamp(
        self,
        lamp_id: str,
        *,
        brightness: float | None = None,
        color_temp_k: int | None = None,
        lerp_ms: int = 250,
    ) -> None:
        self.calls.append(("update", brightness, color_temp_k, lerp_ms))
        if self.fail_on == "update" and brightness != SOFT_START_BRIGHTNESS:
            raise RuntimeError("update failed")


def _run(plan, api, *, kelvin=None, superseded_after_sleeps=None):
    progress: list = []
    sleeps = 0

    async def sleep(seconds: float) -> None:
        nonlocal sleeps
        assert seconds == SOFT_START_SETTLE_S
        sleeps += 1
        api.calls.append(("sleep",))

    def superseded() -> bool:
        return (
            superseded_after_sleeps is not None and sleeps >= superseded_after_sleeps
        )

    asyncio.run(
        async_run_turn_on(
            api,
            "L1",
            plan,
            color_temp_k=kelvin,
            superseded=superseded,
            on_progress=progress.append,
            sleep=sleep,
        )
    )
    return progress


def test_run_soft_start_orders_park_power_then_fade() -> None:
    api = FakeApi()
    plan = plan_turn_on(
        is_on=False, target_brightness=1.0, saved_brightness=1.0, lerp_ms=250
    )
    progress = _run(plan, api, kelvin=3800)
    assert api.calls == [
        ("update", SOFT_START_BRIGHTNESS, 3800, 250),
        ("sleep",),
        ("power", True),
        ("sleep",),
        ("update", 1.0, 3800, SOFT_START_FADE_MS),
    ]
    assert progress == [SOFT_START_BRIGHTNESS, 1.0]


def test_run_low_target_has_no_fade_step() -> None:
    api = FakeApi()
    plan = plan_turn_on(
        is_on=False, target_brightness=0.02, saved_brightness=1.0, lerp_ms=250
    )
    progress = _run(plan, api)
    assert api.calls == [("update", 0.02, None, 250), ("sleep",), ("power", True)]
    assert progress == [0.02]


def test_run_already_on_colour_only_is_one_update() -> None:
    api = FakeApi()
    plan = plan_turn_on(
        is_on=True, target_brightness=None, saved_brightness=0.7, lerp_ms=500
    )
    progress = _run(plan, api, kelvin=4000)
    assert api.calls == [("update", None, 4000, 500)]
    assert progress == [None]


def test_run_superseded_after_park_never_powers_on() -> None:
    api = FakeApi()
    plan = plan_turn_on(
        is_on=False, target_brightness=1.0, saved_brightness=1.0, lerp_ms=250
    )
    progress = _run(plan, api, superseded_after_sleeps=1)
    assert ("power", True) not in api.calls
    assert progress == []


def test_run_superseded_after_power_on_skips_fade() -> None:
    api = FakeApi()
    plan = plan_turn_on(
        is_on=False, target_brightness=1.0, saved_brightness=1.0, lerp_ms=250
    )
    progress = _run(plan, api, superseded_after_sleeps=2)
    assert api.calls[-1] == ("sleep",)
    assert ("power", True) in api.calls
    assert progress == [SOFT_START_BRIGHTNESS]


def test_run_failure_stops_and_raises() -> None:
    api = FakeApi(fail_on="power")
    plan = plan_turn_on(
        is_on=False, target_brightness=1.0, saved_brightness=1.0, lerp_ms=250
    )
    try:
        _run(plan, api)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the power failure to propagate")
    assert api.calls[-1] == ("power", True)
    assert not any(c[0] == "update" and c[1] == 1.0 for c in api.calls)


def test_run_fade_failure_leaves_lamp_at_start_level() -> None:
    api = FakeApi(fail_on="update")
    plan = plan_turn_on(
        is_on=False, target_brightness=1.0, saved_brightness=1.0, lerp_ms=250
    )
    try:
        _run(plan, api)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the fade-up failure to propagate")
    assert ("power", True) in api.calls
    assert api.calls[-1] == ("update", 1.0, None, SOFT_START_FADE_MS)


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} tests passed")


if __name__ == "__main__":
    main()
