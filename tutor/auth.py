"""Keycloak JWT verification — replicates the cortex Scala server's check so the tutor accepts the
**same** tokens (same realm + JWKS).

Reference: ``KeycloakAuthBackend.scala:76-92`` (RS256 signature via JWKS, ``iss`` exact, ``exp/iat/
nbf``, and **aud-contains-OR-azp-equals** — Keycloak's public-SPA quirk: ``aud:["account"]`` but
``azp:"<clientId>"``). When ``AUTH_ENABLED=false`` (dev) we short-circuit to a synthetic principal
so the coach runs locally without Keycloak.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from tutor.config import Settings, get_settings


@dataclass(frozen=True)
class Principal:
    """The validated caller — the only identity the handlers see."""

    sub: str
    preferred_username: str


_bearer = HTTPBearer(auto_error=False)
_jwks_clients: dict[str, PyJWKClient] = {}


def _jwks_client(jwks_url: str) -> PyJWKClient:
    client = _jwks_clients.get(jwks_url)
    if client is None:
        client = PyJWKClient(jwks_url, cache_keys=True, lifespan=300)
        _jwks_clients[jwks_url] = client
    return client


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def verify_jwt(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    # Dev bypass — coach open to all locally, no Keycloak required.
    if not settings.auth_enabled:
        return Principal(sub="dev", preferred_username="dev")

    if creds is None:
        raise _unauthorized("Missing bearer token")
    token = creds.credentials
    try:
        signing_key = _jwks_client(settings.jwks_url).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=settings.keycloak_issuer_url,
            options={"verify_aud": False},  # aud/azp checked manually below (Keycloak quirk)
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token expired") from None
    except jwt.InvalidTokenError as exc:
        raise _unauthorized(f"Invalid token: {exc}") from None

    aud = claims.get("aud", [])
    aud_list = [aud] if isinstance(aud, str) else list(aud or [])
    azp = claims.get("azp")
    expected = settings.keycloak_client_id
    if expected not in aud_list and azp != expected:
        raise _unauthorized("Token audience mismatch")

    sub = claims.get("sub")
    if not sub:
        raise _unauthorized("Token missing sub")
    return Principal(sub=sub, preferred_username=claims.get("preferred_username") or sub)


def is_homelab(principal: Principal, settings: Settings) -> bool:
    """True iff the caller may use the homelab Claude/Ollama path (else: BYOK). Fails closed."""
    return (
        principal.preferred_username in settings.homelab_users
        or principal.sub in settings.homelab_users
    )


# Convenience alias for route signatures.
CurrentPrincipal = Annotated[Principal, Depends(verify_jwt)]
