"""AUD-02 Compliance Pack - tests.

Covers the PHI guardrail redaction profile, deterministic paginated
exports, chain-of-custody manifests, and the setup checklist. Every
customer-facing artifact is checked for the honest product boundary:
guardrails + exports, never a certification claim.
"""

import json

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from sincor2.obs_skus import compliance_pack as cp
from sincor2.obs_skus import forensic_audit as fa


@pytest.fixture()
def signing_key():
    return ed25519.Ed25519PrivateKey.generate()


@pytest.fixture()
def phi_records():
    return [
        {"agent_id": "agent-001", "action": "support_note",
         "patient_name": "Jane Doe", "ssn": "123-45-6789",
         "dob": "1975-03-14", "phone": "415-555-0100",
         "email": "jane.doe@example.com",
         "address_line_1": "123 Main St", "city": "Austin", "state": "TX",
         "mrn": "MRN-88421", "insurance_id": "INS-555",
         "note": "Called 415-555-0100; ssn 123-45-6789 confirmed. "
                 "Email jane.doe@example.com for follow-up.",
         "nested": {"subscriber_id": "SUB-9",
                    "history": ["prior call 650-555-0199"]}},
        {"agent_id": "agent-002", "action": "task_submit",
         "outcome": "ok", "note": "routine, no identifiers here"},
    ]


# ---------------------------------------------------------------------------
# Redaction profile
# ---------------------------------------------------------------------------

def test_redact_direct_identifiers(phi_records):
    red, rep = cp.redact_record(phi_records[0])
    assert red["patient_name"] == "[REDACTED:NAME]"
    assert red["ssn"] == "[REDACTED:SSN]"
    assert red["dob"] == "[REDACTED:DOB]"
    assert red["phone"] == "[REDACTED:PHONE]"
    assert red["email"] == "[REDACTED:EMAIL]"
    assert red["address_line_1"] == "[REDACTED:ADDRESS]"
    assert red["mrn"] == "[REDACTED:MRN]"
    assert red["insurance_id"] == "[REDACTED:INSURANCE_ID]"
    # workforce identifiers and limited data are preserved by design
    assert red["agent_id"] == "agent-001"
    assert red["city"] == "Austin"
    assert red["state"] == "TX"
    assert rep["touch_count"] > 0
    assert rep["profile_id"] == "sincor-phi-guardrail"


def test_redact_free_text_patterns(phi_records):
    red, _ = cp.redact_record(phi_records[0])
    assert "415-555-0100" not in red["note"]
    assert "123-45-6789" not in red["note"]
    assert "jane.doe@example.com" not in red["note"]
    assert "[REDACTED:PHONE]" in red["note"]
    assert "[REDACTED:SSN]" in red["note"]
    assert "[REDACTED:EMAIL]" in red["note"]


def test_redact_nested_structures(phi_records):
    red, _ = cp.redact_record(phi_records[0])
    assert red["nested"]["subscriber_id"] == "[REDACTED:INSURANCE_ID]"
    assert "650-555-0199" not in red["nested"]["history"][0]
    assert "[REDACTED:PHONE]" in red["nested"]["history"][0]


def test_redact_report_carries_no_original_values(phi_records):
    _, rep = cp.redact_record(phi_records[0])
    blob = json.dumps(rep)
    for secret in ("Jane Doe", "123-45-6789", "1975-03-14", "415-555-0100",
                   "jane.doe@example.com", "123 Main St", "MRN-88421"):
        assert secret not in blob


def test_redact_batch_report(phi_records):
    reds, agg = cp.redact_records(phi_records)
    assert len(reds) == 2
    assert agg["records"] == 2
    assert agg["total_touches"] > 0
    assert agg["touches_by_rule"]["PHI-01"] >= 1  # ssn field rule
    assert agg["touches_by_rule"]["PHI-T1"] >= 1  # ssn in free text


def test_biometric_fields_dropped_not_tokenized():
    red, rep = cp.redact_record({"agent_id": "a1", "photo_url": "http://x/y.jpg",
                                 "face_id": "abc"})
    assert "photo_url" not in red
    assert "face_id" not in red
    assert any(h["action"] == "drop" for h in rep["fields_touched"])


def test_minimization():
    recs = [{"a": 1, "b": 2, "c": 3}]
    out = cp.apply_minimization(recs, ["a", "c"])
    assert out == [{"a": 1, "c": 3}]


# ---------------------------------------------------------------------------
# Deterministic exports
# ---------------------------------------------------------------------------

def _sample_records():
    return [
        {"seq": 2, "agent_id": "agent-002", "action": "b",
         "details": {"z": [3, 1], "a": 1}},
        {"seq": 1, "agent_id": "agent-001", "action": "a",
         "details": {"z": [3, 1], "a": 1}},
        {"seq": 0, "agent_id": "agent-001", "action": "a",
         "details": {"note": "caf\u00e9"}},
    ]


def test_jsonl_export_deterministic():
    pages_a, info_a = cp.export_jsonl_pages(_sample_records(), page_size=2,
                                            sort_by="seq")
    pages_b, _ = cp.export_jsonl_pages(_sample_records(), page_size=2,
                                       sort_by="seq")
    assert pages_a == pages_b
    assert info_a["page_count"] == 2
    assert info_a["record_count"] == 3
    assert info_a["order"] == "sort_by=seq"
    # sorted by seq: first line of first page is seq 0
    first = json.loads(pages_a[0].decode().splitlines()[0])
    assert first["seq"] == 0
    # unicode is deterministic (ensure_ascii)
    assert "caf\\u00e9" in pages_a[0].decode()


def test_jsonl_pagination_and_hashes():
    pages, info = cp.export_jsonl_pages(_sample_records(), page_size=1)
    assert info["page_count"] == 3
    hashes = cp.page_hashes(pages)
    assert len(hashes) == 3 and len(set(hashes)) == 3
    assert all(len(h) == 64 for h in hashes)


def test_csv_export_deterministic():
    body_a, info_a = cp.export_csv(_sample_records(), sort_by="seq")
    body_b, _ = cp.export_csv(_sample_records(), sort_by="seq")
    assert body_a == body_b
    text = body_a.decode()
    header = text.splitlines()[0]
    cols = header.split(",")
    assert cols == sorted(cols)  # sorted union of flattened keys
    assert "details.z" in cols  # nested flattened with dotted keys
    assert info_a["record_count"] == 3


def test_export_empty_records():
    pages, info = cp.export_jsonl_pages([], page_size=10)
    assert pages == [] and info["record_count"] == 0
    body, cinfo = cp.export_csv([], columns=["a", "b"])
    assert body.decode() == "a,b\n"
    assert cinfo["record_count"] == 0


# ---------------------------------------------------------------------------
# Chain-of-custody manifest
# ---------------------------------------------------------------------------

def _custody(signing_key, pages):
    man = cp.build_custody_manifest(
        "exp-001", "headhash" + "ab" * 28, pages,
        {"type": "org", "id": "sincor-ops"},
        export_info={"record_count": 3, "page_count": len(pages)})
    return cp.sign_custody_manifest(man, signing_key)


def test_custody_manifest_fields(signing_key):
    pages, _ = cp.export_jsonl_pages(_sample_records(), page_size=2)
    man = _custody(signing_key, pages)
    assert man["manifest_type"] == "sincor-export-custody"
    assert man["page_count"] == 2
    assert len(man["page_hashes"]) == 2
    assert len(man["full_export_sha256"]) == 64
    assert man["signature_algorithm"] == "ed25519"
    assert man["signer_public_key_hex"]
    assert "HIPAA" in man["disclaimer"]  # honest boundary embedded
    assert man["redaction_profile"]["profile_id"] == "sincor-phi-guardrail"


def test_custody_signature_verifies(signing_key):
    pages, _ = cp.export_jsonl_pages(_sample_records(), page_size=2)
    man = _custody(signing_key, pages)
    pub = signing_key.public_key().public_bytes_raw().hex()
    ok, detail = cp.verify_custody_manifest(man, public_key_hex=pub)
    assert ok, detail
    # and without pinning (embedded key)
    ok2, _ = cp.verify_custody_manifest(man)
    assert ok2


def test_custody_wrong_key_pinning_refused(signing_key):
    pages, _ = cp.export_jsonl_pages(_sample_records(), page_size=2)
    man = _custody(signing_key, pages)
    other = ed25519.Ed25519PrivateKey.generate()
    other_pub = other.public_key().public_bytes_raw().hex()
    ok, detail = cp.verify_custody_manifest(man, public_key_hex=other_pub)
    assert not ok
    assert "pinned key" in detail


def test_custody_tampered_manifest_refused(signing_key):
    pages, _ = cp.export_jsonl_pages(_sample_records(), page_size=2)
    man = _custody(signing_key, pages)
    man["record_count"] = 999  # tamper after signing
    ok, _ = cp.verify_custody_manifest(man)
    assert not ok


def test_verify_export_pages(signing_key):
    pages, _ = cp.export_jsonl_pages(_sample_records(), page_size=1)
    man = _custody(signing_key, pages)
    ok, problems = cp.verify_export_pages(pages, man)
    assert ok and problems == []
    tampered = [pages[0] + b" ", pages[1], pages[2]]
    ok2, problems2 = cp.verify_export_pages(tampered, man)
    assert not ok2 and problems2


# ---------------------------------------------------------------------------
# Setup checklist
# ---------------------------------------------------------------------------

def test_checklist_structure():
    cl = cp.setup_checklist(customer="Acme Corp", engagement_id="ENG-002")
    assert cl["customer"] == "Acme Corp"
    assert "$2,500" in cl["pricing"] and "$999/month" in cl["pricing"]
    section_ids = [s["id"] for s in cl["sections"]]
    assert section_ids == ["data-sources", "retention", "reviewers",
                           "verification"]
    for section in cl["sections"]:
        assert section["items"]
        for it in section["items"]:
            assert it["title"] and it["detail"] and it["status"] == "open"


def test_checklist_out_of_scope_is_explicit():
    cl = cp.setup_checklist()
    excluded = " ".join(cl["scope"]["not_included"])
    assert "certification" in excluded
    assert "Legal advice" in excluded
    assert "Business Associate Agreement" in excluded
    assert "HIPAA" in cl["disclaimer"]
    md = cp.render_checklist_markdown(cl)
    assert "## Scope" in md
    assert "### Not included" in md
    assert "## Product boundaries" in md


# ---------------------------------------------------------------------------
# Honest boundaries: no certification claims anywhere customer-facing
# ---------------------------------------------------------------------------

FORBIDDEN_CLAIMS = ("HIPAA compliant", "HIPAA-compliant", "HIPAA certified",
                    "HITECH compliant", "SOC 2 compliant", "SOC2 compliant",
                    "guarantees compliance", "ensures compliance")


def _customer_facing_strings():
    cl = cp.setup_checklist(customer="Acme Corp")
    pages, _ = cp.export_jsonl_pages(_sample_records(), page_size=5)
    man = cp.build_custody_manifest("exp-x", "00" * 32, pages,
                                    {"type": "org", "id": "sincor-ops"})
    return [
        cp.COMPLIANCE_DISCLAIMER,
        cp.PRODUCT_SCOPE,
        cp.render_checklist_markdown(cl),
        json.dumps(man),
        cp.REDACTION_PROFILE["description"],
    ]


def test_no_certification_claims():
    for text in _customer_facing_strings():
        for claim in FORBIDDEN_CLAIMS:
            assert claim.lower() not in text.lower(), \
                "forbidden claim %r found in customer-facing copy" % claim


def test_disclaimer_states_guardrails_only():
    assert "technical guardrails and export formats" in \
        cp.COMPLIANCE_DISCLAIMER.lower() or \
        "Technical guardrails and export formats" in cp.COMPLIANCE_DISCLAIMER
    assert "does not provide" in cp.COMPLIANCE_DISCLAIMER


# ---------------------------------------------------------------------------
# AUD-01 + AUD-02 combined walkthrough: audit, redact, export, custody
# ---------------------------------------------------------------------------

def test_combined_walkthrough(signing_key):
    """End-to-end across both SKUs: pack -> verify -> analyze -> report,
    then redact timeline records -> deterministic export -> signed custody
    manifest -> re-verify. The 'working product' verdict for B5.
    """
    entries = [
        {"ts": "2026-09-29T14:00:00+00:00", "agent_id": "agent-001",
         "action": "support_note",
         "details": {"patient_name": "Jane Doe", "note": "called 415-555-0100"}},
        {"ts": "2026-09-29T14:05:00+00:00", "agent_id": "agent-001",
         "action": "task_submit", "details": {"outcome": "ok"}},
    ]
    blob = fa.pack_archive(entries, {"type": "org", "id": "sincor-ops"},
                           signing_key, "aud-combined-001")
    out = fa.audit_pipeline(blob, customer="Acme Corp")
    assert out["verified"] is True

    # AUD-02: redact the timeline records, export, custody
    tl = fa.reconstruct_timeline(fa.ingest_archive(blob))
    records = [{"seq": i["seq"], "ts": i["ts"], "agent_id": i["agent_id"],
                "action": i["action"], **i["details"]} for i in tl["items"]]
    redacted, agg = cp.redact_records(records)
    assert agg["total_touches"] > 0
    assert "Jane Doe" not in json.dumps(redacted)

    pages_a, info = cp.export_jsonl_pages(redacted, page_size=10,
                                          sort_by="seq")
    pages_b, _ = cp.export_jsonl_pages(redacted, page_size=10, sort_by="seq")
    assert pages_a == pages_b  # deterministic

    man = cp.build_custody_manifest(
        "exp-combined-001", out["report"]["chain_of_custody"]["head_hash"],
        pages_a, {"type": "org", "id": "sincor-ops"}, export_info=info)
    man = cp.sign_custody_manifest(man, signing_key)
    ok, detail = cp.verify_custody_manifest(man)
    assert ok, detail
    ok_pages, problems = cp.verify_export_pages(pages_a, man)
    assert ok_pages and not problems
