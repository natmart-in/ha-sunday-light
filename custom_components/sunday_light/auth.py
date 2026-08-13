"""Cognito authentication for the Sunday cloud.

Initial sign-in uses SRP via ``pycognito`` (synchronous; callers must run it
in an executor). Only the resulting refresh token is kept. Token refresh is a
plain unauthenticated Cognito ``InitiateAuth`` call done with aiohttp, so the
steady state has no boto3 involvement.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable

import aiohttp

from .const import COGNITO_CLIENT_ID, COGNITO_IDP_URL, COGNITO_USER_POOL_ID

_LOGGER = logging.getLogger(__name__)

# Refresh this many seconds before the ID token actually expires.
TOKEN_SAFETY_WINDOW = 300

_REFRESH_HEADERS = {
    "Content-Type": "application/x-amz-json-1.1",
    "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
}

_INVALID_AUTH_CODES = (
    "NotAuthorizedException",
    "UserNotFoundException",
    "UserNotConfirmedException",
    "PasswordResetRequiredException",
)


class SundayAuthError(Exception):
    """Authentication failed for a transient or unexpected reason."""


class SundayInvalidAuth(SundayAuthError):
    """Credentials or refresh token are no longer valid; re-auth required."""


def login(email: str, password: str) -> dict[str, str]:
    """Blocking SRP sign-in. Returns id and refresh tokens.

    Must be run in an executor — pycognito/boto3 are synchronous.
    """
    from botocore.exceptions import ClientError  # bundled with pycognito
    from pycognito import Cognito

    user = Cognito(COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID, username=email)
    try:
        user.authenticate(password=password)
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "")
        if code in _INVALID_AUTH_CODES:
            raise SundayInvalidAuth(code) from err
        raise SundayAuthError(str(err)) from err
    except SundayAuthError:
        raise
    except Exception as err:  # pycognito challenge errors etc.
        raise SundayAuthError(str(err)) from err
    return {"id_token": user.id_token, "refresh_token": user.refresh_token}


class SundayAuth:
    """Holds the refresh token and serves fresh ID tokens on demand."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str,
        on_refresh_token_update: Callable[[str], None] | None = None,
    ) -> None:
        self._session = session
        self._refresh_token = refresh_token
        self._on_refresh_token_update = on_refresh_token_update
        self._id_token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        """Force the next token request to refresh (e.g. after a 401)."""
        self._expires_at = 0.0

    async def async_get_id_token(self) -> str:
        async with self._lock:
            if (
                self._id_token
                and time.time() < self._expires_at - TOKEN_SAFETY_WINDOW
            ):
                return self._id_token
            await self._async_refresh()
            assert self._id_token is not None
            return self._id_token

    async def _async_refresh(self) -> None:
        payload = {
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "ClientId": COGNITO_CLIENT_ID,
            "AuthParameters": {"REFRESH_TOKEN": self._refresh_token},
        }
        try:
            resp = await self._session.post(
                COGNITO_IDP_URL,
                data=json.dumps(payload),
                headers=_REFRESH_HEADERS,
                timeout=aiohttp.ClientTimeout(total=15),
            )
            body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SundayAuthError(f"Token refresh request failed: {err}") from err

        if resp.status != 200 or not isinstance(body, dict):
            err_type = ""
            if isinstance(body, dict):
                err_type = str(body.get("__type", ""))
            if any(code in err_type for code in _INVALID_AUTH_CODES):
                raise SundayInvalidAuth(err_type)
            raise SundayAuthError(
                f"Token refresh failed: HTTP {resp.status} {err_type}"
            )

        result = body.get("AuthenticationResult") or {}
        id_token = result.get("IdToken")
        if not id_token:
            raise SundayAuthError("Token refresh returned no IdToken")

        self._id_token = id_token
        expires_in = result.get("ExpiresIn")
        self._expires_at = time.time() + float(
            expires_in if isinstance(expires_in, (int, float)) else 3600
        )

        # Refresh-token rotation: persist a replacement token if issued.
        new_refresh = result.get("RefreshToken")
        if new_refresh and new_refresh != self._refresh_token:
            _LOGGER.debug("Refresh token rotated; persisting replacement")
            self._refresh_token = new_refresh
            if self._on_refresh_token_update is not None:
                self._on_refresh_token_update(new_refresh)
