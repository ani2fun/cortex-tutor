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
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from tutor.config import Settings, get_settings
from tutor.models.catalog import Tier

log = structlog.get_logger()


@dataclass(frozen=True)
class Principal:
    """The validated caller — the only identity the handlers see."""

    sub: str
    preferred_username: str


_bearer = HTTPBearer(auto_error=False)
_jwks_clients: dict[str, PyJWKClient] = {}


# Keycloak sits behind Cloudflare, whose bot protection 403s PyJWT's default urllib User-Agent
# ("Python-urllib/X" — a known-bot signature) while letting the cortex Scala server's Nimbus/Java client
# through. That asymmetry was the whole outage: the tutor's JWKS fetch got a 403 it couldn't resolve, so
# every signed-in token failed validation. A real User-Agent gets the fetch past the edge. (Belt-and-
# suspenders — the proper fix is a Cloudflare "skip" rule for the public OIDC endpoints; see the runbook.)
_JWKS_HEADERS = {
    "User-Agent": "cortex-tutor/1.0 (JWKS fetch; +https://cortex.kakde.eu)",
    "Accept": "application/json",
}


def _jwks_client(jwks_url: str) -> PyJWKClient:
    client = _jwks_clients.get(jwks_url)
    if client is None:
        client = PyJWKClient(jwks_url, cache_keys=True, lifespan=300, headers=_JWKS_HEADERS)
        _jwks_clients[jwks_url] = client
    return client


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _auth_unavailable(detail: str) -> HTTPException:
    # 503 (not 500): the server can't validate the token right now — a dependency failure (JWKS
    # unreachable), not a bad request. Lets callers/monitoring tell "broken token" from "broken infra".
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


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

    # Resolve the token's signing key from Keycloak's JWKS. A failure HERE is the server's inability to
    # validate — the JWKS endpoint is unreachable from this pod, or the key id isn't published — NOT a
    # bad token. PyJWT raises PyJWKClientError (incl. PyJWKClientConnectionError); letting it escape
    # makes a bare 500 that the SPA reads as "tutor unavailable" and silently degrades the Coach to its
    # static fallback for every signed-in user. Map it to 503 + a structured log so it's diagnosable.
    # (A malformed token whose header yields no key id is a client error → 401, as before.)
    try:
        signing_key = _jwks_client(settings.jwks_url).get_signing_key_from_jwt(token).key
    except PyJWKClientError as exc:
        log.error("tutor.jwks_unavailable", error=str(exc), jwks_url=settings.jwks_url)
        raise _auth_unavailable("Auth temporarily unavailable — cannot reach the identity provider") from None
    except jwt.InvalidTokenError as exc:
        raise _unauthorized(f"Invalid token: {exc}") from None

    try:
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
    return principal.preferred_username in settings.homelab_users or principal.sub in settings.homelab_users


def wants_byok(principal: Principal, settings: Settings) -> bool:
    """True iff a session created for this caller is BYOK-tier (client-direct key, server records
    only). FORCE_BYOK overrides for local dev; otherwise an authenticated caller off the homelab
    allowlist is BYOK. With auth off (and no force) the synthetic dev principal rides the homelab
    path so ``bin/dev`` keeps working."""
    if settings.force_byok:
        return True
    return settings.auth_enabled and not is_homelab(principal, settings)


def tier_for(principal: Principal, settings: Settings) -> Tier:
    """The caller's coach tier — BYOK (client-direct) or HOMELAB (server key). A thin alias over
    ``wants_byok`` so the homelab-vs-BYOK decision stays single-sourced."""
    return Tier.BYOK if wants_byok(principal, settings) else Tier.HOMELAB


# Convenience alias for route signatures.
CurrentPrincipal = Annotated[Principal, Depends(verify_jwt)]
