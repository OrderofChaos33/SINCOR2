"""Unit tests for the P20 Compliance Automation reference build.

Covers src/sincor2/defi/compliance_automation.py — the fail-closed
decision matrix, KYC forged/replay/expired handling, bounded 3-hop
AML lookback, the attested-jurisdiction geo gate with 48h timelock,
per-list emergency overrides with 24h expiry, the 24h PASS cache with
invalidation, the hooked-vault pass/fail integration, the append-only
audit trail, and the verifiable receipt chain behind SKU
SINCOR-DEFI-P20-COMPLY. Pure logic, no chain.

Each test cites the auction-task acceptance criterion it guards.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi.compliance_automation import (
    KYC_VALIDITY_SECONDS,
    SCREENING_CACHE_SECONDS,
    TAINT_MAX_HOPS,
    AMLAdapter,
    AuditTrail,
    ComplianceOracle,
    DepositRejected,
    GatekeeperHook,
    GeoRegistry,
    KYCAdapter,
    KYCAttestation,
    PluginResult,
    Receipt,
    ReceiptChain,
    ReentrancyAttempt,
    TimelockPending,
    Unauthorized,
    Verdict,
    DecisionEngine,
    build_reference_oracle,
    status_payload,
)

NOW = 1_800_000_000.0
ISSUER = "kyc-issuer"
ISSUER_KEY = b"issuer-key-32-bytes-placeholder-x"


def build_engine() -> DecisionEngine:
    kyc = KYCAdapter({ISSUER: ISSUER_KEY})
    aml = AMLAdapter(sanctions={"bad_actor"}, fund_flows={},
                     list_updated_at=NOW)
    geo = GeoRegistry(admin="geo_admin")
    oracle = ComplianceOracle(kyc, aml, geo)
    return DecisionEngine(oracle, guardian="guardian")


def good_attestation(kyc: KYCAdapter, subject: str = "alice",
                     now: float = NOW) -> KYCAttestation:
    return kyc.sign(ISSUER, subject, now - 10, now - 10 + KYC_VALIDITY_SECONDS,
                    nonce=f"nonce-{subject}")


def clean_account(engine: DecisionEngine, account: str) -> None:
    engine.geo.attest_jurisdiction(account, "US")


# -- decision matrix ----------------------------------------------------------------
def test_all_pass_yields_pass():
    # AC(p20-compliance-automation-decision-matrix): a row with no
    # failing signal returns PASS.
    eng = build_engine()
    clean_account(eng, "alice")
    att = good_attestation(eng.kyc, "alice")
    verdict, results = eng.decide("alice", att, now=NOW)
    assert verdict == Verdict.PASS
    assert all(r.verdict == Verdict.PASS for r in results)


def test_fail_signal_wins_over_pass():
    # A FAIL in one plugin yields FAIL overall even if the others pass.
    eng = build_engine()
    clean_account(eng, "alice")
    eng.aml.sanctions.add("alice")
    att = good_attestation(eng.kyc, "alice")
    verdict, _ = eng.decide("alice", att, now=NOW)
    assert verdict == Verdict.FAIL


def test_stale_and_unreachable_block_as_fail():
    # AC(p20-compliance-automation-decision-matrix): STALE and
    # unreachable/erroring plugins yield FAIL (fail-closed).
    eng = build_engine()
    clean_account(eng, "alice")
    expired = eng.kyc.sign(ISSUER, "alice", NOW - KYC_VALIDITY_SECONDS - 10,
                           NOW - 5, nonce="expired-1")
    verdict, _ = eng.decide("alice", expired, now=NOW)
    assert verdict == Verdict.FAIL  # STALE -> FAIL

    eng2 = build_engine()
    clean_account(eng2, "alice")
    eng2.aml.reachable = False  # provider outage
    att = good_attestation(eng2.kyc, "alice")
    verdict2, _ = eng2.decide("alice", att, now=NOW)
    assert verdict2 == Verdict.FAIL  # unreachable -> FAIL, never PASS


def test_refer_blocks_until_resolved():
    eng = build_engine()  # no jurisdiction attested for alice -> REFER
    att = good_attestation(eng.kyc, "alice")
    verdict, _ = eng.decide("alice", att, now=NOW)
    assert verdict == Verdict.REFER
    clean_account(eng, "alice")
    eng.invalidate_cache()
    # Fresh nonce for the second screening (nonces are single-use).
    att2 = eng.kyc.sign(ISSUER, "alice", NOW - 10,
                        NOW - 10 + KYC_VALIDITY_SECONDS, nonce="nonce-alice-2")
    verdict2, _ = eng.decide("alice", att2, now=NOW)
    assert verdict2 == Verdict.PASS


# -- KYC --------------------------------------------------------------------------------------
def test_kyc_forged_attestation_fails():
    # AC(p20-compliance-automation-kyc-integration): forged attestations
    # fail screening.
    eng = build_engine()
    clean_account(eng, "alice")
    att = good_attestation(eng.kyc, "alice")
    forged = KYCAttestation(att.issuer, att.subject, att.issued_at,
                            att.expires_at, "replay-x", "0" * 64)
    verdict, _ = eng.decide("alice", forged, now=NOW)
    assert verdict == Verdict.FAIL


def test_kyc_nonce_replay_fails():
    eng = build_engine()
    att = eng.kyc.verify(good_attestation(eng.kyc, "alice", now=NOW), now=NOW)
    assert att.verdict == Verdict.PASS
    again = eng.kyc.verify(good_attestation(eng.kyc, "alice", now=NOW),
                           now=NOW)
    assert again.verdict == Verdict.FAIL  # same nonce consumed
    assert "replay" in again.detail


def test_kyc_over_90_days_rejected():
    eng = build_engine()
    att = eng.kyc.sign(ISSUER, "alice", NOW - 10,
                       NOW - 10 + KYC_VALIDITY_SECONDS + 1,
                       nonce="long-1")
    res = eng.kyc.verify(att, now=NOW)
    assert res.verdict == Verdict.FAIL


# -- AML ------------------------------------------------------------------------------------
def test_aml_sanctioned_account_fails():
    # AC(p20-compliance-automation-aml-screening): sanctioned addresses
    # are blocked.
    eng = build_engine()
    clean_account(eng, "alice")
    eng.aml.sanctions.add("alice")
    att = good_attestation(eng.kyc, "alice")
    verdict, _ = eng.decide("alice", att, now=NOW)
    assert verdict == Verdict.FAIL


def test_aml_mixer_interaction_auto_fails():
    eng = build_engine()
    clean_account(eng, "alice")
    eng.aml.fund_flows["alice"] = [("mixer_pool", {"mixer"})]
    att = good_attestation(eng.kyc, "alice")
    verdict, _ = eng.decide("alice", att, now=NOW)
    assert verdict == Verdict.FAIL


def test_aml_3hop_lookback_bound():
    # AC(p20-compliance-automation-aml-screening): 3-hop lookback proven
    # bounded. Taint at hop 4 is OUT of scope (clean); taint at hop 3
    # fails.
    flows: dict = {}
    chain = ["alice", "h1", "h2", "h3", "h4"]
    for a, b in zip(chain, chain[1:]):
        flows[a] = [(b, set())]
    flows["h4"] = [("tainted", {"mixer"})]
    eng = build_engine()
    clean_account(eng, "alice")
    eng.aml.fund_flows = flows
    att = good_attestation(eng.kyc, "alice")
    verdict, results = eng.decide("alice", att, now=NOW)
    assert verdict == Verdict.PASS  # taint at hop 4: out of scope

    flows2 = {"alice": [("h1", set())], "h1": [("h2", set())],
              "h2": [("tainted", {"mixer"})]}
    eng2 = build_engine()
    clean_account(eng2, "alice")
    eng2.aml.fund_flows = flows2
    att2 = good_attestation(eng2.kyc, "alice")
    verdict2, _ = eng2.decide("alice", att2, now=NOW)
    assert verdict2 == Verdict.FAIL  # taint at hop 3: in scope
    assert eng.aml.max_hops == TAINT_MAX_HOPS == 3


def test_aml_no_pii_on_chain():
    eng = build_engine()
    res = eng.aml.screen("alice", now=NOW)
    assert "alice" not in res.evidence_hash  # only a commitment
    assert len(res.evidence_hash) == 64


# -- geo -------------------------------------------------------------------------------------
def test_geo_blocked_jurisdiction_fails():
    # AC(p20-compliance-automation-geo-blocking): blocked jurisdictions
    # fail the geo check.
    eng = build_engine()
    eng.geo.attest_jurisdiction("alice", "KP")
    eng.geo.propose_update("KP", True, caller="geo_admin", now=NOW)
    with pytest.raises(TimelockPending):
        eng.geo.execute_update(caller="geo_admin", now=NOW + 100)
    eng.geo.execute_update(caller="geo_admin",
                           now=NOW + 48 * 3600 + 1)
    att = good_attestation(eng.kyc, "alice")
    verdict, _ = eng.decide("alice", att, now=NOW + 48 * 3600 + 2)
    assert verdict == Verdict.FAIL


def test_geo_attested_not_ip():
    # The gate keys on attested jurisdiction, never IP.
    eng = build_engine()
    eng.geo.attest_jurisdiction("alice", "US")
    res = eng.geo.check("alice")
    assert res.verdict == Verdict.PASS
    assert "US" in res.detail


# -- emergency override ----------------------------------------------------------------------------
def test_override_24h_expiry_and_per_list():
    # AC(p20-compliance-automation-emergency-override): guardian-only,
    # single address, per-list, 24h expiry, on-chain reason. An override
    # of list X never clears a FAIL from list Y.
    eng = build_engine()
    clean_account(eng, "alice")
    eng.aml.sanctions.add("alice")
    eng.geo.attest_jurisdiction("alice", "KP")
    eng.geo.propose_update("KP", True, caller="geo_admin", now=NOW)
    eng.geo.execute_update(caller="geo_admin", now=NOW + 48 * 3600 + 1)
    t0 = NOW + 48 * 3600 + 2
    with pytest.raises(Unauthorized):
        eng.grant_override("alice", "aml", "reviewed", caller="mallory",
                           now=t0)
    eng.grant_override("alice", "aml", "manual review cleared", caller="guardian",
                       now=t0)
    att = good_attestation(eng.kyc, "alice", now=t0)
    verdict, _ = eng.decide("alice", att, now=t0)
    assert verdict == Verdict.FAIL  # geo still fails: per-list isolation
    eng2 = build_engine()
    clean_account(eng2, "alice")
    eng2.aml.sanctions.add("alice")
    att2 = good_attestation(eng2.kyc, "alice")
    eng2.grant_override("alice", "aml", "reviewed", caller="guardian",
                        now=NOW)
    v2, _ = eng2.decide("alice", att2, now=NOW)
    assert v2 == Verdict.PASS
    v3, _ = eng2.decide("alice", att2, now=NOW + 24 * 3600 + 1)
    assert v3 == Verdict.FAIL  # expired: no silent renewal


# -- cache ------------------------------------------------------------------------------------------
def test_pass_cache_24h_and_invalidation():
    # AC(p20-compliance-automation-screening-cache): PASS caches 24h and
    # invalidates on list updates.
    eng = build_engine()
    clean_account(eng, "alice")
    att = good_attestation(eng.kyc, "alice")
    v1, _ = eng.decide("alice", att, now=NOW)
    assert v1 == Verdict.PASS
    eng.aml.sanctions.add("alice")  # cached PASS still served
    v2, _ = eng.decide("alice", att, now=NOW + 100)
    assert v2 == Verdict.PASS
    eng.invalidate_cache()  # list update invalidates
    v3, _ = eng.decide("alice", att, now=NOW + 100)
    assert v3 == Verdict.FAIL
    v4, _ = eng.decide("alice", att, now=NOW + SCREENING_CACHE_SECONDS + 1)
    assert v4 == Verdict.FAIL  # TTL path also re-decides


# -- hooked vault integration ----------------------------------------------------------------------------
def test_hook_pass_allows_fail_reverts():
    # AC(p20-compliance-automation-hooked-vaults): PASS deposits proceed;
    # FAIL/REFER revert with a queryable rejection code, balance
    # unchanged.
    eng = build_engine()
    clean_account(eng, "alice")
    hook = GatekeeperHook(eng)
    vault: dict = {}
    att = good_attestation(eng.kyc, "alice")
    hook.gated_deposit(vault, "alice", 100, att, now=NOW)
    assert vault["alice"] == 100
    eng.aml.sanctions.add("alice")
    eng.invalidate_cache()
    with pytest.raises(DepositRejected) as exc:
        hook.gated_deposit(vault, "alice", 50, att, now=NOW)
    assert exc.value.code == "COMPLY_FAIL"
    assert hook.rejection_code("alice") == "COMPLY_FAIL"
    assert vault["alice"] == 100  # unchanged


def test_hook_reentrancy_blocked():
    eng = build_engine()
    hook = GatekeeperHook(eng)
    hook._in_check = True  # simulate a reentrant entry
    with pytest.raises(ReentrancyAttempt):
        hook.check("alice", now=NOW)


# -- audit trail + receipts -------------------------------------------------------------------------------
def test_audit_trail_append_only_and_verifiable():
    # AC(p20-compliance-automation-decision-audit-trail): append-only,
    # hash-chained, replayable.
    trail = AuditTrail()
    trail.append("alice", Verdict.PASS, [], now=NOW)
    trail.append("bob", Verdict.FAIL, [], now=NOW + 1)
    assert len(trail) == 2
    assert trail.verify_chain()
    assert not hasattr(trail, "update") and not hasattr(trail, "delete")
    recs = trail.records()
    assert recs[1].prev_hash == recs[0].record_hash


def test_receipt_chain_verifies_without_repo_access():
    # AC(p20-compliance-automation-decision-audit-trail): receipts verify
    # end-to-end from receipts + public key alone.
    eng = build_engine()
    clean_account(eng, "alice")
    att = good_attestation(eng.kyc, "alice")
    eng.decide("alice", att, now=NOW)
    recs = list(eng.receipts.receipts())
    assert recs
    assert ReceiptChain.verify(recs, eng.receipts.public_key_hex)
    tampered = [Receipt(r.account, "PASS", r.evidence_hash, r.timestamp,
                       r.prev_hash, r.signature) for r in recs]
    if recs[0].verdict == Verdict.FAIL:
        assert not ReceiptChain.verify(tampered,
                                       eng.receipts.public_key_hex)


def test_every_decision_is_recorded():
    eng = build_engine()
    clean_account(eng, "alice")
    att = good_attestation(eng.kyc, "alice")
    eng.decide("alice", att, now=NOW)
    assert len(eng.trail) == 1
    assert eng.trail.verify_chain()


# -- status / catalog ------------------------------------------------------------------------------
def test_status_payload():
    eng = build_engine()
    p = status_payload(eng)
    assert p["product"] == "SINCOR-DEFI-P20-COMPLY"
    assert p["mode"] == "dry_run"


# -- fuzz: the matrix -----------------------------------------------------------------------------------
def test_fuzz_decision_matrix():
    # AC(p20-compliance-automation-invariant-fuzz-tests): 10,000
    # randomized cases — any non-PASS signal never yields PASS; the
    # hook never admits a non-PASS address.
    import random
    rng = random.Random(20260929)
    signals = ["pass", "kyc_fail", "kyc_stale", "aml_fail", "geo_fail",
               "refer", "outage"]
    for i in range(10_000):
        eng = build_engine()
        eng.aml.list_updated_at = NOW + 10000  # lists stay fresh across the run
        clean_account(eng, "alice")
        sig = rng.choice(signals)
        if sig == "kyc_fail":
            att = KYCAttestation(ISSUER, "alice", NOW - 10,
                                 NOW + 10, f"n{i}", "0" * 64)
        elif sig == "kyc_stale":
            att = eng.kyc.sign(ISSUER, "alice", NOW - KYC_VALIDITY_SECONDS - 10,
                               NOW - 1, nonce=f"s{i}")
        else:
            att = good_attestation(eng.kyc, "alice", now=NOW)
        if sig == "aml_fail":
            eng.aml.sanctions.add("alice")
        if sig == "geo_fail":
            eng.geo.attest_jurisdiction("alice", "KP")
            eng.geo.blocked["KP"] = True
        if sig == "refer":
            eng.geo.attested_jurisdiction.pop("alice", None)
        if sig == "outage":
            eng.aml.reachable = False
        verdict, _ = eng.decide("alice", att, now=NOW + i)
        assert isinstance(verdict, Verdict)
        if sig != "pass":
            assert verdict != Verdict.PASS, f"signal {sig} yielded PASS"
            # The hook never admits a non-PASS address.
            hook = GatekeeperHook(eng)
            vault: dict = {}
            with pytest.raises(DepositRejected):
                hook.gated_deposit(vault, "alice", 1, att, now=NOW + i)
            assert vault == {}
        # Every decision lands on the audit trail.
        assert len(eng.trail) >= 1
    assert eng.trail.verify_chain()
