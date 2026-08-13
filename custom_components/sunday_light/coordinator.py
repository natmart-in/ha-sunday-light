"""Polling coordinator for Sunday lamps."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import SundayApiClient, SundayApiError
from .auth import SundayAuthError, SundayInvalidAuth
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    INVENTORY_MAX_AGE,
)
from .models import LampState, SundayHome, SundayLampInfo, SundayRoom

_LOGGER = logging.getLogger(__name__)


@dataclass
class SundayData:
    """Snapshot of inventory plus live lamp states."""

    homes: dict[str, SundayHome] = field(default_factory=dict)
    rooms: dict[str, SundayRoom] = field(default_factory=dict)
    lamps: dict[str, SundayLampInfo] = field(default_factory=dict)
    states: dict[str, LampState] = field(default_factory=dict)


class SundayCoordinator(DataUpdateCoordinator[SundayData]):
    """Polls lamp state per room (one bulk request per room per tick)."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, api: SundayApiClient
    ) -> None:
        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api
        self._homes: dict[str, SundayHome] = {}
        self._rooms: dict[str, SundayRoom] = {}
        self._lamps: dict[str, SundayLampInfo] = {}
        self._inventory_ts = 0.0

    async def _async_setup(self) -> None:
        await self._async_refresh_inventory()

    async def _async_refresh_inventory(self) -> None:
        try:
            homes = await self.api.async_get_homes()
            rooms: dict[str, SundayRoom] = {}
            lamps: dict[str, SundayLampInfo] = {}
            for home in homes:
                for room in await self.api.async_get_rooms(home.home_id):
                    rooms[room.room_id] = room
                for lamp in await self.api.async_get_home_lamps(home.home_id):
                    lamps[lamp.lamp_id] = lamp
        except SundayInvalidAuth:
            raise
        except (SundayApiError, SundayAuthError):
            if not self._lamps:
                raise
            _LOGGER.warning(
                "Inventory refresh failed; keeping cached inventory", exc_info=True
            )
            return
        self._homes = {home.home_id: home for home in homes}
        self._rooms = rooms
        self._lamps = lamps
        self._inventory_ts = time.monotonic()

    async def _async_update_data(self) -> SundayData:
        try:
            if time.monotonic() - self._inventory_ts > INVENTORY_MAX_AGE:
                await self._async_refresh_inventory()

            room_ids = {
                info.room_id for info in self._lamps.values() if info.room_id
            }
            roomless = [
                lamp_id
                for lamp_id, info in self._lamps.items()
                if not info.room_id
            ]
            tasks = [self.api.async_get_room_state(rid) for rid in room_ids]
            tasks += [self.api.async_get_lamp_state(lid) for lid in roomless]

            states: dict[str, LampState] = dict(self.data.states) if self.data else {}
            results = await asyncio.gather(*tasks, return_exceptions=True)
            successes = 0
            last_error: BaseException | None = None
            for result in results:
                if isinstance(result, BaseException):
                    if isinstance(result, SundayInvalidAuth):
                        raise result
                    last_error = result
                    continue
                successes += 1
                items = result if isinstance(result, list) else [result]
                for state in items:
                    if state is not None:
                        states[state.lamp_id] = state
            if tasks and successes == 0:
                raise UpdateFailed(f"All state requests failed: {last_error}")

            return SundayData(
                homes=dict(self._homes),
                rooms=dict(self._rooms),
                lamps=dict(self._lamps),
                states=states,
            )
        except SundayInvalidAuth as err:
            raise ConfigEntryAuthFailed from err
        except (SundayApiError, SundayAuthError) as err:
            raise UpdateFailed(str(err)) from err
