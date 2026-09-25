"""Light platform for Sunday SL1 lamps."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SundayConfigEntry
from .const import DEFAULT_LERP_MS, DOMAIN, MAX_KELVIN, MIN_KELVIN
from .coordinator import SundayCoordinator, SundayData
from .models import LampState, SundayLampInfo
from .soft_start import SOFT_START_SETTLE_S, plan_turn_on


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SundayConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_lamps() -> None:
        new_ids = [
            lamp_id for lamp_id in coordinator.data.lamps if lamp_id not in known
        ]
        if new_ids:
            known.update(new_ids)
            async_add_entities(
                SundayLightEntity(coordinator, lamp_id) for lamp_id in new_ids
            )

    _add_new_lamps()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_lamps))


class SundayLightEntity(CoordinatorEntity[SundayCoordinator], LightEntity):
    """A Sunday SL1 lamp: on/off, brightness, tunable white, transitions."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP}
    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_min_color_temp_kelvin = MIN_KELVIN
    _attr_max_color_temp_kelvin = MAX_KELVIN
    _attr_supported_features = LightEntityFeature.TRANSITION

    def __init__(self, coordinator: SundayCoordinator, lamp_id: str) -> None:
        super().__init__(coordinator)
        self._lamp_id = lamp_id
        self._attr_unique_id = lamp_id

    @property
    def _info(self) -> SundayLampInfo | None:
        return self.coordinator.data.lamps.get(self._lamp_id)

    @property
    def _state(self) -> LampState | None:
        return self.coordinator.data.states.get(self._lamp_id)

    @property
    def device_info(self) -> DeviceInfo:
        info = self._info
        room = (
            self.coordinator.data.rooms.get(info.room_id)
            if info and info.room_id
            else None
        )
        return DeviceInfo(
            identifiers={(DOMAIN, self._lamp_id)},
            name=info.name if info else self._lamp_id,
            manufacturer="Sunday",
            model="SL1",
            serial_number=self._lamp_id,
            suggested_area=room.name if room else None,
        )

    @property
    def available(self) -> bool:
        state = self._state
        return super().available and state is not None and state.online

    @property
    def is_on(self) -> bool | None:
        state = self._state
        return state.is_on if state else None

    @property
    def brightness(self) -> int | None:
        state = self._state
        if state is None or state.brightness is None:
            return None
        return max(1, min(255, round(state.brightness * 255)))

    @property
    def color_temp_kelvin(self) -> int | None:
        state = self._state
        if state is None or state.color_temp_k is None:
            return None
        # Old firmware/API can report values outside the SL1's physical
        # 2650-6000K range (e.g. 2039K); clamp for display like the app does.
        return max(MIN_KELVIN, min(MAX_KELVIN, state.color_temp_k))

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness_api: float | None = None
        if ATTR_BRIGHTNESS in kwargs:
            brightness_api = max(0.01, min(1.0, kwargs[ATTR_BRIGHTNESS] / 255))
        kelvin: int | None = None
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = max(
                MIN_KELVIN, min(MAX_KELVIN, int(kwargs[ATTR_COLOR_TEMP_KELVIN]))
            )
        lerp_ms = (
            int(kwargs[ATTR_TRANSITION] * 1000)
            if ATTR_TRANSITION in kwargs
            else DEFAULT_LERP_MS
        )

        state = self._state
        plan = plan_turn_on(
            is_on=state.is_on if state else None,
            target_brightness=brightness_api,
            saved_brightness=state.brightness if state else None,
            lerp_ms=lerp_ms,
        )
        api = self.coordinator.api
        if plan.pre_brightness is not None:
            # Soft start: park a low level (and the colour) while still dark,
            # so power-on doesn't jump straight to full power.
            await api.async_update_lamp(
                self._lamp_id,
                brightness=plan.pre_brightness,
                color_temp_k=kelvin,
            )
            await asyncio.sleep(SOFT_START_SETTLE_S)
        # Power through the dedicated endpoint, then levels — the ordering
        # rule the mobile app follows (the park above only sets the saved
        # level while the lamp is dark).
        if plan.power_on:
            await api.async_set_power(self._lamp_id, True)
        if plan.brightness is not None or (
            kelvin is not None and plan.pre_brightness is None
        ):
            await api.async_update_lamp(
                self._lamp_id,
                brightness=plan.brightness,
                color_temp_k=kelvin,
                lerp_ms=plan.lerp_ms,
            )
        self._apply_optimistic(
            is_on=True,
            brightness=(
                plan.brightness
                if plan.brightness is not None
                else plan.pre_brightness
            ),
            color_temp_k=kelvin,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.api.async_set_power(self._lamp_id, False)
        self._apply_optimistic(is_on=False)

    @callback
    def _apply_optimistic(
        self,
        *,
        is_on: bool,
        brightness: float | None = None,
        color_temp_k: int | None = None,
    ) -> None:
        """Reflect a command immediately; the next poll confirms it."""
        data = self.coordinator.data
        state = data.states.get(self._lamp_id)
        if state is None:
            return
        new_state = replace(
            state,
            is_on=is_on,
            brightness=brightness if brightness is not None else state.brightness,
            color_temp_k=(
                color_temp_k if color_temp_k is not None else state.color_temp_k
            ),
        )
        self.coordinator.async_set_updated_data(
            SundayData(
                homes=data.homes,
                rooms=data.rooms,
                lamps=data.lamps,
                states={**data.states, self._lamp_id: new_state},
            )
        )
