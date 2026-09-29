"""AUD-01 Agent Forensic Audit - tests.

Every test builds its own fixture archive with pack_archive() (the same
canonical hashing the verifier checks), then exercises the toolkit. Tamper
tests mutate the packed bytes to prove verification refuses them.
"""

import io
import json
import zipfile

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from sincor2.obs_skus import forensic_audit as fa


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def signing_key():
    return ed25519.Ed25519PrivateKey.generate()


@pytest.fixture()
def signer_identity():
    return {"type": "org", "id": "sincor-ops"}


def _ts(minute):
    return "2026-09-29T14:%02d:00+00:00" % minute


def rich_entries():
    """Fixture entries covering every rule R1-R6 plus attribution gaps."""
    entries = [
        # auction auc-1: agent-001 commits, never reveals -> R3 (high)
        {"ts": _ts(0), "agent_id": "agent-001", "action": "bid_commit",
         "details": {"auction_id": "auc-1", "note": "commit 40 AXM"}},
        {"ts": _ts(1), "agent_id": "agent-002", "action": "bid_commit",
         "details": {"auction_id": "auc-1"}},
        {"ts": _ts(2), "agent_id": "agent-002", "action": "bid_reveal",
         "details": {"auction_id": "auc-1"}},
        {"ts": _ts(3), "agent_id": "agent-001", "action": "auction_close",
         "details": {"auction_id": "auc-1", "commits": 2, "reveals": 1}},
        # auction auc-2: commits but zero reveals -> R4 (critical)
        {"ts": _ts(4), "agent_id": "agent-003", "action": "bid_commit",
         "details": {"auction_id": "auc-2"}},
        {"ts": _ts(5), "agent_id": "agent-004", "action": "bid_commit",
         "details": {"auction_id": "auc-2"}},
        {"ts": _ts(6), "agent_id": "agent-003", "action": "auction_close",
         "details": {"auction_id": "auc-2", "commits": 2, "reveals": 0}},
        # 3 errors on the same action/agent -> R1 recurring + R6 pattern
        {"ts": _ts(7), "agent_id": "agent-005", "action": "task_submit",
         "details": {"outcome": "error", "exception": "ValueError: bad payload"}},
        {"ts": _ts(8), "agent_id": "agent-005", "action": "task_submit",
         "details": {"outcome": "error", "exception": "ValueError: bad payload"}},
        {"ts": _ts(9), "agent_id": "agent-005", "action": "task_submit",
         "details": {"outcome": "error", "error": "timeout upstream"}},
        # single error elsewhere -> R1 observed
        {"ts": _ts(10), "agent_id": "agent-006", "action": "invoice_render",
         "details": {"outcome": "error", "exception": "KeyError: 'total'"}},
        # timeouts: explicit action + SLA breach -> R2 x2
        {"ts": _ts(11), "agent_id": "agent-007", "action": "timeout",
         "details": {"note": "webhook never returned"}},
        {"ts": _ts(12), "agent_id": "agent-007", "action": "data_pull",
         "details": {"duration_s": 61.5, "sla_s": 30.0}},
        # quality: below threshold + 3 consecutive declines -> R5 x2
        {"ts": _ts(13), "agent_id": "agent-008", "action": "quality_score",
         "details": {"metric": "task_success_rate", "score": 0.95,
                     "threshold": 0.90}},
        {"ts": _ts(14), "agent_id": "agent-008", "action": "quality_score",
         "details": {"metric": "task_success_rate", "score": 0.88,
                     "threshold": 0.90}},
        {"ts": _ts(15), "agent_id": "agent-008", "action": "quality_score",
         "details": {"metric": "task_success_rate", "score": 0.84,
                     "threshold": 0.90}},
        {"ts": _ts(16), "agent_id": "agent-008", "action": "quality_score",
         "details": {"metric": "task_success_rate", "score": 0.79,
                     "threshold": 0.90}},
        # unattributed entry: must be flagged, never assigned
        {"ts": _ts(17), "agent_id": "", "action": "heartbeat",
         "details": {"note": "no agent id recorded"}},
        # clean entries: no findings expected
        {"ts": _ts(18), "agent_id": "agent-009", "action": "task_submit",
         "details": {"outcome": "ok"}},
    ]
    return entries


@pytest.fixture()
def archive_bytes(signing_key, signer_identity):
    return fa.pack_archive(rich_entries(), signer_identity, signing_key,
                           "aud-fixture-001",
                           created_at="2026-09-29T15:00:00+00:00")


@pytest.fixture()
def verified(archive_bytes):
    return fa.ingest_archive(archive_bytes)


def _repack(manifest, entry_lines):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("entries.jsonl", "\n".join(entry_lines))
    return buf.getvalue()


def _load_parts(archive_bytes):
    zf = zipfile.ZipFile(io.BytesIO(archive_bytes))
    manifest = json.loads(zf.read("manifest.json").decode())
    lines = zf.read("entries.jsonl").decode().splitlines()
    return manifest, lines


# ---------------------------------------------------------------------------
# Verification: happy path
# ---------------------------------------------------------------------------

def test_verify_ok(verified):
    assert verified.archive_id == "aud-fixture-001"
    assert len(verified.entries) == 19
    assert verified.head_hash == verified.entries[-1]["entry_hash"]
    check_names = {c["check"] for c in verified.checks}
    assert {"zip_integrity", "manifest_schema", "manifest_signature",
            "entry_count", "hash_chain", "head_hash"} <= check_names


def test_pack_is_deterministic(signing_key, signer_identity):
    a = fa.pack_archive(rich_entries(), signer_identity, signing_key,
                        "aud-fixture-001",
                        created_at="2026-09-29T15:00:00+00:00")
    b = fa.pack_archive(rich_entries(), signer_identity, signing_key,
                        "aud-fixture-001",
                        created_at="2026-09-29T15:00:00+00:00")
    assert a == b


# ---------------------------------------------------------------------------
# Verification: tamper refusal
# ---------------------------------------------------------------------------

def test_tamper_entry_details_refused(archive_bytes):
    manifest, lines = _load_parts(archive_bytes)
    obj = json.loads(lines[4])
    obj["details"]["auction_id"] = "auc-999"  # tamper one entry
    lines[4] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    with pytest.raises(fa.ArchiveVerificationError) as exc:
        fa.ingest_archive(_repack(manifest, lines))
    checks = {f["check"] for f in exc.value.failures}
    assert "entry_hash" in checks


def test_tamper_chain_link_refused(archive_bytes):
    # Change entry 4 AND fix its hash, so the next entry's prev_hash breaks.
    manifest, lines = _load_parts(archive_bytes)
    obj = json.loads(lines[4])
    obj["details"]["auction_id"] = "auc-999"
    prev = json.loads(lines[3])["entry_hash"]
    obj["entry_hash"] = fa.compute_entry_hash(
        obj["seq"], obj["ts"], obj["agent_id"], obj["action"],
        obj["details"], prev)
    lines[4] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    with pytest.raises(fa.ArchiveVerificationError) as exc:
        fa.ingest_archive(_repack(manifest, lines))
    checks = {f["check"] for f in exc.value.failures}
    assert "chain_link" in checks


def test_tamper_manifest_signature_refused(archive_bytes):
    manifest, lines = _load_parts(archive_bytes)
    manifest["archive_id"] = "aud-forged-001"  # signature no longer matches
    with pytest.raises(fa.ArchiveVerificationError) as exc:
        fa.ingest_archive(_repack(manifest, lines))
    checks = {f["check"] for f in exc.value.failures}
    assert "manifest_signature" in checks


def test_tamper_head_hash_detected(archive_bytes, signing_key):
    manifest, lines = _load_parts(archive_bytes)
    manifest["head_hash"] = "0" * 64
    # re-sign so only the head-hash check fires
    manifest.pop("signature")
    manifest = fa.sign_manifest(manifest, signing_key)
    with pytest.raises(fa.ArchiveVerificationError) as exc:
        fa.ingest_archive(_repack(manifest, lines))
    checks = {f["check"] for f in exc.value.failures}
    assert "head_hash" in checks
    assert "manifest_signature" not in checks


def test_wrong_format_tag_refused(archive_bytes, signing_key):
    manifest, lines = _load_parts(archive_bytes)
    manifest["format"] = "something-else"
    manifest.pop("signature")
    manifest = fa.sign_manifest(manifest, signing_key)
    with pytest.raises(fa.ArchiveVerificationError) as exc:
        fa.ingest_archive(_repack(manifest, lines))
    assert "manifest_format" in {f["check"] for f in exc.value.failures}


def test_not_a_zip_refused():
    with pytest.raises(fa.ArchiveVerificationError) as exc:
        fa.ingest_archive(b"definitely not a zip file")
    assert exc.value.failures[0]["check"] == "zip_integrity"


def test_truncated_entries_refused(archive_bytes):
    manifest, lines = _load_parts(archive_bytes)
    lines = lines[:-3]  # drop entries but keep manifest count
    with pytest.raises(fa.ArchiveVerificationError) as exc:
        fa.ingest_archive(_repack(manifest, lines))
    assert "entry_count" in {f["check"] for f in exc.value.failures}


# ---------------------------------------------------------------------------
# Timeline reconstruction
# ---------------------------------------------------------------------------

def test_timeline_reconstruction(verified):
    tl = fa.reconstruct_timeline(verified)
    assert len(tl["items"]) == 19
    seqs = [i["seq"] for i in tl["items"]]
    assert seqs == sorted(seqs)
    assert set(tl["agents"]) == {"agent-001", "agent-002", "agent-003",
                                "agent-004", "agent-005", "agent-006",
                                "agent-007", "agent-008", "agent-009"}
    assert tl["agents"]["agent-005"]["action_counts"]["task_submit"] == 3
    # attribution: the heartbeat entry is unattributed, never assigned
    assert tl["attribution"]["unattributed"] == 1
    assert tl["attribution"]["unattributed_entries"][0]["seq"] == 17
    assert tl["attribution"]["attributed"] == 18
    assert tl["window"]["entry_count"] == 19


# ---------------------------------------------------------------------------
# Failure analysis: rules fire on real evidence
# ---------------------------------------------------------------------------

def _by_rule(findings, rule):
    return [f for f in findings if f["rule"] == rule]


def test_every_finding_cites_real_evidence(verified):
    findings = fa.failure_mode_analysis(verified)
    assert findings, "fixture should produce findings"
    archive_hashes = set(verified.entry_hashes())
    for f in findings:
        assert f["evidence"], "finding %s has no evidence" % f["id"]
        for ev in f["evidence"]:
            assert ev["entry_hash"] in archive_hashes, \
                "finding %s cites unknown hash" % f["id"]


def test_r3_unrevealed_commit(verified):
    r3 = _by_rule(fa.failure_mode_analysis(verified), "R3")
    # agent-001 ghosted auc-1 (closed with one reveal). auc-2's commits are
    # covered by the R4 systemic finding instead - no double counting.
    assert len(r3) == 1
    ghost = r3[0]
    assert ghost["agents"] == ["agent-001"]
    assert ghost["severity"] == "high"
    assert len(ghost["evidence"]) == 2  # commit + close


def test_r4_subsumes_r3_no_double_count(verified):
    findings = fa.failure_mode_analysis(verified)
    r4 = _by_rule(findings, "R4")
    assert len(r4) == 1
    # the two auc-2 commits appear as evidence inside R4, not as R3 findings
    notes = [ev["note"] for ev in r4[0]["evidence"]]
    assert any("unrevealed commit" in n for n in notes)
    assert len(r4[0]["evidence"]) == 3  # close + 2 commits


def test_r4_zero_reveal_close(verified):
    r4 = _by_rule(fa.failure_mode_analysis(verified), "R4")
    assert len(r4) == 1
    assert r4[0]["severity"] == "critical"
    assert "zero reveals" in r4[0]["title"]


def test_r1_and_r6_error_pattern(verified):
    findings = fa.failure_mode_analysis(verified)
    r1 = _by_rule(findings, "R1")
    recurring = [f for f in r1 if "Recurring" in f["title"]]
    assert len(recurring) == 1 and len(recurring[0]["evidence"]) == 3
    r6 = _by_rule(findings, "R6")
    assert len(r6) == 1
    assert r6[0]["severity"] == "high"


def test_r2_timeouts(verified):
    r2 = _by_rule(fa.failure_mode_analysis(verified), "R2")
    assert len(r2) == 2  # explicit timeout action + SLA breach


def test_r5_quality_signals(verified):
    r5 = _by_rule(fa.failure_mode_analysis(verified), "R5")
    kinds = {f["rule_name"] for f in r5}
    assert kinds == {"Quality below threshold", "Quality declining trend"}


def test_findings_sorted_by_severity(verified):
    findings = fa.failure_mode_analysis(verified)
    order = [fa.SEVERITY_ORDER[f["severity"]] for f in findings]
    assert order == sorted(order)


# ---------------------------------------------------------------------------
# Drift section
# ---------------------------------------------------------------------------

def _drift_report():
    return {
        "generated_at": "2026-09-29T15:00:00+00:00",
        "window": {"start": "2026-09-22T00:00:00+00:00",
                   "end": "2026-09-29T00:00:00+00:00"},
        "metrics": [
            {"metric": "task_success_rate", "agent_id": "agent-008",
             "trend": "down", "drift_detected": True, "p_value": 0.031,
             "latest_value": 0.79, "baseline_value": 0.94,
             "notes": "steady slide over 7 days"},
            {"metric": "reveal_rate", "agent_id": "agent-001",
             "trend": "stable", "drift_detected": False, "p_value": None,
             "latest_value": 0.98, "baseline_value": 0.98, "notes": ""},
        ],
        "alerts": [
            {"severity": "high", "metric": "task_success_rate",
             "agent_id": "agent-008",
             "message": "success rate drifted below baseline"},
        ],
    }


def test_drift_section_present():
    section = fa.drift_report_section(_drift_report())
    assert section["available"] is True
    assert len(section["metrics"]) == 2
    assert len(section["alerts"]) == 1
    assert "drift detected in 1 of 2 metrics" in section["summary"]


def test_drift_section_missing_is_honest():
    section = fa.drift_report_section(None)
    assert section["available"] is False
    assert "No drift/quality report was supplied" in section["summary"]
    assert section["metrics"] == []


def test_drift_section_malformed_is_honest():
    section = fa.drift_report_section({"nope": True})
    assert section["available"] is False
    assert "metrics" in section["summary"]


# ---------------------------------------------------------------------------
# Remediation list
# ---------------------------------------------------------------------------

def test_remediations_are_checkable(verified):
    findings = fa.failure_mode_analysis(verified)
    rems = fa.remediation_list(findings)
    assert len(rems) == len(findings)
    ids = [r["id"] for r in rems]
    assert len(set(ids)) == len(ids)
    for r in rems:
        assert r["check_steps"], "remediation %s has no steps" % r["id"]
        assert r["acceptance"], "remediation %s has no acceptance" % r["id"]
        assert r["status"] == "open"
        assert r["finding_ids"], "remediation %s cites no findings" % r["id"]
        assert r["owner_role"]


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def test_report_structure(verified):
    report = fa.build_audit_report(verified, drift_report=_drift_report(),
                                   customer="Acme Corp",
                                   engagement_id="ENG-001")
    assert report["report_type"] == "sincor-agent-forensic-audit"
    assert report["engagement"]["sku"] == "AUD-01"
    assert report["engagement"]["customer"] == "Acme Corp"
    assert report["verification"]["entry_count"] == 19
    assert report["chain_of_custody"]["head_hash"] == verified.head_hash
    assert report["methodology"]["limitations"], "limitations must be stated"
    # JSON-serializable
    json.dumps(report)


def test_report_renders(verified):
    report = fa.build_audit_report(verified, drift_report=_drift_report())
    md = fa.render_markdown(report)
    assert "# Agent Forensic Audit Report" in md
    assert "## Remediation checklist" in md
    assert "## Chain of custody" in md
    assert "- [ ]" in md
    html_doc = fa.render_html(report)
    assert html_doc.startswith("<!DOCTYPE html>")
    assert "Agent Forensic Audit Report" in html_doc
    # positive framing: strengths section exists, no blame language
    assert "What's working" in md


# ---------------------------------------------------------------------------
# End-to-end walkthrough: verify -> reconstruct -> analyze -> report -> render
# ---------------------------------------------------------------------------

def test_walkthrough_end_to_end(archive_bytes):
    """The AUD-01 'working product' verdict: the whole toolkit runs on
    fixture data and every finding cites evidence from the verified archive.
    """
    out = fa.audit_pipeline(archive_bytes, drift_report=_drift_report(),
                            customer="Acme Corp", engagement_id="ENG-001")
    assert out["verified"] is True
    assert out["entry_count"] == 19
    assert out["finding_count"] == len(out["report"]["findings"]) > 0
    assert out["remediation_count"] == out["finding_count"]
    assert out["report_json"]  # structured JSON
    assert out["report_markdown"].startswith("# Agent Forensic Audit Report")
    assert out["report_html"].startswith("<!DOCTYPE html>")

    archive_hashes = set(fa.ingest_archive(archive_bytes).entry_hashes())
    for f in out["report"]["findings"]:
        assert f["evidence"]
        for ev in f["evidence"]:
            assert ev["entry_hash"] in archive_hashes
    for r in out["report"]["remediations"]:
        assert r["check_steps"] and r["acceptance"] and r["status"] == "open"
    assert out["report"]["drift_quality"]["available"] is True


def test_walkthrough_refuses_tampered_archive(archive_bytes):
    manifest, lines = _load_parts(archive_bytes)
    obj = json.loads(lines[0])
    obj["details"]["note"] = "forged"
    lines[0] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    with pytest.raises(fa.ArchiveVerificationError):
        fa.audit_pipeline(_repack(manifest, lines))
