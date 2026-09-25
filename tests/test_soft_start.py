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
from models import LampState  # noqa: E402
from soft_start import (  # noqa: E402
    SOFT_START_ACK_TIMEOUT_S,
    SOFT_START_BRIGHTNESS,
    SOFT_START_FADE_MS,
    SOFT_START_POLL_S,
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


class FakeLamp:
    """A lamp behind its shadow, as the API sees it.

    Commands reach the lamp `lag` state reads later. Parking a level while it
    is dark is kept as the saved level and triggers the firmware's repair,
    which writes desired.is_on=false - with the level report by default, or
    one read after it (`repair_after_report`), the worst ordering.
    """

    def __init__(
        self,
        *,
        lag: int = 1,
        park_ack: bool = True,
        power_ack: bool = True,
        repair_after_report: bool = False,
        fail_on: str | None = None,
        read_errors: int = 0,
    ) -> None:
        self.calls: list[tuple] = []
        self.reads = 0
        self.lag = lag
        self.park_ack = park_ack
        self.power_ack = power_ack
        self.repair_after_report = repair_after_report
        self.fail_on = fail_on
        self.read_errors = read_errors
        self.desired_on = False
        self.reported_on = False
        self.reported_brightness = 1.0
        self._pending: list[list] = []

    def _later(self, reads: int, fn) -> None:
        self._pending.append([reads, fn])

    def _repair(self) -> None:
        self.desired_on = False

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
        if brightness is not None and not self.reported_on and self.park_ack:

            def deliver() -> None:
                self.reported_brightness = brightness
                if self.repair_after_report:
                    self._later(1, self._repair)
                else:
                    self._repair()

            self._later(self.lag, deliver)

    async def async_set_power(self, lamp_id: str, is_on: bool) -> None:
        self.calls.append(("power", is_on))
        if self.fail_on == "power":
            raise RuntimeError("power failed")
        self.desired_on = is_on
        if self.power_ack:

            def deliver() -> None:
                if self.desired_on:
                    self.reported_on = True

            self._later(self.lag, deliver)

    async def async_get_lamp_state(self, lamp_id: str) -> LampState:
        self.reads += 1
        for item in self._pending:
            item[0] -= 1
        due = [item for item in self._pending if item[0] <= 0]
        self._pending = [item for item in self._pending if item[0] > 0]
        for _, fn in due:
            fn()
        if self.read_errors:
            self.read_errors -= 1
            raise RuntimeError("read failed")
        return LampState(
            lamp_id=lamp_id,
            is_on=self.desired_on,
            brightness=self.reported_brightness,
            color_temp_k=None,
            online=True,
            display_state="Online",
            reported_is_on=self.reported_on,
            reported_brightness=self.reported_brightness,
        )


def _run(plan, api, *, kelvin=None, superseded_after_sleeps=None):
    progress: list = []
    sleeps = 0

    async def sleep(seconds: float) -> None:
        nonlocal sleeps
        assert seconds == SOFT_START_POLL_S
        sleeps += 1

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


def _full_plan() -> TurnOnPlan:
    return plan_turn_on(
        is_on=False, target_brightness=1.0, saved_brightness=1.0, lerp_ms=250
    )


def test_run_soft_start_orders_park_power_then_fade() -> None:
    lamp = FakeLamp()
    progress = _run(_full_plan(), lamp, kelvin=3800)
    assert lamp.calls == [
        ("update", SOFT_START_BRIGHTNESS, 3800, 250),
        ("power", True),
        ("update", 1.0, 3800, SOFT_START_FADE_MS),
    ]
    assert progress == [SOFT_START_BRIGHTNESS, 1.0]
    assert lamp.desired_on and lamp.reported_on


def test_run_waits_for_a_slow_lamp_to_confirm_the_park() -> None:
    lamp = FakeLamp(lag=4)
    _run(_full_plan(), lamp)
    # Power-on only after the lamp reported the parked level (4 reads).
    assert lamp.calls[1] == ("power", True)
    assert lamp.calls.count(("power", True)) == 1
    assert lamp.desired_on and lamp.reported_on


def test_run_late_repair_is_undone_before_the_fade() -> None:
    # Worst ordering: the repair lands after the level report, so after our
    # power-on write. The lamp stays dark; power-on is re-sent once.
    lamp = FakeLamp(lag=2, repair_after_report=True)
    progress = _run(_full_plan(), lamp)
    assert lamp.calls.count(("power", True)) == 2
    assert lamp.calls[-1] == ("update", 1.0, None, SOFT_START_FADE_MS)
    assert progress[-1] == 1.0
    assert lamp.desired_on and lamp.reported_on


def test_run_unconfirmed_park_still_powers_on() -> None:
    lamp = FakeLamp(park_ack=False)
    _run(_full_plan(), lamp)
    assert ("power", True) in lamp.calls
    assert lamp.reads >= round(SOFT_START_ACK_TIMEOUT_S / SOFT_START_POLL_S)


def test_run_lamp_that_never_reports_on_is_not_faded_up() -> None:
    lamp = FakeLamp(power_ack=False)
    progress = _run(_full_plan(), lamp)
    assert ("power", True) in lamp.calls
    assert not any(c[0] == "update" and c[1] == 1.0 for c in lamp.calls)
    assert progress == [SOFT_START_BRIGHTNESS]


def test_run_read_errors_while_waiting_are_retried() -> None:
    lamp = FakeLamp(read_errors=2)
    progress = _run(_full_plan(), lamp)
    assert progress == [SOFT_START_BRIGHTNESS, 1.0]


def test_run_low_target_has_no_fade_step() -> None:
    lamp = FakeLamp()
    plan = plan_turn_on(
        is_on=False, target_brightness=0.02, saved_brightness=1.0, lerp_ms=250
    )
    progress = _run(plan, lamp)
    assert lamp.calls == [("update", 0.02, None, 250), ("power", True)]
    assert progress == [0.02]


def test_run_low_target_late_repair_keeps_lamp_on() -> None:
    lamp = FakeLamp(lag=2, repair_after_report=True)
    plan = plan_turn_on(
        is_on=False, target_brightness=0.02, saved_brightness=1.0, lerp_ms=250
    )
    progress = _run(plan, lamp)
    assert lamp.calls == [("update", 0.02, None, 250), ("power", True), ("power", True)]
    assert progress == [0.02]
    assert lamp.desired_on and lamp.reported_on


def test_run_already_on_colour_only_is_one_update() -> None:
    lamp = FakeLamp()
    plan = plan_turn_on(
        is_on=True, target_brightness=None, saved_brightness=0.7, lerp_ms=500
    )
    progress = _run(plan, lamp, kelvin=4000)
    assert lamp.calls == [("update", None, 4000, 500)]
    assert lamp.reads == 0
    assert progress == [None]


def test_run_superseded_while_parking_never_powers_on() -> None:
    lamp = FakeLamp(lag=5)
    progress = _run(_full_plan(), lamp, superseded_after_sleeps=1)
    assert ("power", True) not in lamp.calls
    assert progress == []


def test_run_superseded_while_powering_on_skips_fade() -> None:
    lamp = FakeLamp(lag=2)
    progress = _run(_full_plan(), lamp, superseded_after_sleeps=3)
    assert ("power", True) in lamp.calls
    assert not any(c[0] == "update" and c[1] == 1.0 for c in lamp.calls)
    assert progress == [SOFT_START_BRIGHTNESS]


def test_run_failure_stops_and_raises() -> None:
    lamp = FakeLamp(fail_on="power")
    try:
        _run(_full_plan(), lamp)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the power failure to propagate")
    assert lamp.calls[-1] == ("power", True)
    assert not any(c[0] == "update" and c[1] == 1.0 for c in lamp.calls)


def test_run_fade_failure_leaves_lamp_at_start_level() -> None:
    lamp = FakeLamp(fail_on="update")
    try:
        _run(_full_plan(), lamp)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the fade-up failure to propagate")
    assert ("power", True) in lamp.calls
    assert lamp.calls[-1] == ("update", 1.0, None, SOFT_START_FADE_MS)


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} tests passed")


if __name__ == "__main__":
    main()
