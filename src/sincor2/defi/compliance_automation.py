"""SINCOR DeFi P20 — Compliance Automation (reference build).

Python reference simulation of the gatekeeper decision engine behind
SKU ``SINCOR-DEFI-P20-COMPLY``. This protocol produces **pass/fail
decisions, never yield**, and **never auto-allowlists**:

- :class:`KYCAdapter` — verifies attestations (issuer allowlisted,
  90-day validity, nonce replay protection). Forged attestations FAIL;
  expired ones are STALE (which the engine treats as FAIL).
- :class:`AMLAdapter` — screens addresses and fund flows against
  sanctions/taint lists with a provably bounded 3-hop lookback;
  mixer/darknet/scam interaction is an automatic FAIL. Only
  ``sha3_256(attestation)`` commitments are recorded — no PII.
- :class:`GeoRegistry` — ISO-3166 jurisdiction allow/block registry;
  updates behind a 48h timelock; keyed on *attested* jurisdiction,
  never IP.
- :class:`DecisionEngine` — fail-closed combination: FAIL if *any*
  plugin returns FAIL, STALE, or is unreachable/erroring. REFER blocks
  until a reviewer resolves it. PASS decisions cache for 24h; any list
  or geo update invalidates the cache immediately.
- :class:`AuditTrail` — append-only decision log with hash chaining;
  no update/delete path exists. Off-chain, Ed25519-signed,
  hash-chained receipts verify end-to-end without repo access.
- :class:`GatekeeperHook` — the integration hook protocols call before
  accepting value: FAIL/REFER reverts with a queryable rejection code.
- Emergency override: guardian-role, single address, per-list, 24h
  auto-expiry, on-chain reason. An override of list X never clears a
  FAIL from list Y.

PENDING FOUNDER DECISION (not implemented here): the deep spec
proposes flipping ``onchain/src/ComplianceGuard.sol`` from fail-open
(when no oracle is set) to fail-closed, plus a time-boxed
``LEGACY_ALLOWLIST`` migration for pre-existing integrations. Both are
**unratified** as of 2026-09-27. This module implements neither: it is
a conservative Python reference that is fail-closed by construction
and contains no allowlist-migration path whatsoever.

Safety rules (hard):
- Default mode is DRY_RUN. Verdicts are emitted, never enforced
  on-chain; nothing here touches a chain, a wallet, or funds.
- There is no "unknown -> pass" path, no auto-allowlist, and no
  renewal of an expired override without a new reason.

This is a REFERENCE build for design validation and agent simulation —
not a deployed protocol.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

try:
    from nacl.signing import SigningKey, VerifyKey
    from nacl.exceptions import BadSignatureError
    _NACL = True
except ImportError:  # pragma: no cover
    _NACL = False

DRY_RUN = os.getenv("P20_DRY_RUN", "1").strip() != "0"

# Normative parameters (deep spec §1).
KYC_VALIDITY_SECONDS = 90 * 24 * 3600
SCREENING_CACHE_SECONDS = 24 * 3600
TAINT_MAX_HOPS = 3
OVERRIDE_TTL_SECONDS = 24 * 3600
GEO_TIMELOCK_SECONDS = 48 * 3600
LIST_FRESHNESS_ALERT_SECONDS = 24 * 3600
LIST_FRESHNESS_FAIL_SECONDS = 48 * 3600


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    STALE = "STALE"
    REFER = "REFER"


class ComplianceError(RuntimeError):
    """Base error for compliance rule violations."""


class DepositRejected(ComplianceError):
    """Hook refused the deposit; carries the rejection code."""

    def __init__(self, code: str):
        super().__init__(f"deposit rejected: {code}")
        self.code = code


class ReentrancyAttempt(ComplianceError):
    """Reentrant call into the gatekeeper hook."""


class TimelockPending(ComplianceError):
    """Registry update still inside the 48h timelock."""


class Unauthorized(ComplianceError):
    """Caller lacks the required role."""


# -- plugin results ------------------------------------------------------------------
@dataclass(frozen=True)
class PluginResult:
    plugin: str
    verdict: Verdict
    evidence_hash: str
    plugin_version: str
    detail: str = ""


def _commit(payload: str) -> str:
    return hashlib.sha3_256(payload.encode()).hexdigest()


# -- KYC adapter ----------------------------------------------------------------------
@dataclass
class KYCAttestation:
    issuer: str
    subject: str
    issued_at: float
    expires_at: float
    nonce: str
    signature: str  # hex HMAC-SHA256 over the canonical payload (reference)


class KYCAdapter:
    """Reference KYC verifier.

    The reference signature scheme is HMAC-SHA256 with the issuer's key
    (a stand-in for the EIP-712 signature the production contract
    verifies). Replay protection via a consumed-nonce set; validity
    capped at 90 days.
    """

    VERSION = "kyc-adapter-v1"

    def __init__(self, issuer_keys: Dict[str, bytes]):
        self.issuer_keys = dict(issuer_keys)
        self._consumed_nonces: Set[str] = set()
        self.reachable = True

    def _canonical(self, a: KYCAttestation) -> str:
        return "|".join((a.issuer, a.subject, repr(a.issued_at),
                         repr(a.expires_at), a.nonce))

    def sign(self, issuer: str, subject: str, issued_at: float,
             expires_at: float, nonce: str) -> KYCAttestation:
        key = self.issuer_keys[issuer]
        att = KYCAttestation(issuer, subject, issued_at, expires_at, nonce, "")
        sig = hmac.new(key, self._canonical(att).encode(),
                       hashlib.sha256).hexdigest()
        return KYCAttestation(issuer, subject, issued_at, expires_at, nonce,
                              sig)

    def verify(self, att: KYCAttestation,
               now: Optional[float] = None) -> PluginResult:
        now = now if now is not None else time.time()
        if not self.reachable:
            raise ComplianceError("KYC provider unreachable")
        key = self.issuer_keys.get(att.issuer)
        if key is None:
            return PluginResult("kyc", Verdict.FAIL,
                                _commit(self._canonical(att)), self.VERSION,
                                "issuer not allowlisted")
        expect = hmac.new(key, self._canonical(att).encode(),
                          hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, att.signature):
            return PluginResult("kyc", Verdict.FAIL,
                                _commit(self._canonical(att)), self.VERSION,
                                "signature invalid")
        if att.nonce in self._consumed_nonces:
            return PluginResult("kyc", Verdict.FAIL,
                                _commit(self._canonical(att)), self.VERSION,
                                "nonce replay")
        if att.expires_at - att.issued_at > KYC_VALIDITY_SECONDS:
            return PluginResult("kyc", Verdict.FAIL,
                                _commit(self._canonical(att)), self.VERSION,
                                "validity exceeds 90 days")
        if now > att.expires_at:
            return PluginResult("kyc", Verdict.STALE,
                                _commit(self._canonical(att)), self.VERSION,
                                "attestation expired")
        self._consumed_nonces.add(att.nonce)
        return PluginResult("kyc", Verdict.PASS,
                            _commit(self._canonical(att)), self.VERSION, "ok")


# -- AML adapter -----------------------------------------------------------------------
MIXER_TAGS = {"mixer", "darknet", "scam"}


class AMLAdapter:
    """Reference sanctions/taint screening with bounded 3-hop lookback."""

    VERSION = "aml-adapter-v1"

    def __init__(self, sanctions: Set[str],
                 fund_flows: Dict[str, List[Tuple[str, Set[str]]]],
                 list_updated_at: Optional[float] = None):
        # fund_flows: account -> [(counterparty, tags)]
        self.sanctions = set(sanctions)
        self.fund_flows = {k: list(v) for k, v in fund_flows.items()}
        self.reachable = True
        self.list_updated_at = (list_updated_at if list_updated_at is not None
                                else time.time())

    def screen(self, account: str,
               now: Optional[float] = None) -> PluginResult:
        now = now if now is not None else time.time()
        if not self.reachable:
            raise ComplianceError("AML provider unreachable")
        if now - self.list_updated_at > LIST_FRESHNESS_FAIL_SECONDS:
            return PluginResult("aml", Verdict.STALE,
                                _commit(account), self.VERSION,
                                "sanctions lists stale >48h")
        # Hop 0: the account itself is screened first — a sanctioned
        # account with no recorded fund flows still fails.
        if account in self.sanctions:
            return PluginResult("aml", Verdict.FAIL,
                                _commit(account), self.VERSION,
                                "account on sanctions list")
        hops_used = 0
        visited = {account}
        frontier = [account]
        while frontier and hops_used < TAINT_MAX_HOPS:
            hops_used += 1
            nxt: List[str] = []
            for node in frontier:
                for counterparty, tags in self.fund_flows.get(node, []):
                    if counterparty in self.sanctions or tags & MIXER_TAGS:
                        return PluginResult(
                            "aml", Verdict.FAIL, _commit(account),
                            self.VERSION,
                            f"taint at hop {hops_used}: {counterparty}")
                    if counterparty not in visited:
                        visited.add(counterparty)
                        nxt.append(counterparty)
            frontier = nxt
        assert hops_used <= TAINT_MAX_HOPS  # the lookback is bounded
        return PluginResult("aml", Verdict.PASS, _commit(account),
                            self.VERSION,
                            f"clean within {TAINT_MAX_HOPS} hops")

    @property
    def max_hops(self) -> int:
        return TAINT_MAX_HOPS


# -- geo registry -----------------------------------------------------------------------
class GeoRegistry:
    """ISO-3166 jurisdiction block registry with 48h timelocked updates."""

    VERSION = "geo-registry-v1"

    def __init__(self, admin: str):
        self.admin = admin
        self.blocked: Dict[str, bool] = {}
        self._pending: Optional[Dict[str, Any]] = None
        self.reachable = True
        # Attested jurisdiction per account (NOT IP-derived).
        self.attested_jurisdiction: Dict[str, str] = {}

    def attest_jurisdiction(self, account: str, iso_code: str) -> None:
        if len(iso_code) != 2 or not iso_code.isalpha():
            raise ValueError("ISO-3166 alpha-2 code required")
        self.attested_jurisdiction[account] = iso_code.upper()

    def propose_update(self, code: str, blocked: bool, caller: str,
                       now: Optional[float] = None) -> None:
        if caller != self.admin:
            raise Unauthorized("geo admin only")
        self._pending = {"code": code.upper(), "blocked": blocked,
                         "executable_at": (now or time.time())
                         + GEO_TIMELOCK_SECONDS}

    def execute_update(self, caller: str,
                       now: Optional[float] = None) -> None:
        if caller != self.admin:
            raise Unauthorized("geo admin only")
        if not self._pending:
            raise ComplianceError("no pending geo update")
        if (now or time.time()) < self._pending["executable_at"]:
            raise TimelockPending("48h geo timelock not elapsed")
        self.blocked[self._pending["code"]] = self._pending["blocked"]
        self._pending = None

    def check(self, account: str) -> PluginResult:
        if not self.reachable:
            raise ComplianceError("geo provider unreachable")
        jurisdiction = self.attested_jurisdiction.get(account)
        if jurisdiction is None:
            return PluginResult("geo", Verdict.REFER,
                                _commit(account), self.VERSION,
                                "no attested jurisdiction")
        if self.blocked.get(jurisdiction, False):
            return PluginResult("geo", Verdict.FAIL,
                                _commit(account), self.VERSION,
                                f"blocked jurisdiction {jurisdiction}")
        return PluginResult("geo", Verdict.PASS, _commit(account),
                            self.VERSION, f"allowed {jurisdiction}")


# -- audit trail --------------------------------------------------------------------------
@dataclass
class TrailRecord:
    account: str
    verdict: str
    evidence_hash: str
    plugin_versions: Dict[str, str]
    timestamp: float
    prev_hash: str
    record_hash: str


class AuditTrail:
    """Append-only decision log with hash chaining. No update/delete path."""

    def __init__(self):
        self._records: List[TrailRecord] = []

    def append(self, account: str, verdict: Verdict,
               results: List[PluginResult],
               now: Optional[float] = None) -> TrailRecord:
        ts = now or time.time()
        prev = self._records[-1].record_hash if self._records else "GENESIS"
        evidence = _commit("|".join(r.evidence_hash for r in results))
        versions = {r.plugin: r.plugin_version for r in results}
        body = "|".join((account, verdict.value, evidence, repr(ts), prev))
        rec = TrailRecord(account, verdict.value, evidence, versions, ts,
                          prev, _commit(body))
        self._records.append(rec)
        return rec

    def __len__(self) -> int:
        return len(self._records)

    def records(self) -> Tuple[TrailRecord, ...]:
        return tuple(self._records)

    def verify_chain(self) -> bool:
        prev = "GENESIS"
        for rec in self._records:
            if rec.prev_hash != prev:
                return False
            body = "|".join((rec.account, rec.verdict, rec.evidence_hash,
                             repr(rec.timestamp), rec.prev_hash))
            if _commit(body) != rec.record_hash:
                return False
            prev = rec.record_hash
        return True


# -- off-chain signed receipts ---------------------------------------------------------------
@dataclass
class Receipt:
    account: str
    verdict: str
    evidence_hash: str
    timestamp: float
    prev_hash: str
    signature: str  # hex Ed25519 signature


class ReceiptChain:
    """Ed25519-signed, hash-chained receipts verifiable without repo access."""

    def __init__(self):
        if not _NACL:  # pragma: no cover
            raise RuntimeError("pynacl required for receipt signing")
        self._signing_key = SigningKey.generate()
        self.verify_key: VerifyKey = self._signing_key.verify_key
        self._receipts: List[Receipt] = []

    @property
    def public_key_hex(self) -> str:
        return self.verify_key.encode().hex()

    def issue(self, account: str, verdict: Verdict,
              evidence_hash: str,
              now: Optional[float] = None) -> Receipt:
        ts = now or time.time()
        prev = (self._receipts[-1].signature if self._receipts
                else "GENESIS")
        body = "|".join((account, verdict.value, evidence_hash,
                         repr(ts), prev)).encode()
        sig = self._signing_key.sign(body).signature.hex()
        rec = Receipt(account, verdict.value, evidence_hash, ts, prev, sig)
        self._receipts.append(rec)
        return rec

    def receipts(self) -> Tuple[Receipt, ...]:
        return tuple(self._receipts)

    @staticmethod
    def verify(receipts: List[Receipt], public_key_hex: str) -> bool:
        """Verify without repo access: only receipts + the public key."""
        if not _NACL:  # pragma: no cover
            return False
        vk = VerifyKey(bytes.fromhex(public_key_hex))
        prev = "GENESIS"
        for r in receipts:
            if r.prev_hash != prev:
                return False
            body = "|".join((r.account, r.verdict, r.evidence_hash,
                             repr(r.timestamp), r.prev_hash)).encode()
            try:
                vk.verify(body, bytes.fromhex(r.signature))
            except BadSignatureError:
                return False
            prev = r.signature
        return True


# -- decision engine ---------------------------------------------------------------------------
@dataclass
class Override:
    account: str
    list_name: str   # "kyc" | "aml" | "geo"
    reason: str
    granted_by: str
    granted_at: float
    expires_at: float


class DecisionEngine:
    """Fail-closed combination of KYC/AML/geo signals."""

    def __init__(self, kyc: KYCAdapter, aml: AMLAdapter, geo: GeoRegistry,
                 guardian: str):
        self.kyc = kyc
        self.aml = aml
        self.geo = geo
        self.guardian = guardian
        self.trail = AuditTrail()
        self.receipts = ReceiptChain()
        self._cache: Dict[str, Tuple[Verdict, float]] = {}
        self._overrides: List[Override] = []

    # -- emergency override ------------------------------------------------
    def grant_override(self, account: str, list_name: str, reason: str,
                       caller: str, now: Optional[float] = None) -> Override:
        if caller != self.guardian:
            raise Unauthorized("guardian only")
        if list_name not in ("kyc", "aml", "geo"):
            raise ValueError("override is per-list: kyc | aml | geo")
        if not reason:
            raise ValueError("override requires an on-chain reason")
        ts = now or time.time()
        ov = Override(account, list_name, reason, caller, ts,
                      ts + OVERRIDE_TTL_SECONDS)
        self._overrides.append(ov)
        self.invalidate_cache()
        return ov

    def _override_active(self, account: str, list_name: str,
                         now: float) -> bool:
        return any(o.account == account and o.list_name == list_name
                   and o.expires_at > now for o in self._overrides)

    def invalidate_cache(self) -> None:
        self._cache.clear()

    # -- the decision ------------------------------------------------------
    def decide(self, account: str,
               attestation: Optional[KYCAttestation] = None,
               now: Optional[float] = None) -> Tuple[Verdict, List[PluginResult]]:
        """One call, one verdict. FAIL if any plugin FAILs, is STALE, or
        is unreachable; REFER blocks until resolved; else PASS."""
        now = now if now is not None else time.time()
        cached = self._cache.get(account)
        if cached and now - cached[1] < SCREENING_CACHE_SECONDS:
            verdict = cached[0]
            self.trail.append(account, verdict, [], now=now)
            return verdict, []

        results: List[PluginResult] = []
        try:
            if attestation is None:
                results.append(PluginResult(
                    "kyc", Verdict.FAIL, _commit(account + "|nokyc"),
                    KYCAdapter.VERSION, "no attestation presented"))
            else:
                results.append(self.kyc.verify(attestation, now=now))
        except ComplianceError as e:
            # Provider outage fails closed — never degrades to PASS.
            results.append(PluginResult("kyc", Verdict.FAIL,
                                        _commit(account + "|kyc-outage"),
                                        KYCAdapter.VERSION, str(e)))
        try:
            results.append(self.aml.screen(account, now=now))
        except ComplianceError as e:
            results.append(PluginResult("aml", Verdict.FAIL,
                                        _commit(account + "|aml-outage"),
                                        AMLAdapter.VERSION, str(e)))
        try:
            results.append(self.geo.check(account))
        except ComplianceError as e:
            results.append(PluginResult("geo", Verdict.FAIL,
                                        _commit(account + "|geo-outage"),
                                        GeoRegistry.VERSION, str(e)))

        # Per-list emergency overrides: an override of list X never
        # clears a FAIL from list Y.
        final = []
        for r in results:
            if r.verdict == Verdict.FAIL and self._override_active(
                    account, r.plugin, now):
                final.append(PluginResult(
                    r.plugin, Verdict.PASS, r.evidence_hash,
                    r.plugin_version,
                    "emergency override active (24h, per-list)"))
            else:
                final.append(r)

        verdicts = {r.verdict for r in final}
        if Verdict.FAIL in verdicts or Verdict.STALE in verdicts:
            verdict = Verdict.FAIL
        elif Verdict.REFER in verdicts:
            verdict = Verdict.REFER
        else:
            verdict = Verdict.PASS

        if verdict == Verdict.PASS:
            self._cache[account] = (verdict, now)
        self.trail.append(account, verdict, final, now=now)
        self.receipts.issue(account, verdict,
                            _commit("|".join(r.evidence_hash
                                             for r in final)), now=now)
        return verdict, final


# -- integration hook ----------------------------------------------------------------------------
REJECTION_CODES = {
    Verdict.FAIL: "COMPLY_FAIL",
    Verdict.REFER: "COMPLY_REFER",
}


class GatekeeperHook:
    """The modifier-style hook protocols call before accepting value."""

    def __init__(self, engine: DecisionEngine):
        self.engine = engine
        self._in_check = False
        self._last_codes: Dict[str, str] = {}

    def check(self, account: str,
              attestation: Optional[KYCAttestation] = None,
              now: Optional[float] = None) -> Verdict:
        if self._in_check:
            raise ReentrancyAttempt("reentrant hook call")
        self._in_check = True
        try:
            verdict, _ = self.engine.decide(account, attestation, now=now)
            return verdict
        finally:
            self._in_check = False

    def rejection_code(self, account: str) -> Optional[str]:
        return self._last_codes.get(account)

    def gated_deposit(self, vault: Dict[str, int], account: str,
                      amount_wei: int,
                      attestation: Optional[KYCAttestation] = None,
                      now: Optional[float] = None) -> int:
        """Hooked vault deposit: FAIL/REFER reverts, balance unchanged."""
        verdict = self.check(account, attestation, now=now)
        if verdict != Verdict.PASS:
            code = REJECTION_CODES[verdict]
            self._last_codes[account] = code
            raise DepositRejected(code)
        vault[account] = vault.get(account, 0) + amount_wei
        return vault[account]


# -- status feed ----------------------------------------------------------------------
def status_payload(engine: DecisionEngine) -> Dict[str, Any]:
    return {
        "product": "SINCOR-DEFI-P20-COMPLY",
        "ts": time.time(),
        "mode": "dry_run" if DRY_RUN else "live_intent",
        "decisions_recorded": len(engine.trail),
        "chain_valid": engine.trail.verify_chain(),
        "cache_entries": len(engine._cache),
        "fee_bps": 0,
    }
