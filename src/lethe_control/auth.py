from __future__ import annotations

import secrets

from lethe_control.config import Settings
from lethe_control.errors import AuthorizationError


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise AuthorizationError("missing_bearer_token", "bearer token required")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        raise AuthorizationError("invalid_bearer_token", "valid bearer token required")
    return token


def require_control_token(settings: Settings, authorization: str | None) -> None:
    token = bearer_token(authorization)
    if not secrets.compare_digest(token, settings.control_token):
        raise AuthorizationError("invalid_control_token", "control token rejected")


def require_unsafe_token(settings: Settings, authorization: str | None) -> None:
    if not settings.demo_profile:
        raise AuthorizationError("unsafe_endpoint_disabled", "unsafe endpoint is disabled")
    token = bearer_token(authorization)
    if not secrets.compare_digest(token, settings.unsafe_token):
        raise AuthorizationError("invalid_unsafe_token", "unsafe demo token rejected")


def resolve_principal(settings: Settings, authorization: str | None) -> str:
    token = bearer_token(authorization)
    if secrets.compare_digest(token, settings.alice_token):
        return "principal://demo/alice"
    if secrets.compare_digest(token, settings.bob_token):
        return "principal://demo/bob"
    raise AuthorizationError("unknown_principal", "query token does not map to a principal")
