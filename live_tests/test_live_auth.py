from __future__ import annotations

import os

import pytest
from conftest import LiveCredentials

from anylist_sdk import AnyListClient, AuthenticationError, PermissionDeniedError
from anylist_sdk.transport import AnyListTransport

pytestmark = pytest.mark.live


def _auth_mutations_enabled() -> bool:
    return os.environ.get("ANYLIST_LIVE_AUTH_MUTATIONS") == "1"


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
