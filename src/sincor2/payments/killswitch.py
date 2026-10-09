"""WP4: replay-safe kill-switch evaluation for payment intents.

Red-team W-40: the shadow effect boundary returned a CACHED receipt before
evaluating the kill switch, so replaying an already-approved money intent
while the switch was engaged bypassed the brake.

This module provides the payment-side fix:
- Every payment intent evaluation checks the kill switch FIRST, before
  consulting any idempotency/decision cache.
- A replayed intent (same idempotency_key) whose ORIGINAL decision was
  "allow" is RE-EVALUATED against current kill-switch state. If the
  switch is now engaged, the verdict is "blocked_kill_switch" — the
  cached "allow" is never honored while engaged.

Coordinates with WP1's contract (PolicyDecision carries kill_switch_engaged
and idempotency_key); this module owns the payment-specific evaluation
order. WP1 owns the boundary-level dispatch order.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from sincor2.payments.intent import PaymentIntent


@dataclass(frozen=True, slots=True)
class PaymentPolicyVerdict:
    """Immutable verdict for a payment intent evaluation."""

    intent_id: str
    idempotency_key: str
    verdict: str  # "allow" | "deny" | "blocked_kill_switch" | "expired"
    reason: str
    kill_switch_engaged: bool
    evaluated_at: float = field(default_factory=time.time)
    # True when this verdict came from re-evaluating a replayed intent
    # rather than a first-time evaluation.
    is_replay: bool = False


class PaymentKillSwitch:
    """Thread-safe kill switch dedicated to the payment path.

    Separate from the shadow boundary's KillSwitch so the payment path
    has an independent brake that payment code — not boundary code —
    is responsible for checking. Defense in depth: both must allow.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engaged = False
        self._engagements: list[float] = []

    @property
    def engaged(self) -> bool:
        with self._lock:
            return self._engaged

    def engage(self) -> None:
        with self._lock:
            self._engaged = True
            self._engagements.append(time.time())

    def disengage(self) -> None:
        with self._lock:
            self._engaged = False


# Module-global payment kill switch.
payment_kill_switch = PaymentKillSwitch()


class PaymentPolicyEvaluator:
    """Evaluates payment intents with kill-switch-first ordering.

    Evaluation order (W-40 fix):
    1. Kill switch engaged? -> blocked_kill_switch (BEFORE cache lookup).
    2. Intent expired? -> deny.
    3. Replay (idempotency_key seen)? -> re-evaluate as new; mark is_replay.
       The original "allow" is NOT trusted — only the fresh evaluation.
    4. Otherwise -> allow (proposal; actual execution needs D5 quorum).
    """

    def __init__(self, kill_switch: Optional[PaymentKillSwitch] = None) -> None:
        self._kill_switch = kill_switch or payment_kill_switch
        self._lock = threading.Lock()
        self._seen: Dict[str, PaymentPolicyVerdict] = {}

    def evaluate(self, intent: PaymentIntent) -> PaymentPolicyVerdict:
        # STEP 1: kill switch FIRST — before any cache/idempotency lookup.
        # This is the W-40 fix: a cached "allow" must never bypass an
        # engaged switch.
        ks_engaged = self._kill_switch.engaged
        if ks_engaged:
            return PaymentPolicyVerdict(
                intent_id=intent.intent_id,
                idempotency_key=intent.idempotency_key,
                verdict="blocked_kill_switch",
                reason="payment kill switch engaged; all value movement halted",
                kill_switch_engaged=True,
            )

        # STEP 2: expiry.
        if intent.is_expired:
            return PaymentPolicyVerdict(
                intent_id=intent.intent_id,
                idempotency_key=intent.idempotency_key,
                verdict="deny",
                reason="intent expired",
                kill_switch_engaged=False,
            )

        # STEP 3: replay detection — re-evaluate, do not trust the cache.
        with self._lock:
            prior = self._seen.get(intent.idempotency_key)
            is_replay = prior is not None

        # STEP 4: allow as proposal (D5 quorum still required for execution).
        verdict = PaymentPolicyVerdict(
            intent_id=intent.intent_id,
            idempotency_key=intent.idempotency_key,
            verdict="allow",
            reason="re-evaluated replay; kill switch disengaged"
            if is_replay
            else "first evaluation; kill switch disengaged",
            kill_switch_engaged=False,
            is_replay=is_replay,
        )
        with self._lock:
            # Only store first-seen verdicts; replays don't overwrite.
            self._seen.setdefault(intent.idempotency_key, verdict)
        return verdict

    def reset(self) -> None:
        """Test helper."""
        with self._lock:
            self._seen.clear()
