"""Diagnostics for the Sunday Light integration (no tokens included)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import SundayConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SundayConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data.data
    return {
        "homes": [asdict(home) for home in data.homes.values()],
        "rooms": [asdict(room) for room in data.rooms.values()],
        "lamps": [asdict(lamp) for lamp in data.lamps.values()],
        "states": [asdict(state) for state in data.states.values()],
    }
