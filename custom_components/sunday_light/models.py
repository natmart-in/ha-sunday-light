"""Data models and tolerant parsers for the Sunday cloud API.

This module is deliberately self-contained (stdlib only, no intra-package
imports) so it can be unit-tested without Home Assistant installed, and can
later be extracted into a standalone client library.

The parsers accept every response envelope the backend has historically
produced: bare objects/lists, ``{"data": {...}}`` wrappers, and legacy
``{"lamp": {...}}`` / ``{"groups": [...]}`` shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# API brightness scale. Brightness is never 0 — off is ``is_on: false``.
API_BRIGHTNESS_MIN = 0.01
API_BRIGHTNESS_MAX = 1.0


@dataclass(frozen=True)
class SundayHome:
    """A home the account is a member of."""

    home_id: str
    name: str
    role: str


@dataclass(frozen=True)
class SundayRoom:
    """A room within a home."""

    room_id: str
    home_id: str
    name: str


@dataclass(frozen=True)
class SundayLampInfo:
    """Inventory record for a lamp (name and room assignment)."""

    lamp_id: str
    home_id: str
    name: str
    room_id: str | None


@dataclass(frozen=True)
class LampState:
    """Live state of a lamp, parsed from its device shadow."""

    lamp_id: str
    is_on: bool
    brightness: float | None  # API scale 0.01–1.0
    color_temp_k: int | None
    online: bool
    display_state: str | None
    # What the lamp itself last reported, without the desired-first merge
    # above. None when the shadow has no reported value.
    reported_is_on: bool | None = None
    reported_brightness: float | None = None


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "on", "yes")
    return default


def _as_brightness(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    result = float(value)
    if result > API_BRIGHTNESS_MAX:
        # Tolerate legacy 0–100 scale.
        result = result / 100
    return min(API_BRIGHTNESS_MAX, max(API_BRIGHTNESS_MIN, result))


def _as_kelvin(value: Any) -> int | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return round(value)


def _unwrap_list(payload: Any, *keys: str) -> list[Any]:
    """Extract a list from ``payload`` under any of ``keys``, tolerating a
    ``data`` wrapper or a bare list."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        if isinstance(payload.get(key), list):
            return payload[key]
    data = payload.get("data")
    if isinstance(data, dict):
        for key in keys:
            if isinstance(data.get(key), list):
                return data[key]
    if isinstance(data, list):
        return data
    return []


def parse_homes(payload: Any) -> list[SundayHome]:
    homes: list[SundayHome] = []
    for item in _unwrap_list(payload, "homes"):
        if not isinstance(item, dict):
            continue
        home_id = _first(item.get("home_id"), item.get("id"))
        if not home_id:
            continue
        homes.append(
            SundayHome(
                home_id=str(home_id),
                name=str(item.get("name") or "Home"),
                role=str(item.get("role") or "unknown"),
            )
        )
    return homes


def parse_rooms(payload: Any, home_id: str) -> list[SundayRoom]:
    rooms: list[SundayRoom] = []
    for item in _unwrap_list(payload, "rooms", "groups"):
        if not isinstance(item, dict):
            continue
        room_id = _first(item.get("room_id"), item.get("group_id"), item.get("id"))
        if not room_id:
            continue
        rooms.append(
            SundayRoom(
                room_id=str(room_id),
                home_id=home_id,
                name=str(item.get("name") or "Room"),
            )
        )
    return rooms


def parse_home_lamps(payload: Any, home_id: str) -> list[SundayLampInfo]:
    lamps: list[SundayLampInfo] = []
    for item in _unwrap_list(payload, "lamps"):
        if not isinstance(item, dict):
            continue
        lamp_id = _first(item.get("lamp_id"), item.get("device_id"), item.get("id"))
        if not lamp_id:
            continue
        room_id = _first(item.get("room_id"), item.get("group_id"))
        lamps.append(
            SundayLampInfo(
                lamp_id=str(lamp_id),
                home_id=home_id,
                name=str(
                    _first(item.get("friendly_name"), item.get("name")) or lamp_id
                ),
                room_id=str(room_id) if room_id else None,
            )
        )
    return lamps


def parse_lamp_state(payload: Any) -> LampState | None:
    if not isinstance(payload, dict):
        return None
    # Unwrap legacy envelopes: {"lamp": {...}} and {"data": {...}}.
    for key in ("lamp", "data"):
        inner = payload.get(key)
        if isinstance(inner, dict) and ("state" in inner or "lamp_id" in inner):
            payload = inner
            break

    lamp_id = _first(
        payload.get("lamp_id"), payload.get("device_id"), payload.get("id")
    )
    if not lamp_id:
        return None

    state = payload.get("state")
    if not isinstance(state, dict):
        state = {}
    desired = state.get("desired")
    if not isinstance(desired, dict):
        desired = {}
    reported = state.get("reported")
    if not isinstance(reported, dict):
        reported = {}

    # Precedence mirrors the mobile app: power reads desired-first (a pending
    # command should win in the UI); brightness/CCT read reported-first.
    is_on = _as_bool(_first(desired.get("is_on"), reported.get("is_on")))
    brightness = _as_brightness(
        _first(reported.get("brightness"), desired.get("brightness"))
    )
    color_temp_k = _as_kelvin(
        _first(
            reported.get("colourTemperatureK"),
            desired.get("colourTemperatureK"),
            reported.get("cct"),  # legacy key, read-only
            desired.get("cct"),
        )
    )

    display_state = payload.get("display_state")
    if payload.get("online") is not None:
        online = _as_bool(payload.get("online"))
    elif isinstance(display_state, str):
        online = display_state.strip().lower() in ("online", "updating", "restarting")
    else:
        online = bool(reported)

    return LampState(
        lamp_id=str(lamp_id),
        is_on=is_on,
        brightness=brightness,
        color_temp_k=color_temp_k,
        online=online,
        display_state=str(display_state) if display_state is not None else None,
        reported_is_on=(
            _as_bool(reported["is_on"]) if reported.get("is_on") is not None else None
        ),
        reported_brightness=_as_brightness(reported.get("brightness")),
    )


def parse_room_state(payload: Any) -> list[LampState]:
    states: list[LampState] = []
    for item in _unwrap_list(payload, "lamps"):
        state = parse_lamp_state(item)
        if state is not None:
            states.append(state)
    return states
