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
- :class:`ComplianceOracle` — the configured oracle. Pluggable
  KYC/AML/geo plugins behind one :class:`OraclePlugin` interface, plus
  the registry-management API (sanctions updates, issuer allowlist,
  timelocked geo changes). Every registry mutation notifies the
  engine, which invalidates its 24h PASS cache immediately.
  :func:`build_reference_oracle` builds a deterministic oracle from the
  labeled reference fixture in ``defi/data/p20_reference_lists.json``.
- :class:`EvaluationContext` — the per-decision plugin input
  (account, attestation, timestamp).
- :class:`AuditTrail` — append-only decision log with hash chaining;
  no update/delete path exists. Off-chain, Ed25519-signed,
  hash-chained receipts verify end-to-end without repo access.
- :class:`GatekeeperHook` — the integration hook protocols call before
  accepting value: FAIL/REFER reverts with a queryable rejection code.
- Emergency override: guardian-role, single address, per-list, 24h
  auto-expiry, on-chain reason. An override of list X never clears a
  FAIL from list Y.

ORACLE STATUS (2026-09-29): the compliance oracle is built.
:class:`DecisionEngine` requires a configured :class:`ComplianceOracle`
— there is no unset-oracle (fail-open) mode in this module, and the
"no oracle configured" defer path from
``onchain/src/ComplianceGuard.sol`` ``isAllowed()`` has no counterpart
here: constructing an engine without an oracle raises. Reference
deployments use :func:`build_reference_oracle`; production deployments
wire production adapters into :class:`ComplianceOracle`. The on-chain
``ComplianceGuard.sol`` fail-open default is untouched (its flip is
still unratified), as is the time-boxed ``LEGACY_ALLOWLIST`` migration.
This module implements neither.

PENDING FOUNDER DECISION (not implemented here): the deep spec
proposes flipping ``onchain/src/ComplianceGuard.sol`` from fail-open
(when no oracle is set) to fail-closed, plus a time-boxed
``LEGACY_ALLOWLIST`` migration for pre-existing integrations. Both are
**unratified** as of 2026-09-27. This module is a conservative Python
reference that is fail-closed by construction and contains no
allowlist-migration path whatsoever.

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
import json
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
        list_age = now - self.list_updated_at
        if list_age > LIST_FRESHNESS_FAIL_SECONDS:
            return PluginResult("aml", Verdict.STALE,
                                _commit(account), self.VERSION,
                                "sanctions lists stale >48h")
        freshness_note = ""
        if list_age > LIST_FRESHNESS_ALERT_SECONDS:
            freshness_note = (f"; freshness alert: lists "
                              f"{list_age / 3600:.1f}h old")
        # Hop 0: the account itself is screened first — a sanctioned
        # account with no recorded fund flows still fails.
        if account in self.sanctions:
            return PluginResult("aml", Verdict.FAIL,
                                _commit(account), self.VERSION,
                                "account on sanctions list" + freshness_note)
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
                            f"taint at hop {hops_used}: {counterparty}"
                            + freshness_note)
                    if counterparty not in visited:
                        visited.add(counterparty)
                        nxt.append(counterparty)
            frontier = nxt
        assert hops_used <= TAINT_MAX_HOPS  # the lookback is bounded
        return PluginResult("aml", Verdict.PASS, _commit(account),
                            self.VERSION,
                            f"clean within {TAINT_MAX_HOPS} hops"
                            + freshness_note)

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


# -- compliance oracle ----------------------------------------------------------------------
# The oracle eliminates the "no oracle configured" defer path: it bundles
# the KYC/AML/geo adapters behind one pluggable interface and owns every
# registry mutation, so the decision engine can never run in an
# unset-oracle (fail-open) mode. Constructing a DecisionEngine without an
# oracle raises; there is no defer branch anywhere in this module.


@dataclass
class EvaluationContext:
    """Per-decision plugin input: the account under review, the KYC
    attestation it presented (None when it presented none), and the
    decision timestamp."""
    account: str
    attestation: Optional[KYCAttestation]
    now: float


class OraclePlugin:
    """Interface for oracle plugins. Subclass and override
    :meth:`evaluate` / :meth:`health`; register via
    :class:`ComplianceOracle` ``extra_plugins``."""

    @property
    def name(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    @property
    def version(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def evaluate(self, ctx: EvaluationContext) -> PluginResult:  # pragma: no cover - interface
        raise NotImplementedError

    def health(self) -> Dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError


def _outage_result(plugin: str, version: str, account: str,
                   err: Exception) -> PluginResult:
    # Provider outage fails closed — never degrades to PASS.
    return PluginResult(plugin, Verdict.FAIL,
                        _commit(account + "|" + plugin + "-outage"),
                        version, f"{plugin} provider error: {err}")


class _KYCPlugin(OraclePlugin):
    def __init__(self, adapter: KYCAdapter):
        self._adapter = adapter

    @property
    def name(self) -> str:
        return "kyc"

    @property
    def version(self) -> str:
        return self._adapter.VERSION

    def evaluate(self, ctx: EvaluationContext) -> PluginResult:
        if ctx.attestation is None:
            return PluginResult("kyc", Verdict.FAIL,
                                _commit(ctx.account + "|nokyc"),
                                self.version, "no attestation presented")
        try:
            return self._adapter.verify(ctx.attestation, now=ctx.now)
        except ComplianceError as e:
            return _outage_result("kyc", self.version, ctx.account, e)

    def health(self) -> Dict[str, Any]:
        return {"reachable": self._adapter.reachable,
                "issuers": len(self._adapter.issuer_keys)}


class _AMLPlugin(OraclePlugin):
    def __init__(self, adapter: AMLAdapter):
        self._adapter = adapter

    @property
    def name(self) -> str:
        return "aml"

    @property
    def version(self) -> str:
        return self._adapter.VERSION

    def evaluate(self, ctx: EvaluationContext) -> PluginResult:
        try:
            return self._adapter.screen(ctx.account, now=ctx.now)
        except ComplianceError as e:
            return _outage_result("aml", self.version, ctx.account, e)

    def health(self) -> Dict[str, Any]:
        age = time.time() - self._adapter.list_updated_at
        return {"reachable": self._adapter.reachable,
                "list_age_hours": round(age / 3600, 2),
                "freshness": ("stale" if age > LIST_FRESHNESS_FAIL_SECONDS
                              else "alert" if age > LIST_FRESHNESS_ALERT_SECONDS
                              else "fresh"),
                "sanctions_entries": len(self._adapter.sanctions)}


class _GeoPlugin(OraclePlugin):
    def __init__(self, registry: GeoRegistry):
        self._registry = registry

    @property
    def name(self) -> str:
        return "geo"

    @property
    def version(self) -> str:
        return self._registry.VERSION

    def evaluate(self, ctx: EvaluationContext) -> PluginResult:
        try:
            return self._registry.check(ctx.account)
        except ComplianceError as e:
            return _outage_result("geo", self.version, ctx.account, e)

    def health(self) -> Dict[str, Any]:
        return {"reachable": self._registry.reachable,
                "blocked_jurisdictions": sorted(
                    c for c, b in self._registry.blocked.items() if b),
                "timelock_pending": self._registry._pending is not None}


_REFERENCE_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "data", "p20_reference_lists.json")


def _reference_issuer_key(name: str) -> bytes:
    # Synthetic, deterministic test keys — obviously not real issuer
    # material. Derived from a fixed label so the reference oracle is
    # reproducible without storing secrets.
    return hashlib.sha256(b"p20-reference-issuer:" + name.encode()).digest()


def load_reference_data() -> Dict[str, Any]:
    """Load the labeled reference fixture. Every entry is synthetic
    test data — never real sanctions/issuer material."""
    with open(_REFERENCE_FIXTURE, encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("reference_only"):
        raise ComplianceError("reference fixture missing reference_only flag")
    return data


class ComplianceOracle:
    """The configured compliance oracle.

    Bundles the KYC/AML/geo adapters behind the :class:`OraclePlugin`
    interface and owns every registry mutation. Any list, issuer, or
    geo change notifies registered listeners (the decision engine
    registers its cache invalidation), so a PASS can never survive a
    registry update.
    """

    ORACLE_VERSION = "p20-oracle-v1"

    def __init__(self, kyc: KYCAdapter, aml: AMLAdapter, geo: GeoRegistry,
                 extra_plugins: Optional[List[OraclePlugin]] = None,
                 reference_only: bool = False):
        self.kyc = kyc
        self.aml = aml
        self.geo = geo
        self.reference_only = reference_only
        self.plugins: List[OraclePlugin] = [
            _KYCPlugin(kyc), _AMLPlugin(aml), _GeoPlugin(geo),
        ]
        for plugin in extra_plugins or []:
            if not isinstance(plugin, OraclePlugin):
                raise TypeError("extra_plugins must be OraclePlugin "
                                "instances")
            self.plugins.append(plugin)
        self._listeners: List[Any] = []

    # -- listener fan-out --------------------------------------------------
    def on_registry_update(self, listener: Any) -> None:
        self._listeners.append(listener)

    def _notify(self) -> None:
        for listener in self._listeners:
            listener()

    # -- plugin evaluation -------------------------------------------------
    def evaluate(self, account: str,
                 attestation: Optional[KYCAttestation],
                 now: float) -> List[PluginResult]:
        ctx = EvaluationContext(account, attestation, now)
        return [plugin.evaluate(ctx) for plugin in self.plugins]

    # -- registry management: every mutation invalidates downstream ------
    def update_sanctions(self, sanctions: Set[str],
                         now: Optional[float] = None) -> None:
        self.aml.sanctions = set(sanctions)
        self.aml.list_updated_at = now if now is not None else time.time()
        self._notify()

    def update_fund_flows(self,
                          fund_flows: Dict[str, List[Tuple[str, Set[str]]]],
                          now: Optional[float] = None) -> None:
        self.aml.fund_flows = {k: list(v) for k, v in fund_flows.items()}
        self.aml.list_updated_at = now if now is not None else time.time()
        self._notify()

    def register_issuer(self, issuer: str, key: bytes) -> None:
        self.kyc.issuer_keys[issuer] = key
        self._notify()

    def revoke_issuer(self, issuer: str) -> None:
        self.kyc.issuer_keys.pop(issuer, None)
        self._notify()

    def geo_propose_update(self, code: str, blocked: bool, caller: str,
                           now: Optional[float] = None) -> None:
        # Proposing does not change decisions; only execution does.
        self.geo.propose_update(code, blocked, caller, now=now)

    def geo_execute_update(self, caller: str,
                           now: Optional[float] = None) -> None:
        self.geo.execute_update(caller, now=now)
        self._notify()

    # -- observability -----------------------------------------------------
    def health(self) -> Dict[str, Any]:
        return {"oracle_version": self.ORACLE_VERSION,
                "reference_only": self.reference_only,
                "plugins": {p.name: p.health() for p in self.plugins}}


def build_reference_oracle(now: Optional[float] = None) -> ComplianceOracle:
    """Build the deterministic reference oracle from the labeled
    fixture. Same fixture + same ``now`` => same decisions. All data
    is synthetic and labeled reference-only; this oracle is for tests
    and design validation, never production screening."""
    data = load_reference_data()
    ts = now if now is not None else time.time()
    kyc = KYCAdapter({name: _reference_issuer_key(name)
                      for name in data["issuers"]})
    flows = {acct: [(cp, set(tags)) for cp, tags in edges]
             for acct, edges in data["fund_flows"].items()}
    aml = AMLAdapter(sanctions=set(data["sanctions"]), fund_flows=flows,
                     list_updated_at=ts)
    geo = GeoRegistry(admin=data["geo_admin"])
    # Genesis blocklist: the fixture's blocked jurisdictions are the
    # starting state; later changes go through the 48h timelock.
    for code in data["geo_blocked"]:
        geo.blocked[code] = True
    return ComplianceOracle(kyc, aml, geo, reference_only=True)


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


REVIEWER_VERSION = "reviewer-v1"


class DecisionEngine:
    """Fail-closed combination of the oracle's KYC/AML/geo signals.

    The engine requires a configured :class:`ComplianceOracle` — there
    is deliberately no unset-oracle mode, so the "no oracle
    configured" defer path cannot exist here. Registry mutations on
    the oracle invalidate the 24h PASS cache immediately via a
    listener registered at construction.
    """

    def __init__(self, oracle: ComplianceOracle, guardian: str):
        if oracle is None:
            raise ValueError(
                "DecisionEngine requires a configured ComplianceOracle: "
                "there is no unset-oracle (fail-open) mode")
        if not isinstance(oracle, ComplianceOracle):
            raise TypeError("oracle must be a ComplianceOracle instance")
        self.oracle = oracle
        self.guardian = guardian
        self.trail = AuditTrail()
        self.receipts = ReceiptChain()
        self._cache: Dict[str, Tuple[Verdict, float]] = {}
        self._overrides: List[Override] = []
        self._resolutions: Dict[str, Tuple[bool, str, str, float]] = {}
        oracle.on_registry_update(self.invalidate_cache)

    # Adapter access for callers/tests that work with the plugins
    # directly (attesting jurisdictions, signing test attestations).
    @property
    def kyc(self) -> KYCAdapter:
        return self.oracle.kyc

    @property
    def aml(self) -> AMLAdapter:
        return self.oracle.aml

    @property
    def geo(self) -> GeoRegistry:
        return self.oracle.geo

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

    # -- reviewer resolution -------------------------------------------------
    def resolve_referral(self, account: str, reviewer: str, approve: bool,
                         reason: str,
                         now: Optional[float] = None) -> None:
        """Record a reviewer's resolution of a REFER-blocked account.

        The resolution is one-shot: the next :meth:`decide` applies it
        exactly once, then the underlying plugins are re-evaluated from
        scratch. An approval is never cached — if the blocking
        condition persists, the account returns to REFER and needs a
        fresh review. Both the resolution and its application are
        appended to the audit trail.
        """
        if not reviewer:
            raise ValueError("reviewer identity required")
        if not reason:
            raise ValueError("resolution requires a reason")
        ts = now if now is not None else time.time()
        self._resolutions[account] = (bool(approve), reviewer, reason, ts)
        intended = Verdict.PASS if approve else Verdict.FAIL
        self.trail.append(
            account, intended,
            [PluginResult("reviewer", intended,
                          _commit(account + "|review|" + repr(ts)),
                          REVIEWER_VERSION,
                          f"{reviewer} recorded "
                          f"{'approval' if approve else 'rejection'}: "
                          f"{reason}")],
            now=ts)
        self.invalidate_cache()

    # -- the decision ------------------------------------------------------
    def decide(self, account: str,
               attestation: Optional[KYCAttestation] = None,
               now: Optional[float] = None) -> Tuple[Verdict, List[PluginResult]]:
        """One call, one verdict. FAIL if any plugin FAILs, is STALE, or
        is unreachable; REFER blocks until a reviewer resolves it; else
        PASS. The oracle is always configured — there is no defer."""
        now = now if now is not None else time.time()
        cached = self._cache.get(account)
        if cached and now - cached[1] < SCREENING_CACHE_SECONDS:
            verdict = cached[0]
            self.trail.append(account, verdict, [], now=now)
            return verdict, []

        results = self.oracle.evaluate(account, attestation, now)

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
        reviewer_applied = False
        if Verdict.FAIL in verdicts or Verdict.STALE in verdicts:
            verdict = Verdict.FAIL
        elif Verdict.REFER in verdicts:
            resolution = self._resolutions.pop(account, None)
            if resolution is None:
                verdict = Verdict.REFER
            else:
                approve, reviewer, reason, _ = resolution
                final.append(PluginResult(
                    "reviewer", Verdict.PASS if approve else Verdict.FAIL,
                    _commit(account + "|review-applied"),
                    REVIEWER_VERSION,
                    f"reviewer {reviewer} "
                    f"{'approved' if approve else 'rejected'}: {reason}"))
                verdict = Verdict.PASS if approve else Verdict.FAIL
                reviewer_applied = approve
        else:
            verdict = Verdict.PASS

        # A reviewer approval is one-shot and never cached: the next
        # decision re-runs the plugins, so a persisting block returns
        # to REFER instead of riding a stale approval.
        if verdict == Verdict.PASS and not reviewer_applied:
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
        "oracle": {
            "version": ComplianceOracle.ORACLE_VERSION,
            "reference_only": engine.oracle.reference_only,
            "plugins": {p.name: p.version for p in engine.oracle.plugins},
            "health": engine.oracle.health(),
        },
    }
