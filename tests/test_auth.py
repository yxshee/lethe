from __future__ import annotations

import pytest

from lethe_control.auth import require_control_token, require_unsafe_token, resolve_principal
from lethe_control.errors import AuthorizationError


def test_query_token_maps_to_server_owned_principal(settings) -> None:  # type: ignore[no-untyped-def]
    assert resolve_principal(settings, f"Bearer {settings.alice_token}") == (
        "principal://demo/alice"
    )
    assert resolve_principal(settings, f"Bearer {settings.bob_token}") == ("principal://demo/bob")


def test_invalid_tokens_fail_closed(settings) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AuthorizationError, match="bearer token required"):
        resolve_principal(settings, None)
    with pytest.raises(AuthorizationError, match="control token rejected"):
        require_control_token(settings, "Bearer wrong")


def test_unsafe_endpoint_requires_demo_profile(settings) -> None:  # type: ignore[no-untyped-def]
    require_unsafe_token(settings, f"Bearer {settings.unsafe_token}")
    production = type(settings)(state_dir=settings.state_dir, demo_profile=False)
    with pytest.raises(AuthorizationError, match="disabled"):
        require_unsafe_token(production, f"Bearer {settings.unsafe_token}")
