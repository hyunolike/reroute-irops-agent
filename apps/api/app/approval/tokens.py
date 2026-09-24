"""Signed, short-lived, single-plan approval tokens.

The Approval Gateway mints a token only after an operator approved a plan. The Airline Booking API
refuses any write without a valid token whose item digest matches the request - so neither the agent
nor anyone else can change bookings (or swap passengers/flights after approval) without human sign-off.
The signing secret lives with the gateway and the booking API, never inside the agent sandbox.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


class ApprovalTokenError(ValueError):
    pass


def items_digest(items: list[dict]) -> str:
    canon = sorted(f"{i['reservation_id']}>{i['flight_no']}/{i['cabin']}" for i in items)
    return hashlib.sha256("|".join(canon).encode()).hexdigest()


@dataclass(frozen=True)
class ApprovalClaims:
    plan_id: str
    approval_id: str
    approved_by: str
    digest: str
    exp: int


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint_token(secret: str, claims: ApprovalClaims) -> str:
    body = _b64(json.dumps(claims.__dict__, sort_keys=True).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(secret: str, token: str, *, plan_id: str, items: list[dict], now: float | None = None) -> ApprovalClaims:
    try:
        body, sig = token.split(".", 1)
    except ValueError as e:
        raise ApprovalTokenError("malformed approval token") from e
    expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise ApprovalTokenError("invalid approval token signature")
    claims = ApprovalClaims(**json.loads(_unb64(body)))
    if claims.exp < (now or time.time()):
        raise ApprovalTokenError("approval token expired")
    if claims.plan_id != plan_id:
        raise ApprovalTokenError("approval token issued for a different plan")
    if claims.digest != items_digest(items):
        raise ApprovalTokenError("booking items differ from the approved plan")
    return claims
