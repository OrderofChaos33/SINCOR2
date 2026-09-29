#!/usr/bin/env python3
"""AUD-01 Agent Forensic Audit - engagement toolkit.

The wedge product: given a signed audit archive, verify it end to end,
reconstruct an attributable per-agent timeline, classify failure modes from
real evidence, and render a customer-ready report. Every finding cites the
entry hashes it was derived from; a finding with no evidence is treated as a
bug and is never emitted.

ARCHIVE SCHEMA (what ingest_archive() expects)
----------------------------------------------
The archive is a ZIP file containing exactly two members:

  manifest.json
      {
        "format": "sincor-audit-archive",
        "format_version": 1,
        "archive_id": "aud-2026-09-29-001",
        "created_at": "2026-09-29T15:00:00+00:00",
        "chain_algorithm": "sha256",
        "signature_algorithm": "ed25519",
        "signer_identity": {"type": "agent"|"org", "id": "<signer id>"},
        "signer_public_key_hex": "<ed25519 public key, hex>",
        "entry_count": 4,
        "first_seq": 0,
        "head_hash": "<sha256 hex of the last entry>",
        "signature": "<ed25519 hex signature>"
      }

  entries.jsonl
      One JSON object per line, seq ascending from first_seq:
      {
        "seq": 0,
        "ts": "2026-09-29T14:58:01+00:00",
        "agent_id": "agent-001",
        "action": "bid_commit",
        "details": {"auction_id": "auc-7", "...": "anything JSON-serializable"},
        "prev_hash": "GENESIS",
        "entry_hash": "<sha256 hex>"
      }

Hashing:
  entry_hash = sha256(canonical_json({
      "seq","ts","agent_id","action","details","prev_hash"})).hexdigest()
  canonical_json = json.dumps(obj, sort_keys=True, separators=(",",":"),
                              ensure_ascii=True).encode("utf-8")
  The first entry's prev_hash is the literal string "GENESIS".

Signature:
  signature = ed25519_sign(canonical_json(manifest minus the "signature"
  field)) made with the private key matching signer_public_key_hex.

Schema assumptions vs the sibling audit_trail.py (B2, built in parallel):
  - Assumed: manifest carries head_hash, entry_count, signer identity and an
    ed25519 signature over the manifest; entries are hash-chained with
    per-entry sha256.
  - RESOLVED (Track B integration, 2026-09-29): B2's real archive schema is
    now consumed directly by ingest_audit_trail_archive(), which delegates
    chain + manifest verification to obs_skus.audit_trail.AuditTrail
    (keccak256 chain, 32-zero-byte genesis, per-entry Ed25519 signatures).
    The ZIP/sha256 path above remains for archives packed by this module's
    own pack_archive(); it is NOT used for B2 exports.

Trust boundary (stated honestly):
  - Signature verification proves the manifest was signed by the private key
    matching signer_public_key_hex. It does NOT prove who owns that key.
    Binding the key to a real-world signer (key pinning, done out-of-band by
    the engagement operator before the audit) is what makes the signature
    meaningful. An attacker who generates their own keypair can produce a
    perfectly "valid" archive - verification catches tampering, not
    impersonation.

DRIFT REPORT SCHEMA (what drift_report_section() expects)
----------------------------------------------------------
This is the schema the sibling drift_quality.py is expected to produce:

  {
    "generated_at": "2026-09-29T15:00:00+00:00",
    "window": {"start": "...", "end": "..."},
    "metrics": [
      {"metric": "task_success_rate", "agent_id": "agent-001",
       "trend": "down",                 # "up" | "down" | "stable"
       "drift_detected": true,
       "p_value": 0.031,               # float or null
       "latest_value": 0.81, "baseline_value": 0.94,
       "notes": "free text"}
    ],
    "alerts": [
      {"severity": "high", "metric": "task_success_rate",
       "agent_id": "agent-001", "message": "free text"}
    ]
  }

If the drift report is missing or does not match, the section says so
plainly instead of inventing numbers.

FAILURE RULE CATALOG (documented, no invented taxonomies)
----------------------------------------------------------
Rules fire only on evidence present in the archive. Rule IDs are stable.

  R1 exception_cluster   entries whose details mark outcome "error" (or carry
                         an "exception" field), grouped by (agent_id, action).
                         >=3 occurrences -> recurring; else observed once.
  R2 timeout             action == "timeout", or details has both duration_s
                         and sla_s with duration_s > sla_s.
  R3 unrevealed_commit   auction domain: a bid_commit (details: auction_id,
                         agent_id) with no matching bid_reveal for the same
                         (auction_id, agent_id) on or before the auction_close
                         for that auction. Mirrors the sealed-bid flow where a
                         commit without a reveal is a ghosted commitment.
  R4 zero_reveal_close   auction_close with details.commits > 0 and
                         details.reveals == 0.
  R5 quality_decline     quality_score entries: score below details.threshold
                         when a threshold is recorded, or 3 consecutive
                         declining scores for the same (agent_id, metric).
  R6 repeat_failure      same (agent_id, action) failing (R1 evidence) >= 3
                         times - escalated as a pattern rather than incidents.

Every rule cites evidence as [{"seq","ts","entry_hash","note"}]. A rule that
cannot cite at least one entry hash emits nothing.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

try:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.exceptions import InvalidSignature
except ImportError:  # pragma: no cover
    ed25519 = None
    InvalidSignature = Exception

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FORMAT_TAG = "sincor-audit-archive"
FORMAT_VERSION = 1
CHAIN_ALGORITHM = "sha256"
SIGNATURE_ALGORITHM = "ed25519"
GENESIS_PREV = "GENESIS"
MANIFEST_NAME = "manifest.json"
ENTRIES_NAME = "entries.jsonl"

_MANIFEST_REQUIRED = {
    "format", "format_version", "archive_id", "created_at", "chain_algorithm",
    "signature_algorithm", "signer_identity", "signer_public_key_hex",
    "entry_count", "first_seq", "head_hash", "signature",
}
_ENTRY_REQUIRED = {"seq", "ts", "agent_id", "action", "details", "prev_hash",
                   "entry_hash"}

REPORT_TYPE = "sincor-agent-forensic-audit"
REPORT_VERSION = 1

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ArchiveVerificationError(Exception):
    """Raised when an archive cannot be verified. Carries every failed check."""

    def __init__(self, message: str,
                 failures: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.failures = failures or []

    def __str__(self) -> str:  # pragma: no cover - convenience only
        lines = [super().__str__()]
        for f in self.failures:
            lines.append("  - [%s] %s" % (f.get("check"), f.get("detail")))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Canonical hashing / signing primitives
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON encoding used for all hashing and signing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def compute_entry_hash(seq: int, ts: str, agent_id: str, action: str,
                       details: Dict[str, Any], prev_hash: str) -> str:
    payload = {"seq": seq, "ts": ts, "agent_id": agent_id, "action": action,
               "details": details, "prev_hash": prev_hash}
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def manifest_sign_bytes(manifest: Dict[str, Any]) -> bytes:
    """Canonical bytes covered by the manifest signature."""
    unsigned = {k: v for k, v in manifest.items() if k != "signature"}
    return canonical_json(unsigned)


def sign_manifest(manifest: Dict[str, Any],
                  private_key: "ed25519.Ed25519PrivateKey") -> Dict[str, Any]:
    """Return a copy of manifest with a hex ed25519 signature attached."""
    if ed25519 is None:  # pragma: no cover
        raise RuntimeError("cryptography package is required for signing")
    signed = dict(manifest)
    signed["signature"] = private_key.sign(
        manifest_sign_bytes(manifest)).hex()
    return signed


def verify_manifest_signature(manifest: Dict[str, Any]) -> Tuple[bool, str]:
    """Verify the manifest's ed25519 signature. Returns (ok, detail)."""
    if ed25519 is None:  # pragma: no cover
        return False, "cryptography package not available"
    try:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(manifest["signer_public_key_hex"]))
        pub.verify(bytes.fromhex(manifest["signature"]),
                   manifest_sign_bytes(manifest))
        return True, "ed25519 signature valid"
    except (ValueError, KeyError) as exc:
        return False, "malformed signature fields: %s" % exc
    except InvalidSignature:
        return False, "signature does not match manifest bytes"
    except Exception as exc:  # defensive: never let verify crash the audit
        return False, "signature verification error: %s" % exc


# ---------------------------------------------------------------------------
# Archive packing (engagement tooling: package a verified entry list)
# ---------------------------------------------------------------------------

def pack_archive(entries_data: Sequence[Dict[str, Any]],
                 signer_identity: Dict[str, str],
                 private_key: "ed25519.Ed25519PrivateKey",
                 archive_id: str,
                 created_at: Optional[str] = None) -> bytes:
    """Build a signed archive ZIP from raw entry dicts.

    Each entry dict needs: ts, agent_id, action, details (dict). seq,
    prev_hash and entry_hash are assigned here so the chain is always
    well-formed. Deterministic: identical inputs produce identical bytes
    (fixed ZIP member metadata).
    """
    if ed25519 is None:  # pragma: no cover
        raise RuntimeError("cryptography package is required for packing")
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    public_hex = private_key.public_key().public_bytes_raw().hex()

    entries: List[Dict[str, Any]] = []
    prev = GENESIS_PREV
    for i, raw in enumerate(entries_data):
        details = raw.get("details") or {}
        if not isinstance(details, dict):
            raise ValueError("entry %d: details must be a dict" % i)
        eh = compute_entry_hash(i, raw["ts"], raw.get("agent_id", ""),
                                raw["action"], details, prev)
        entries.append({"seq": i, "ts": raw["ts"],
                        "agent_id": raw.get("agent_id", ""),
                        "action": raw["action"], "details": details,
                        "prev_hash": prev, "entry_hash": eh})
        prev = eh

    manifest = {
        "format": FORMAT_TAG,
        "format_version": FORMAT_VERSION,
        "archive_id": archive_id,
        "created_at": created_at,
        "chain_algorithm": CHAIN_ALGORITHM,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "signer_identity": signer_identity,
        "signer_public_key_hex": public_hex,
        "entry_count": len(entries),
        "first_seq": 0,
        "head_hash": entries[-1]["entry_hash"] if entries else GENESIS_PREV,
    }
    manifest = sign_manifest(manifest, private_key)

    buf = io.BytesIO()
    # Fixed metadata -> deterministic bytes for identical inputs.
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in ((MANIFEST_NAME, canonical_json(manifest)),
                              (ENTRIES_NAME, "\n".join(
                                  canonical_json(e).decode("utf-8")
                                  for e in entries).encode("utf-8"))):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, payload)
    return buf.getvalue()

# ---------------------------------------------------------------------------
# ingest_archive: load + verify. Refuses tampered archives explicitly.
# ---------------------------------------------------------------------------

@dataclass
class VerifiedArchive:
    manifest: Dict[str, Any]
    entries: List[Dict[str, Any]]
    archive_sha256: str
    verified_at: str
    checks: List[Dict[str, str]] = field(default_factory=list)

    @property
    def archive_id(self) -> str:
        return str(self.manifest.get("archive_id"))

    @property
    def head_hash(self) -> str:
        return str(self.manifest.get("head_hash"))

    def entry_hashes(self) -> List[str]:
        return [e["entry_hash"] for e in self.entries]


def _fail(failures: List[Dict[str, Any]], check: str, detail: str,
          seq: Any = None) -> None:
    failures.append({"check": check, "detail": detail, "seq": seq})


def ingest_archive(source: Union[str, Path, bytes]) -> VerifiedArchive:
    """Load a signed audit archive and verify it completely.

    Runs, in order: ZIP integrity, manifest schema, signature over the
    manifest, entry count, sequence contiguity, per-entry hash
    recomputation, hash-chain linkage, head-hash match. Any failure raises
    ArchiveVerificationError listing every failed check - the archive is
    refused, never partially trusted.
    """
    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    else:
        raw = bytes(source)
    archive_sha256 = hashlib.sha256(raw).hexdigest()
    failures: List[Dict[str, Any]] = []
    checks: List[Dict[str, str]] = []

    def _ok(name: str, detail: str) -> None:
        checks.append({"check": name, "result": "pass", "detail": detail})

    # 1. ZIP integrity + required members
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        names = set(zf.namelist())
    except zipfile.BadZipFile:
        raise ArchiveVerificationError(
            "Archive refused: not a valid ZIP file.",
            [{"check": "zip_integrity",
              "detail": "bytes do not parse as a ZIP archive", "seq": None}])
    missing = {MANIFEST_NAME, ENTRIES_NAME} - names
    if missing:
        raise ArchiveVerificationError(
            "Archive refused: missing members %s." % sorted(missing),
            [{"check": "zip_members",
              "detail": "missing: %s" % sorted(missing), "seq": None}])
    _ok("zip_integrity", "valid ZIP with manifest.json + entries.jsonl")

    # 2. Manifest schema
    try:
        manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ArchiveVerificationError(
            "Archive refused: manifest.json does not parse.",
            [{"check": "manifest_parse", "detail": str(exc), "seq": None}])
    if not isinstance(manifest, dict):
        raise ArchiveVerificationError(
            "Archive refused: manifest is not a JSON object.",
            [{"check": "manifest_schema",
              "detail": "manifest root is not an object", "seq": None}])
    missing_keys = _MANIFEST_REQUIRED - set(manifest.keys())
    if missing_keys:
        _fail(failures, "manifest_schema",
              "missing required keys: %s" % sorted(missing_keys))
    else:
        _ok("manifest_schema", "all required keys present")
    if manifest.get("format") != FORMAT_TAG:
        _fail(failures, "manifest_format",
              "format tag is %r, expected %r"
              % (manifest.get("format"), FORMAT_TAG))
    else:
        _ok("manifest_format", "format tag %r" % FORMAT_TAG)
    if manifest.get("chain_algorithm") != CHAIN_ALGORITHM:
        _fail(failures, "chain_algorithm",
              "unsupported chain algorithm %r" % manifest.get(
                  "chain_algorithm"))
    else:
        _ok("chain_algorithm", CHAIN_ALGORITHM)
    if manifest.get("signature_algorithm") != SIGNATURE_ALGORITHM:
        _fail(failures, "signature_algorithm",
              "unsupported signature algorithm %r" % manifest.get(
                  "signature_algorithm"))
    else:
        _ok("signature_algorithm", SIGNATURE_ALGORITHM)

    # 3. Manifest signature (only attempted when schema is intact)
    if not missing_keys:
        sig_ok, sig_detail = verify_manifest_signature(manifest)
        if sig_ok:
            _ok("manifest_signature", sig_detail)
        else:
            _fail(failures, "manifest_signature", sig_detail)

    # 4. Entries parse + count
    entries: List[Dict[str, Any]] = []
    try:
        lines = zf.read(ENTRIES_NAME).decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ArchiveVerificationError(
            "Archive refused: entries.jsonl does not decode as UTF-8.",
            [{"check": "entries_parse", "detail": str(exc), "seq": None}])
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            _fail(failures, "entries_parse",
                  "line %d is not valid JSON: %s" % (i, exc), seq=i)
            continue
        if not isinstance(obj, dict):
            _fail(failures, "entries_parse",
                  "line %d is not a JSON object" % i, seq=i)
            continue
        entries.append(obj)
    expected = manifest.get("entry_count")
    if isinstance(expected, int) and len(entries) != expected:
        _fail(failures, "entry_count",
              "manifest declares %d entries, file holds %d"
              % (expected, len(entries)))
    else:
        _ok("entry_count", "%d entries match manifest" % len(entries))

    # 5. Sequence contiguity + per-entry hash + chain linkage
    first_seq = manifest.get("first_seq", 0)
    prev = GENESIS_PREV
    for pos, entry in enumerate(entries):
        seq = first_seq + pos
        if entry.get("seq") != seq:
            _fail(failures, "sequence",
                  "expected seq %d at position %d, found %r"
                  % (seq, pos, entry.get("seq")), seq=entry.get("seq"))
            continue
        missing_e = _ENTRY_REQUIRED - set(entry.keys())
        if missing_e:
            _fail(failures, "entry_schema",
                  "missing keys %s" % sorted(missing_e), seq=seq)
            continue
        details = entry.get("details")
        if not isinstance(details, dict):
            _fail(failures, "entry_schema",
                  "details is not an object", seq=seq)
            continue
        recomputed = compute_entry_hash(
            entry["seq"], entry["ts"], entry.get("agent_id", ""),
            entry["action"], details, entry["prev_hash"])
        if recomputed != entry.get("entry_hash"):
            _fail(failures, "entry_hash",
                  "entry hash mismatch: stored %s recomputed %s"
                  % (str(entry.get("entry_hash"))[:16], recomputed[:16]),
                  seq=seq)
            # Keep walking the claimed chain so every break is reported.
            prev = entry.get("entry_hash") or prev
            continue
        if entry.get("prev_hash") != prev:
            _fail(failures, "chain_link",
                  "prev_hash %s does not match previous entry hash %s"
                  % (str(entry.get("prev_hash"))[:16], str(prev)[:16]),
                  seq=seq)
        prev = entry["entry_hash"]
    if not any(f["check"] in ("sequence", "entry_schema", "entry_hash",
                              "chain_link") for f in failures):
        _ok("hash_chain", "%d entries re-hashed, chain linked" % len(entries))

    # 6. Head hash
    if entries and manifest.get("head_hash") != entries[-1].get("entry_hash"):
        _fail(failures, "head_hash",
              "manifest head %s != last entry hash %s"
              % (str(manifest.get("head_hash"))[:16],
                 str(entries[-1].get("entry_hash"))[:16]))
    elif entries:
        _ok("head_hash", "manifest head matches last entry")

    if failures:
        raise ArchiveVerificationError(
            "Archive refused: %d verification check(s) failed. "
            "The archive cannot be trusted for a forensic audit."
            % len(failures), failures)

    return VerifiedArchive(
        manifest=manifest, entries=entries, archive_sha256=archive_sha256,
        verified_at=datetime.now(timezone.utc).isoformat(), checks=checks)


# ---------------------------------------------------------------------------
# ingest_audit_trail_archive: consume a real OBS-02 (B2) archive directory
# ---------------------------------------------------------------------------

def ingest_audit_trail_archive(archive_dir: Union[str, Path],
                               verify_key_hex: Optional[str] = None
                               ) -> VerifiedArchive:
    """Load and verify an archive exported by ``obs_skus.audit_trail`` (B2).

    B2's real on-disk format (``AuditTrail.export_archive``):

    * directory containing ``manifest.json`` + ``audit_log.jsonl``
    * hash chain: ``entry_hash = keccak256(prev_hash_raw || canonical(body))``
    * genesis ``prev_hash``: 32 zero bytes
    * per-entry Ed25519 signatures over ``entry_hash`` (``sig`` field)
    * manifest: ``sku`` / ``format_version`` / ``hash_alg="keccak-256"`` /
      ``sig_alg="ed25519"`` / ``entries`` / ``head_hash`` / ``verify_key`` /
      ``exported_at_ms`` / ``manifest_sig``

    Chain and manifest verification is delegated to
    :meth:`AuditTrail.verify_archive` — the sibling module is the single
    source of truth for its own crypto; this function only adapts the
    verified entries into the forensic entry shape (``seq``/``ts``/
    ``agent_id``/``action``/``details``/``prev_hash``/``entry_hash``) so
    ``reconstruct_timeline`` and ``failure_mode_analysis`` work unchanged.
    Any verification failure raises :class:`ArchiveVerificationError` —
    the archive is refused, never partially trusted.
    """
    try:
        from .audit_trail import AuditTrail
    except ImportError as exc:  # pragma: no cover - sibling absent
        raise ArchiveVerificationError(
            "Cannot ingest OBS-02 archive: sibling module "
            "obs_skus.audit_trail is not importable: %s" % exc,
            [{"check": "sibling_available",
              "detail": "obs_skus.audit_trail import failed", "seq": None}])

    archive = Path(archive_dir)
    raw_log = archive / "audit_log.jsonl"
    archive_sha256 = (hashlib.sha256(raw_log.read_bytes()).hexdigest()
                      if raw_log.exists() else "")
    report = AuditTrail.verify_archive(archive, verify_key_hex)
    failures = report.get("failures") or []
    checks: List[Dict[str, str]] = []
    if not failures:
        checks.append({"check": "obs02_chain", "result": "pass",
                       "detail": "%d entries re-hashed (keccak256), chain "
                                 "linked from 32-zero-byte genesis"
                                 % report.get("entries", 0)})
        checks.append({"check": "obs02_manifest_pins", "result": "pass",
                       "detail": "entry count + head_hash match manifest"})
        checks.append({"check": "obs02_signatures", "result": "pass",
                       "detail": "%d signed / %d unsigned entries; every "
                                 "present Ed25519 signature verifies"
                                 % (report.get("signed", 0),
                                    report.get("unsigned", 0))})
    if failures:
        raise ArchiveVerificationError(
            "OBS-02 archive refused: %d verification check(s) failed. "
            "The archive cannot be trusted for a forensic audit."
            % len(failures),
            [{"check": f.get("kind", "unknown"),
              "detail": f.get("detail", ""), "seq": f.get("seq")}
             for f in failures])

    manifest = json.loads((archive / "manifest.json").read_text(
        encoding="utf-8"))
    head_hash = report["head_hash"]
    adapted_manifest = dict(manifest)
    adapted_manifest["archive_id"] = "obs02-archive:%s" % head_hash[:16]
    adapted_manifest["format"] = "sincor-obs02-audit-trail"
    adapted_manifest["signed_entries"] = report.get("signed", 0)
    adapted_manifest["unsigned_entries"] = report.get("unsigned", 0)

    entries: List[Dict[str, Any]] = []
    for line in raw_log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        src = json.loads(line)
        ts_ms = src.get("timestamp_ms", 0)
        try:
            ts = datetime.fromtimestamp(ts_ms / 1000,
                                        tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            ts = ""
        entries.append({
            "seq": src.get("seq"),
            "ts": ts,
            "agent_id": src.get("agent_id") or "",
            "action": src.get("action") or "",
            "details": src.get("detail") or {},
            "prev_hash": src.get("prev_hash") or "",
            "entry_hash": src.get("entry_hash") or "",
            "provenance": {
                "sig": src.get("sig"),
                "verify_key": src.get("verify_key"),
                "causal_parent": src.get("causal_parent"),
                "timestamp_ms": ts_ms,
            },
        })

    return VerifiedArchive(
        manifest=adapted_manifest, entries=entries,
        archive_sha256=archive_sha256,
        verified_at=datetime.now(timezone.utc).isoformat(), checks=checks)


# ---------------------------------------------------------------------------
# reconstruct_timeline: attributable per-agent action timeline
# ---------------------------------------------------------------------------

def _summarize(entry: Dict[str, Any]) -> str:
    details = entry.get("details") or {}
    note = details.get("note") or details.get("reason") or ""
    action = entry.get("action", "?")
    if note:
        return "%s - %s" % (action, note)
    interesting = {k: v for k, v in details.items()
                   if k in ("auction_id", "task_id", "score", "metric",
                            "outcome", "duration_s", "commits", "reveals")}
    if interesting:
        bits = ", ".join("%s=%s" % (k, v) for k, v in interesting.items())
        return "%s (%s)" % (action, bits)
    return action


def reconstruct_timeline(archive: VerifiedArchive) -> Dict[str, Any]:
    """Build an attributable per-agent timeline from verified entries.

    Attribution is explicit: entries with a missing or empty agent_id are
    listed as unattributed rather than assigned to anyone.
    """
    items: List[Dict[str, Any]] = []
    agents: Dict[str, Dict[str, Any]] = {}
    unattributed: List[Dict[str, Any]] = []

    for entry in sorted(archive.entries, key=lambda e: e["seq"]):
        agent_id = (entry.get("agent_id") or "").strip()
        item = {"seq": entry["seq"], "ts": entry["ts"],
                "agent_id": agent_id or None, "action": entry["action"],
                "summary": _summarize(entry), "details": entry["details"],
                "entry_hash": entry["entry_hash"]}
        items.append(item)
        if not agent_id:
            unattributed.append({"seq": entry["seq"],
                                 "entry_hash": entry["entry_hash"]})
            continue
        agg = agents.setdefault(agent_id, {
            "agent_id": agent_id, "actions": 0, "first_seen": entry["ts"],
            "last_seen": entry["ts"], "action_counts": {}})
        agg["actions"] += 1
        agg["last_seen"] = entry["ts"]
        agg["action_counts"][entry["action"]] = \
            agg["action_counts"].get(entry["action"], 0) + 1

    window = None
    if items:
        window = {"start": items[0]["ts"], "end": items[-1]["ts"],
                  "entry_count": len(items)}
    return {
        "archive_id": archive.archive_id,
        "window": window,
        "items": items,
        "agents": agents,
        "attribution": {
            "attributed": len(items) - len(unattributed),
            "unattributed": len(unattributed),
            "unattributed_entries": unattributed,
        },
        "integrity": {
            "head_hash": archive.head_hash,
            "archive_sha256": archive.archive_sha256,
            "signer_identity": archive.manifest.get("signer_identity"),
            "verified_at": archive.verified_at,
        },
    }

# ---------------------------------------------------------------------------
# failure_mode_analysis: rule-based classification over real evidence
# ---------------------------------------------------------------------------

def _evidence(entry: Dict[str, Any], note: str = "") -> Dict[str, Any]:
    return {"seq": entry["seq"], "ts": entry["ts"],
            "entry_hash": entry["entry_hash"], "note": note}


def _is_error(entry: Dict[str, Any]) -> bool:
    details = entry.get("details") or {}
    return (str(details.get("outcome", "")).lower() == "error"
            or "exception" in details or "error" in details)


def _is_timeout(entry: Dict[str, Any]) -> bool:
    if entry.get("action") == "timeout":
        return True
    details = entry.get("details") or {}
    try:
        dur = details.get("duration_s")
        sla = details.get("sla_s")
        if dur is not None and sla is not None:
            return float(dur) > float(sla)
    except (TypeError, ValueError):
        return False
    return False


def _rule_exception_clusters(entries: List[Dict[str, Any]],
                             fid: List[int]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for e in entries:
        if _is_error(e):
            groups.setdefault((e.get("agent_id") or "unattributed",
                               e["action"]), []).append(e)
    findings = []
    for (agent_id, action), ev in sorted(groups.items()):
        fid[0] += 1
        recurring = len(ev) >= 3
        findings.append({
            "id": "F-%03d" % fid[0],
            "rule": "R1",
            "rule_name": "Error cluster",
            "severity": "high" if recurring else "medium",
            "title": "%s errors on %s by %s"
                     % ("Recurring" if recurring else "Observed",
                        action, agent_id),
            "description": (
                "%d entr%s record an error outcome for action %r by agent "
                "%r. Each entry below carries its hash so the raw evidence "
                "can be re-pulled from the archive."
                % (len(ev), "ies" if len(ev) != 1 else "y", action,
                   agent_id)),
            "agents": [agent_id],
            "action": action,
            "evidence": [_evidence(e, str((e.get("details") or {}).get(
                "exception") or (e.get("details") or {}).get(
                    "error") or "outcome=error")) for e in ev],
            "recommendation_hint": "Triage the exception text, then add a "
                                   "targeted remediation item.",
        })
    return findings


def _rule_timeouts(entries: List[Dict[str, Any]],
                   fid: List[int]) -> List[Dict[str, Any]]:
    findings = []
    for e in entries:
        if _is_timeout(e):
            details = e.get("details") or {}
            fid[0] += 1
            findings.append({
                "id": "F-%03d" % fid[0],
                "rule": "R2",
                "rule_name": "Timeout",
                "severity": "medium",
                "title": "Timeout on %s by %s"
                         % (e["action"], e.get("agent_id") or "unattributed"),
                "description": (
                    "Action %r exceeded its expected window%s. Timeouts are "
                    "worth distinguishing from errors: the work may have "
                    "succeeded off-record, so downstream state deserves a "
                    "reconciliation check."
                    % (e["action"],
                       " (%.1fs vs %.1fs SLA)"
                       % (details["duration_s"], details["sla_s"])
                       if details.get("duration_s") is not None
                       and details.get("sla_s") is not None else "")),
                "agents": [e.get("agent_id") or "unattributed"],
                "evidence": [_evidence(e, "timeout")],
                "recommendation_hint": "Confirm whether the timed-out work "
                                       "completed and reconcile state.",
            })
    return findings


def _auction_commits(entries: List[Dict[str, Any]]
                     ) -> Dict[Tuple[str, str], Dict[str, Any]]:
    commits: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in entries:
        d = e.get("details") or {}
        if e["action"] == "bid_commit" and d.get("auction_id"):
            commits[(str(d["auction_id"]),
                     e.get("agent_id") or "")] = e
    return commits


def _rule_unrevealed_commits(entries: List[Dict[str, Any]],
                             fid: List[int],
                             skip_auctions: Optional[set] = None
                             ) -> List[Dict[str, Any]]:
    """R3: commit without a matching reveal before close.

    Commits belonging to an auction already covered by an R4 zero-reveal
    finding are skipped: the systemic finding subsumes the per-commit ones
    and carries the commit entries as evidence, so nothing is double-counted
    and nothing is lost.
    """
    skip_auctions = skip_auctions or set()
    commits = _auction_commits(entries)
    reveals = {(str((e.get("details") or {}).get("auction_id")),
                e.get("agent_id") or "")
               for e in entries if e["action"] == "bid_reveal"}
    closes: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        d = e.get("details") or {}
        if e["action"] == "auction_close" and d.get("auction_id"):
            closes[str(d["auction_id"])] = e
    findings = []
    for (auction_id, agent_id), commit in sorted(commits.items()):
        if (auction_id, agent_id) in reveals:
            continue
        if auction_id in skip_auctions:
            continue  # covered by the R4 systemic finding for this auction
        fid[0] += 1
        close = closes.get(auction_id)
        ev = [_evidence(commit, "bid_commit without matching bid_reveal")]
        if close is not None:
            ev.append(_evidence(close, "auction closed"))
            title = ("Commitment without reveal in auction %s by %s"
                     % (auction_id, agent_id or "unattributed"))
            severity = "high"
            desc = ("Agent %r committed to auction %r but no reveal was "
                    "recorded before the auction closed. In the sealed-bid "
                    "flow a commit without a reveal is a ghosted commitment: "
                    "the auction ran with fewer live bids than committed."
                    % (agent_id or "unattributed", auction_id))
        else:
            title = ("Commitment awaiting reveal in auction %s by %s"
                     % (auction_id, agent_id or "unattributed"))
            severity = "low"
            desc = ("Agent %r committed to auction %r and the archive ends "
                    "before a close is recorded, so this may simply be an "
                    "open auction rather than a ghosted commitment."
                    % (agent_id or "unattributed", auction_id))
        findings.append({
            "id": "F-%03d" % fid[0], "rule": "R3",
            "rule_name": "Unrevealed auction commitment",
            "severity": severity, "title": title, "description": desc,
            "agents": [agent_id or "unattributed"], "evidence": ev,
            "recommendation_hint": "Decide whether this was an open auction "
                                   "or a ghosted commitment, then act.",
        })
    return findings


def _zero_reveal_auctions(entries: List[Dict[str, Any]]) -> set:
    out = set()
    for e in entries:
        d = e.get("details") or {}
        if e["action"] != "auction_close":
            continue
        commits = d.get("commits")
        reveals = d.get("reveals")
        if (isinstance(commits, int) and isinstance(reveals, int)
                and commits > 0 and reveals == 0
                and d.get("auction_id")):
            out.add(str(d["auction_id"]))
    return out


def _rule_zero_reveal_close(entries: List[Dict[str, Any]],
                            fid: List[int]) -> List[Dict[str, Any]]:
    findings = []
    commits = _auction_commits(entries)
    for e in entries:
        d = e.get("details") or {}
        if e["action"] != "auction_close":
            continue
        commit_n = d.get("commits")
        reveals = d.get("reveals")
        if (isinstance(commit_n, int) and isinstance(reveals, int)
                and commit_n > 0 and reveals == 0):
            fid[0] += 1
            auction_id = str(d.get("auction_id"))
            evidence = [_evidence(e, "close: commits=%d reveals=0"
                                  % commit_n)]
            evidence.extend(
                _evidence(c, "unrevealed commit by %s" % (c.get("agent_id")
                                                          or "unattributed"))
                for (aid, _agent), c in sorted(commits.items())
                if aid == auction_id)
            findings.append({
                "id": "F-%03d" % fid[0], "rule": "R4",
                "rule_name": "Auction closed with zero reveals",
                "severity": "critical",
                "title": "Auction %s closed with %d commits and zero reveals"
                         % (d.get("auction_id"), commit_n),
                "description": (
                    "Every committed bidder ghosted: %d commits, 0 reveals. "
                    "The auction produced no winner from its committed set, "
                    "which points at a systemic problem (reveal window too "
                    "short, reveal path broken, or coordinated "
                    "non-participation) rather than one agent. The individual "
                    "commitments are cited below as evidence; they are not "
                    "reported as separate findings."
                    % commit_n),
                "agents": sorted({agent for (a, agent) in commits
                                  if a == auction_id}),
                "evidence": evidence,
                "recommendation_hint": "Treat as systemic: check the reveal "
                                       "path before blaming bidders.",
            })
    return findings


def _rule_quality_decline(entries: List[Dict[str, Any]],
                          fid: List[int]) -> List[Dict[str, Any]]:
    series: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for e in entries:
        if e["action"] != "quality_score":
            continue
        d = e.get("details") or {}
        if not isinstance(d.get("score"), (int, float)):
            continue
        series.setdefault((e.get("agent_id") or "unattributed",
                           str(d.get("metric", "quality"))), []).append(e)
    findings = []
    for (agent_id, metric), ev in sorted(series.items()):
        ev_sorted = sorted(ev, key=lambda e: e["seq"])
        # Below-threshold observations.
        below = [e for e in ev_sorted
                 if isinstance((e.get("details") or {}).get("threshold"),
                               (int, float))
                 and (e["details"]["score"] < e["details"]["threshold"])]
        if below:
            fid[0] += 1
            findings.append({
                "id": "F-%03d" % fid[0], "rule": "R5",
                "rule_name": "Quality below threshold",
                "severity": "medium",
                "title": "%s for %s below threshold (%d observation%s)"
                         % (metric, agent_id, len(below),
                            "s" if len(below) != 1 else ""),
                "description": (
                    "Recorded %s scores fell below the configured threshold. "
                    "Thresholds are set by the operator, so a breach is a "
                    "signal to investigate the work behind the scores, not a "
                    "verdict on the agent." % metric),
                "agents": [agent_id],
                "evidence": [_evidence(e, "score=%.3f threshold=%.3f"
                                        % (e["details"]["score"],
                                           e["details"]["threshold"]))
                             for e in below],
                "recommendation_hint": "Sample the underlying work behind "
                                       "the low scores.",
            })
        # Three consecutive declines.
        scores = [e["details"]["score"] for e in ev_sorted]
        declined = (len(scores) >= 3
                    and all(b < a for a, b in zip(scores[-3:], scores[-2:])))
        if declined:
            fid[0] += 1
            findings.append({
                "id": "F-%03d" % fid[0], "rule": "R5",
                "rule_name": "Quality declining trend",
                "severity": "medium",
                "title": "%s for %s declining for %d consecutive readings"
                         % (metric, agent_id, len(scores)),
                "description": (
                    "The last %d recorded %s scores for %s decline "
                    "monotonically (%.3f -> %.3f). A steady slide is more "
                    "actionable than a single dip: it suggests drift rather "
                    "than noise."
                    % (len(scores), metric, agent_id, scores[0],
                       scores[-1])),
                "agents": [agent_id],
                "evidence": [_evidence(e, "score=%.3f" % e["details"]["score"])
                             for e in ev_sorted[-3:]],
                "recommendation_hint": "Compare recent work inputs against "
                                       "the baseline period.",
            })
    return findings


def _rule_repeat_failures(entries: List[Dict[str, Any]],
                          fid: List[int],
                          r1_findings: List[Dict[str, Any]]
                          ) -> List[Dict[str, Any]]:
    findings = []
    for f in r1_findings:
        if len(f["evidence"]) >= 3:
            fid[0] += 1
            findings.append({
                "id": "F-%03d" % fid[0], "rule": "R6",
                "rule_name": "Repeated failure pattern",
                "severity": "high",
                "title": "Repeated failures: %s by %s (%d times)"
                         % (f.get("action") or f["rule_name"],
                            f["agents"][0], len(f["evidence"])),
                "description": (
                    "The same failure recurred %d times. Recurrence turns an "
                    "incident into a pattern: the fix belongs in the workflow "
                    "or the agent's operating conditions, not in retrying the "
                    "same step." % len(f["evidence"])),
                "agents": f["agents"],
                "evidence": f["evidence"],
                "recommendation_hint": "Fix the cause; retries alone have "
                                       "already been tried.",
            })
    return findings


def failure_mode_analysis(archive: VerifiedArchive,
                          timeline: Optional[Dict[str, Any]] = None
                          ) -> List[Dict[str, Any]]:
    """Classify failure modes from the verified entries. Rule-based only.

    Every returned finding cites at least one entry hash; findings are
    sorted by severity then id. Raises AssertionError (a bug, not a
    finding) if any rule emits evidence-free output.
    """
    entries = archive.entries
    fid = [0]
    findings: List[Dict[str, Any]] = []
    r1 = _rule_exception_clusters(entries, fid)
    findings.extend(r1)
    findings.extend(_rule_timeouts(entries, fid))
    zero_reveal = _zero_reveal_auctions(entries)
    findings.extend(_rule_unrevealed_commits(entries, fid, zero_reveal))
    findings.extend(_rule_zero_reveal_close(entries, fid))
    findings.extend(_rule_quality_decline(entries, fid))
    findings.extend(_rule_repeat_failures(entries, fid, r1))

    for f in findings:  # evidence is non-negotiable
        assert f.get("evidence"), \
            "rule %s emitted a finding with no evidence" % f.get("rule")
        for ev in f["evidence"]:
            assert ev.get("entry_hash"), \
                "finding %s cites evidence without an entry hash" % f["id"]
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9),
                                 f["id"]))
    return findings

# ---------------------------------------------------------------------------
# drift_report_section: summarize a drift/quality report (schema documented)
# ---------------------------------------------------------------------------

def drift_report_section(drift_report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize drift/quality findings from the expected drift report schema.

    The expected schema is documented in the module docstring (the shape the
    sibling drift_quality.py is designed to produce). Missing or malformed
    input produces an explicit "unavailable" section - never invented data.
    """
    base = {"available": False, "summary": "", "metrics": [], "alerts": [],
            "notes": ""}
    if drift_report is None:
        base["summary"] = ("No drift/quality report was supplied with this "
                           "engagement, so no drift conclusions are drawn.")
        base["notes"] = ("Supply the drift_quality.py report to include "
                         "trend and drift analysis.")
        return base
    if not isinstance(drift_report, dict):
        base["summary"] = "Drift report supplied but not a JSON object."
        return base
    metrics = drift_report.get("metrics")
    alerts = drift_report.get("alerts")
    if not isinstance(metrics, list):
        base["summary"] = ("Drift report is missing the 'metrics' list "
                           "required by the documented schema.")
        base["notes"] = "Expected keys: generated_at, window, metrics, alerts."
        return base

    condensed = []
    for m in metrics:
        if not isinstance(m, dict):
            continue
        condensed.append({
            "metric": m.get("metric"), "agent_id": m.get("agent_id"),
            "trend": m.get("trend"), "drift_detected": bool(
                m.get("drift_detected")),
            "p_value": m.get("p_value"),
            "latest_value": m.get("latest_value"),
            "baseline_value": m.get("baseline_value"),
            "notes": m.get("notes", ""),
        })
    clean_alerts = [a for a in (alerts or []) if isinstance(a, dict)]
    drifted = [m for m in condensed if m["drift_detected"]]
    base.update({
        "available": True,
        "generated_at": drift_report.get("generated_at"),
        "window": drift_report.get("window"),
        "metrics": condensed,
        "alerts": clean_alerts,
        "notes": "",
    })
    if not condensed:
        base["summary"] = "Drift report supplied but contains no metrics."
    elif not drifted and not clean_alerts:
        base["summary"] = ("Drift analysis covers %d metric(s); no drift "
                           "detected and no alerts raised in the window."
                           % len(condensed))
    else:
        parts = []
        if drifted:
            parts.append("drift detected in %d of %d metrics"
                         % (len(drifted), len(condensed)))
        if clean_alerts:
            parts.append("%d alert(s) raised" % len(clean_alerts))
        base["summary"] = ("Drift analysis covers %d metric(s): %s."
                           % (len(condensed), "; ".join(parts)))
    return base


# ---------------------------------------------------------------------------
# remediation_list: concrete, checkable items derived from findings
# ---------------------------------------------------------------------------

def _rem(rule: str, finding_ids: List[str], title: str,
         check_steps: List[str], acceptance: str,
         owner_role: str) -> Dict[str, Any]:
    return {"id": "", "rule": rule, "finding_ids": finding_ids,
            "title": title, "check_steps": check_steps,
            "acceptance": acceptance, "owner_role": owner_role,
            "status": "open"}


def _remediate_r1(f: Dict[str, Any]) -> Dict[str, Any]:
    n = len(f["evidence"])
    return _rem(
        "R1", [f["id"]],
        "Triage the %d error(s): %s" % (n, f["title"]),
        ["Pull the cited entr%s from the archive by entry hash and read the "
         "exception/error text." % ("ies" if n != 1 else "y"),
         "Classify each as transient (retry-safe), environmental "
         "(dependency/config), or logic (code change needed).",
         "Re-run the failing action once under observation after the fix."],
        "Each cited entry is classified and the re-run completes without "
        "the same error, or a tracked code/config change exists.",
        "agent owner")


def _remediate_r2(f: Dict[str, Any]) -> Dict[str, Any]:
    return _rem(
        "R2", [f["id"]], "Reconcile timed-out work: %s" % f["title"],
        ["Check whether the timed-out action actually completed off-record "
         "(query downstream state for the cited seq).",
         "If it completed, mark the duplicate-safe outcome; if not, requeue "
         "with an idempotency key.",
         "Review the SLA: raise it, or break the work into smaller steps."],
        "Downstream state matches exactly one intended outcome per cited "
        "timeout; SLA decision recorded.",
        "platform operator")


def _remediate_r3(f: Dict[str, Any]) -> Dict[str, Any]:
    return _rem(
        "R3", [f["id"]], "Resolve unrevealed commitment: %s" % f["title"],
        ["Confirm the auction is closed and the reveal window has passed "
         "(cite the close entry).",
         "Decide: penalize per the posted auction rules, or waive with a "
         "written reason.",
         "Check whether the reveal path was reachable during the window "
         "(client logs, relayer status)."],
        "A written disposition exists for the commitment (penalty applied "
        "or waiver with reason); reveal-path health confirmed.",
        "auction operator")


def _remediate_r4(f: Dict[str, Any]) -> Dict[str, Any]:
    return _rem(
        "R4", [f["id"]], "Investigate zero-reveal auction: %s" % f["title"],
        ["Verify the reveal endpoint/relayer was reachable for the full "
         "reveal window.",
         "Review the reveal window length against observed bidder latency.",
         "Re-run a dry-run auction with test bidders before the next real "
         "auction."],
        "Root cause identified (reveal path, window length, or bidder "
        "behavior) and the dry-run auction completes with reveals.",
        "auction operator")


def _remediate_r5(f: Dict[str, Any]) -> Dict[str, Any]:
    return _rem(
        "R5", [f["id"]], "Address quality signal: %s" % f["title"],
        ["Sample the work behind the cited scores (at least 3 items).",
         "Compare current inputs/conditions against the baseline period.",
         "Adjust the workflow, the prompt/config, or the threshold - and "
         "record which one changed and why."],
        "Sampled work reviewed; one concrete change recorded with a "
        "re-measurement date.",
        "agent owner")


def _remediate_r6(f: Dict[str, Any]) -> Dict[str, Any]:
    return _rem(
        "R6", [f["id"]], "Break the failure pattern: %s" % f["title"],
        ["Stop ad-hoc retries of the failing step until the cause is named.",
         "Apply the R1 remediation for the underlying errors first.",
         "Add a guard (circuit breaker, validation, or alert) so the next "
         "occurrence is caught before the third retry."],
        "Root cause named and a guard is in place; the pattern has not "
        "recurred in the following window.",
        "agent owner")


_REMEDIATION_BUILDERS = {
    "R1": _remediate_r1, "R2": _remediate_r2, "R3": _remediate_r3,
    "R4": _remediate_r4, "R5": _remediate_r5, "R6": _remediate_r6,
}


def remediation_list(findings: Sequence[Dict[str, Any]]
                     ) -> List[Dict[str, Any]]:
    """Build concrete, checkable remediation items from findings.

    Each item names check steps, an acceptance criterion (how you know it is
    done), and a suggested owner role. Items start as "open".
    """
    items: List[Dict[str, Any]] = []
    for f in findings:
        builder = _REMEDIATION_BUILDERS.get(f.get("rule", ""))
        if builder is None:
            continue
        items.append(builder(f))
    for i, item in enumerate(items, 1):
        item["id"] = "REM-%03d" % i
    return items

# ---------------------------------------------------------------------------
# Report building + rendering
# ---------------------------------------------------------------------------

def _short_hash(h: str, n: int = 16) -> str:
    return (h or "")[:n]


def build_audit_report(archive: VerifiedArchive,
                       drift_report: Optional[Dict[str, Any]] = None,
                       customer: Optional[str] = None,
                       engagement_id: Optional[str] = None
                       ) -> Dict[str, Any]:
    """Run the full AUD-01 analysis and return the structured report dict."""
    timeline = reconstruct_timeline(archive)
    findings = failure_mode_analysis(archive, timeline)
    remediations = remediation_list(findings)
    drift = drift_report_section(drift_report)

    sev_counts: Dict[str, int] = {}
    for f in findings:
        sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1
    agents = timeline["agents"]
    attr = timeline["attribution"]
    error_actions = sum(
        1 for e in archive.entries if _is_error(e))
    strengths = [
        "Archive integrity verified: %d entries, hash chain intact, "
        "manifest signature valid (signer %s)."
        % (len(archive.entries),
           (archive.manifest.get("signer_identity") or {}).get("id", "?")),
        "%d of %d entries attributable to a named agent."
        % (attr["attributed"], len(archive.entries)),
        "%d distinct agents with recorded activity."
        % len(agents),
    ]
    if error_actions == 0:
        strengths.append("No error outcomes recorded in the window.")
    if not any(f["severity"] in ("critical", "high") for f in findings):
        strengths.append("No critical or high-severity findings.")

    report = {
        "report_type": REPORT_TYPE,
        "report_version": REPORT_VERSION,
        "engagement": {
            "engagement_id": engagement_id,
            "customer": customer,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sku": "AUD-01",
            "sku_name": "Agent Forensic Audit",
        },
        "verification": {
            "archive_id": archive.archive_id,
            "archive_sha256": archive.archive_sha256,
            "head_hash": archive.head_hash,
            "entry_count": len(archive.entries),
            "signer_identity": archive.manifest.get("signer_identity"),
            "verified_at": archive.verified_at,
            "checks": archive.checks,
        },
        "executive_summary": {
            "headline": ("Forensic audit of archive %s: %d entries verified, "
                         "%d finding(s) with cited evidence, %d remediation "
                         "item(s) proposed."
                         % (archive.archive_id, len(archive.entries),
                            len(findings), len(remediations))),
            "strengths": strengths,
            "findings_by_severity": sev_counts,
            "opportunities": (
                "The opportunities below are ordered by severity. Each one "
                "cites the exact archive entries it was derived from, so "
                "every claim in this report can be re-checked against the "
                "signed archive.")
        },
        "timeline_stats": {
            "window": timeline["window"],
            "entry_count": len(timeline["items"]),
            "agents": [{"agent_id": a["agent_id"], "actions": a["actions"],
                        "first_seen": a["first_seen"],
                        "last_seen": a["last_seen"],
                        "action_counts": a["action_counts"]}
                       for a in agents.values()],
            "attribution": attr,
        },
        "findings": findings,
        "remediations": remediations,
        "drift_quality": drift,
        "methodology": {
            "approach": "Rule-based analysis over a verified, hash-chained, "
                        "signed audit archive. Rules are documented in the "
                        "forensic_audit module (R1-R6).",
            "evidence_rule": "Every finding cites at least one entry hash "
                             "from the verified archive. Findings without "
                             "evidence are never emitted.",
            "limitations": [
                "Only events recorded in the archive can be analyzed; "
                "off-record activity is invisible to this report.",
                "Rules detect patterns, not intent: a ghosted commitment "
                "may be an outage, not misbehavior.",
                "Quality scores reflect the operator's thresholds and "
                "scoring, which this report does not re-validate.",
                "Signature verification proves the archive was signed by "
                "the key named in the manifest; binding that key to a "
                "real-world signer (key pinning) is the engagement "
                "operator's responsibility.",
            ],
        },
        "chain_of_custody": {
            "archive_sha256": archive.archive_sha256,
            "head_hash": archive.head_hash,
            "signer_identity": archive.manifest.get("signer_identity"),
            "signer_public_key_hex": archive.manifest.get(
                "signer_public_key_hex"),
            "verified_at": archive.verified_at,
        },
    }
    return report


def render_markdown(report: Dict[str, Any]) -> str:
    """Render the report as clean Markdown for handing to a customer."""
    eng = report["engagement"]
    ver = report["verification"]
    summ = report["executive_summary"]
    lines: List[str] = []
    lines.append("# Agent Forensic Audit Report")
    lines.append("")
    if eng.get("customer"):
        lines.append("**Customer:** %s" % eng["customer"])
    if eng.get("engagement_id"):
        lines.append("**Engagement:** %s" % eng["engagement_id"])
    lines.append("**Generated:** %s" % eng["generated_at"])
    lines.append("**Archive:** `%s` (%d entries)"
                 % (ver["archive_id"], ver["entry_count"]))
    lines.append("")
    lines.append("## Executive summary")
    lines.append("")
    lines.append(summ["headline"])
    lines.append("")
    lines.append("### What's working")
    lines.append("")
    for s in summ["strengths"]:
        lines.append("- %s" % s)
    lines.append("")
    lines.append("### Opportunities to strengthen")
    lines.append("")
    if not report["findings"]:
        lines.append("No findings. The analyzed window shows clean, "
                     "attributable operation.")
    else:
        for f in report["findings"]:
            lines.append("#### %s `%s` [%s]"
                         % (f["id"], f["title"], f["severity"].upper()))
            lines.append("")
            lines.append(f["description"])
            lines.append("")
            lines.append("**Evidence** (entry hashes):")
            for ev in f["evidence"]:
                lines.append("- seq %d `%s` %s"
                             % (ev["seq"], _short_hash(ev["entry_hash"]),
                                ("- " + ev["note"]) if ev.get("note") else ""))
            lines.append("")
    lines.append("## Remediation checklist")
    lines.append("")
    if not report["remediations"]:
        lines.append("Nothing to remediate.")
    else:
        for r in report["remediations"]:
            lines.append("### %s %s" % (r["id"], r["title"]))
            lines.append("")
            lines.append("Suggested owner: %s | Status: %s"
                         % (r["owner_role"], r["status"]))
            lines.append("")
            lines.append("Check steps:")
            for step in r["check_steps"]:
                lines.append("- [ ] %s" % step)
            lines.append("")
            lines.append("**Done when:** %s" % r["acceptance"])
            lines.append("")
    drift = report["drift_quality"]
    lines.append("## Drift & quality")
    lines.append("")
    lines.append(drift["summary"] or "No drift data.")
    if drift.get("metrics"):
        lines.append("")
        for m in drift["metrics"]:
            lines.append("- %s / %s: trend=%s drift=%s" %
                         (m.get("metric"), m.get("agent_id"),
                          m.get("trend"), m.get("drift_detected")))
    if drift.get("alerts"):
        lines.append("")
        for a in drift["alerts"]:
            lines.append("- [%s] %s" % (a.get("severity"), a.get("message")))
    lines.append("")
    lines.append("## Methodology & limitations")
    lines.append("")
    lines.append(report["methodology"]["approach"])
    lines.append("")
    lines.append(report["methodology"]["evidence_rule"])
    lines.append("")
    for lim in report["methodology"]["limitations"]:
        lines.append("- %s" % lim)
    lines.append("")
    lines.append("## Chain of custody")
    lines.append("")
    coc = report["chain_of_custody"]
    lines.append("- Archive SHA-256: `%s`" % coc["archive_sha256"])
    lines.append("- Head hash: `%s`" % coc["head_hash"])
    lines.append("- Signer: `%s`"
                 % json.dumps(coc["signer_identity"]))
    lines.append("- Verified at: %s" % coc["verified_at"])
    lines.append("")
    lines.append("_Full entry hashes for every cited finding are in the JSON "
                 "appendix of this report._")
    lines.append("")
    return "\n".join(lines)


def render_html(report: Dict[str, Any]) -> str:
    """Render the report as a single self-contained HTML document."""
    eng = report["engagement"]
    ver = report["verification"]
    summ = report["executive_summary"]

    def esc(x: Any) -> str:
        return html.escape(str(x if x is not None else ""))

    parts: List[str] = []
    parts.append("<h1>Agent Forensic Audit Report</h1>")
    parts.append("<p><strong>Archive:</strong> <code>%s</code> (%d entries)<br>"
                 "<strong>Generated:</strong> %s%s</p>"
                 % (esc(ver["archive_id"]), ver["entry_count"],
                    esc(eng["generated_at"]),
                    "<br><strong>Customer:</strong> " + esc(eng["customer"])
                    if eng.get("customer") else ""))
    parts.append("<h2>Executive summary</h2><p>%s</p>"
                 % esc(summ["headline"]))
    parts.append("<h3>What&rsquo;s working</h3><ul>%s</ul>"
                 % "".join("<li>%s</li>" % esc(s) for s in summ["strengths"]))
    parts.append("<h2>Findings</h2>")
    if not report["findings"]:
        parts.append("<p>No findings. The analyzed window shows clean, "
                     "attributable operation.</p>")
    for f in report["findings"]:
        parts.append(
            "<div class='finding sev-%s'><h3>%s &mdash; %s "
            "<span class='sev'>%s</span></h3><p>%s</p>"
            "<p><strong>Evidence</strong></p><ul>%s</ul></div>"
            % (esc(f["severity"]), esc(f["id"]), esc(f["title"]),
               esc(f["severity"].upper()), esc(f["description"]),
               "".join("<li>seq %d <code>%s</code>%s</li>"
                       % (ev["seq"], esc(_short_hash(ev["entry_hash"])),
                          " &mdash; " + esc(ev["note"]) if ev.get("note")
                          else "")
                       for ev in f["evidence"])))
    parts.append("<h2>Remediation checklist</h2>")
    for r in report["remediations"]:
        parts.append(
            "<div class='rem'><h3>%s &mdash; %s</h3>"
            "<p>Suggested owner: %s | Status: %s</p><ol>%s</ol>"
            "<p><strong>Done when:</strong> %s</p></div>"
            % (esc(r["id"]), esc(r["title"]), esc(r["owner_role"]),
               esc(r["status"]),
               "".join("<li>%s</li>" % esc(s) for s in r["check_steps"]),
               esc(r["acceptance"])))
    drift = report["drift_quality"]
    parts.append("<h2>Drift &amp; quality</h2><p>%s</p>"
                 % esc(drift["summary"] or "No drift data."))
    coc = report["chain_of_custody"]
    parts.append(
        "<h2>Chain of custody</h2><ul>"
        "<li>Archive SHA-256: <code>%s</code></li>"
        "<li>Head hash: <code>%s</code></li>"
        "<li>Signer: <code>%s</code></li>"
        "<li>Verified at: %s</li></ul>"
        % (esc(coc["archive_sha256"]), esc(coc["head_hash"]),
           esc(json.dumps(coc["signer_identity"])), esc(coc["verified_at"])))

    body = "\n".join(parts)
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Agent Forensic Audit Report</title>"
            "<style>body{font-family:system-ui,-apple-system,sans-serif;"
            "max-width:900px;margin:2rem auto;padding:0 1rem;color:#1a1a1a;}"
            "h1{border-bottom:3px solid #b98a2f;padding-bottom:.5rem;}"
            "h2{color:#333;border-bottom:1px solid #ddd;padding-bottom:.25rem;}"
            "code{background:#f4f1ea;padding:.1rem .3rem;border-radius:3px;}"
            ".finding{border:1px solid #ddd;border-left:5px solid #999;"
            "padding:.5rem 1rem;margin:1rem 0;border-radius:4px;}"
            ".sev-critical{border-left-color:#b00020;}"
            ".sev-high{border-left-color:#d46a00;}"
            ".sev-medium{border-left-color:#b98a2f;}"
            ".sev-low{border-left-color:#2e7d32;}"
            ".sev{font-size:.8rem;background:#eee;padding:.1rem .4rem;"
            "border-radius:3px;}"
            ".rem{background:#fafafa;border:1px solid #eee;padding:.5rem 1rem;"
            "margin:1rem 0;border-radius:4px;}</style></head>"
            "<body>%s</body></html>" % body)


# ---------------------------------------------------------------------------
# audit_pipeline: the runnable end-to-end walkthrough
# ---------------------------------------------------------------------------

def audit_pipeline(archive_source: Union[str, Path, bytes],
                   drift_report: Optional[Dict[str, Any]] = None,
                   customer: Optional[str] = None,
                   engagement_id: Optional[str] = None) -> Dict[str, Any]:
    """Run the full AUD-01 walkthrough: verify -> timeline -> analyze ->
    remediate -> report -> render. Raises ArchiveVerificationError if the
    archive does not verify; the walkthrough never runs on untrusted input.
    """
    archive = ingest_archive(archive_source)
    report = build_audit_report(archive, drift_report=drift_report,
                                customer=customer,
                                engagement_id=engagement_id)
    return {
        "verified": True,
        "archive_id": archive.archive_id,
        "entry_count": len(archive.entries),
        "report": report,
        "report_json": canonical_json(report).decode("utf-8"),
        "report_markdown": render_markdown(report),
        "report_html": render_html(report),
        "finding_count": len(report["findings"]),
        "remediation_count": len(report["remediations"]),
    }
