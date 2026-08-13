"""Sunday Light — Home Assistant integration for the Sunday SL1 lamp."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SundayApiClient
from .auth import SundayAuth
from .const import CONF_REFRESH_TOKEN
from .coordinator import SundayCoordinator

PLATFORMS: list[Platform] = [Platform.LIGHT]

type SundayConfigEntry = ConfigEntry[SundayCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SundayConfigEntry) -> bool:
    session = async_get_clientsession(hass)

    def _save_refresh_token(new_token: str) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REFRESH_TOKEN: new_token}
        )

    auth = SundayAuth(
        session,
        entry.data[CONF_REFRESH_TOKEN],
        on_refresh_token_update=_save_refresh_token,
    )
    coordinator = SundayCoordinator(hass, entry, SundayApiClient(session, auth))
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: SundayConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SundayConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
