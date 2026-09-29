"""Oracle tests for the P20 Compliance Automation build.

Covers src/sincor2/defi/compliance_automation.py — the ComplianceOracle
itself: the eliminated unset-oracle (fail-open) path, deterministic
reference-oracle construction from labeled fixtures, pluggable plugins,
registry-update cache invalidation, reviewer REFER resolution, and the
adversarial matrix (unreachable providers, stale lists, conflicting
signals, expired overrides). Pure logic, no chain.

The reference oracle's data is synthetic and labeled reference-only;
these tests assert that labeling holds.
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
    DRY_RUN,
    KYC_VALIDITY_SECONDS,
    SCREENING_CACHE_SECONDS,
    TAINT_MAX_HOPS,
    ComplianceOracle,
    DecisionEngine,
    DepositRejected,
    GatekeeperHook,
    OraclePlugin,
    PluginResult,
    ReceiptChain,
    Verdict,
    build_reference_oracle,
    load_reference_data,
    status_payload,
    _commit,
)

NOW = 1_800_000_000.0


def ref_engine(now: float = NOW):
    oracle = build_reference_oracle(now=now)
    return DecisionEngine(oracle, guardian="ref-guardian"), oracle


def ref_attestation(oracle: ComplianceOracle, subject: str,
                    now: float = NOW,
                    issuer: str = "ref-kyc-issuer-alpha",
                    nonce: str | None = None):
    return oracle.kyc.sign(
        issuer, subject, now - 10, now - 10 + KYC_VALIDITY_SECONDS,
        nonce or f"nonce-{subject}-{int(now)}")


def clean(oracle: ComplianceOracle, account: str) -> None:
    oracle.geo.attest_jurisdiction(account, "US")


# -- the defer path is gone -------------------------------------------------
def test_engine_requires_configured_oracle():
    with pytest.raises(ValueError):
        DecisionEngine(None, guardian="g")
    with pytest.raises(TypeError):
        DecisionEngine("not-an-oracle", guardian="g")


def test_no_fail_open_branch_in_module():
    # The unset-oracle defer from ComplianceGuard.sol isAllowed() has no
    # counterpart here: the engine cannot be constructed without an
    # oracle (proven by test_engine_requires_configured_oracle), and
    # evaluate() always fans out to every registered plugin — no
    # code path can skip screening and return PASS.
    eng, oracle = ref_engine()
    assert eng.oracle is oracle
    assert {p.name for p in oracle.plugins} >= {"kyc", "aml", "geo"}
    clean(oracle, "REF-CLEAN-0001")
    att = ref_attestation(oracle, "REF-CLEAN-0001")
    verdict, results = eng.decide("REF-CLEAN-0001", att, now=NOW)
    assert verdict == Verdict.PASS
    assert {r.plugin for r in results} >= {"kyc", "aml", "geo"}
    src = Path(ROOT, "src/sincor2/defi/compliance_automation.py").read_text()
    assert "oracleEnabled" not in src  # the .sol fail-open flag, absent here


# -- reference data is labeled synthetic ------------------------------------
def test_reference_fixture_is_labeled():
    data = load_reference_data()
    assert data["reference_only"] is True
    assert "NOT real" in data["label"]
    assert all(s.startswith("REF-") for s in data["sanctions"])
    assert all(i.startswith("ref-") for i in data["issuers"])
    assert data["geo_admin"].startswith("ref-")


def test_reference_oracle_is_deterministic():
    e1, _ = ref_engine()
    e2, _ = ref_engine()
    accounts = ["REF-CLEAN-0001", "REF-SDN-0001", "REF-TAINT-2HOP-0001",
                "REF-TAINT-4HOP-0001", "REF-UNKNOWN-9999"]
    for acct in accounts:
        clean(e1.oracle, acct)
        clean(e2.oracle, acct)
        a1 = ref_attestation(e1.oracle, acct, nonce=f"n1-{acct}")
        a2 = ref_attestation(e2.oracle, acct, nonce=f"n1-{acct}")
        v1, r1 = e1.decide(acct, a1, now=NOW)
        v2, r2 = e2.decide(acct, a2, now=NOW)
        assert v1 == v2
        assert [r.evidence_hash for r in r1] == [r.evidence_hash for r in r2]


def test_reference_oracle_flagged():
    _, oracle = ref_engine()
    assert oracle.reference_only is True
    assert oracle.health()["reference_only"] is True


# -- pluggable --------------------------------------------------------------
class _DenyAll(OraclePlugin):
    name = "denyall"
    version = "denyall-v1"

    def evaluate(self, ctx):
        return PluginResult("denyall", Verdict.FAIL, _commit(ctx.account),
                            self.version, "policy deny")

    def health(self):
        return {"ok": True}


def test_extra_plugin_participates():
    eng, oracle = ref_engine()
    clean(oracle, "REF-CLEAN-0001")
    att = ref_attestation(oracle, "REF-CLEAN-0001")
    v, _ = eng.decide("REF-CLEAN-0001", att, now=NOW)
    assert v == Verdict.PASS

    from src.sincor2.defi.compliance_automation import (
        AMLAdapter, GeoRegistry, KYCAdapter)
    kyc = KYCAdapter({"i": b"0" * 32})
    aml = AMLAdapter(set(), {}, list_updated_at=NOW)
    geo = GeoRegistry(admin="a")
    strict = ComplianceOracle(kyc, aml, geo, extra_plugins=[_DenyAll()])
    eng2 = DecisionEngine(strict, guardian="g")
    geo.attest_jurisdiction("bob", "US")
    att2 = kyc.sign("i", "bob", NOW - 10, NOW - 10 + KYC_VALIDITY_SECONDS,
                    nonce="n-bob")
    v2, results = eng2.decide("bob", att2, now=NOW)
    assert v2 == Verdict.FAIL
    assert any(r.plugin == "denyall" for r in results)
    assert "denyall" in strict.health()["plugins"]


def test_non_plugin_rejected():
    from src.sincor2.defi.compliance_automation import (
        AMLAdapter, GeoRegistry, KYCAdapter)
    with pytest.raises(TypeError):
        ComplianceOracle(KYCAdapter({}), AMLAdapter(set(), {}),
                         GeoRegistry(admin="a"), extra_plugins=[object()])


# -- adversarial: unreachable providers -------------------------------------
@pytest.mark.parametrize("plugin", ["kyc", "aml", "geo"])
def test_unreachable_provider_fails_closed(plugin):
    eng, oracle = ref_engine()
    clean(oracle, "REF-CLEAN-0001")
    setattr({"kyc": oracle.kyc, "aml": oracle.aml,
             "geo": oracle.geo}[plugin], "reachable", False)
    att = ref_attestation(oracle, "REF-CLEAN-0001")
    verdict, results = eng.decide("REF-CLEAN-0001", att, now=NOW)
    assert verdict == Verdict.FAIL
    hit = next(r for r in results if r.plugin == plugin)
    assert "unreachable" in hit.detail or "error" in hit.detail
    assert len(eng.trail) == 1  # the outage decision is recorded


# -- adversarial: stale lists -----------------------------------------------
def test_stale_lists_fail_closed():
    eng, oracle = ref_engine()
    clean(oracle, "REF-CLEAN-0001")
    oracle.aml.list_updated_at = time.time() - 50 * 3600
    att = ref_attestation(oracle, "REF-CLEAN-0001")
    verdict, results = eng.decide("REF-CLEAN-0001", att)
    assert verdict == Verdict.FAIL
    aml = next(r for r in results if r.plugin == "aml")
    assert aml.verdict == Verdict.STALE


def test_freshness_alert_between_24_and_48h():
    eng, oracle = ref_engine()
    clean(oracle, "REF-CLEAN-0001")
    oracle.aml.list_updated_at = time.time() - 30 * 3600
    att = ref_attestation(oracle, "REF-CLEAN-0001")
    verdict, results = eng.decide("REF-CLEAN-0001", att)
    assert verdict == Verdict.PASS  # alert, not a block
    aml = next(r for r in results if r.plugin == "aml")
    assert "freshness alert" in aml.detail
    assert oracle.health()["plugins"]["aml"]["freshness"] == "alert"


# -- adversarial: conflicting signals ---------------------------------------
def test_conflicting_signals_fail():
    eng, oracle = ref_engine()
    # kyc PASS + aml FAIL
    clean(oracle, "REF-SDN-0001")
    att = ref_attestation(oracle, "REF-SDN-0001")
    v, _ = eng.decide("REF-SDN-0001", att, now=NOW)
    assert v == Verdict.FAIL

    # aml PASS + kyc FAIL (forged attestation)
    eng2, oracle2 = ref_engine()
    clean(oracle2, "REF-CLEAN-0001")
    good = ref_attestation(oracle2, "REF-CLEAN-0001")
    from src.sincor2.defi.compliance_automation import KYCAttestation
    forged = KYCAttestation(good.issuer, good.subject, good.issued_at,
                            good.expires_at, "fresh-nonce", "0" * 64)
    v2, _ = eng2.decide("REF-CLEAN-0001", forged, now=NOW)
    assert v2 == Verdict.FAIL

    # kyc+aml PASS + geo FAIL (genesis-blocked jurisdiction)
    eng3, oracle3 = ref_engine()
    oracle3.geo.attest_jurisdiction("REF-CLEAN-0001", "KP")
    att3 = ref_attestation(oracle3, "REF-CLEAN-0001")
    v3, _ = eng3.decide("REF-CLEAN-0001", att3, now=NOW)
    assert v3 == Verdict.FAIL


def test_unknown_never_passes():
    # No attestation, no jurisdiction, unknown to every list -> FAIL,
    # never PASS. There is no "unknown -> pass" path.
    eng, _ = ref_engine()
    verdict, results = eng.decide("REF-UNKNOWN-9999", None, now=NOW)
    assert verdict == Verdict.FAIL
    assert any(r.verdict == Verdict.FAIL for r in results)


# -- adversarial: expired override ------------------------------------------
def test_expired_override_blocks_and_needs_new_reason():
    eng, oracle = ref_engine()
    clean(oracle, "REF-SDN-0001")
    att = ref_attestation(oracle, "REF-SDN-0001")
    eng.grant_override("REF-SDN-0001", "aml", "manual review cleared",
                       caller="ref-guardian", now=NOW)
    v1, _ = eng.decide("REF-SDN-0001", att, now=NOW)
    assert v1 == Verdict.PASS
    # Expired: the same override cannot be silently renewed.
    v2, _ = eng.decide("REF-SDN-0001",
                       ref_attestation(oracle, "REF-SDN-0001",
                                       nonce="n2", now=NOW + 24 * 3600 + 1),
                       now=NOW + 24 * 3600 + 1)
    assert v2 == Verdict.FAIL
    assert not hasattr(eng, "renew_override")
    with pytest.raises(ValueError):
        eng.grant_override("REF-SDN-0001", "aml", "",
                           caller="ref-guardian", now=NOW)
    # A new reason re-grants.
    eng.grant_override("REF-SDN-0001", "aml", "second review, new evidence",
                       caller="ref-guardian", now=NOW + 24 * 3600 + 2)
    v3, _ = eng.decide("REF-SDN-0001",
                       ref_attestation(oracle, "REF-SDN-0001",
                                       nonce="n3", now=NOW + 24 * 3600 + 2),
                       now=NOW + 24 * 3600 + 2)
    assert v3 == Verdict.PASS


# -- cache invalidation on registry updates ---------------------------------
def test_sanctions_update_invalidates_cache():
    eng, oracle = ref_engine()
    clean(oracle, "REF-CLEAN-0001")
    att = ref_attestation(oracle, "REF-CLEAN-0001")
    assert eng.decide("REF-CLEAN-0001", att, now=NOW)[0] == Verdict.PASS
    data = load_reference_data()
    oracle.update_sanctions(set(data["sanctions"]) | {"REF-CLEAN-0001"},
                            now=NOW + 10)
    v, _ = eng.decide("REF-CLEAN-0001",
                      ref_attestation(oracle, "REF-CLEAN-0001",
                                      nonce="n2", now=NOW + 10),
                      now=NOW + 10)
    assert v == Verdict.FAIL  # no manual invalidate_cache() call


def test_fund_flow_update_invalidates_cache():
    eng, oracle = ref_engine()
    clean(oracle, "REF-CLEAN-0001")
    att = ref_attestation(oracle, "REF-CLEAN-0001")
    assert eng.decide("REF-CLEAN-0001", att, now=NOW)[0] == Verdict.PASS
    data = load_reference_data()
    flows = {a: [(c, set(t)) for c, t in e]
             for a, e in data["fund_flows"].items()}
    flows["REF-CLEAN-0001"] = [("REF-MIXER-0001", {"mixer"})]
    oracle.update_fund_flows(flows, now=NOW + 10)
    v, _ = eng.decide("REF-CLEAN-0001",
                      ref_attestation(oracle, "REF-CLEAN-0001",
                                      nonce="n2", now=NOW + 10),
                      now=NOW + 10)
    assert v == Verdict.FAIL


def test_issuer_revocation_invalidates_cache():
    eng, oracle = ref_engine()
    clean(oracle, "REF-CLEAN-0001")
    att = ref_attestation(oracle, "REF-CLEAN-0001")
    assert eng.decide("REF-CLEAN-0001", att, now=NOW)[0] == Verdict.PASS
    # Sign the next attestation BEFORE revoking: the issuer key is gone
    # afterwards, so the presented attestation can no longer verify.
    att2 = ref_attestation(oracle, "REF-CLEAN-0001", nonce="n2",
                           now=NOW + 10)
    oracle.revoke_issuer("ref-kyc-issuer-alpha")
    v, results = eng.decide("REF-CLEAN-0001", att2, now=NOW + 10)
    assert v == Verdict.FAIL
    kyc = next(r for r in results if r.plugin == "kyc")
    assert "not allowlisted" in kyc.detail


def test_geo_execute_invalidates_cache_but_propose_does_not():
    eng, oracle = ref_engine()
    clean(oracle, "REF-CLEAN-0001")
    att = ref_attestation(oracle, "REF-CLEAN-0001")
    assert eng.decide("REF-CLEAN-0001", att, now=NOW)[0] == Verdict.PASS
    oracle.geo_propose_update("US", True, caller="ref-geo-admin", now=NOW)
    # Proposing changes nothing yet: the cached PASS is still correct.
    v, _ = eng.decide("REF-CLEAN-0001",
                      ref_attestation(oracle, "REF-CLEAN-0001",
                                      nonce="n2", now=NOW + 10),
                      now=NOW + 10)
    assert v == Verdict.PASS
    oracle.geo_execute_update(caller="ref-geo-admin",
                              now=NOW + 48 * 3600 + 1)
    v2, _ = eng.decide("REF-CLEAN-0001",
                       ref_attestation(oracle, "REF-CLEAN-0001",
                                       nonce="n3", now=NOW + 48 * 3600 + 2),
                       now=NOW + 48 * 3600 + 2)
    assert v2 == Verdict.FAIL


def test_direct_adapter_mutation_bypasses_invalidation():
    # Documents the contract: registry mutations must go through the
    # oracle API. Direct adapter mutation does not notify listeners.
    eng, oracle = ref_engine()
    clean(oracle, "REF-CLEAN-0001")
    att = ref_attestation(oracle, "REF-CLEAN-0001")
    assert eng.decide("REF-CLEAN-0001", att, now=NOW)[0] == Verdict.PASS
    oracle.aml.sanctions.add("REF-CLEAN-0001")  # bypasses the oracle API
    v, _ = eng.decide("REF-CLEAN-0001",
                      ref_attestation(oracle, "REF-CLEAN-0001",
                                      nonce="n2", now=NOW + 10),
                      now=NOW + 10)
    assert v == Verdict.PASS  # stale cache served: use update_sanctions()


# -- REFER resolution -------------------------------------------------------
def test_refer_blocks_hook_until_reviewer_resolves():
    eng, oracle = ref_engine()
    # No jurisdiction attested -> REFER.
    att = ref_attestation(oracle, "REF-NOJURIS-1", nonce="n1")
    v, _ = eng.decide("REF-NOJURIS-1", att, now=NOW)
    assert v == Verdict.REFER
    hook = GatekeeperHook(eng)
    # Fresh attestation: the first decide consumed att's nonce.
    hook_att = ref_attestation(oracle, "REF-NOJURIS-1", nonce="n1b",
                               now=NOW)
    with pytest.raises(DepositRejected) as exc:
        hook.gated_deposit({}, "REF-NOJURIS-1", 100, hook_att, now=NOW)
    assert exc.value.code == "COMPLY_REFER"

    before = len(eng.trail)
    eng.resolve_referral("REF-NOJURIS-1", reviewer="officer-1",
                         approve=True, reason="manual KYC review passed",
                         now=NOW + 5)
    assert len(eng.trail) == before + 1  # resolution itself is recorded

    v2, results = eng.decide("REF-NOJURIS-1",
                             ref_attestation(oracle, "REF-NOJURIS-1",
                                             nonce="n2", now=NOW + 5),
                             now=NOW + 5)
    assert v2 == Verdict.PASS
    assert any(r.plugin == "reviewer" and "approved" in r.detail
               for r in results)

    # One-shot: the approval is not cached. The blocking condition
    # persists, so the next decision returns to REFER.
    v3, _ = eng.decide("REF-NOJURIS-1",
                       ref_attestation(oracle, "REF-NOJURIS-1",
                                       nonce="n3", now=NOW + 6),
                       now=NOW + 6)
    assert v3 == Verdict.REFER


def test_reviewer_rejection_fails():
    eng, oracle = ref_engine()
    att = ref_attestation(oracle, "REF-NOJURIS-2", nonce="n1")
    assert eng.decide("REF-NOJURIS-2", att, now=NOW)[0] == Verdict.REFER
    eng.resolve_referral("REF-NOJURIS-2", reviewer="officer-1",
                         approve=False, reason="documents insufficient",
                         now=NOW + 5)
    v, results = eng.decide("REF-NOJURIS-2",
                            ref_attestation(oracle, "REF-NOJURIS-2",
                                            nonce="n2", now=NOW + 5),
                            now=NOW + 5)
    assert v == Verdict.FAIL
    assert any(r.plugin == "reviewer" and "rejected" in r.detail
               for r in results)


def test_resolution_requires_reviewer_and_reason():
    eng, _ = ref_engine()
    with pytest.raises(ValueError):
        eng.resolve_referral("x", reviewer="", approve=True,
                             reason="r", now=NOW)
    with pytest.raises(ValueError):
        eng.resolve_referral("x", reviewer="officer-1", approve=True,
                             reason="", now=NOW)


def test_resolution_only_applies_to_refer():
    # A resolution for an account the plugins PASS does nothing: it is
    # not consumed and does not alter the verdict.
    eng, oracle = ref_engine()
    clean(oracle, "REF-CLEAN-0001")
    eng.resolve_referral("REF-CLEAN-0001", reviewer="officer-1",
                         approve=False, reason="stale note", now=NOW)
    att = ref_attestation(oracle, "REF-CLEAN-0001")
    v, results = eng.decide("REF-CLEAN-0001", att, now=NOW)
    assert v == Verdict.PASS
    assert not any(r.plugin == "reviewer" for r in results)


# -- audit: every decision recorded, tamper-evident --------------------------
def test_every_decision_exactly_one_record():
    eng, oracle = ref_engine()
    clean(oracle, "REF-CLEAN-0001")
    for i, acct in enumerate(["REF-CLEAN-0001", "REF-SDN-0001"]):
        clean(oracle, acct)
        eng.decide(acct, ref_attestation(oracle, acct, nonce=f"n{i}"),
                   now=NOW + i)
    assert len(eng.trail) == 2
    assert eng.trail.verify_chain()


def test_trail_tamper_detected():
    eng, oracle = ref_engine()
    clean(oracle, "REF-SDN-0001")
    eng.decide("REF-SDN-0001", ref_attestation(oracle, "REF-SDN-0001"),
               now=NOW)
    assert eng.trail.verify_chain()
    eng.trail._records[0].verdict = "PASS"  # tamper with the record
    assert not eng.trail.verify_chain()


def test_receipts_verify_offline_and_tamper_fails():
    eng, oracle = ref_engine()
    clean(oracle, "REF-CLEAN-0001")
    eng.decide("REF-CLEAN-0001", ref_attestation(oracle, "REF-CLEAN-0001"),
               now=NOW)
    receipts = list(eng.receipts.receipts())
    assert receipts
    assert ReceiptChain.verify(receipts, eng.receipts.public_key_hex)
    # 1-bit flip in the signature breaks verification.
    bad_sig = ("0" if receipts[-1].signature[0] != "0" else "1") \
        + receipts[-1].signature[1:]
    tampered = [r for r in receipts[:-1]] + [
        type(receipts[-1])(receipts[-1].account, receipts[-1].verdict,
                           receipts[-1].evidence_hash,
                           receipts[-1].timestamp, receipts[-1].prev_hash,
                           bad_sig)]
    assert not ReceiptChain.verify(tampered, eng.receipts.public_key_hex)
    # Wrong public key also fails.
    other = build_reference_oracle(now=NOW)
    other_eng = DecisionEngine(other, guardian="g")
    assert not ReceiptChain.verify(receipts, other_eng.receipts.public_key_hex)


# -- safety: dry-run, no chain ----------------------------------------------
def test_dry_run_default_and_no_chain_surface():
    assert DRY_RUN is True
    src = Path(ROOT, "src/sincor2/defi/compliance_automation.py").read_text()
    for token in ("web3", "eth_account", "eth_utils", "jsonrpc",
                  "send_transaction", "eth_sendTransaction"):
        assert token not in src
    # The hooked vault only mutates the caller-supplied dict.
    eng, oracle = ref_engine()
    clean(oracle, "REF-CLEAN-0001")
    hook = GatekeeperHook(eng)
    vault: dict = {}
    hook.gated_deposit(vault, "REF-CLEAN-0001", 7,
                       ref_attestation(oracle, "REF-CLEAN-0001"), now=NOW)
    assert vault == {"REF-CLEAN-0001": 7}


def test_status_payload_carries_oracle():
    eng, _ = ref_engine()
    p = status_payload(eng)
    assert p["product"] == "SINCOR-DEFI-P20-COMPLY"
    assert p["mode"] == "dry_run"
    assert p["oracle"]["version"] == "p20-oracle-v1"
    assert p["oracle"]["reference_only"] is True
    assert set(p["oracle"]["plugins"]) == {"kyc", "aml", "geo"}
    assert p["chain_valid"] is True
