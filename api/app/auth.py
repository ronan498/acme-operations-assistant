"""Bearer-token validation against Keycloak.

Tokens are minted at the PUBLIC issuer (http://localhost:8080/realms/acme —
what the browser and curl see) while the api verifies signatures against
JWKS fetched over the INTERNAL compose network (http://keycloak:8080).
Conflating those two URLs is the classic Keycloak-in-docker failure, so
they are two distinct settings.

Audience is deliberately not verified: Keycloak access tokens carry
aud=account by default, and mapping a custom audience buys nothing for a
single-API deployment. Signature (RS256), expiry, and issuer are enforced.
"""

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from .config import settings

KNOWN_ROLES = frozenset({"sales_user", "support_user", "admin"})


@dataclass(frozen=True)
class Principal:
    sub: str
    username: str
    roles: frozenset[str]


_jwks_client: PyJWKClient | None = None


def _jwks() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(
            f"{settings.keycloak_url}/realms/{settings.keycloak_realm}/protocol/openid-connect/certs",
            cache_keys=True,
        )
    return _jwks_client


_bearer = HTTPBearer(auto_error=False)


async def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if credentials is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = credentials.credentials
    try:
        signing_key = _jwks().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=settings.keycloak_issuer,
            options={"verify_aud": False},  # see module docstring
        )
    except jwt.PyJWTError as exc:
        # Type only, never the message: token contents must not leak into responses
        raise HTTPException(status_code=401, detail=f"invalid token ({type(exc).__name__})") from exc

    roles = frozenset(claims.get("realm_access", {}).get("roles", [])) & KNOWN_ROLES
    return Principal(
        sub=claims["sub"],
        username=claims.get("preferred_username", "<unknown>"),
        roles=roles,
    )
