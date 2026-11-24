"""SINAX commercial licensing — seat licenses for the 26-module library.

P14 productization build (2026-11-23). Until now SINAX was real code with
no licensing surface at all: no terms, no issuance, no delivery process.
This module is the issuance/verification core of the license pack:

  * A seat license is a small signed record: licensee, seat count, issue
    time, term. The license ID is the SHA-256 of the canonical JSON of
    the record (content-addressed, the same pattern the treasury swarm
    uses for holds), and the license key is an HMAC-SHA256 over that
    canonical JSON under the issuer secret — verification is fully
    offline, no server round-trip, no database.
  * :class:`SeatRegistry` enforces the seat cap at activation time:
    activating a seat id is idempotent, a new seat beyond the licensed
    count is refused, and expired licenses activate nothing.
  * Terms constants are the catalog's: $1,149 per developer seat per
    year (2026-11-16 researched list price), 365-day term. Changing the
    price is a founder/catalog act, not a code default to drift.

The issuer secret is supplied by the operator at issuance time (env or
secret store); it is never stored in this module or in the license
record. See LICENSING.md in this package for the terms summary and the
end-to-end delivery process (payment -> issuance -> delivery -> renewal).

Safety: this module moves no money, signs nothing on-chain, and grants
nothing by itself — it records and verifies license claims so the
delivery process has something checkable to hand a buyer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, Optional, Set

LICENSE_PRICE_USD_PER_SEAT_YEAR = 1149
TERM_DAYS = 365
SECONDS_PER_DAY = 86_400
PRODUCT = "SINAX Geometric Proof Navigator"


class LicenseError(ValueError):
    """Base error for licensing rule violations."""


class SeatLimitExceeded(LicenseError):
    """Activation would exceed the licensed seat count."""


class LicenseExpired(LicenseError):
    """The license term has ended; nothing may activate against it."""


class LicenseSignatureError(LicenseError):
    """The presented key does not match the license record."""


def _canonical(record: dict) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class SeatLicense:
    licensee: str
    seats: int
    issued_at: int  # unix seconds
    term_days: int = TERM_DAYS
    product: str = PRODUCT

    def __post_init__(self) -> None:
        if not self.licensee:
            raise LicenseError("licensee must be non-empty")
        if self.seats <= 0:
            raise LicenseError("seats must be >= 1")
        if self.term_days <= 0:
            raise LicenseError("term_days must be >= 1")

    @property
    def expires_at(self) -> int:
        return self.issued_at + self.term_days * SECONDS_PER_DAY

    def record(self) -> dict:
        return asdict(self)

    @property
    def license_id(self) -> str:
        return hashlib.sha256(_canonical(self.record())).hexdigest()

    def is_expired(self, at: Optional[int] = None) -> bool:
        return (at if at is not None else int(time.time())) >= self.expires_at


@dataclass(frozen=True)
class IssuedLicense:
    license: SeatLicense
    key: str

    @property
    def license_id(self) -> str:
        return self.license.license_id

    def payload(self) -> dict:
        """The hand-to-buyer record: license fields + id + key."""
        out = self.license.record()
        out["license_id"] = self.license_id
        out["key"] = self.key
        return out


def _sign(record: dict, secret: str) -> str:
    if not secret:
        raise LicenseError("issuer secret must be non-empty")
    return hmac.new(secret.encode(), _canonical(record), hashlib.sha256).hexdigest()


def issue_license(
    licensee: str,
    seats: int,
    secret: str,
    issued_at: Optional[int] = None,
    term_days: int = TERM_DAYS,
) -> IssuedLicense:
    """Issue a signed seat license. Called once payment is confirmed."""
    lic = SeatLicense(
        licensee=licensee,
        seats=seats,
        issued_at=int(issued_at if issued_at is not None else time.time()),
        term_days=term_days,
    )
    return IssuedLicense(license=lic, key=_sign(lic.record(), secret))


def verify_license(
    payload: dict,
    secret: str,
    at: Optional[int] = None,
    require_unexpired: bool = True,
) -> SeatLicense:
    """Verify a buyer-presented license payload offline.

    Raises LicenseSignatureError on any tampering (including a changed
    seat count or licensee), LicenseExpired when the term has ended.
    Returns the reconstructed SeatLicense on success.
    """
    try:
        record = {
            "licensee": payload["licensee"],
            "seats": int(payload["seats"]),
            "issued_at": int(payload["issued_at"]),
            "term_days": int(payload.get("term_days", TERM_DAYS)),
            "product": payload.get("product", PRODUCT),
        }
        presented_key = str(payload["key"])
        presented_id = str(payload["license_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LicenseSignatureError(f"malformed license payload: {exc}") from exc

    lic = SeatLicense(**record)
    if lic.license_id != presented_id:
        raise LicenseSignatureError("license_id does not match the record")
    expected_key = _sign(record, secret)
    if not hmac.compare_digest(expected_key, presented_key):
        raise LicenseSignatureError("license key does not match the record")
    if require_unexpired and lic.is_expired(at):
        raise LicenseExpired(f"license {lic.license_id} expired at {lic.expires_at}")
    return lic


@dataclass
class SeatRegistry:
    """Activation ledger: which seat ids are live against which license."""

    _seats: Dict[str, Set[str]] = field(default_factory=dict)

    def activate(self, payload: dict, seat_id: str, secret: str, at: Optional[int] = None) -> SeatLicense:
        lic = verify_license(payload, secret, at=at)
        active = self._seats.setdefault(lic.license_id, set())
        if seat_id in active:
            return lic  # idempotent re-activation of the same seat
        if len(active) >= lic.seats:
            raise SeatLimitExceeded(
                f"license {lic.license_id} allows {lic.seats} seats; "
                f"{seat_id!r} would exceed it"
            )
        active.add(seat_id)
        return lic

    def deactivate(self, license_id: str, seat_id: str) -> bool:
        active = self._seats.get(license_id)
        if not active or seat_id not in active:
            return False
        active.discard(seat_id)
        return True

    def active_seats(self, license_id: str) -> Set[str]:
        return set(self._seats.get(license_id, set()))
