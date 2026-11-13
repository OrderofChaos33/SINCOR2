"""(e) accepts[] hedge: the server matches the client's claimed terms
against its OWN retained accepts[] and passes ITS OWN requirement to the
facilitator — never the client's. Negative tests: price-rewrite and
payTo-swap vectors, plus scheme/network/asset/timeout rewrites.
"""
from sincor_x402 import VerifiedEndpoint

from x402pkg_helpers import make_payment_payload, payment_header


def _tampered_header(requirement, **claim_overrides):
    claimed = requirement.to_accepts_dict()
    claimed.update(claim_overrides)
    return payment_header(make_payment_payload(requirement, claimed=claimed))


def _assert_rejected_before_facilitator(endpoint, facilitator, platform,
                                        header):
    resp = endpoint.handle(headers={"X-PAYMENT": header}, kwargs={})
    assert resp.status == 402
    assert resp.body["error"] == "accepts_mismatch"
    # The facilitator was never touched: no settlement was even attempted.
    assert facilitator.calls == 0
    assert platform.released_total == 0 and platform.refunded_total == 0
    assert len(platform._escrows) == 0


def test_price_rewrite_rejected(endpoint, facilitator, platform, requirement):
    # Attacker claims the price was 1 atomic unit instead of 10000.
    header = _tampered_header(requirement, maxAmountRequired="1")
    _assert_rejected_before_facilitator(endpoint, facilitator, platform, header)


def test_payto_swap_rejected(endpoint, facilitator, platform, requirement):
    # Attacker claims payTo was their own address.
    header = _tampered_header(
        requirement, payTo="0x9999999999999999999999999999999999999999")
    _assert_rejected_before_facilitator(endpoint, facilitator, platform, header)


def test_scheme_rewrite_rejected(endpoint, facilitator, platform, requirement):
    header = _tampered_header(requirement, scheme="upto")
    _assert_rejected_before_facilitator(endpoint, facilitator, platform, header)


def test_network_rewrite_rejected(endpoint, facilitator, platform, requirement):
    header = _tampered_header(requirement, network="ethereum")
    _assert_rejected_before_facilitator(endpoint, facilitator, platform, header)


def test_asset_swap_rejected(endpoint, facilitator, platform, requirement):
    header = _tampered_header(
        requirement, asset="0x0000000000000000000000000000000000000001")
    _assert_rejected_before_facilitator(endpoint, facilitator, platform, header)


def test_timeout_rewrite_rejected(endpoint, facilitator, platform, requirement):
    header = _tampered_header(requirement, maxTimeoutSeconds=999999)
    _assert_rejected_before_facilitator(endpoint, facilitator, platform, header)


def test_facilitator_receives_servers_own_requirement(
        endpoint, facilitator, requirement):
    # Honest claim, but a *different dict object* carrying junk in
    # non-hedge fields. The hedge passes on the six fields — and the
    # facilitator must still receive the server's OWN object, not this one.
    claimed = requirement.to_accepts_dict()
    claimed["resource"] = "/attacker-controlled"
    claimed["description"] = "rewritten by client"
    claimed["extra"] = {"evil": True}
    header = payment_header(make_payment_payload(requirement, claimed=claimed))
    resp = endpoint.handle(headers={"X-PAYMENT": header}, kwargs={})
    assert resp.status == 200
    assert facilitator.calls == 1
    seen = facilitator.seen_requirements[0]
    assert seen is endpoint.requirement          # identity: the server's own
    assert seen.resource == "/summarize"          # not the client's rewrite
    assert seen.extra == {}                       # client junk never propagated


def test_authorization_amount_below_requirement_rejected_by_facilitator(
        endpoint, facilitator, platform, requirement):
    # Claim matches, but the signed authorization itself underpays: the
    # facilitator checks it against the server's requirement and refuses.
    payload = make_payment_payload(requirement, value=1)
    resp = endpoint.handle(headers={"X-PAYMENT": payment_header(payload)},
                           kwargs={})
    assert resp.status == 402
    assert resp.body["error"] == "payment_verification_failed"
    assert len(platform._escrows) == 0  # no escrow opened on a bad payment


def test_authorization_payto_diversion_rejected_by_facilitator(
        endpoint, facilitator, platform, requirement):
    payload = make_payment_payload(
        requirement, auth_to="0x9999999999999999999999999999999999999999")
    resp = endpoint.handle(headers={"X-PAYMENT": payment_header(payload)},
                           kwargs={})
    assert resp.status == 402
    assert resp.body["error"] == "payment_verification_failed"
    assert len(platform._escrows) == 0


def test_missing_signature_rejected(endpoint, platform, requirement):
    payload = make_payment_payload(requirement, signature="")
    resp = endpoint.handle(headers={"X-PAYMENT": payment_header(payload)},
                           kwargs={})
    assert resp.status == 402
    assert resp.body["error"] == "payment_verification_failed"


def test_malformed_payment_header_rejected(endpoint):
    resp = endpoint.handle(headers={"X-PAYMENT": "!!!not-json!!!"},
                           kwargs={})
    assert resp.status == 400
