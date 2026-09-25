"""Turn-on planning and sequencing with a soft start.

Self-contained (no Home Assistant or package-relative imports) so it can be
unit-tested with plain Python, like models.py.

The lamp's power command always ramps to its saved brightness in a fixed
300 ms. From dark straight to full brightness that is a jump to ~410 W, which
can briefly drop the supply on some lamps and restart the control board
(issue #2). So when turning on from off, park the saved brightness low while
the lamp is still dark, power on to that, then fade up to the target.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

_LOGGER = logging.getLogger(__name__)

# Level the lamp powers on to before fading up. In the affected lamp's
# history, turn-ons that powered on at a low saved level (5-94 %) and then
# faded to full over 1 s (Adaptive Lighting's fade) never restarted it.
SOFT_START_BRIGHTNESS = 0.05
# Minimum fade from the start level up to the target (ms).
SOFT_START_FADE_MS = 2000
# Pause after parking the level, and again after powering on, so each command
# reaches the lamp before the next one. Parking also makes the lamp write
# desired.is_on=false ~150 ms later, which must land before power-on's write -
# don't shorten this much.
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


class LampCommands(Protocol):
    """The API calls a turn-on needs (SundayApi satisfies this)."""

    async def async_set_power(self, lamp_id: str, is_on: bool) -> None: ...

    async def async_update_lamp(
        self,
        lamp_id: str,
        *,
        brightness: float | None = ...,
        color_temp_k: int | None = ...,
        lerp_ms: int = ...,
    ) -> None: ...


async def async_run_turn_on(
    api: LampCommands,
    lamp_id: str,
    plan: TurnOnPlan,
    *,
    color_temp_k: int | None,
    superseded: Callable[[], bool],
    on_progress: Callable[[float | None], None],
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> None:
    """Send the plan's commands in order.

    Stops quietly if `superseded()` turns true during a pause (a newer command
    for the lamp took over). `on_progress(brightness)` is called after each
    step that changes what the lamp shows, so callers can mirror it. A failed
    step is logged with its name and re-raised; the lamp never goes further
    than the last step that succeeded, so a failure is never full power.
    """
    step = "park"
    try:
        if plan.pre_brightness is not None:
            await api.async_update_lamp(
                lamp_id, brightness=plan.pre_brightness, color_temp_k=color_temp_k
            )
            await sleep(SOFT_START_SETTLE_S)
            if superseded():
                _LOGGER.debug("Turn-on of %s superseded before power-on", lamp_id)
                return

        step = "power on"
        if plan.power_on:
            await api.async_set_power(lamp_id, True)
            on_progress(plan.pre_brightness)
            if plan.brightness is not None:
                # Let set_on land first; a fade-up that arrives while the
                # lamp is still off would be stored as its power-on level.
                await sleep(SOFT_START_SETTLE_S)
                if superseded():
                    _LOGGER.debug("Turn-on of %s superseded before fade-up", lamp_id)
                    return

        step = "levels"
        if plan.brightness is not None or (
            color_temp_k is not None and plan.pre_brightness is None
        ):
            await api.async_update_lamp(
                lamp_id,
                brightness=plan.brightness,
                color_temp_k=color_temp_k,
                lerp_ms=plan.lerp_ms,
            )
            on_progress(plan.brightness)
    except Exception:
        _LOGGER.warning("Turning on %s failed at the %s step", lamp_id, step)
        raise
