from __future__ import annotations

import asyncio
import base64
import json
import os
import time

import pytest
from conftest import LiveCredentials

from aioanylist import AnyListClient, AuthenticationError, PermissionDeniedError
from aioanylist.transport import AnyListTransport

pytestmark = pytest.mark.live


def _auth_mutations_enabled() -> bool:
    return os.environ.get("ANYLIST_LIVE_AUTH_MUTATIONS") == "1"


def _jwt_times(token: str) -> tuple[int | None, int | None]:
    """Read non-secret JWT timing claims without validating or printing the token."""

    parts = token.split(".")
    if len(parts) != 3:
        return None, None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    issued = data.get("iat")
    expires = data.get("exp")
    return (
        int(issued) if isinstance(issued, int | float) else None,
        int(expires) if isinstance(expires, int | float) else None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    ["https://www.anylist.com", "https://production.anylist.com"],
)
async def test_native_logout_revokes_fresh_token_session(
    live_credentials: LiveCredentials,
    base_url: str,
) -> None:
    """Exercise native token-session logout using a fresh disposable auth session.

    This test does not touch list/recipe/account data or a device push registration. It is
    separately gated because successful sign-out is intentionally destructive to the newly
    minted token pair.
    """

    if not _auth_mutations_enabled():
        pytest.skip("set ANYLIST_LIVE_AUTH_MUTATIONS=1 to enable token sign-out verification")

    email = live_credentials.email
    password = live_credentials.password
    client = AnyListClient(base_url=base_url, user_email=email)
    try:
        original = await client.sign_in(email, password)
        assert client.account is not None
        await client.account.get()
        await client.logout()
        assert client.tokens is None
    finally:
        await client.close()

    access_revoked = False
    access_probe = AnyListTransport(base_url=base_url, tokens=original)
    try:
        try:
            await access_probe.request("GET", "/data/account/info", retry_auth=False)
        except PermissionDeniedError:
            access_revoked = True
    finally:
        await access_probe.close()

    refresh_revoked = False
    refresh_probe = AnyListTransport(base_url=base_url, tokens=original)
    try:
        try:
            await refresh_probe.refresh_access_token(force=True)
        except AuthenticationError:
            refresh_revoked = True
    finally:
        await refresh_probe.close()

    print(
        f"{base_url}: access_token_revoked={access_revoked} refresh_token_revoked={refresh_revoked}"
    )

    # Verified live on both www.anylist.com and production.anylist.com: sign-out revokes the
    # refresh token immediately but does not invalidate the already-issued access token.
    # The latter therefore remains usable until its normal expiry, after which the revoked
    # refresh token prevents the signed-out session from continuing.
    assert not access_revoked
    assert refresh_revoked


@pytest.mark.asyncio
async def test_logged_out_access_token_lifetime(live_credentials: LiveCredentials) -> None:
    """Characterize how long an access token remains usable after native sign-out."""

    if not _auth_mutations_enabled():
        pytest.skip("set ANYLIST_LIVE_AUTH_MUTATIONS=1 to enable token sign-out verification")

    base_url = "https://www.anylist.com"
    interval = float(os.environ.get("ANYLIST_ACCESS_TOKEN_PROBE_INTERVAL", "30"))
    max_seconds = float(os.environ.get("ANYLIST_ACCESS_TOKEN_MAX_SECONDS", "3600"))
    if interval <= 0 or max_seconds <= 0:
        raise ValueError("access-token probe interval and max seconds must be positive")

    client = AnyListClient(base_url=base_url, user_email=live_credentials.email)
    try:
        original = await client.sign_in(live_credentials.email, live_credentials.password)
        issued, expires = _jwt_times(original.access_token)
        if issued is not None and expires is not None:
            print(f"{base_url}: access token JWT lifetime={expires - issued}s")
        else:
            print(f"{base_url}: access token is not JWT-shaped; measuring by polling")
        await client.logout()
    finally:
        await client.close()

    started = time.monotonic()
    probe = AnyListTransport(base_url=base_url, tokens=original)
    try:
        while True:
            elapsed = time.monotonic() - started
            try:
                await probe.request("GET", "/data/account/info", retry_auth=False)
            except PermissionDeniedError:
                print(f"{base_url}: access token rejected {elapsed:.1f}s after logout")
                return

            print(f"{base_url}: access token still accepted {elapsed:.1f}s after logout")
            if elapsed >= max_seconds:
                print(
                    f"{base_url}: access token remained valid for at least {elapsed:.1f}s "
                    "after logout"
                )
                return

            wait = min(interval, max_seconds - elapsed)
            if expires is not None:
                # If the token advertises its own expiry, avoid needless polling while still
                # checking shortly before/after the boundary.
                until_expiry = expires - time.time()
                if until_expiry > interval * 2:
                    wait = min(wait, max(interval, until_expiry - interval))
            await asyncio.sleep(wait)
    finally:
        await probe.close()
