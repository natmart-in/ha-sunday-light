"""Standalone tests for turn-on planning (soft start).

Runs with plain Python (no pytest, no Home Assistant):

    python tests/test_soft_start.py
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

import soft_start  # noqa: E402
from soft_start import (  # noqa: E402
    SOFT_START_BRIGHTNESS,
    SOFT_START_FADE_MS,
    TurnOnPlan,
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


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} tests passed")


if __name__ == "__main__":
    main()
