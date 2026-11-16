"""
SINCOR2 Streaming Dynamic Utility Bidding (CNP-8).

Offchain protocol layer that lets agents STREAM bid updates as their
private valuations change inside an auction's commit window — without ever
revealing plaintext valuations.

Privacy model (the hard constraint):
  * Every streamed update is a *commitment* — the exact 32-byte hash from
    ``sincor2.onchain.bidder_client.commitment()``, which mirrors
    ``CommitRevealAuction`` byte-for-byte:
    ``keccak256(auctionId || chainId || price || salt || agentIdHash)``.
  * The stream store accepts and persists ONLY commitments.  Plaintext
    prices/salts never enter the submit path; they appear solely in the
    post-window reveal package (exactly like the contract's ``reveal``).
  * An observer of the stream learns nothing about bid values or their
    ordering beyond what the commitments themselves reveal — which, for a
    keccak-based PRF with fresh 32-byte salts, is nothing.  See
    ``tests/pytest/test_streaming_bids.py`` for the statistical proof.

Anti-spam / anti-grind:
  * Per-agent rate limit (minimum interval between updates).
  * Per-stream update cap.
  * Per-auction stream cap.
  * Minimum absolute price increment between consecutive updates, enforced
    at reveal-chain validation (it cannot be checked pre-reveal without
    breaking privacy).  Updates that violate it are VOIDED via the
    longest-valid-prefix rule — grinding yields no settlement advantage and
    burns the grinder's own rate-limit budget.
  * Duplicate commitments rejected (replay protection within a stream).

Settlement:
  * This layer NEVER settles.  ``effective_final_bid()`` hands the validated
    final (price, salt, commitment) back to the existing money path:
    onchain ``commit`` -> ``reveal`` -> ``close_auction`` (first-price).
    Streaming integrates with, and does not replace, final-commitment
    settlement.  Any change to the settlement economics is out of scope
    (founder-gated).

Threats considered (adversarial self-review, 2026-10-02):
  1. Front-running via stream sniffing — mitigated by commitment opacity
     (privacy tests prove hash order is uncorrelated with price order).
  2. Spam / DoS via rapid updates — rate limit + caps; fixed 32-byte
     payloads; O(1) amortized per update; short critical sections.
  3. Increment grinding — voided at reveal validation; no settlement effect.
  4. Commitment malleability / replay — auction_id, chain_id and agent_id
     are bound inside every commitment (inherited from bidder_client);
     cross-auction replay fails ``validate_reveal_chain``; duplicate
     commitments within a stream are rejected at submit time.
  5. Residual metadata leak — update *counts and timing* are visible to
     stream observers (same as onchain commit txs being visible).  Accepted
     and documented; values and ordering are not leaked.
  6. Unauthenticated updates — production wiring MUST pass
     ``auth_verifier``; constructing the manager without one in a
     production environment raises (fail-closed, mirroring BiddingEngine).
  7. Unauthenticated stream lifecycle (W-162) — ``open_stream`` and
     ``close_stream`` authenticate through the same ``auth_verifier``
     whenever one is set: the caller must present an ``auth_proof`` for
     the agent identity it claims.  Without this, anyone could squat a
     victim's stream, close it irreversibly, or exhaust the per-auction
     stream cap with sybil identities.
  8. Opener-declared stream terms (W-163) — an auction's commit deadline
     and minimum increment live in the auction record
     (``register_auction``), never in the opener's request.  When an
     ``auth_verifier`` is set (production), ``open_stream`` sources both
     values from the record and rejects unregistered auctions; the
     opener-supplied arguments are honoured only in no-verifier dev/test
     mode, for backward compatibility with existing callers.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from sincor2.onchain.bidder_client import commitment as _commitment

logger = logging.getLogger("sincor.streaming_bids")

COMMITMENT_LEN = 32

#: Domain-separation prefix the manager puts in front of a lifecycle
#: ``auth_proof`` before handing it to ``auth_verifier`` (W-162).  A
#: submitted update's proof is its raw 32-byte commitment; a lifecycle
#: proof arrives tagged with this prefix, so one verifier can apply
#: lifecycle-grade credential validation (e.g. a signature check) to
#: open/close without confusing it with a commitment, and a streamed
#: commitment can never be replayed as a lifecycle credential.
LIFECYCLE_PROOF_PREFIX = b"sincor2.streaming_bids.lifecycle:"


# ---------------------------------------------------------------------------
# Config / result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StreamConfig:
    """Tunable anti-spam parameters for streaming bid updates."""

    max_updates_per_stream: int = 12
    """Hard cap on accepted updates per (auction, agent) stream."""

    min_update_interval_seconds: float = 20.0
    """Minimum seconds between two accepted updates from the same agent."""

    max_streams_per_auction: int = 64
    """Cap on distinct agent streams tracked per auction (DoS bound)."""

    def validate(self) -> None:
        if self.max_updates_per_stream < 1:
            raise ValueError("max_updates_per_stream must be >= 1")
        if self.min_update_interval_seconds < 0:
            raise ValueError("min_update_interval_seconds must be >= 0")
        if self.max_streams_per_auction < 1:
            raise ValueError("max_streams_per_auction must be >= 1")


@dataclass(frozen=True)
class AuctionTerms:
    """Auction-level source of truth for stream terms (W-163).

    Set once by the auction operator via ``register_auction``; every
    stream opened for the auction inherits these values.  Openers never
    declare them.
    """

    commit_deadline_ts: float
    min_increment_wei: int = 0


@dataclass(frozen=True)
class StreamUpdate:
    """One accepted streamed update.  Contains NO plaintext valuation."""

    auction_id: bytes
    agent_id: str
    seq: int  # 0-based position in this agent's stream
    commitment: bytes  # 32-byte opaque commitment
    received_at: float  # manager-clock timestamp


@dataclass(frozen=True)
class StreamResult:
    """Structured outcome of an open/submit/close operation.  Never raises."""

    ok: bool
    reason: str = ""  # machine-readable code, e.g. "rate_limited"
    detail: str = ""  # human-readable detail
    seq: int = -1  # accepted update's sequence number (-1 when rejected)


@dataclass(frozen=True)
class RevealValidation:
    """Outcome of validating a streamed commitment chain at reveal time."""

    ok: bool  # True iff the ENTIRE chain validates
    effective_seq: int  # last seq of the longest valid prefix (-1 = none)
    effective_price: Optional[int]  # price at effective_seq (None if none)
    failures: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class FinalBid:
    """Validated handoff to the settlement path (commit -> reveal -> close)."""

    ok: bool
    reason: str = ""
    price_wei: Optional[int] = None
    salt: Optional[bytes] = None
    commitment: Optional[bytes] = None
    seq: int = -1


# ---------------------------------------------------------------------------
# Internal stream record
# ---------------------------------------------------------------------------

@dataclass
class _StreamRecord:
    auction_id: bytes
    agent_id: str
    commit_deadline_ts: float
    min_increment_wei: int
    open: bool = True
    updates: List[StreamUpdate] = field(default_factory=list)
    seen_commitments: set = field(default_factory=set)
    last_update_at: Optional[float] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class StreamingBidManager:
    """
    Offchain handler for streaming sealed-bid updates.

    All public methods return structured results instead of raising.

    Parameters
    ----------
    config:
        Anti-spam tuning.  Defaults are conservative.
    auth_verifier:
        Optional ``callable(agent_id, auction_id_hex, proof) -> bool``.
        When set, every submitted update must pass it (the update's
        commitment is the proof), and ``open_stream``/``close_stream``
        must pass it too (the caller supplies an opaque ``auth_proof`` —
        in production wiring, a credential only the agent's key holder
        can produce for that agent identity; lifecycle proofs reach the
        verifier domain-tagged with ``LIFECYCLE_PROOF_PREFIX``).  When
        None, no authentication is performed — permitted in dev/test
        only; in a production environment the constructor raises
        (fail-closed).
    clock:
        Optional ``callable() -> float`` timestamp source (default
        ``time.time``).  Injectable for deterministic tests.
    """

    def __init__(
        self,
        config: Optional[StreamConfig] = None,
        auth_verifier: Optional[Callable[[str, str, bytes], bool]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._config = config or StreamConfig()
        self._config.validate()
        if auth_verifier is None:
            env = (
                os.environ.get("ENVIRONMENT")
                or os.environ.get("FLASK_ENV")
                or ""
            ).strip().lower()
            if env in ("production", "prod"):
                raise ValueError(
                    "StreamingBidManager requires an auth_verifier in "
                    "production"
                )
            logger.warning(
                "[STREAM-BIDS] auth_verifier=None — streamed updates are "
                "UNAUTHENTICATED (dev/test only)"
            )
        self._auth = auth_verifier
        self._clock: Callable[[], float] = clock or time.time
        self._lock = threading.RLock()
        # auction_id_hex -> {agent_id -> _StreamRecord}
        self._streams: Dict[str, Dict[str, _StreamRecord]] = {}
        # auction_id_hex -> AuctionTerms (auction record, W-163)
        self._auctions: Dict[str, AuctionTerms] = {}

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _auction_hex(auction_id: bytes) -> str:
        return bytes(auction_id).hex()

    def _get_record(
        self, auction_id: bytes, agent_id: str
    ) -> Optional[_StreamRecord]:
        return self._streams.get(self._auction_hex(auction_id), {}).get(agent_id)

    def _check_auth(
        self, agent_id: str, auction_id: bytes, proof: Optional[bytes],
        *, lifecycle: bool = False,
    ) -> Optional[StreamResult]:
        """
        Authenticate one operation through ``auth_verifier``.

        Returns None when the operation is authorized (or no verifier is
        configured — dev/test mode), otherwise the rejection result.
        ``proof`` is the commitment bytes for ``submit_update`` and the
        caller-supplied ``auth_proof`` for lifecycle operations; a
        missing proof fails closed whenever a verifier is set (W-162).
        Lifecycle proofs are domain-tagged with
        ``LIFECYCLE_PROOF_PREFIX`` before reaching the verifier, so a
        commitment can never double as a lifecycle credential.
        """
        if self._auth is None:
            return None
        if proof is None:
            return StreamResult(ok=False, reason="auth_failed",
                                detail="auth proof required")
        presented = bytes(proof)
        if lifecycle:
            presented = LIFECYCLE_PROOF_PREFIX + presented
        try:
            authed = self._auth(
                agent_id, self._auction_hex(auction_id), presented)
        except Exception:
            logger.exception(
                "[STREAM-BIDS] auth_verifier crashed agent=%s", agent_id)
            return StreamResult(ok=False, reason="auth_failed",
                                detail="auth verifier error")
        if not authed:
            return StreamResult(ok=False, reason="auth_failed",
                                detail="operation not authenticated")
        return None

    # -- auction record (terms source of truth, W-163) ----------------------

    def register_auction(
        self,
        auction_id: bytes,
        *,
        commit_deadline_ts: float,
        min_increment_wei: int = 0,
    ) -> StreamResult:
        """
        Register an auction's terms — the source of truth every stream
        for this auction inherits (W-163).

        This is the auction operator's act, performed when the auction
        is created; openers never declare terms.  Registration is
        first-write-wins: re-registering with identical terms is an
        idempotent no-op, re-registering with different terms is
        rejected (``auction_terms_conflict``) and the original record
        stands, so terms can never be rewritten mid-window.
        """
        try:
            auction_id = bytes(auction_id)
        except (TypeError, ValueError):
            return StreamResult(ok=False, reason="bad_auction_id",
                                detail="auction_id must be bytes-like")
        if len(auction_id) != COMMITMENT_LEN:
            return StreamResult(ok=False, reason="bad_auction_id",
                                detail="auction_id must be 32 bytes")
        if int(min_increment_wei) < 0:
            return StreamResult(ok=False, reason="bad_increment",
                                detail="min_increment_wei must be >= 0")
        if not commit_deadline_ts > self._clock():
            return StreamResult(ok=False, reason="window_closed",
                                detail="commit deadline is not in the future")
        terms = AuctionTerms(commit_deadline_ts=float(commit_deadline_ts),
                             min_increment_wei=int(min_increment_wei))
        with self._lock:
            existing = self._auctions.get(self._auction_hex(auction_id))
            if existing is not None:
                if existing == terms:
                    return StreamResult(ok=True, reason="registered",
                                        detail="terms already registered")
                return StreamResult(
                    ok=False, reason="auction_terms_conflict",
                    detail="auction already registered with different terms")
            self._auctions[self._auction_hex(auction_id)] = terms
        logger.info("[STREAM-BIDS] auction registered auction=%s",
                    self._auction_hex(auction_id)[:16])
        return StreamResult(ok=True, reason="registered")

    def auction_terms(self, auction_id: bytes) -> Optional[AuctionTerms]:
        """The registered terms for an auction, or None if unregistered."""
        with self._lock:
            return self._auctions.get(self._auction_hex(bytes(auction_id)))

    # -- stream lifecycle ---------------------------------------------------

    def open_stream(
        self,
        auction_id: bytes,
        agent_id: str,
        *,
        commit_deadline_ts: Optional[float] = None,
        min_increment_wei: Optional[int] = None,
        auth_proof: Optional[bytes] = None,
    ) -> StreamResult:
        """
        Register an agent's update stream for one auction.

        Terms (commit deadline, minimum increment) are sourced from the
        auction record registered via ``register_auction`` — never from
        the opener (W-163).  The ``commit_deadline_ts`` /
        ``min_increment_wei`` arguments are honoured only when no
        ``auth_verifier`` is configured (dev/test mode, where there is
        no production auction wiring); whenever a verifier is set, the
        auction must be registered and these arguments are ignored.

        Whenever an ``auth_verifier`` is configured, the opener must
        also authenticate as ``agent_id`` by presenting ``auth_proof``
        (W-162): an unauthenticated caller cannot squat another agent's
        stream or burn the per-auction stream cap with sybil identities.
        """
        try:
            auction_id = bytes(auction_id)
        except (TypeError, ValueError):
            return StreamResult(ok=False, reason="bad_auction_id",
                                detail="auction_id must be bytes-like")
        agent_id = str(agent_id or "")
        if len(auction_id) != COMMITMENT_LEN:
            return StreamResult(ok=False, reason="bad_auction_id",
                                detail="auction_id must be 32 bytes")
        if not agent_id:
            return StreamResult(ok=False, reason="bad_agent_id",
                                detail="agent_id must be non-empty")
        if min_increment_wei is not None and int(min_increment_wei) < 0:
            return StreamResult(ok=False, reason="bad_increment",
                                detail="min_increment_wei must be >= 0")
        now = self._clock()

        with self._lock:
            # Authenticate before revealing anything about stream state:
            # an unauthenticated caller learns neither whether the
            # stream exists nor whether the cap is exhausted.
            rejection = self._check_auth(agent_id, auction_id, auth_proof,
                                         lifecycle=True)
            if rejection is not None:
                return rejection

            terms = self._auctions.get(self._auction_hex(auction_id))
            if terms is not None:
                deadline = terms.commit_deadline_ts
                increment = terms.min_increment_wei
            elif self._auth is not None:
                # Production mode: terms come from the auction record,
                # full stop — an unregistered auction has no streams.
                return StreamResult(
                    ok=False, reason="unknown_auction",
                    detail="auction not registered; stream terms must "
                           "come from the auction record")
            else:
                # Dev/test mode (no verifier): legacy opener-declared
                # terms, kept so existing callers/tests are unaffected.
                if commit_deadline_ts is None:
                    return StreamResult(
                        ok=False, reason="missing_terms",
                        detail="commit_deadline_ts is required when the "
                               "auction is not registered")
                deadline = float(commit_deadline_ts)
                increment = int(min_increment_wei or 0)
            if not deadline > now:
                return StreamResult(ok=False, reason="window_closed",
                                    detail="commit deadline is not in "
                                           "the future")

            per_auction = self._streams.setdefault(
                self._auction_hex(auction_id), {})
            if agent_id in per_auction:
                return StreamResult(ok=False, reason="stream_exists",
                                    detail="stream already open for agent")
            if len(per_auction) >= self._config.max_streams_per_auction:
                return StreamResult(ok=False, reason="auction_stream_cap",
                                    detail="too many streams for this auction")
            per_auction[agent_id] = _StreamRecord(
                auction_id=auction_id,
                agent_id=agent_id,
                commit_deadline_ts=deadline,
                min_increment_wei=increment,
            )
        logger.info("[STREAM-BIDS] stream opened auction=%s agent=%s",
                    self._auction_hex(auction_id)[:16], agent_id)
        return StreamResult(ok=True, reason="opened", seq=-1)

    def submit_update(
        self,
        auction_id: bytes,
        agent_id: str,
        commitment_bytes: bytes,
    ) -> StreamResult:
        """
        Accept one streamed update — a 32-byte *commitment*, never plaintext.

        Rejections (never raise): unknown_stream, stream_closed,
        window_closed, malformed_commitment, duplicate_commitment,
        auth_failed, rate_limited, update_cap_reached.
        """
        try:
            auction_id = bytes(auction_id)
            commitment_bytes = bytes(commitment_bytes)
        except (TypeError, ValueError):
            return StreamResult(ok=False, reason="malformed_commitment",
                                detail="commitment must be bytes-like")
        agent_id = str(agent_id or "")
        now = self._clock()

        with self._lock:
            rec = self._get_record(auction_id, agent_id)
            if rec is None:
                return StreamResult(ok=False, reason="unknown_stream",
                                    detail="no open stream for agent")
            if not rec.open:
                return StreamResult(ok=False, reason="stream_closed",
                                    detail="stream is closed")
            if now > rec.commit_deadline_ts:
                return StreamResult(ok=False, reason="window_closed",
                                    detail="commit window has closed")
            if len(commitment_bytes) != COMMITMENT_LEN:
                return StreamResult(ok=False, reason="malformed_commitment",
                                    detail="commitment must be 32 bytes")
            if commitment_bytes in rec.seen_commitments:
                # Replay of an already-streamed commitment: no new
                # information, treated as spam.
                return StreamResult(ok=False, reason="duplicate_commitment",
                                    detail="commitment already streamed")
            rejection = self._check_auth(agent_id, auction_id,
                                         commitment_bytes)
            if rejection is not None:
                return rejection
            if (rec.last_update_at is not None
                    and now - rec.last_update_at
                    < self._config.min_update_interval_seconds):
                return StreamResult(ok=False, reason="rate_limited",
                                    detail="update too soon after previous")
            if len(rec.updates) >= self._config.max_updates_per_stream:
                return StreamResult(ok=False, reason="update_cap_reached",
                                    detail="per-stream update cap reached")

            seq = len(rec.updates)
            rec.updates.append(StreamUpdate(
                auction_id=rec.auction_id,
                agent_id=agent_id,
                seq=seq,
                commitment=commitment_bytes,
                received_at=now,
            ))
            rec.seen_commitments.add(commitment_bytes)
            rec.last_update_at = now

        logger.debug("[STREAM-BIDS] update accepted auction=%s agent=%s seq=%d",
                     self._auction_hex(auction_id)[:16], agent_id, seq)
        return StreamResult(ok=True, reason="accepted", seq=seq)

    def close_stream(
        self,
        auction_id: bytes,
        agent_id: str,
        *,
        auth_proof: Optional[bytes] = None,
    ) -> StreamResult:
        """
        Close a stream; further updates are rejected.  Idempotent-safe.

        Whenever an ``auth_verifier`` is configured, the caller must
        authenticate as the stream's agent via ``auth_proof`` (W-162):
        closing is irreversible (the record persists and blocks
        re-opening), so an unauthenticated close would be a permanent
        per-auction DoS of the victim bidder.
        """
        try:
            auction_id = bytes(auction_id)
        except (TypeError, ValueError):
            return StreamResult(ok=False, reason="bad_auction_id",
                                detail="auction_id must be bytes-like")
        agent_id = str(agent_id or "")
        with self._lock:
            rejection = self._check_auth(agent_id, auction_id, auth_proof,
                                         lifecycle=True)
            if rejection is not None:
                return rejection
            rec = self._get_record(auction_id, agent_id)
            if rec is None:
                return StreamResult(ok=False, reason="unknown_stream")
            rec.open = False
        return StreamResult(ok=True, reason="closed")

    # -- read paths (commitments only — no plaintext ever stored) -------------

    def reveal_package(
        self, auction_id: bytes, agent_id: str
    ) -> List[bytes]:
        """Ordered commitment chain for an agent's reveal submission."""
        with self._lock:
            rec = self._get_record(bytes(auction_id), str(agent_id or ""))
            if rec is None:
                return []
            return [u.commitment for u in rec.updates]

    def latest_commitment(
        self, auction_id: bytes, agent_id: str
    ) -> Optional[bytes]:
        """The agent's most recent streamed commitment (settlement input)."""
        with self._lock:
            rec = self._get_record(bytes(auction_id), str(agent_id or ""))
            if rec is None or not rec.updates:
                return None
            return rec.updates[-1].commitment

    def stream_summary(self, auction_id: bytes) -> Dict[str, Any]:
        """
        Public metadata for an auction's streams: per-agent update counts,
        open/closed flags, window deadlines.  Contains no bid values.
        """
        with self._lock:
            per_auction = self._streams.get(
                self._auction_hex(bytes(auction_id)), {})
            return {
                agent_id: {
                    "updates": len(rec.updates),
                    "open": rec.open,
                    "commit_deadline_ts": rec.commit_deadline_ts,
                    "min_increment_wei": rec.min_increment_wei,
                }
                for agent_id, rec in per_auction.items()
            }

    # -- reveal-chain validation ----------------------------------------------

    def validate_reveal_chain(
        self,
        auction_id: bytes,
        agent_id: str,
        prices_wei: Sequence[int],
        salts: Sequence[bytes],
        chain_id: int,
    ) -> RevealValidation:
        """
        Validate an agent's reveal against its streamed commitment chain.

        For each streamed commitment ``i`` recompute
        ``commitment(price_i, salt_i, agent_id, auction_id, chain_id)`` and
        compare; then enforce the minimum absolute increment between
        consecutive *valid* prices.

        Uses the longest-valid-prefix rule: the effective bid is the last
        update of the longest prefix that fully validates.  A suffix that
        fails (wrong salt, tampered price, increment grind) is VOIDED — it
        cannot move settlement, which still runs on the final *committed*
        (i.e. validated) bid via the existing close_auction path.

        ``prices_wei``/``salts`` are the agent's reveal plaintexts — the only
        place plaintext valuations exist in this module, mirroring the
        contract's ``reveal``.
        """
        agent_id = str(agent_id or "")
        failures: List[str] = []
        with self._lock:
            rec = self._get_record(bytes(auction_id), agent_id)
            if rec is None:
                return RevealValidation(
                    ok=False, effective_seq=-1, effective_price=None,
                    failures=["unknown_stream"])
            stored = [u.commitment for u in rec.updates]
            min_inc = rec.min_increment_wei
            auction_id = rec.auction_id  # canonical bytes

        n = len(stored)
        if len(prices_wei) != n or len(salts) != n:
            return RevealValidation(
                ok=False, effective_seq=-1, effective_price=None,
                failures=[f"reveal_length_mismatch:got "
                          f"{len(prices_wei)}/{len(salts)} want {n}/{n}"])

        effective_seq = -1
        effective_price: Optional[int] = None
        prev_price: Optional[int] = None
        for i, (price, salt) in enumerate(zip(prices_wei, salts)):
            try:
                recomputed = _commitment(int(price), bytes(salt), agent_id,
                                         auction_id, int(chain_id))
            except (ValueError, TypeError) as exc:
                failures.append(f"seq={i}:bad_reveal_input:{exc}")
                break
            if recomputed != stored[i]:
                failures.append(f"seq={i}:commitment_mismatch")
                break
            price = int(price)
            if prev_price is not None and abs(price - prev_price) < min_inc:
                failures.append(
                    f"seq={i}:increment_too_small:"
                    f"|{price}-{prev_price}|<{min_inc}")
                break
            # This update fully validates — it becomes the effective bid.
            effective_seq = i
            effective_price = price
            prev_price = price

        return RevealValidation(
            ok=(not failures) and effective_seq == n - 1,
            effective_seq=effective_seq,
            effective_price=effective_price,
            failures=failures,
        )

    def effective_final_bid(
        self,
        auction_id: bytes,
        agent_id: str,
        prices_wei: Sequence[int],
        salts: Sequence[bytes],
        chain_id: int,
    ) -> FinalBid:
        """
        Validated handoff to settlement: returns the (price, salt,
        commitment) of the longest valid reveal prefix.

        Feed the returned ``price_wei``/``salt`` into the existing
        commit -> reveal -> ``close_auction`` money path.  This layer never
        settles anything itself.
        """
        agent_id = str(agent_id or "")
        validation = self.validate_reveal_chain(
            auction_id, agent_id, prices_wei, salts, chain_id)
        if validation.effective_seq < 0:
            return FinalBid(
                ok=False,
                reason="no_valid_update:" + ";".join(validation.failures),
                seq=-1,
            )
        i = validation.effective_seq
        with self._lock:
            rec = self._get_record(bytes(auction_id), agent_id)
            commitment_bytes = (
                rec.updates[i].commitment if rec is not None else None)
        if commitment_bytes is None:  # pragma: no cover - defensive
            return FinalBid(ok=False, reason="stream_vanished", seq=-1)
        return FinalBid(
            ok=True,
            reason=("full_chain_valid" if validation.ok
                    else "suffix_voided:" + ";".join(validation.failures)),
            price_wei=validation.effective_price,
            salt=bytes(salts[i]),
            commitment=commitment_bytes,
            seq=i,
        )
