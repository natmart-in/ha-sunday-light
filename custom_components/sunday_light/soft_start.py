"""Turn-on planning with a soft start.

Self-contained (no Home Assistant or package-relative imports) so it can be
unit-tested with plain Python, like models.py.

The lamp's power command always ramps to its saved brightness in a fixed
300 ms. From dark straight to full brightness that is a jump to ~410 W, which
can briefly drop the supply on some lamps and restart the control board
(issue #2). So when turning on from off, park the saved brightness low while
the lamp is still dark, power on to that, then fade up to the target.
"""

from __future__ import annotations

from dataclasses import dataclass

# Level the lamp powers on to before fading up. Turn-ons from 5 % faded to
# full brightness over 1 s have not restarted any lamp.
SOFT_START_BRIGHTNESS = 0.05
# Minimum fade from the start level up to the target (ms).
SOFT_START_FADE_MS = 2000
# Pause between parking the brightness and powering on (s), so the parked
# level reaches the lamp before the power command does.
SOFT_START_SETTLE_S = 0.5

# Firmware falls back to full brightness when nothing usable is saved.
FIRMWARE_DEFAULT_BRIGHTNESS = 1.0


@dataclass(frozen=True)
class TurnOnPlan:
    """Commands to send, in order."""

    pre_brightness: float | None  # set while still off, before powering on
    power_on: bool
    brightness: float | None  # final level, sent after power-on
    lerp_ms: int  # fade for the final level


def plan_turn_on(
    *,
    is_on: bool | None,
    target_brightness: float | None,
    saved_brightness: float | None,
    lerp_ms: int,
) -> TurnOnPlan:
    """Work out how to turn a lamp on (or adjust it if it is already on)."""
    if is_on:
        return TurnOnPlan(
            pre_brightness=None,
            power_on=False,
            brightness=target_brightness,
            lerp_ms=lerp_ms,
        )

    target = target_brightness
    if target is None:
        target = (
            saved_brightness
            if saved_brightness is not None
            else FIRMWARE_DEFAULT_BRIGHTNESS
        )

    if target <= SOFT_START_BRIGHTNESS:
        # Low target: power on straight to it, no fade-up needed.
        return TurnOnPlan(
            pre_brightness=target, power_on=True, brightness=None, lerp_ms=lerp_ms
        )
    return TurnOnPlan(
        pre_brightness=SOFT_START_BRIGHTNESS,
        power_on=True,
        brightness=target,
        lerp_ms=max(lerp_ms, SOFT_START_FADE_MS),
    )
