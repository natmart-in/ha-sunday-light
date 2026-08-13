"""Async client for the Sunday cloud API (management-v2 + control-v2)."""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from .auth import SundayAuth
from .const import (
    ACTIONED_BY,
    CONTROL_BASE,
    DEFAULT_LERP_MS,
    MANAGEMENT_BASE,
    USER_AGENT,
)
from .models import (
    API_BRIGHTNESS_MAX,
    API_BRIGHTNESS_MIN,
    LampState,
    SundayHome,
    SundayLampInfo,
    SundayRoom,
    parse_home_lamps,
    parse_homes,
    parse_lamp_state,
    parse_room_state,
    parse_rooms,
)

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=15)


class SundayApiError(Exception):
    """A Sunday cloud request failed."""


class SundayApiClient:
    """Thin async wrapper over the Sunday cloud endpoints the app uses."""

    def __init__(self, session: aiohttp.ClientSession, auth: SundayAuth) -> None:
        self._session = session
        self._auth = auth

    async def _request(
        self, method: str, url: str, body: dict[str, Any] | None = None
    ) -> Any:
        for attempt in (0, 1):
            token = await self._auth.async_get_id_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": USER_AGENT,
            }
            try:
                resp = await self._session.request(
                    method,
                    url,
                    json=body,
                    headers=headers,
                    timeout=_TIMEOUT,
                )
            except (aiohttp.ClientError, TimeoutError) as err:
                raise SundayApiError(f"{method} {url} failed: {err}") from err

            if resp.status == 401 and attempt == 0:
                # Token may have just expired server-side; refresh and retry.
                self._auth.invalidate()
                continue

            text = await resp.text()
            if resp.status >= 400:
                raise SundayApiError(
                    f"{method} {url} -> HTTP {resp.status}: {text[:200]}"
                )
            if not text:
                return {}
            try:
                return json.loads(text)
            except ValueError:
                return {}
        raise SundayApiError(f"{method} {url}: unauthorized after token refresh")

    # -- Discovery (management-v2) -------------------------------------------

    async def async_get_homes(self) -> list[SundayHome]:
        return parse_homes(await self._request("GET", f"{MANAGEMENT_BASE}/homes"))

    async def async_get_rooms(self, home_id: str) -> list[SundayRoom]:
        return parse_rooms(
            await self._request("GET", f"{MANAGEMENT_BASE}/homes/{home_id}/rooms"),
            home_id,
        )

    async def async_get_home_lamps(self, home_id: str) -> list[SundayLampInfo]:
        return parse_home_lamps(
            await self._request("GET", f"{MANAGEMENT_BASE}/homes/{home_id}/lamps"),
            home_id,
        )

    # -- State (control-v2) --------------------------------------------------

    async def async_get_room_state(self, room_id: str) -> list[LampState]:
        return parse_room_state(
            await self._request(
                "GET", f"{CONTROL_BASE}/control/rooms/{room_id}/state"
            )
        )

    async def async_get_lamp_state(self, lamp_id: str) -> LampState | None:
        return parse_lamp_state(
            await self._request(
                "GET", f"{CONTROL_BASE}/control/lamps/{lamp_id}/state"
            )
        )

    # -- Commands (control-v2) -----------------------------------------------

    async def async_set_power(self, lamp_id: str, is_on: bool) -> None:
        """Power on/off via the dedicated endpoint (the app's ordering rule:
        power changes always go through /on-off, never /update)."""
        await self._request(
            "PATCH",
            f"{CONTROL_BASE}/control/lamps/{lamp_id}/on-off",
            {"actioned_by": ACTIONED_BY, "is_on": is_on},
        )

    async def async_update_lamp(
        self,
        lamp_id: str,
        *,
        brightness: float | None = None,
        color_temp_k: int | None = None,
        lerp_ms: int = DEFAULT_LERP_MS,
    ) -> None:
        """Set brightness (API scale 0.01–1.0) and/or colour temperature."""
        body: dict[str, Any] = {
            "actioned_by": ACTIONED_BY,
            "simulate_led_output": 0,
            "lerp_ms": int(lerp_ms),
        }
        if brightness is not None:
            body["brightness"] = round(
                min(API_BRIGHTNESS_MAX, max(API_BRIGHTNESS_MIN, brightness)), 4
            )
        if color_temp_k is not None:
            body["colourTemperatureK"] = int(round(color_temp_k))
        await self._request(
            "PATCH", f"{CONTROL_BASE}/control/lamps/{lamp_id}/update", body
        )
