#!/usr/bin/env python3
"""AUD-02 Compliance Pack - guardrails, exports, and setup checklist.

What this product IS, stated plainly (this copy appears in every
customer-facing artifact this module produces):

  Technical guardrails + export formats. Field-level redaction and
  minimization profiles for sensitive fields, deterministic paginated audit
  exports with a chain-of-custody manifest, and a setup engagement checklist.

What this product IS NOT (also stated in every customer-facing artifact):

  It is not HIPAA/HITECH/SOC 2 certification, attestation, or legal
  compliance. It does not replace qualified legal or compliance counsel, a
  risk assessment, workforce training, or a Business Associate Agreement.
  Guardrails reduce the chance that sensitive data lands in logs and
  exports; they do not make a system compliant.

Design notes
------------
- The redaction profile targets direct identifiers (names, contact details,
  government/insurance IDs, dates of birth, street addresses). Workforce
  identifiers such as agent_id / agent_name are deliberately NOT redacted:
  attribution is the core of the audit product, and agents are workforce
  members, not patients.
- Exports are deterministic: identical inputs produce byte-identical pages,
  so a reviewer can re-hash and compare. Determinism assumes the caller
  passes records in a stable order; when a stable sort key is available the
  helpers sort by it, otherwise input order is preserved and documented in
  the manifest.
- The chain-of-custody manifest records: export id, source archive
  head hash, record/page counts, per-page hashes, full-export hash, signer
  identity, export timestamp, and the redaction profile version. It can be
  signed with ed25519 and verified by a third party holding the public key.
- Nothing here touches the money path, auth, stake/pool ledgers, task
  state, contracts, or credentials.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .forensic_audit import (
    canonical_json, sign_manifest as _sign_manifest,
    verify_manifest_signature as _verify_manifest_signature,
)

try:
    from cryptography.hazmat.primitives.asymmetric import ed25519
except ImportError:  # pragma: no cover
    ed25519 = None


# ---------------------------------------------------------------------------
# Honest product boundaries - embedded in every customer-facing artifact
# ---------------------------------------------------------------------------

COMPLIANCE_DISCLAIMER = (
    "SINCOR Compliance Pack provides technical guardrails and export "
    "formats: field-level redaction/minimization profiles, deterministic "
    "paginated audit exports, and chain-of-custody manifests. It does not "
    "provide HIPAA, HITECH, or SOC 2 certification or attestation, and it "
    "does not constitute legal advice or a compliance determination. "
    "Regulated deployments need qualified legal/compliance counsel, a risk "
    "assessment, workforce training, and executed agreements (e.g. a "
    "Business Associate Agreement where applicable)."
)

PRODUCT_SCOPE = (
    "Included: redaction/minimization logging profile, deterministic JSONL "
    "and CSV audit exports, chain-of-custody manifest (optionally signed), "
    "setup engagement checklist. Not included: certification, attestation, "
    "legal advice, penetration testing, or filing paperwork on your behalf."
)

SETUP_PRICE_LINE = ("Setup engagement: $2,500 one-time. Compliance Pack "
                    "subscription: $999/month. Prices in USD.")


# ---------------------------------------------------------------------------
# PHI guardrail redaction profile
# ---------------------------------------------------------------------------

REDACTED = "[REDACTED:%s]"

# Each rule: id, human name, field regex (matched against dotted field path,
# case-insensitive), action ("redact" replaces the value with a typed token,
# "drop" removes the field), and why it exists.
PHI_FIELD_RULES: List[Dict[str, str]] = [
    {"id": "PHI-01", "name": "Social Security number",
     "field_regex": r"(^|\.)(ssn|social_?security(_number)?)$",
     "action": "redact", "token": "SSN",
     "why": "Direct identifier; never belongs in logs or exports."},
    {"id": "PHI-02", "name": "Date of birth",
     "field_regex": r"(^|\.)(dob|date_?of_?birth|birthdate)$",
     "action": "redact", "token": "DOB",
     "why": "Direct identifier under the HIPAA Privacy Rule's identifier list."},
    {"id": "PHI-03", "name": "Medical record / patient / member ID",
     "field_regex": r"(^|\.)(mrn|medical_?record_?number|patient_?id|member_?id)$",
     "action": "redact", "token": "MRN",
     "why": "Patient identifier; redact before any export leaves the boundary."},
    {"id": "PHI-04", "name": "Phone number",
     "field_regex": r"(^|\.)(phone(_number)?|mobile(_number)?|tel(ephone)?)$",
     "action": "redact", "token": "PHONE",
     "why": "Direct contact identifier."},
    {"id": "PHI-05", "name": "Email address",
     "field_regex": r"(^|\.)(e-?mail(_address)?)$",
     "action": "redact", "token": "EMAIL",
     "why": "Direct contact identifier."},
    {"id": "PHI-06", "name": "Street address",
     "field_regex": r"(^|\.)(address(_line_?\d*|_1|_2)?|street(_address)?)$",
     "action": "redact", "token": "ADDRESS",
     "why": "Street-level address is identifying; city/state/zip are kept "
            "as limited data and can be restricted further by policy."},
    {"id": "PHI-07", "name": "Insurance / policy / subscriber ID",
     "field_regex": r"(^|\.)(insurance_?id|policy_?number|subscriber_?id|group_?number)$",
     "action": "redact", "token": "INSURANCE_ID",
     "why": "Health plan beneficiary identifier."},
    {"id": "PHI-08", "name": "Account numbers",
     "field_regex": r"(^|\.)((bank_)?account_?number|routing_?number)$",
     "action": "redact", "token": "ACCOUNT",
     "why": "Financial identifiers sometimes co-occur with health data."},
    {"id": "PHI-09", "name": "Person names (patient-side)",
     "field_regex": r"(^|\.)(patient_?name|full_?name|first_?name|last_?name|"
                    r"given_?name|family_?name)$",
     "action": "redact", "token": "NAME",
     "why": "Names are direct identifiers. Note: agent_id / agent_name are "
            "workforce identifiers and are deliberately NOT redacted, "
            "because attribution is the core of the audit product."},
    {"id": "PHI-10", "name": "IP address",
     "field_regex": r"(^|\.)(ip_?address|client_?ip|source_?ip)$",
     "action": "redact", "token": "IP",
     "why": "Network identifier that can be linked to an individual."},
    {"id": "PHI-11", "name": "Biometric / photo identifiers",
     "field_regex": r"(^|\.)(photo(_url)?|face_?id|fingerprint|voiceprint)$",
     "action": "drop", "token": "BIOMETRIC",
     "why": "Biometric identifiers; dropped entirely rather than tokenized."},
]

# Free-text patterns applied to string values (catches identifiers pasted
# into notes fields). Applied after field rules.
PHI_TEXT_PATTERNS: List[Dict[str, str]] = [
    {"id": "PHI-T1", "name": "SSN in free text",
     "pattern": r"\b\d{3}-\d{2}-\d{4}\b", "token": "SSN"},
    {"id": "PHI-T2", "name": "Phone number in free text",
     "pattern": r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
     "token": "PHONE"},
    {"id": "PHI-T3", "name": "Email address in free text",
     "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
     "token": "EMAIL"},
]

_COMPILED_FIELD_RULES = [
    (r["id"], re.compile(r["field_regex"], re.IGNORECASE), r["action"],
     r["token"]) for r in PHI_FIELD_RULES
]
_COMPILED_TEXT_PATTERNS = [
    (p["id"], re.compile(p["pattern"]), p["token"])
    for p in PHI_TEXT_PATTERNS
]

REDACTION_PROFILE = {
    "profile_id": "sincor-phi-guardrail",
    "profile_version": 1,
    "description": ("Field-level redaction/minimization profile for "
                    "PHI-like direct identifiers. Technical guardrail only; "
                    "not a certification of any kind."),
    "field_rules": [{"id": r["id"], "name": r["name"], "action": r["action"],
                     "why": r["why"]} for r in PHI_FIELD_RULES],
    "text_patterns": [{"id": p["id"], "name": p["name"]}
                      for p in PHI_TEXT_PATTERNS],
    "not_redacted": [
        "agent_id / agent_name: workforce identifiers; attribution is the "
        "core of the audit product and is preserved by design.",
        "city / state / zip: kept as limited data; restrict further by "
        "policy if your counsel requires it.",
        "Clinical codes (e.g. procedure/diagnosis codes): kept by default "
        "because they carry the analytic value; your data-use policy "
        "decides.",
    ],
    "disclaimer": COMPLIANCE_DISCLAIMER,
}


def _apply_text_patterns(value: str, hits: List[Dict[str, str]],
                         path: str) -> str:
    for pid, rx, token in _COMPILED_TEXT_PATTERNS:
        def _sub(m: "re.Match", _pid=pid, _token=token) -> str:
            hits.append({"path": path, "rule_id": _pid,
                         "action": "redact", "token": _token})
            return REDACTED % _token
        value = rx.sub(_sub, value)
    return value


def redact_record(record: Dict[str, Any],
                  _path: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Apply the PHI guardrail profile to one record (recursive).

    Returns (redacted_record, redaction_report). The report lists every
    touched field path with the rule id - it never includes the original
    values.
    """
    hits: List[Dict[str, str]] = []

    def _walk(obj: Any, path: str) -> Any:
        if isinstance(obj, dict):
            out: Dict[str, Any] = {}
            for k, v in obj.items():
                p = "%s.%s" % (path, k) if path else str(k)
                rule_hit = None
                for rid, rx, action, token in _COMPILED_FIELD_RULES:
                    if rx.search(p):
                        rule_hit = (rid, action, token)
                        break
                if rule_hit:
                    rid, action, token = rule_hit
                    hits.append({"path": p, "rule_id": rid,
                                 "action": action, "token": token})
                    if action == "drop":
                        continue
                    out[k] = REDACTED % token
                else:
                    out[k] = _walk(v, p)
            return out
        if isinstance(obj, list):
            return [_walk(v, "%s[%d]" % (path, i))
                    for i, v in enumerate(obj)]
        if isinstance(obj, str):
            return _apply_text_patterns(obj, hits, path)
        return obj

    redacted = _walk(record, _path)
    report = {"profile_id": REDACTION_PROFILE["profile_id"],
              "profile_version": REDACTION_PROFILE["profile_version"],
              "fields_touched": hits, "touch_count": len(hits)}
    return redacted, report


def redact_records(records: Sequence[Dict[str, Any]]
                   ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Redact a batch; returns (redacted_records, aggregate_report)."""
    out: List[Dict[str, Any]] = []
    total = 0
    by_rule: Dict[str, int] = {}
    for rec in records:
        red, rep = redact_record(rec)
        out.append(red)
        total += rep["touch_count"]
        for h in rep["fields_touched"]:
            by_rule[h["rule_id"]] = by_rule.get(h["rule_id"], 0) + 1
    return out, {"profile_id": REDACTION_PROFILE["profile_id"],
                 "profile_version": REDACTION_PROFILE["profile_version"],
                 "records": len(records), "total_touches": total,
                 "touches_by_rule": by_rule}


def apply_minimization(records: Sequence[Dict[str, Any]],
                       allowed_fields: Sequence[str]) -> List[Dict[str, Any]]:
    """Data minimization: keep only allowlisted top-level fields.

    Anything not on the list is dropped before export. Use for exports
    where the reviewer only needs a subset of columns.
    """
    allowed = set(allowed_fields)
    return [{k: v for k, v in rec.items() if k in allowed}
            for rec in records]

# ---------------------------------------------------------------------------
# Deterministic paginated exports (JSONL + CSV)
# ---------------------------------------------------------------------------

def _sort_key(rec: Dict[str, Any]) -> str:
    return canonical_json(rec).decode("utf-8")


def order_records(records: Sequence[Dict[str, Any]],
                  sort_by: Optional[str] = None) -> List[Dict[str, Any]]:
    """Establish a deterministic record order.

    If sort_by names a top-level key present on every record, sort by it.
    Otherwise preserve input order (documented in the manifest as
    order="input").
    """
    recs = list(records)
    if sort_by and all(isinstance(r, dict) and sort_by in r for r in recs):
        recs.sort(key=lambda r: (str(r[sort_by]), _sort_key(r)))
        return recs
    return recs


def _flatten(record: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for k, v in record.items():
        key = "%s.%s" % (prefix, k) if prefix else str(k)
        if isinstance(v, dict):
            flat.update(_flatten(v, key))
        elif isinstance(v, list):
            flat[key] = canonical_json(v).decode("utf-8")
        else:
            flat[key] = v
    return flat


def export_jsonl_pages(records: Sequence[Dict[str, Any]],
                       page_size: int = 500,
                       sort_by: Optional[str] = None
                       ) -> Tuple[List[bytes], Dict[str, Any]]:
    """Deterministic paginated JSONL export.

    Each page is canonical JSON, one record per line, UTF-8, LF endings.
    Returns (pages, export_info) where export_info records the ordering
    rule so a reviewer can reproduce the bytes exactly.
    """
    if page_size < 1:
        raise ValueError("page_size must be >= 1")
    ordered = order_records(records, sort_by=sort_by)
    pages: List[bytes] = []
    for start in range(0, len(ordered), page_size):
        chunk = ordered[start:start + page_size]
        body = "".join(
            canonical_json(r).decode("utf-8") + "\n" for r in chunk)
        pages.append(body.encode("utf-8"))
    info = {"format": "jsonl", "page_size": page_size,
            "record_count": len(ordered), "page_count": len(pages),
            "order": ("sort_by=%s" % sort_by) if sort_by else "input",
            "encoding": "utf-8", "line_ending": "LF",
            "record_encoding": "canonical JSON (sorted keys)"}
    return pages, info


def export_csv(records: Sequence[Dict[str, Any]],
               columns: Optional[Sequence[str]] = None,
               sort_by: Optional[str] = None) -> Tuple[bytes, Dict[str, Any]]:
    """Deterministic single-file CSV export (flattened, sorted columns).

    Nested objects are flattened with dotted keys; lists become canonical
    JSON strings. Column order is the sorted union of flattened keys unless
    the caller pins `columns`.

    Note: values are exported verbatim. A cell beginning with =, +, - or @
    can be interpreted as a formula by spreadsheet applications (CSV
    formula injection). Reviewers opening exports in Excel/Sheets should
    import with formulas disabled, or the export operator should add a
    quoting policy for their review workflow.
    """
    ordered = order_records(records, sort_by=sort_by)
    flat = [_flatten(r) for r in ordered]
    if columns is None:
        cols = sorted({k for r in flat for k in r.keys()})
    else:
        cols = list(columns)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(cols)
    for r in flat:
        writer.writerow([_csv_cell(r.get(c)) for c in cols])
    body = buf.getvalue().encode("utf-8")
    info = {"format": "csv", "record_count": len(ordered),
            "column_count": len(cols), "columns": cols,
            "order": ("sort_by=%s" % sort_by) if sort_by else "input",
            "encoding": "utf-8", "line_ending": "LF",
            "flattening": "dotted keys; lists as canonical JSON"}
    return body, info


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def page_hashes(pages: Sequence[bytes]) -> List[str]:
    return [hashlib.sha256(p).hexdigest() for p in pages]


# ---------------------------------------------------------------------------
# Chain-of-custody manifest
# ---------------------------------------------------------------------------

def build_custody_manifest(export_id: str,
                           source_archive_head_hash: str,
                           pages: Sequence[bytes],
                           signer_identity: Dict[str, str],
                           redaction_profile_version: Optional[int] = None,
                           export_info: Optional[Dict[str, Any]] = None,
                           exported_at: Optional[str] = None) -> Dict[str, Any]:
    """Build the chain-of-custody manifest for an export.

    Everything a reviewer needs to re-verify: per-page hashes, the hash of
    the concatenated pages, who exported, when, from which archive head,
    and which redaction profile was applied.
    """
    hashes = page_hashes(pages)
    full = hashlib.sha256(b"".join(pages)).hexdigest()
    return {
        "manifest_type": "sincor-export-custody",
        "manifest_version": 1,
        "export_id": export_id,
        "exported_at": exported_at or datetime.now(timezone.utc).isoformat(),
        "source_archive_head_hash": source_archive_head_hash,
        "signer_identity": signer_identity,
        "record_count": (export_info or {}).get("record_count"),
        "page_count": len(pages),
        "page_hashes": hashes,
        "full_export_sha256": full,
        "export_info": export_info or {},
        "redaction_profile": {
            "profile_id": REDACTION_PROFILE["profile_id"],
            "profile_version": (redaction_profile_version
                                if redaction_profile_version is not None
                                else REDACTION_PROFILE["profile_version"]),
        },
        "disclaimer": COMPLIANCE_DISCLAIMER,
    }


def sign_custody_manifest(manifest: Dict[str, Any],
                          private_key: "ed25519.Ed25519PrivateKey"
                          ) -> Dict[str, Any]:
    """Attach an ed25519 signature to a custody manifest.

    The manifest carries its own signer_public_key_hex and the
    signature_algorithm label; the signature covers everything except the
    signature field itself (same scheme as audit archive manifests).
    """
    if ed25519 is None:  # pragma: no cover
        raise RuntimeError("cryptography package is required for signing")
    base = dict(manifest)
    base["signature_algorithm"] = "ed25519"
    base["signer_public_key_hex"] = \
        private_key.public_key().public_bytes_raw().hex()
    return _sign_manifest(base, private_key)


def verify_custody_manifest(manifest: Dict[str, Any],
                            public_key_hex: Optional[str] = None
                            ) -> Tuple[bool, str]:
    """Verify a signed custody manifest.

    If public_key_hex is given, the manifest's embedded signer key must
    match it (key pinning); otherwise the embedded key is used.
    """
    embedded = manifest.get("signer_public_key_hex")
    if public_key_hex is not None and embedded != public_key_hex:
        return False, "manifest signer key does not match the pinned key"
    if not embedded:
        return False, "manifest carries no signer_public_key_hex"
    return _verify_manifest_signature(manifest)


def verify_export_pages(pages: Sequence[bytes],
                        manifest: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Re-hash export pages against the custody manifest. Returns (ok, problems)."""
    problems: List[str] = []
    expected = manifest.get("page_hashes") or []
    if len(pages) != len(expected):
        problems.append("page count %d != manifest %d"
                        % (len(pages), len(expected)))
    for i, (page, want) in enumerate(zip(pages, expected)):
        got = hashlib.sha256(page).hexdigest()
        if got != want:
            problems.append("page %d hash mismatch" % i)
    full = hashlib.sha256(b"".join(pages)).hexdigest()
    if full != manifest.get("full_export_sha256"):
        problems.append("full-export hash mismatch")
    return (not problems, problems)

# ---------------------------------------------------------------------------
# Setup engagement checklist ($2,500 one-time)
# ---------------------------------------------------------------------------

def setup_checklist(customer: Optional[str] = None,
                    engagement_id: Optional[str] = None) -> Dict[str, Any]:
    """Generate the AUD-02 setup engagement checklist.

    A documented, honest artifact: what the $2,500 setup covers, the data
    sources to inventory, the retention policy to define (with counsel),
    the reviewer roles to assign, and - explicitly - what is out of scope.
    Every item is checkable and starts as "open".
    """
    def item(title: str, detail: str,
             owner: str) -> Dict[str, Any]:
        return {"title": title, "detail": detail,
                "suggested_owner": owner, "status": "open"}

    checklist = {
        "checklist_type": "sincor-compliance-pack-setup",
        "checklist_version": 1,
        "customer": customer,
        "engagement_id": engagement_id,
        "pricing": SETUP_PRICE_LINE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "included": [
                "Deploy the PHI guardrail redaction/minimization profile "
                "against the customer's log and export pipeline.",
                "Wire deterministic paginated JSONL/CSV exports with "
                "signed chain-of-custody manifests.",
                "Walk the customer's reviewers through one full export and "
                "verification cycle on their own data.",
                "Hand over this checklist, completed and signed off.",
            ],
            "not_included": [
                "HIPAA, HITECH, or SOC 2 certification, attestation, or "
                "audit - this engagement produces technical guardrails "
                "and exports, not a compliance determination.",
                "Legal advice, regulatory filings, or paperwork prepared "
                "on the customer's behalf.",
                "Penetration testing or vulnerability assessment.",
                "Business Associate Agreements - the customer's counsel "
                "prepares them; we supply the technical description of "
                "the guardrails for the exhibits.",
            ],
        },
        "sections": [
            {
                "id": "data-sources",
                "title": "1. Data sources inventory",
                "detail": ("List every system that feeds agent logs or "
                           "exports. One row per source; nothing ships "
                           "until every row is filled."),
                "items": [
                    item("Source name and owner",
                         "System name, owning team, and a contact who can "
                         "answer data questions.", "customer"),
                    item("Data classes handled",
                         "What kinds of records flow through (e.g. agent "
                         "action logs, task payloads, support transcripts). "
                         "Flag any source that may carry PHI-like fields.",
                         "customer"),
                    item("Volume and retention today",
                         "Rough daily volume and how long data is kept "
                         "now, before the new policy.", "customer"),
                    item("Export boundary",
                         "Where redacted exports leave the customer's "
                         "environment and who receives them.",
                         "customer + SINCOR"),
                ],
            },
            {
                "id": "retention",
                "title": "2. Retention policy",
                "detail": ("Define, per data class, how long data is kept "
                           "and how it is deleted. Finalize with the "
                           "customer's counsel - this checklist records the "
                           "decision, it does not make it."),
                "items": [
                    item("Data classes and retention periods",
                         "Table: data class | retention period | legal/ "
                         "business basis | deletion method. Example basis "
                         "entries are placeholders until counsel signs off.",
                         "customer counsel"),
                    item("Deletion method per class",
                         "How deletion happens (cryptographic erasure, "
                         "table drop, archive purge) and who runs it.",
                         "customer"),
                    item("Export retention",
                         "How long signed export pages and custody "
                         "manifests are kept for re-verification, "
                         "separate from source data retention.",
                         "customer counsel"),
                    item("Policy sign-off",
                         "Named approver and date. The policy is not "
                         "active until this is signed.", "customer"),
                ],
            },
            {
                "id": "reviewers",
                "title": "3. Reviewer roles",
                "detail": ("Name the humans. Exports are only as trustworthy "
                           "as the people who verify them."),
                "items": [
                    item("Export operator",
                         "Runs exports, holds no signing key. Named "
                         "individual, not a shared account.", "customer"),
                    item("Custody signer",
                         "Holds the export signing key (kept in the "
                         "customer's key management, never in the export "
                         "pipeline config).", "customer"),
                    item("Independent reviewer",
                         "Re-verifies page hashes and manifest signatures "
                         "on a sample of exports; must not be the export "
                         "operator.", "customer"),
                    item("Escalation contact",
                         "Who is paged when verification fails or a "
                         "redaction report looks wrong.", "customer"),
                ],
            },
            {
                "id": "verification",
                "title": "4. Verification walkthrough",
                "detail": ("Done live on the customer's data before sign-off."),
                "items": [
                    item("Redaction dry run",
                         "Run redact_records over a sample of real records; "
                         "review the redaction report for missed "
                         "identifiers and over-redaction.", "SINCOR + customer"),
                    item("Export and re-verify",
                         "Produce a paginated export, re-hash every page "
                         "against the custody manifest, and verify the "
                         "manifest signature with the pinned public key.",
                         "SINCOR + customer"),
                    item("Tamper drill",
                         "Flip one byte in a copied export page and confirm "
                         "verify_export_pages reports the mismatch.",
                         "SINCOR + customer"),
                    item("Checklist sign-off",
                         "All items closed or explicitly deferred with a "
                         "date. Signed by the customer approver.",
                         "customer"),
                ],
            },
        ],
        "disclaimer": COMPLIANCE_DISCLAIMER,
        "product_scope": PRODUCT_SCOPE,
    }
    return checklist


def render_checklist_markdown(checklist: Dict[str, Any]) -> str:
    """Render the setup checklist as Markdown for the engagement file."""
    lines: List[str] = []
    lines.append("# Compliance Pack - Setup Checklist")
    lines.append("")
    if checklist.get("customer"):
        lines.append("**Customer:** %s" % checklist["customer"])
    if checklist.get("engagement_id"):
        lines.append("**Engagement:** %s" % checklist["engagement_id"])
    lines.append("**Generated:** %s" % checklist["generated_at"])
    lines.append("**Pricing:** %s" % checklist["pricing"])
    lines.append("")
    lines.append("## Scope")
    lines.append("")
    lines.append("### Included")
    for s in checklist["scope"]["included"]:
        lines.append("- %s" % s)
    lines.append("")
    lines.append("### Not included")
    for s in checklist["scope"]["not_included"]:
        lines.append("- %s" % s)
    lines.append("")
    for section in checklist["sections"]:
        lines.append("## %s" % section["title"])
        lines.append("")
        lines.append(section["detail"])
        lines.append("")
        for it in section["items"]:
            lines.append("### %s" % it["title"])
            lines.append("")
            lines.append(it["detail"])
            lines.append("")
            lines.append("Suggested owner: %s | Status: %s"
                         % (it["suggested_owner"], it["status"]))
            lines.append("")
    lines.append("## Product boundaries")
    lines.append("")
    lines.append(checklist["disclaimer"])
    lines.append("")
    lines.append(checklist["product_scope"])
    lines.append("")
    return "\n".join(lines)
