"""P14 — SINAX licensing pack: issuance, offline verification, seat caps.

Covers src/sincor2/sinax/licensing.py — the issuance/verification core
of the SINAX license pack (terms + delivery process in
src/sincor2/sinax/LICENSING.md). Price/term constants are pinned to the
catalog: $1,149 per developer seat per year, 365-day term.
"""

import pytest

from sincor2.sinax.licensing import (
    LICENSE_PRICE_USD_PER_SEAT_YEAR,
    TERM_DAYS,
    LicenseExpired,
    LicenseSignatureError,
    SeatLimitExceeded,
    SeatRegistry,
    issue_license,
    verify_license,
)

SECRET = "test-issuer-secret"
T0 = 1_700_000_000


def _issued(seats=2, licensee="Acme Proofs Inc", issued_at=T0):
    return issue_license(licensee, seats, SECRET, issued_at=issued_at)


def test_catalog_terms_constants():
    assert LICENSE_PRICE_USD_PER_SEAT_YEAR == 1149
    assert TERM_DAYS == 365


def test_issue_and_verify_roundtrip():
    issued = _issued()
    lic = verify_license(issued.payload(), SECRET, at=T0 + 60)
    assert lic.licensee == "Acme Proofs Inc"
    assert lic.seats == 2
    assert lic.license_id == issued.license_id
    assert lic.expires_at == T0 + 365 * 86_400


def test_tampered_seat_count_fails():
    payload = _issued().payload()
    payload["seats"] = 99
    with pytest.raises(LicenseSignatureError):
        verify_license(payload, SECRET, at=T0 + 60)


def test_tampered_licensee_fails():
    payload = _issued().payload()
    payload["licensee"] = "Mallory GmbH"
    with pytest.raises(LicenseSignatureError):
        verify_license(payload, SECRET, at=T0 + 60)


def test_wrong_secret_fails():
    payload = _issued().payload()
    with pytest.raises(LicenseSignatureError):
        verify_license(payload, "not-the-secret", at=T0 + 60)


def test_expired_license_fails_verification():
    payload = _issued().payload()
    with pytest.raises(LicenseExpired):
        verify_license(payload, SECRET, at=T0 + 366 * 86_400)


def test_seat_cap_enforced_and_activation_idempotent():
    registry = SeatRegistry()
    payload = _issued(seats=2).payload()
    registry.activate(payload, "dev-1", SECRET, at=T0 + 60)
    registry.activate(payload, "dev-2", SECRET, at=T0 + 60)
    registry.activate(payload, "dev-1", SECRET, at=T0 + 60)  # idempotent
    assert registry.active_seats(payload["license_id"]) == {"dev-1", "dev-2"}
    with pytest.raises(SeatLimitExceeded):
        registry.activate(payload, "dev-3", SECRET, at=T0 + 60)


def test_deactivate_frees_a_seat():
    registry = SeatRegistry()
    payload = _issued(seats=1).payload()
    registry.activate(payload, "dev-1", SECRET, at=T0 + 60)
    assert registry.deactivate(payload["license_id"], "dev-1") is True
    registry.activate(payload, "dev-2", SECRET, at=T0 + 60)
    assert registry.active_seats(payload["license_id"]) == {"dev-2"}


def test_expired_license_activates_nothing():
    registry = SeatRegistry()
    payload = _issued().payload()
    with pytest.raises(LicenseExpired):
        registry.activate(payload, "dev-1", SECRET, at=T0 + 400 * 86_400)
    assert registry.active_seats(payload["license_id"]) == set()


def test_registries_isolate_licenses():
    registry = SeatRegistry()
    a = _issued(seats=1, licensee="A").payload()
    b = _issued(seats=1, licensee="B").payload()
    registry.activate(a, "dev-1", SECRET, at=T0 + 60)
    registry.activate(b, "dev-1", SECRET, at=T0 + 60)
    assert registry.active_seats(a["license_id"]) == {"dev-1"}
    assert registry.active_seats(b["license_id"]) == {"dev-1"}
