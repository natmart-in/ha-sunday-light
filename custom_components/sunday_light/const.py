"""Constants for the Sunday Light integration."""

from __future__ import annotations

DOMAIN = "sunday_light"

# Sunday cloud (production). Base URLs mirror the mobile app's ApiEndpoints.
API_BASE = "https://api.prod.sunday.wiki"
MANAGEMENT_BASE = f"{API_BASE}/management-v2"
CONTROL_BASE = f"{API_BASE}/control-v2"

# Cognito (production pool — same accounts as the Sunday mobile app).
COGNITO_REGION = "us-east-1"
COGNITO_USER_POOL_ID = "us-east-1_Mev3U8BbQ"
COGNITO_CLIENT_ID = "3knfrpj7tjkffh35hsih18fqt4"
COGNITO_IDP_URL = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"

# SL1 LED physical colour-temperature range (Kelvin).
MIN_KELVIN = 2650
MAX_KELVIN = 6000

# Backend-validated actor tag sent with every write.
ACTIONED_BY = "app"

# Default fade duration for writes (ms), matching the app.
DEFAULT_LERP_MS = 250

USER_AGENT = "ha-sunday-light/0.1.0"

CONF_REFRESH_TOKEN = "refresh_token"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 10  # seconds
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 120

# Re-fetch homes/rooms/lamps inventory at most this often (seconds).
INVENTORY_MAX_AGE = 600
