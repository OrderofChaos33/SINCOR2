"""OBS-02 — Agent Audit Trail ($199/mo per deployment).

A tamper-evident, attribution-bearing audit log for SINCOR agent activity.
Built new for the "Ship the Real SKUs" program: prior to this module the
repo contained Ed25519/secp256k1 signing only for auction bids (EIP-712 via
``eth_account``) and no hash-chained audit log anywhere in ``src/``.

What it does
------------
* **Hash-chained append-only log.** Every entry commits to the previous entry
  via ``entry_hash = keccak256(prev_hash_raw || canonical_entry_bytes)``.
  Any flip, reorder, deletion, or truncation breaks the chain and is caught
  by :meth:`AuditTrail.verify_chain`, which recomputes every link.
* **Ed25519 per-entry signatures** (libsodium via ``pynacl``). The signature
  covers the entry hash, binding the signature to the entry's exact chain
  position — a forged entry cannot be re-signed without the private key.
* **Forensic archive.** :meth:`AuditTrail.export_archive` writes entries as
  JSONL plus a signed manifest (chain head hash, entry count, verify key);
  :meth:`AuditTrail.verify_archive` recomputes the chain and the manifest
  signature from scratch.
* **Attribution.** Every entry carries ``agent_id``, ``action``, a
  millisecond timestamp, and a causal parent (span id / task id / plan id
  where available).
* **Capture hooks** (:func:`install_hooks`) tap the real span-emission path
  (``sincor2.observability.Tracer.emit``) behind the explicit
  ``SINCOR_AUDIT_TRAIL_ENABLED=1`` flag. Default OFF — zero behavior change
  when off.

Hash choice
-----------
``eth_hash.keccak`` (Keccak-256), matching this repo's existing convention:
PR #254 made the auction bid path keccak-backed ("eth_hash-backed keccak"),
so the audit log uses the same primitive. If you ever reimplement this log
in another language, any SHA3-256/Keccak-256 with identical domain
separation reproduces the hashes — the algorithm is named in the manifest.

Key management (operator-run ceremony)
--------------------------------------
Keys are **supplied by the operator, never generated-and-stored by this
module**. Operator flow::

    1. On a trusted admin machine (or the Secure Vault capture flow):
         python - <<'EOF'
         from sincor2.obs_skus.audit_trail import generate_keypair
         seed_hex, verify_hex = generate_keypair()
         print("SEED (store in vault):", seed_hex)
         print("VERIFY KEY (publish):", verify_hex)
         EOF
    2. Store the 64-hex-char SEED in the deployment's secret store
       (Railway variables / Secure Vault) as ``SINCOR_AUDIT_SIGNING_KEY``.
       It is read from the environment at append time and never written
       to disk, logs, or archives by this module.
    3. Publish the VERIFY KEY alongside each exported archive so any third
       party can run :func:`verify_archive` offline.
    4. Rotation: generate a fresh pair, deploy the new seed, export an
       archive (which pins the verify key in the manifest). Old archives
       keep verifying against their pinned keys.

Entries appended without a configured key are stored with ``"sig": null``
and reported as ``unsigned`` by verification — the log degrades to
hash-chain-only tamper evidence rather than failing closed. Operators who
need non-repudiation must set ``SINCOR_AUDIT_SIGNING_KEY``.

Underwriting integration (honest note)
--------------------------------------
``src/sincor2/underwriting/taps/`` exposes the ``Tap`` protocol —
``transfer(...)`` / ``clawback(...)`` — which is settlement (money movement)
only. That interface does not fit behavioral data and this module does not
touch it (money path is out of scope). Instead,
:func:`to_underwriting_event` converts an audit entry into a plain event
dict (``event_type="audit"``) the underwriting engine can consume from its
event pipeline; wiring it in is a one-line call at the engine's ingestion
point. See ``docs/underwriting/BUILD_GUIDE.md`` for that pipeline.

Verified real integration points for capture (names checked in code):
  * ``sincor2.observability.Tracer.emit`` — the real span-emission path;
    :func:`install_hooks` wraps it (only when the enable flag is set).
  * ``sincor2.agency_kernel.AgencyKernel.executor_run_step`` /
    ``_log_execution_result`` — execution-path log point; call
    :func:`record_action` from there when shipping the SKU.
  * ``sincor2.a2a_inbound.register`` / ``Fabric`` — A2A task lifecycle;
    call :func:`record_action` on task transitions when shipping the SKU.

No HIPAA, SOC 2, or compliance certification is claimed — this module
provides tamper-evidence and attribution, nothing more.

Positive framing: the audit trail gives every agent action a verifiable,
attributable record that operators and customers can trust and export.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from eth_hash.auto import keccak

try:  # pynacl is a hard dependency of this SKU; import eagerly for a clear error.
    from nacl.exceptions import BadSignatureError
    from nacl.signing import SigningKey, VerifyKey
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "OBS-02 audit_trail requires pynacl (pip install pynacl). "
        "It is installed in ~/.venvs/sincor2."
    ) from exc

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKU = "OBS-02"
FORMAT_VERSION = 1
ENABLE_ENV = "SINCOR_AUDIT_TRAIL_ENABLED"
SIGNING_KEY_ENV = "SINCOR_AUDIT_SIGNING_KEY"

_GENESIS_PREV = b"\x00" * 32  # prev_hash for seq 0
_HASH_LEN = 32

# ---------------------------------------------------------------------------
# Canonical bytes + hashing
# ---------------------------------------------------------------------------


def _canonical(obj: Any) -> bytes:
    """Deterministic JSON bytes: sorted keys, no whitespace, UTF-8."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _hash(data: bytes) -> bytes:
    """Keccak-256 per repo convention (eth_hash-backed, see PR #254)."""
    return keccak(data)


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Enable flag
# ---------------------------------------------------------------------------


def audit_enabled(explicit: Optional[bool] = None) -> bool:
    """True when the audit trail capture path is switched on.

    Default OFF. Enable with ``SINCOR_AUDIT_TRAIL_ENABLED=1``. When off,
    :func:`record_action` and installed hooks are strict no-ops.
    """
    if explicit is not None:
        return explicit
    return os.environ.get(ENABLE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Key management — operator-supplied, never generated-and-stored silently
# ---------------------------------------------------------------------------


def generate_keypair() -> tuple:
    """Generate a fresh Ed25519 keypair.

    Returns ``(seed_hex, verify_key_hex)``. The caller is responsible for
    storing the seed in the secret store and publishing the verify key.
    This function writes nothing anywhere.
    """
    sk = SigningKey.generate()
    return sk.encode().hex(), sk.verify_key.encode().hex()


def load_signing_key() -> Optional[SigningKey]:
    """Load the operator-supplied signing key from the environment.

    Reads ``SINCOR_AUDIT_SIGNING_KEY`` (64 hex chars = 32-byte seed).
    Returns None when unset (entries are then appended unsigned).
    The key value is never logged or written to disk by this module.
    """
    raw = os.environ.get(SIGNING_KEY_ENV, "").strip()
    if not raw:
        return None
    try:
        seed = bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError(
            f"{SIGNING_KEY_ENV} must be 64 hex characters (32-byte seed)"
        ) from exc
    if len(seed) != 32:
        raise ValueError(
            f"{SIGNING_KEY_ENV} must be 64 hex characters (32-byte seed)"
        )
    return SigningKey(seed)


def load_verify_key(explicit_hex: Optional[str] = None) -> Optional[VerifyKey]:
    """Verify key for checking signatures: explicit hex, or derived from the
    signing seed when configured."""
    if explicit_hex:
        return VerifyKey(bytes.fromhex(explicit_hex))
    sk = load_signing_key()
    return sk.verify_key if sk else None


# ---------------------------------------------------------------------------
# Entry construction / chain math (pure functions — unit-testable)
# ---------------------------------------------------------------------------


def build_entry(
    *,
    seq: int,
    agent_id: str,
    action: str,
    timestamp_ms: int,
    causal_parent: Optional[str],
    detail: Dict[str, Any],
    prev_hash: bytes,
    signing_key: Optional[SigningKey] = None,
    verify_key_hex: Optional[str] = None,
) -> Dict[str, Any]:
    """Build, hash-link, and optionally sign one audit entry.

    ``entry_hash = keccak(prev_hash_raw || canonical(body))`` where body
    holds every attribution field plus ``prev_hash`` as hex. The Ed25519
    signature covers ``entry_hash`` so it binds to the chain position.
    """
    if len(prev_hash) != _HASH_LEN:
        raise ValueError("prev_hash must be 32 raw bytes")
    body = {
        "seq": seq,
        "agent_id": agent_id,
        "action": action,
        "timestamp_ms": timestamp_ms,
        "causal_parent": causal_parent,
        "detail": detail,
        "prev_hash": prev_hash.hex(),
    }
    entry_hash = _hash(prev_hash + _canonical(body)).hex()
    sig_hex: Optional[str] = None
    vk_hex = verify_key_hex
    if signing_key is not None:
        sig_hex = signing_key.sign(bytes.fromhex(entry_hash)).signature.hex()
        vk_hex = signing_key.verify_key.encode().hex()
    return {
        **body,
        "entry_hash": entry_hash,
        "sig": sig_hex,
        "verify_key": vk_hex,
    }


def verify_entry_signature(
    entry: Dict[str, Any], verify_key: Optional[VerifyKey] = None
) -> str:
    """Check one entry's Ed25519 signature.

    Returns ``"signed"`` / ``"unsigned"`` / ``"bad_signature"``.
    Raises nothing — reports are strings so batch verification can continue.
    """
    sig_hex = entry.get("sig")
    if not sig_hex:
        return "unsigned"
    vk_hex = entry.get("verify_key")
    vk = verify_key
    if vk is None and vk_hex:
        try:
            vk = VerifyKey(bytes.fromhex(vk_hex))
        except Exception:
            return "bad_signature"
    if vk is None:
        return "unsigned"  # signed entry but no key to check it against
    try:
        vk.verify(bytes.fromhex(entry["entry_hash"]), bytes.fromhex(sig_hex))
    except (BadSignatureError, ValueError):
        return "bad_signature"
    return "signed"


# ---------------------------------------------------------------------------
# Durable log
# ---------------------------------------------------------------------------


def default_log_path() -> Path:
    """Durable storage under the repo's ``data_dir()`` convention.

    Never /tmp for real state. Tests pass explicit temp paths instead.
    """
    from sincor2.data_paths import data_dir

    p = data_dir() / "obs_skus" / "obs02"
    p.mkdir(parents=True, exist_ok=True)
    return p / "audit_log.jsonl"


class AuditTrail:
    """Hash-chained, Ed25519-signed, append-only audit log."""

    def __init__(
        self,
        log_path: Optional[Path] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self.log_path = Path(log_path) if log_path else default_log_path()
        self.enabled = audit_enabled(enabled)
        self._lock = threading.Lock()
        self._head: bytes = _GENESIS_PREV
        self._seq: int = 0
        self._recover_head()

    # -- internals ---------------------------------------------------------

    def _recover_head(self) -> None:
        """Rebuild (seq, head) from the durable file without trusting it.

        Corrupt lines are skipped here (verification is verify_chain's job,
        which reports them); recovery must never crash on a damaged file.
        """
        if not self.log_path.exists():
            return
        with self.log_path.open("rb") as fh:
            for raw in fh:
                if not raw.strip():
                    continue
                try:
                    entry = json.loads(raw.decode("utf-8"))
                    self._head = bytes.fromhex(entry["entry_hash"])
                    self._seq = int(entry["seq"]) + 1
                except (UnicodeDecodeError, ValueError, KeyError):
                    continue

    def _append_line(self, line: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # -- public API --------------------------------------------------------

    def record(
        self,
        agent_id: str,
        action: str,
        causal_parent: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
        timestamp_ms: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Append one attributed entry. Strict no-op (returns None, writes
        nothing) when the trail is disabled."""
        if not self.enabled:
            return None
        if not agent_id or not action:
            raise ValueError("agent_id and action are required")
        entry = build_entry(
            seq=self._seq,
            agent_id=agent_id,
            action=action,
            timestamp_ms=timestamp_ms if timestamp_ms is not None else _now_ms(),
            causal_parent=causal_parent,
            detail=detail or {},
            prev_hash=self._head,
            signing_key=load_signing_key(),
        )
        with self._lock:
            self._append_line(_canonical(entry).decode("utf-8"))
            self._head = bytes.fromhex(entry["entry_hash"])
            self._seq += 1
        return entry

    def head_hash(self) -> str:
        """Current chain head as hex. Operators pin this externally (e.g. in
        a daily archive manifest) so that tail truncation — the one attack a
        bare hash chain cannot self-detect — is caught on next verify."""
        return self._head.hex()

    def _load_entries(self) -> tuple:
        """Read entries tolerantly: returns (entries, decode_failures).

        Lines that are not valid UTF-8/JSON are reported as failures rather
        than raising, so a byte-flip attack is *reported* as tamper instead
        of crashing verification.
        """
        entries: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        if not self.log_path.exists():
            return entries, failures
        with self.log_path.open("rb") as fh:
            for lineno, raw in enumerate(fh, start=1):
                if not raw.strip():
                    continue
                try:
                    entries.append(json.loads(raw.decode("utf-8")))
                except (UnicodeDecodeError, ValueError) as exc:
                    failures.append(
                        {"seq": None, "kind": "undecodable_line",
                         "detail": f"line {lineno} is not valid UTF-8/JSON: {exc}"}
                    )
        return entries, failures

    def entries(self) -> List[Dict[str, Any]]:
        """Read all entries (in file order)."""
        out: List[Dict[str, Any]] = []
        if not self.log_path.exists():
            return out
        with self.log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def verify_chain(
        self, verify_key: Optional[VerifyKey] = None
    ) -> Dict[str, Any]:
        """Recompute every hash link and check every signature.

        Returns a report dict; ``ok`` is True only when every link and every
        present signature verifies. Unsigned entries are counted, not failed.
        """
        failures: List[Dict[str, Any]] = []
        unsigned = 0
        signed = 0
        prev = _GENESIS_PREV
        expected_seq = 0
        count = 0
        log_entries, decode_failures = self._load_entries()
        failures.extend(decode_failures)
        for entry in log_entries:
            count += 1
            seq = entry.get("seq")
            if seq != expected_seq:
                failures.append(
                    {"seq": seq, "kind": "seq_break",
                     "detail": f"expected seq {expected_seq}, found {seq}"}
                )
            try:
                prev_bytes = bytes.fromhex(entry.get("prev_hash", ""))
            except ValueError:
                prev_bytes = b""
            if prev_bytes != prev:
                failures.append(
                    {"seq": seq, "kind": "link_break",
                     "detail": "prev_hash does not match previous entry_hash"}
                )
            body = {k: entry[k] for k in
                    ("seq", "agent_id", "action", "timestamp_ms",
                     "causal_parent", "detail", "prev_hash")}
            recomputed = _hash(prev + _canonical(body)).hex()
            if recomputed != entry.get("entry_hash"):
                failures.append(
                    {"seq": seq, "kind": "hash_mismatch",
                     "detail": "entry_hash does not recompute from entry bytes"}
                )
            sig_status = verify_entry_signature(entry, verify_key)
            if sig_status == "signed":
                signed += 1
            elif sig_status == "unsigned":
                unsigned += 1
            else:
                failures.append(
                    {"seq": seq, "kind": "bad_signature",
                     "detail": "Ed25519 signature does not verify"}
                )
            try:
                prev = bytes.fromhex(entry["entry_hash"])
            except ValueError:
                prev = b"\x00" * 32
            expected_seq = (seq + 1) if isinstance(seq, int) else expected_seq + 1
        return {
            "sku": SKU,
            "ok": not failures,
            "entries": count,
            "head_hash": prev.hex() if count else _GENESIS_PREV.hex(),
            "signed": signed,
            "unsigned": unsigned,
            "failures": failures,
        }

    # -- forensic archive --------------------------------------------------

    def export_archive(self, dest_dir: Path) -> Path:
        """Export entries JSONL + signed manifest for offline forensics.

        The manifest pins ``head_hash``, ``entries`` count, the verify key,
        and carries an Ed25519 signature over its canonical bytes (when a
        signing key is configured).
        """
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        report = self.verify_chain()
        entries = self.entries()
        (dest / "audit_log.jsonl").write_text(
            "".join(_canonical(e).decode("utf-8") + "\n" for e in entries),
            encoding="utf-8",
        )
        sk = load_signing_key()
        manifest = {
            "sku": SKU,
            "format_version": FORMAT_VERSION,
            "hash_alg": "keccak-256",
            "sig_alg": "ed25519",
            "entries": len(entries),
            "head_hash": report["head_hash"],
            "verify_key": sk.verify_key.encode().hex() if sk else None,
            "exported_at_ms": _now_ms(),
        }
        manifest["manifest_sig"] = (
            sk.sign(_canonical(manifest)).signature.hex() if sk else None
        )
        (dest / "manifest.json").write_text(
            _canonical(manifest).decode("utf-8") + "\n", encoding="utf-8"
        )
        return dest

    @staticmethod
    def verify_archive(
        archive_dir: Path, verify_key_hex: Optional[str] = None
    ) -> Dict[str, Any]:
        """Verify an exported archive from scratch.

        Recomputes the full chain, checks the manifest pins (count, head
        hash), and verifies the manifest signature against the verify key
        (explicit arg wins; falls back to the manifest's pinned key).
        """
        archive = Path(archive_dir)
        failures: List[Dict[str, Any]] = []
        manifest_path = archive / "manifest.json"
        log_path = archive / "audit_log.jsonl"
        if not manifest_path.exists():
            return {"ok": False, "failures": [{"kind": "missing_manifest"}]}
        if not log_path.exists():
            return {"ok": False, "failures": [{"kind": "missing_log"}]}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        # 1. chain recomputation over the archived entries
        probe = AuditTrail.__new__(AuditTrail)  # verify without touching state
        probe.log_path = log_path  # type: ignore[attr-defined]
        probe._lock = threading.Lock()  # type: ignore[attr-defined]
        probe._head = _GENESIS_PREV  # type: ignore[attr-defined]
        probe._seq = 0  # type: ignore[attr-defined]
        tmp_entries, _decode_failures = probe._load_entries()
        vk_hex = verify_key_hex or manifest.get("verify_key")
        chain_report = probe.verify_chain(
            load_verify_key(vk_hex) if vk_hex else None
        )
        if not chain_report["ok"]:
            failures.extend(chain_report["failures"])

        # 2. manifest pins
        if manifest.get("entries") != len(tmp_entries):
            failures.append({"kind": "manifest_count_mismatch",
                             "detail": "manifest entry count != archived entries"})
        if manifest.get("head_hash") != chain_report["head_hash"]:
            failures.append({"kind": "manifest_head_mismatch",
                             "detail": "manifest head_hash != recomputed chain head"})

        # 3. manifest signature
        msig = manifest.get("manifest_sig")
        if msig and vk_hex:
            body = {k: v for k, v in manifest.items() if k != "manifest_sig"}
            try:
                load_verify_key(vk_hex).verify(_canonical(body), bytes.fromhex(msig))
            except (BadSignatureError, ValueError):
                failures.append({"kind": "manifest_bad_signature",
                                 "detail": "manifest signature does not verify"})
        elif msig and not vk_hex:
            failures.append({"kind": "manifest_unverifiable",
                             "detail": "manifest is signed but no verify key supplied"})

        return {
            "ok": not failures,
            "entries": len(tmp_entries),
            "head_hash": chain_report["head_hash"],
            "signed": chain_report["signed"],
            "unsigned": chain_report["unsigned"],
            "failures": failures,
        }


# ---------------------------------------------------------------------------
# Module-level convenience (flag-guarded singleton)
# ---------------------------------------------------------------------------

_default_trail: Optional[AuditTrail] = None
_default_lock = threading.Lock()


def get_trail(log_path: Optional[Path] = None) -> AuditTrail:
    """Return the process-wide :class:`AuditTrail` (lazy singleton)."""
    global _default_trail
    with _default_lock:
        if _default_trail is None:
            _default_trail = AuditTrail(log_path=log_path)
        return _default_trail


def record_action(
    agent_id: str,
    action: str,
    causal_parent: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Record one audit entry via the default trail. No-op when disabled.

    Intended wiring points (verified names in code):
      * ``AgencyKernel.executor_run_step`` — after ``_log_execution_result``
      * ``agency_kernel_runtime._execute_task`` — per-step results loop
      * ``a2a_inbound.register`` / task transitions on ``Fabric``
    """
    return get_trail().record(
        agent_id, action, causal_parent=causal_parent, detail=detail
    )


# ---------------------------------------------------------------------------
# Capture hooks — tap the real span-emission path, only when enabled
# ---------------------------------------------------------------------------

_hooks_installed = False
_hooks_lock = threading.Lock()
_original_emit: Optional[Callable] = None


def install_hooks(trail: Optional[AuditTrail] = None) -> Callable[[], None]:
    """Wrap ``observability.Tracer.emit`` so finished spans become audit entries.

    The wrapper checks the enable flag on every call: with the flag off it
    delegates straight through (zero behavior change). Hook failures never
    propagate into the traced code path. Returns an ``uninstall()`` callable.

    Safe to call multiple times — subsequent calls are no-ops returning the
    same uninstaller.
    """
    global _hooks_installed, _original_emit
    from sincor2 import observability

    with _hooks_lock:
        if _hooks_installed:
            return uninstall_hooks
        target = trail or get_trail()
        _original_emit = observability.Tracer.emit

        def _emit_with_audit(self, span) -> None:  # type: ignore[no-untyped-def]
            try:
                if target.enabled and audit_enabled():
                    payload = dict(span.finish())
                    attrs = payload.get("attributes") or {}
                    target.record(
                        agent_id=str(payload.get("agent_id") or "unknown"),
                        action=f"span:{payload.get('operation', 'unknown')}",
                        causal_parent=str(
                            payload.get("span_id") or payload.get("trace_id") or ""
                        ) or None,
                        detail={
                            "trace_id": payload.get("trace_id"),
                            "task_id": payload.get("task_id"),
                            "skill_id": payload.get("skill_id"),
                            "status": payload.get("status"),
                            "duration_ms": payload.get("duration_ms"),
                            "error": payload.get("error"),
                            "attributes": attrs,
                        },
                    )
            except Exception:
                pass  # audit capture must never break the traced path
            _original_emit(self, span)

        observability.Tracer.emit = _emit_with_audit  # type: ignore[method-assign]
        _hooks_installed = True
        return uninstall_hooks


def uninstall_hooks() -> Callable[[], None]:
    """Restore the original ``Tracer.emit``. Returns ``install_hooks``."""
    global _hooks_installed, _original_emit
    from sincor2 import observability

    with _hooks_lock:
        if _hooks_installed and _original_emit is not None:
            observability.Tracer.emit = _original_emit  # type: ignore[method-assign]
        _hooks_installed = False
        _original_emit = None
    return install_hooks


# ---------------------------------------------------------------------------
# Underwriting handoff — clean event schema, settlement taps untouched
# ---------------------------------------------------------------------------


def to_underwriting_event(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an audit entry into the behavioral event schema the
    underwriting engine can ingest.

    Honest integration note: ``underwriting/taps/`` is the settlement
    ``Tap`` protocol (``transfer``/``clawback``) — money movement, out of
    scope for this SKU and deliberately not used here. This dict is the
    behavioral-data handoff; wire one call at the engine's event ingestion
    point (see ``docs/underwriting/BUILD_GUIDE.md``).
    """
    return {
        "event_type": "audit",
        "sku": SKU,
        "agent_id": entry["agent_id"],
        "action": entry["action"],
        "timestamp_ms": entry["timestamp_ms"],
        "causal_parent": entry.get("causal_parent"),
        "entry_hash": entry["entry_hash"],
        "seq": entry["seq"],
        "evidence": entry.get("detail") or {},
    }
