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
# Parking a level while the lamp is dark makes the firmware reject it as a
# light change ("nonzero_while_off"), keep it as the saved level, and publish
# desired.is_on=false as a repair. If that repair lands after our power-on
# write it overwrites it, and the lamp switches itself off again moments
# later. Its latency varies (0.8 s seen on a healthy lamp), so rather than a
# fixed pause we poll the lamp's reported state: once it reports the parked
# level, the repair (published in the same or an earlier shadow update) has
# landed. After power-on we likewise wait for the lamp to report it is on
# before fading up, since a fade that arrives while it is still dark would be
# rejected the same way.
SOFT_START_POLL_S = 0.25
SOFT_START_ACK_TIMEOUT_S = 6.0
# Reported brightness is a float; treat it as the parked level within this.
_LEVEL_TOLERANCE = 0.005

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

    async def async_get_lamp_state(self, lamp_id: str) -> Any: ...

    async def async_set_power(self, lamp_id: str, is_on: bool) -> None: ...

    async def async_update_lamp(
        self,
        lamp_id: str,
        *,
        brightness: float | None = ...,
        color_temp_k: int | None = ...,
        lerp_ms: int = ...,
    ) -> None: ...


async def _async_wait_for(
    api: LampCommands,
    lamp_id: str,
    done: Callable[[Any], bool],
    *,
    sleep: Callable[[float], Awaitable[Any]],
    superseded: Callable[[], bool],
    on_poll: Callable[[Any], Awaitable[None]] | None = None,
) -> bool | None:
    """Poll the lamp until `done(state)`. True when it is, False on timeout,
    None if a newer command took over while waiting."""
    for _ in range(round(SOFT_START_ACK_TIMEOUT_S / SOFT_START_POLL_S)):
        await sleep(SOFT_START_POLL_S)
        if superseded():
            return None
        try:
            state = await api.async_get_lamp_state(lamp_id)
        except Exception:  # noqa: BLE001 - a failed read just means poll again
            _LOGGER.debug("State read for %s failed while waiting", lamp_id)
            continue
        if state is None:
            continue
        if done(state):
            return True
        if on_poll is not None:
            await on_poll(state)
    return False


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
    """Send the plan's commands in order, waiting for the lamp between them.

    Stops quietly if `superseded()` turns true while waiting (a newer command
    for the lamp took over). `on_progress(brightness)` is called after each
    step that changes what the lamp shows, so callers can mirror it. A failed
    step is logged with its name and re-raised; the lamp never goes further
    than the last step that succeeded, so a failure is never full power.
    """
    step = "park"
    try:
        if plan.pre_brightness is not None:
            parked = plan.pre_brightness
            await api.async_update_lamp(
                lamp_id, brightness=parked, color_temp_k=color_temp_k
            )
            if plan.power_on:
                acked = await _async_wait_for(
                    api,
                    lamp_id,
                    lambda s: s.reported_brightness is not None
                    and abs(s.reported_brightness - parked) <= _LEVEL_TOLERANCE,
                    sleep=sleep,
                    superseded=superseded,
                )
                if acked is None:
                    _LOGGER.debug("Turn-on of %s superseded before power-on", lamp_id)
                    return
                if not acked:
                    _LOGGER.warning(
                        "%s did not confirm its start level within %.0f s; "
                        "powering on anyway",
                        lamp_id,
                        SOFT_START_ACK_TIMEOUT_S,
                    )
        step = "power on"
        if plan.power_on:
            await api.async_set_power(lamp_id, True)
            on_progress(plan.pre_brightness)
            if plan.pre_brightness is not None:
                # Wait even with no fade to come: a late repair would switch
                # the lamp off again unless power-on is re-sent.
                resent = False

                async def _repair_power(state: Any) -> None:
                    # A late repair flipped desired back to off. The lamp has
                    # already confirmed the parked level, so powering on again
                    # still starts low. Once only.
                    nonlocal resent
                    if not resent and state.is_on is False:
                        resent = True
                        _LOGGER.debug("Re-sending power-on to %s", lamp_id)
                        await api.async_set_power(lamp_id, True)

                on = await _async_wait_for(
                    api,
                    lamp_id,
                    lambda s: s.reported_is_on is True and s.is_on is True,
                    sleep=sleep,
                    superseded=superseded,
                    on_poll=_repair_power,
                )
                if on is None:
                    _LOGGER.debug("Turn-on of %s superseded before fade-up", lamp_id)
                    return
                if not on:
                    # Fading up a lamp that may still be dark would be
                    # rejected and stored as its next power-on level, so stop
                    # at the start level instead.
                    _LOGGER.warning(
                        "%s did not report on within %.0f s; leaving it at the "
                        "start level",
                        lamp_id,
                        SOFT_START_ACK_TIMEOUT_S,
                    )
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
