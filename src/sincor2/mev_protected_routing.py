"""CNP-9: MEV-protected private routing for sealed-bid auctions.

Offchain routing layer for the A2A sealed-bid commit/reveal pipeline
(``sincor2.a2a_inbound_market``). It answers one question: **who can see a
commit/reveal envelope, and when?**

What this module IS
-------------------
* A *pluggable-backend* routing abstraction. The router authenticates every
  envelope (HMAC binds task/agent/kind/payload), enforces reveal-window
  discipline *before* an envelope ever reaches the auction pipeline, and
  hands the envelope to a backend that controls **pre-inclusion
  observability** — i.e. whether a mempool observer can read a pending
  envelope before it is applied.
* Two *local simulation* backends ship today:
  ``LocalPublicMempoolBackend``  (pending envelopes are world-readable —
  models the public mempool) and ``LocalPrivateRoutingBackend``
  (pending envelopes are visible to nobody until inclusion — models a
  private relay such as Flashbots Protect, **simulated in-process**).
* Inclusion still goes through the real pipeline: ``commit_bid``,
  ``reveal_bid``, ``close_auction``. The router is policy + transport; the
  auction pipeline remains the authority on state.

What this module is NOT
-----------------------
* NOT a live private-mempool integration. ``PrivateMempoolBackend`` and
  ``ThresholdDecryptionBackend`` exist only as documented interfaces whose
  methods raise ``PrivateRoutingNotAuthorized``. Wiring them to a real
  relay / threshold network requires explicit founder approval (CNP-9
  acceptance criterion) and is deliberately impossible to enable by
  accident — there is no env-var kill-switch, no silent fallback.
* NOT a replacement for commit/reveal. Commit/reveal already gives
  bid-value privacy *until reveal*; this layer protects the *pending*
  envelope (pre-inclusion) and hardens the reveal window. Revealed bids are
  public by design after inclusion — the auction must publish them.
* NOT MEV *capture*. ``sincor2.defi.mev_capture`` is a separate DeFi
  product. This module is MEV *protection* for auctions; do not confuse
  the two.

Threat model: docs/architecture/CNP9_MEV_THREAT_MODEL.md
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import os
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor.cnp9.routing")

PROTOCOL_VERSION = "cnp9/1"
ROUTER_SECRET_ENV = "SINCOR_CNP9_ROUTER_SECRET"

# Envelopes older than this (relative to router time) are rejected. Bounds
# replay of captured envelopes and clock-skew abuse.
DEFAULT_MAX_ENVELOPE_AGE_MS = 60_000

KIND_COMMIT = "commit"
KIND_REVEAL = "reveal"
KIND_CLOSE = "close"
_KINDS = (KIND_COMMIT, KIND_REVEAL, KIND_CLOSE)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class RoutingError(Exception):
    """Base class for CNP-9 routing failures."""


class InvalidEnvelope(RoutingError):
    """Envelope authentication, structure, or task binding failed."""


class StaleEnvelope(RoutingError):
    """Envelope is outside the accepted freshness window."""


class ReplayedEnvelope(RoutingError):
    """Envelope id has already been admitted (replay attempt)."""


class WindowViolation(RoutingError):
    """Envelope violates commit/reveal window discipline."""


class PrivateRoutingNotAuthorized(RoutingError):
    """A live private-routing backend was requested without founder approval.

    Raised unconditionally — there is no configuration path that enables
    these backends in this wave. Founder approval + a dedicated wiring
    change are required.
    """


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoutingEnvelope:
    """An authenticated auction message.

    ``payload`` by kind:
      commit -> {"commitment": "0x<64 hex>"}
      reveal -> {"bid_axm": float, "nonce": "0x<64 hex>",
                 "estimated_seconds": int}
      close  -> {}

    The MAC binds (protocol_version, envelope_id, task_id, agent_id, kind,
    issued_at_ms, payload). Cross-task replay of a captured envelope fails
    MAC verification because task_id is inside the MAC input.
    """

    protocol_version: str
    envelope_id: str
    task_id: str
    agent_id: str
    kind: str
    payload: Dict[str, Any]
    issued_at_ms: int
    mac: str = ""

    def _canonical(self) -> bytes:
        payload_json = json.dumps(self.payload, sort_keys=True,
                                  separators=(",", ":"))
        body = "|".join([
            self.protocol_version,
            self.envelope_id,
            self.task_id,
            self.agent_id,
            self.kind,
            str(self.issued_at_ms),
            payload_json,
        ])
        return body.encode("utf-8")

    def with_mac(self, secret: bytes) -> "RoutingEnvelope":
        digest = _hmac.new(secret, self._canonical(),
                           hashlib.sha256).hexdigest()
        return RoutingEnvelope(
            protocol_version=self.protocol_version,
            envelope_id=self.envelope_id,
            task_id=self.task_id,
            agent_id=self.agent_id,
            kind=self.kind,
            payload=dict(self.payload),
            issued_at_ms=self.issued_at_ms,
            mac=digest,
        )

    def verify_mac(self, secret: bytes) -> bool:
        if not self.mac:
            return False
        expected = _hmac.new(secret, self._canonical(),
                             hashlib.sha256).hexdigest()
        return _hmac.compare_digest(expected, self.mac)


@dataclass
class RoutingReceipt:
    """Audit record for one routed envelope.

    ``observable_pre_inclusion`` is the crux of the MEV protection claim:
    True  -> a public observer could read this envelope before inclusion
             (public-mempool backend).
    False -> no public observer could read it before inclusion
             (private-simulation backend).
    """

    envelope_id: str
    task_id: str
    agent_id: str
    kind: str
    backend: str
    submitted_at_ms: int
    observable_pre_inclusion: bool
    included_at_ms: Optional[int] = None
    inclusion_seq: Optional[int] = None
    status: str = "submitted"  # submitted | included | failed
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class PrivateRouterBackend(ABC):
    """Pluggable transport controlling pre-inclusion observability.

    ``submit`` queues an envelope. ``pending_view`` returns what a *public*
    observer (mempool sniffer) can read right now. ``take_for_inclusion``
    pops the next envelope for application to the auction pipeline.
    """

    name: str = "abstract"
    # True when pending envelopes are world-readable (public mempool).
    exposes_pending: bool = True

    @abstractmethod
    def submit(self, envelope: RoutingEnvelope) -> None:
        raise NotImplementedError

    @abstractmethod
    def pending_view(self) -> List[RoutingEnvelope]:
        """What a public observer can read before inclusion."""
        raise NotImplementedError

    @abstractmethod
    def take_for_inclusion(self) -> Optional[RoutingEnvelope]:
        raise NotImplementedError

    @abstractmethod
    def queued(self) -> int:
        raise NotImplementedError


class LocalPublicMempoolBackend(PrivateRouterBackend):
    """Simulated PUBLIC mempool: every pending envelope is world-readable.

    Models today's reality — commit/reveal envelopes broadcast in the
    clear are visible to searchers before inclusion. Used as the control
    arm in front-running simulations.
    """

    name = "public-mempool-sim"
    exposes_pending = True

    def __init__(self) -> None:
        self._pending: List[RoutingEnvelope] = []
        self._lock = threading.Lock()

    def submit(self, envelope: RoutingEnvelope) -> None:
        with self._lock:
            self._pending.append(envelope)

    def pending_view(self) -> List[RoutingEnvelope]:
        with self._lock:
            return list(self._pending)

    def take_for_inclusion(self) -> Optional[RoutingEnvelope]:
        with self._lock:
            return self._pending.pop(0) if self._pending else None

    def queued(self) -> int:
        with self._lock:
            return len(self._pending)


class LocalPrivateRoutingBackend(PrivateRouterBackend):
    """Simulated PRIVATE relay: pending envelopes are visible to NOBODY.

    Models a private-mempool / direct-to-builder relay **in-process**: the
    envelope leaves the bidder's hands and becomes visible only at
    inclusion time. ``pending_view`` always returns [] — this is the arm
    that defeats the mempool sniffer in the front-running simulation.

    This is a simulation, not a live relay. See PrivateMempoolBackend.
    """

    name = "private-routing-sim"
    exposes_pending = False

    def __init__(self) -> None:
        self._queue: List[RoutingEnvelope] = []
        self._lock = threading.Lock()

    def submit(self, envelope: RoutingEnvelope) -> None:
        with self._lock:
            self._queue.append(envelope)

    def pending_view(self) -> List[RoutingEnvelope]:
        # Deliberately empty: no public observer exists on this path.
        return []

    def take_for_inclusion(self) -> Optional[RoutingEnvelope]:
        with self._lock:
            return self._queue.pop(0) if self._queue else None

    def queued(self) -> int:
        with self._lock:
            return len(self._queue)


class PrivateMempoolBackend(PrivateRouterBackend):
    """FUTURE interface: live private-mempool relay (e.g. Flashbots-style).

    NOT IMPLEMENTED and NOT AUTHORIZED. Every method raises
    ``PrivateRoutingNotAuthorized``. Wiring this to a real relay requires
    explicit founder approval per the CNP-9 acceptance criteria.

    Documented trust assumptions (future-backend risks, not live claims):
      * The relay operator / block builder is trusted not to read, copy,
        or front-run the bundle contents.
      * Bundle atomicity and inclusion are best-effort; there is no
        censorship-resistance guarantee against a colluding builder.
      * Relay liveness is an external dependency: if the relay is down,
        envelopes do not move (fail-closed here — they are never silently
        re-routed over the public path).
    """

    name = "private-mempool"
    exposes_pending = False

    def _blocked(self) -> PrivateRoutingNotAuthorized:
        return PrivateRoutingNotAuthorized(
            "live private-mempool routing is not authorized in this wave; "
            "founder approval and a dedicated wiring change are required "
            "(CNP-9). No silent fallback to a public path is performed."
        )

    def submit(self, envelope: RoutingEnvelope) -> None:
        raise self._blocked()

    def pending_view(self) -> List[RoutingEnvelope]:
        raise self._blocked()

    def take_for_inclusion(self) -> Optional[RoutingEnvelope]:
        raise self._blocked()

    def queued(self) -> int:
        raise self._blocked()


class ThresholdDecryptionBackend(PrivateRouterBackend):
    """FUTURE interface: threshold-decryption mempool.

    Envelopes would be encrypted to a threshold committee and decrypted
    only at inclusion. NOT IMPLEMENTED and NOT AUTHORIZED — every method
    raises ``PrivateRoutingNotAuthorized``.

    Documented trust assumptions (future-backend risks, not live claims):
      * Liveness of the decryption committee (DKG participants must be
        online at reveal time or envelopes stall).
      * A threshold of key holders is trusted not to collude to decrypt
        early; the collusion threshold must be sized against the value at
        stake in the auction.
      * Decryption-delay tuning: too fast re-opens the sniping window, too
        slow stalls the reveal window.
    """

    name = "threshold-decryption"
    exposes_pending = False

    def _blocked(self) -> PrivateRoutingNotAuthorized:
        return PrivateRoutingNotAuthorized(
            "threshold-decryption routing is not authorized in this wave; "
            "founder approval and a dedicated wiring change are required "
            "(CNP-9). No silent fallback to a public path is performed."
        )

    def submit(self, envelope: RoutingEnvelope) -> None:
        raise self._blocked()

    def pending_view(self) -> List[RoutingEnvelope]:
        raise self._blocked()

    def take_for_inclusion(self) -> Optional[RoutingEnvelope]:
        raise self._blocked()

    def queued(self) -> int:
        raise self._blocked()


def make_backend(name: str) -> PrivateRouterBackend:
    """Backend factory. Live backends are refused, not degraded."""
    key = str(name or "").strip().lower()
    if key in ("public", "public-mempool", "public-mempool-sim"):
        return LocalPublicMempoolBackend()
    if key in ("private", "private-sim", "private-routing-sim"):
        return LocalPrivateRoutingBackend()
    if key in ("private-mempool", "flashbots", "relay"):
        raise PrivateRoutingNotAuthorized(
            "backend 'private-mempool' requires founder approval (CNP-9); "
            "refusing to construct. Use 'private-sim' for simulations."
        )
    if key in ("threshold-decryption", "threshold", "tdh2"):
        raise PrivateRoutingNotAuthorized(
            "backend 'threshold-decryption' requires founder approval "
            "(CNP-9); refusing to construct. Use 'private-sim' for "
            "simulations."
        )
    raise ValueError(f"unknown routing backend: {name!r}")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class MEVProtectedRouter:
    """Policy + transport for sealed-bid auction envelopes.

    Admission order (fail-closed):
      1. structural validation (kind, task_id, payload shape)
      2. MAC verification (constant-time)
      3. freshness (issued_at within max_envelope_age_ms)
      4. replay (envelope_id unseen)
      5. window / task policy per kind (commit vs reveal discipline)
    Only then is the envelope handed to the backend. Inclusion applies the
    envelope to the real auction pipeline (commit_bid / reveal_bid /
    close_auction), which remains the authority on state.
    """

    def __init__(
        self,
        backend: PrivateRouterBackend,
        *,
        router_secret: Optional[bytes] = None,
        max_envelope_age_ms: int = DEFAULT_MAX_ENVELOPE_AGE_MS,
        time_fn: Any = None,
    ) -> None:
        self.backend = backend
        secret = router_secret
        if secret is None:
            env_secret = (os.environ.get(ROUTER_SECRET_ENV) or "").strip()
            secret = env_secret.encode("utf-8") if env_secret else os.urandom(32)
        if not secret:
            raise ValueError("router_secret must be non-empty")
        self._secret = bytes(secret)
        self._max_age_ms = int(max_envelope_age_ms)
        # time_fn lets tests drive the clock; defaults to the auction clock.
        if time_fn is None:
            from sincor2.a2a_inbound import _now_ms
            time_fn = _now_ms
        self._now_ms = time_fn
        self._lock = threading.Lock()
        # envelope_id -> issued_at_ms; pruned on every admission.
        self._seen: Dict[str, int] = {}
        self._receipts: List[RoutingReceipt] = []
        self._inclusion_seq = 0

    # -- envelope construction ------------------------------------------

    def make_envelope(
        self,
        kind: str,
        task_id: str,
        agent_id: str,
        payload: Dict[str, Any],
        *,
        issued_at_ms: Optional[int] = None,
    ) -> RoutingEnvelope:
        if kind not in _KINDS:
            raise InvalidEnvelope(f"unknown envelope kind: {kind!r}")
        task_id = str(task_id or "").strip()
        agent_id = str(agent_id or "").strip()
        if not task_id or not agent_id:
            raise InvalidEnvelope("task_id and agent_id are required")
        envelope = RoutingEnvelope(
            protocol_version=PROTOCOL_VERSION,
            envelope_id="env_" + uuid.uuid4().hex[:16],
            task_id=task_id,
            agent_id=agent_id,
            kind=kind,
            payload=dict(payload or {}),
            issued_at_ms=int(issued_at_ms if issued_at_ms is not None
                             else self._now_ms()),
        )
        return envelope.with_mac(self._secret)

    # -- public routing API ----------------------------------------------

    def route_commit(self, task_id: str, agent_id: str,
                     commitment: Any) -> RoutingReceipt:
        """Route a sealed-bid commitment (commit phase)."""
        raw = str(commitment or "").strip().lower()
        if raw.startswith("0x"):
            raw = raw[2:]
        if len(raw) != 64:
            raise InvalidEnvelope("commitment must be 32 bytes as hex")
        if raw == "00" * 32:
            raise InvalidEnvelope("commitment must not be zero")
        try:
            bytes.fromhex(raw)
        except ValueError:
            raise InvalidEnvelope("commitment is not valid hex")
        envelope = self.make_envelope(
            KIND_COMMIT, task_id, agent_id, {"commitment": "0x" + raw})
        return self.submit_envelope(envelope)

    def route_reveal(self, task_id: str, agent_id: str, bid_axm: float,
                     nonce: Any,
                     estimated_seconds: int = 3600) -> RoutingReceipt:
        """Route a sealed-bid reveal (reveal phase, hardened window)."""
        bid_axm = float(bid_axm)
        if bid_axm <= 0:
            raise InvalidEnvelope("bid_axm must be positive")
        raw = str(nonce or "").strip().lower()
        if raw.startswith("0x"):
            raw = raw[2:]
        if len(raw) != 64:
            raise InvalidEnvelope("nonce must be 32 bytes as hex")
        try:
            bytes.fromhex(raw)
        except ValueError:
            raise InvalidEnvelope("nonce is not valid hex")
        if int(estimated_seconds) <= 0:
            raise InvalidEnvelope("estimated_seconds must be positive")
        envelope = self.make_envelope(
            KIND_REVEAL, task_id, agent_id,
            {"bid_axm": bid_axm, "nonce": "0x" + raw,
             "estimated_seconds": int(estimated_seconds)})
        return self.submit_envelope(envelope)

    def route_close(self, task_id: str,
                    agent_id: str = "permissionless") -> RoutingReceipt:
        """Route a permissionless auction close (timeout)."""
        envelope = self.make_envelope(KIND_CLOSE, task_id, agent_id, {})
        return self.submit_envelope(envelope)

    def submit_envelope(self, envelope: RoutingEnvelope) -> RoutingReceipt:
        """Admit an envelope (auth + freshness + replay + window policy)
        and hand it to the backend. Returns the submission receipt."""
        self._admit(envelope)
        submitted_at = self._now_ms()
        self.backend.submit(envelope)
        receipt = RoutingReceipt(
            envelope_id=envelope.envelope_id,
            task_id=envelope.task_id,
            agent_id=envelope.agent_id,
            kind=envelope.kind,
            backend=self.backend.name,
            submitted_at_ms=submitted_at,
            observable_pre_inclusion=self.backend.exposes_pending,
        )
        with self._lock:
            self._receipts.append(receipt)
        # Never log bid values or nonces — envelope ids and kinds only.
        logger.info("cnp9 envelope admitted kind=%s task=%s backend=%s "
                    "observable_pre_inclusion=%s",
                    envelope.kind, envelope.task_id, self.backend.name,
                    receipt.observable_pre_inclusion)
        return receipt

    # -- inclusion --------------------------------------------------------

    def include_next(self) -> Optional[RoutingReceipt]:
        """Apply the next queued envelope to the auction pipeline."""
        envelope = self.backend.take_for_inclusion()
        if envelope is None:
            return None
        receipt = self._receipt_for(envelope.envelope_id)
        included_at = self._now_ms()
        try:
            self._apply(envelope)
            status, error = "included", None
        except Exception as err:  # pipeline is the authority; record, don't crash
            status, error = "failed", f"{type(err).__name__}: {err}"
            logger.warning("cnp9 envelope inclusion failed kind=%s task=%s: %s",
                           envelope.kind, envelope.task_id, error)
        with self._lock:
            self._inclusion_seq += 1
            if receipt is not None:
                receipt.status = status
                receipt.error = error
                receipt.included_at_ms = included_at
                receipt.inclusion_seq = self._inclusion_seq
        return receipt

    def include_all(self) -> List[RoutingReceipt]:
        """Apply every queued envelope, in order. Returns the receipts."""
        out: List[RoutingReceipt] = []
        while True:
            receipt = self.include_next()
            if receipt is None:
                break
            out.append(receipt)
        return out

    def _apply(self, envelope: RoutingEnvelope) -> None:
        from sincor2.a2a_inbound_market import (
            close_auction, commit_bid, reveal_bid,
        )
        if envelope.kind == KIND_COMMIT:
            commit_bid(envelope.task_id, envelope.agent_id,
                       envelope.payload["commitment"])
        elif envelope.kind == KIND_REVEAL:
            reveal_bid(envelope.task_id, envelope.agent_id,
                       float(envelope.payload["bid_axm"]),
                       envelope.payload["nonce"],
                       int(envelope.payload.get("estimated_seconds") or 3600))
        elif envelope.kind == KIND_CLOSE:
            close_auction(envelope.task_id)
        else:  # pragma: no cover — admission rejects unknown kinds
            raise InvalidEnvelope(f"unknown kind: {envelope.kind!r}")

    # -- admission --------------------------------------------------------

    def _admit(self, envelope: RoutingEnvelope) -> None:
        now = self._now_ms()
        if envelope.protocol_version != PROTOCOL_VERSION:
            raise InvalidEnvelope("protocol version mismatch")
        if envelope.kind not in _KINDS:
            raise InvalidEnvelope(f"unknown kind: {envelope.kind!r}")
        if not envelope.verify_mac(self._secret):
            # Covers tampering with task_id/agent_id/kind/payload AND
            # cross-task replay of a captured envelope (task_id is MAC-bound).
            raise InvalidEnvelope("envelope authentication failed")
        age = now - int(envelope.issued_at_ms)
        if age < 0 or age > self._max_age_ms:
            raise StaleEnvelope(
                f"envelope age {age}ms outside window (max "
                f"{self._max_age_ms}ms)")
        with self._lock:
            # Prune the replay set so it cannot grow without bound.
            cutoff = now - self._max_age_ms
            stale = [eid for eid, ts in self._seen.items() if ts < cutoff]
            for eid in stale:
                del self._seen[eid]
            if envelope.envelope_id in self._seen:
                raise ReplayedEnvelope(
                    f"envelope {envelope.envelope_id} already admitted")
            self._seen[envelope.envelope_id] = int(envelope.issued_at_ms)
        self._check_window_policy(envelope, now)

    def _check_window_policy(self, envelope: RoutingEnvelope, now: int) -> None:
        """Hardened reveal-window discipline, checked at admission.

        * commit envelopes: task must exist, be sealed, be open/auction,
          and the commit window must still be open.
        * reveal envelopes: task must exist, be sealed, be open/auction, the
          commit window must be CLOSED (no reveal-while-committing), the
          reveal window must still be open, and the bidder must hold an
          unrevealed commitment.
        * close envelopes: task must exist and be open/auction
          (permissionless timeout, mirroring close_auction).
        """
        from sincor2.a2a_inbound import get_fabric
        fabric = get_fabric()
        with fabric.lock:
            task = fabric.tasks.get(envelope.task_id)
            task = dict(task) if task else None
            commit_key = f"{envelope.task_id}\x00{envelope.agent_id}"
            commit_rec = fabric.commits.get(commit_key)
            commit_rec = dict(commit_rec) if commit_rec else None
        if task is None:
            raise WindowViolation("unknown task")
        if not task.get("sealed"):
            raise WindowViolation("task is not a sealed auction")
        if task.get("state") not in ("open", "auction"):
            raise WindowViolation("auction not open")
        commit_deadline = task.get("commit_deadline")
        reveal_deadline = task.get("reveal_deadline")
        if envelope.kind == KIND_COMMIT:
            if commit_deadline is not None and now > int(commit_deadline):
                raise WindowViolation("commit window closed")
            if commit_rec is not None:
                raise WindowViolation("already committed")
        elif envelope.kind == KIND_REVEAL:
            if commit_deadline is not None and now <= int(commit_deadline):
                # Hardened: reveals are never routable while commits are
                # still being accepted — no reveal-while-committing.
                raise WindowViolation("reveal window not open yet")
            if reveal_deadline is not None and now > int(reveal_deadline):
                raise WindowViolation("reveal window closed")
            if commit_rec is None:
                raise WindowViolation("no commitment to reveal")
            if commit_rec.get("revealed"):
                raise WindowViolation("already revealed")
        # KIND_CLOSE: permissionless; the pipeline decides when close takes
        # effect (reveal deadline / auction window).

    # -- observability ----------------------------------------------------

    def reveal_window_status(self, task_id: str) -> Dict[str, Any]:
        """Operator view of the hardened reveal window for a task."""
        from sincor2.a2a_inbound import get_fabric
        fabric = get_fabric()
        with fabric.lock:
            task = fabric.tasks.get(str(task_id))
            task = dict(task) if task else None
        if task is None:
            return {"task_id": task_id, "phase": "unknown"}
        now = self._now_ms()
        commit_deadline = task.get("commit_deadline")
        reveal_deadline = task.get("reveal_deadline")
        if not task.get("sealed"):
            phase = "not_sealed"
        elif commit_deadline is not None and now <= int(commit_deadline):
            phase = "commit"
        elif reveal_deadline is not None and now <= int(reveal_deadline):
            phase = "reveal"
        else:
            phase = "closed"
        return {
            "task_id": task_id,
            "phase": phase,
            "commit_deadline": commit_deadline,
            "reveal_deadline": reveal_deadline,
            "state": task.get("state"),
        }

    def audit_log(self) -> List[RoutingReceipt]:
        """Who-saw-what-when: every routed envelope with its pre-inclusion
        observability flag. Post-inclusion, reveals are public by design."""
        with self._lock:
            return list(self._receipts)

    def _receipt_for(self, envelope_id: str) -> Optional[RoutingReceipt]:
        with self._lock:
            for receipt in self._receipts:
                if receipt.envelope_id == envelope_id:
                    return receipt
        return None
