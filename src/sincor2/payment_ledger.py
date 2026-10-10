"""Canonical durable payment state contract (PAY-01).

Every payment ingress (Stripe webhooks, x402 challenges, A2A/auction payments,
agent billing entries, platform checkouts) maps into ONE canonical
:class:`PaymentRecord` with ONE legal state machine and ONE append-only history.

The contract exists because payment state used to be scattered across
``platform_payments.py`` (platform_checkouts table), ``x402_payments.py``
(x402_challenges table), ``stripe_routes.py`` (orders table) and
``agent_billing.py`` (JSONL log) with no shared schema.  That made it
possible for a payment to look "paid" in one store while failing in another,
and made finality, refunds, and reorg handling provider-specific guesswork.

Design rules (additive-only integration):
  * The mappers below READ the existing provider-specific record shapes and
    return :class:`PaymentRecord`.  They never modify the existing stores.
  * The durable store (SQLite, WAL mode) keeps ``payments`` plus an
    append-only ``payment_history`` table.  SQL triggers reject any
    UPDATE/DELETE on history rows, so immutability holds even for callers
    with raw SQL access.
  * State transitions go through :func:`transition_payment`, which enforces
    the legal transition table and uses compare-and-swap on a version
    column: concurrent attempts race, exactly one wins, the losers get
    :class:`PaymentConflictError`.
  * ``quarantined`` is the only state for legacy/unknown rows.  A payment
    must never be assumed paid from a record we cannot parse.
  * Skipping finality is blocked at the store level: a transition to
    ``settled`` for a payment carrying onchain evidence (a ``tx_hash``)
    requires ``finality_confirmed`` to be set (set by the finality module
    in ``payment_finality.py``).  Offchain/fiat providers (no ``tx_hash``)
    may settle directly from ``verified``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Optional


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class PaymentState(str, Enum):
    """Legal payment states."""

    CREATED = "created"
    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"
    AWAITING_FINALITY = "awaiting_finality"
    SETTLED = "settled"
    FULFILLED = "fulfilled"
    FAILED = "failed"
    REFUNDED = "refunded"
    DISPUTED = "disputed"
    QUARANTINED = "quarantined"


def _as_state(value: PaymentState | str) -> PaymentState:
    """Normalize a state given as enum member or plain string to PaymentState.

    ``str()`` on a str-mixin Enum member yields ``'PaymentState.X'`` (not the
    value), so callers must not round-trip through ``str``.
    """
    if isinstance(value, PaymentState):
        return value
    return PaymentState(str(value))


# Legal transitions: from_state -> set of allowed to_states.
_ALLOWED_TRANSITIONS: dict[PaymentState, frozenset[PaymentState]] = {
    PaymentState.CREATED: frozenset(
        {
            PaymentState.PENDING_VERIFICATION,
            PaymentState.FAILED,
            PaymentState.QUARANTINED,
        }
    ),
    PaymentState.PENDING_VERIFICATION: frozenset(
        {
            PaymentState.VERIFIED,
            PaymentState.FAILED,
            PaymentState.QUARANTINED,
        }
    ),
    PaymentState.VERIFIED: frozenset(
        {
            # Offchain providers (fiat, no tx_hash) may settle directly.
            PaymentState.SETTLED,
            # Onchain providers must pass through awaiting_finality.
            PaymentState.AWAITING_FINALITY,
            PaymentState.FAILED,
            PaymentState.DISPUTED,
            PaymentState.QUARANTINED,
        }
    ),
    PaymentState.AWAITING_FINALITY: frozenset(
        {
            PaymentState.SETTLED,
            PaymentState.FAILED,
            PaymentState.DISPUTED,
            PaymentState.QUARANTINED,
        }
    ),
    PaymentState.SETTLED: frozenset(
        {
            PaymentState.FULFILLED,
            PaymentState.REFUNDED,
            PaymentState.DISPUTED,
            PaymentState.QUARANTINED,
        }
    ),
    PaymentState.FULFILLED: frozenset(
        {
            PaymentState.REFUNDED,
            PaymentState.DISPUTED,
            PaymentState.QUARANTINED,
        }
    ),
    # Terminal-side states.
    PaymentState.FAILED: frozenset(
        {
            # Retry is allowed: a failed verification may be re-attempted.
            PaymentState.PENDING_VERIFICATION,
            PaymentState.QUARANTINED,
        }
    ),
    PaymentState.DISPUTED: frozenset(
        {
            # Dispute resolution outcomes.
            PaymentState.SETTLED,
            PaymentState.REFUNDED,
            PaymentState.QUARANTINED,
        }
    ),
    PaymentState.QUARANTINED: frozenset(
        {
            # Human/adjudicator review may re-admit a quarantined payment.
            PaymentState.PENDING_VERIFICATION,
            PaymentState.FAILED,
        }
    ),
    PaymentState.REFUNDED: frozenset(),
}


class PaymentLedgerError(Exception):
    """Base error for the payment ledger."""


class IllegalPaymentTransition(PaymentLedgerError):
    """Raised when a transition is not in the legal transition table."""


class PaymentNotFound(PaymentLedgerError):
    """Raised when a payment_id is unknown to the store."""


class DuplicatePaymentError(PaymentLedgerError):
    """Raised when the same provider payment is ingested twice."""


class PaymentConflictError(PaymentLedgerError):
    """Raised when a concurrent transition loses the compare-and-swap race."""


class FinalityNotConfirmed(PaymentLedgerError):
    """Raised when a caller tries to settle an onchain payment without finality."""


# ---------------------------------------------------------------------------
# PaymentRecord
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StateTransition:
    """One immutable entry in a payment's history.

    Appended exactly once per successful transition; never updated.
    """

    payment_id: str
    from_state: str
    to_state: str
    ts: str
    actor: str
    reason: str = ""


@dataclass
class PaymentRecord:
    """Canonical payment contract every ingress maps to.

    Attributes:
        payment_id: canonical uuid for this payment.
        owner: tenant/customer id (email, wallet, agent id — provider-native).
        amount_atomic: amount in the token's smallest unit.
        amount_display: human-readable amount.
        currency: token symbol or fiat code (e.g. "AXM", "USDC", "USD").
        provider: one of "stripe", "x402", "a2a", "manual", "checkout",
            "legacy".
        provider_evidence: provider-specific proof dict (tx_hash, receipt id,
            event id, session id, challenge id...).  Never modified by the
            ledger; mirrored verbatim from the source record.
        entitlements: what the payment unlocks (order_id, challenge_id,
            task_id, access_token, subscription_id...).
        refunds: refund links (refund_id, status).
        state: current :class:`PaymentState`.
        history: immutable append-only list of :class:`StateTransition`.
        version: compare-and-swap counter, bumped per transition.
        block_number / block_hash / confirmations: finality evidence for
            onchain payments (populated by payment_finality.py).
        finality_confirmed: set only by the finality checker; required for
            settling onchain payments.
        created_at / updated_at: ISO-8601 timestamps.
    """

    payment_id: str
    owner: str
    amount_atomic: int
    amount_display: float
    currency: str
    provider: str
    provider_evidence: dict[str, Any] = field(default_factory=dict)
    entitlements: dict[str, Any] = field(default_factory=dict)
    refunds: dict[str, Any] = field(default_factory=dict)
    state: PaymentState = PaymentState.CREATED
    history: list[StateTransition] = field(default_factory=list)
    version: int = 0
    block_number: Optional[int] = None
    block_hash: Optional[str] = None
    confirmations: Optional[int] = None
    finality_confirmed: bool = False
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if isinstance(self.state, str):
            self.state = PaymentState(self.state)

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize the record (history included) to a plain dict."""
        d = asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "PaymentRecord":
        """Rebuild a record from :meth:`to_dict` output."""
        data = dict(d)
        data["state"] = _as_state(data.get("state", PaymentState.CREATED.value))
        history = [StateTransition(**h) for h in (data.get("history") or [])]
        data["history"] = history
        return cls(**{k: v for k, v in data.items() if k in _RECORD_FIELDS})

    # -- state machine ------------------------------------------------------

    def can_transition(self, to_state: PaymentState | str) -> bool:
        """Return True if the transition from the current state is legal."""
        target = _as_state(to_state)
        return target in _ALLOWED_TRANSITIONS[self.state]

    def transition(self, to_state: PaymentState | str, *, actor: str, reason: str = "") -> StateTransition:
        """Apply a legal transition in memory, appending to history.

        Raises:
            IllegalPaymentTransition: if the transition is not legal.
        """
        target = _as_state(to_state)
        if target not in _ALLOWED_TRANSITIONS[self.state]:
            raise IllegalPaymentTransition(
                f"illegal transition {self.state.value} -> {target.value} "
                f"for payment {self.payment_id}"
            )
        entry = StateTransition(
            payment_id=self.payment_id,
            from_state=self.state.value,
            to_state=target.value,
            ts=_utc_now_iso(),
            actor=actor,
            reason=reason,
        )
        self.state = target
        self.history.append(entry)
        self.version += 1
        self.updated_at = entry.ts
        return entry


_RECORD_FIELDS = {f.name for f in PaymentRecord.__dataclass_fields__.values()}


# ---------------------------------------------------------------------------
# Mappers — additive only; each reads a provider-specific record shape and
# returns a PaymentRecord without touching the source store.
# ---------------------------------------------------------------------------


def _evidence(**kwargs: Any) -> dict[str, Any]:
    """Build an evidence dict, dropping None values."""
    return {k: v for k, v in kwargs.items() if v is not None}


def from_stripe_event(event_data: Mapping[str, Any]) -> PaymentRecord:
    """Map a Stripe webhook event to the canonical contract.

    Expects the shape emitted by ``stripe_routes._process_payment_event``:
    ``event`` (e.g. ``checkout.session.completed``), ``session_id``,
    ``customer_email``, ``plan_name``/``plan_id``, ``amount`` (dollars),
    ``currency`` and ``payment_status``.

    A Stripe "completed" event is fiat money that already cleared on the
    provider side, so it lands in ``verified`` (fiat has no onchain
    finality to await).  Anything not verifiably completed lands in
    ``pending_verification`` or ``failed`` — never settled.
    """
    data = dict(event_data)
    session_id = str(
        data.get("session_id")
        or data.get("stripe_session_id")
        or data.get("id")
        or f"stripe-unknown-{uuid.uuid4().hex[:12]}"
    )
    amount = float(data.get("amount") or 0.0)
    currency = str(data.get("currency") or "USD").upper()
    payment_status = str(data.get("payment_status") or "").lower()
    event_type = str(data.get("event") or data.get("type") or "")

    completed = payment_status == "completed" or event_type in (
        "checkout.session.completed",
        "invoice.paid",
        "payment_intent.succeeded",
        "charge.succeeded",
    )

    rec = PaymentRecord(
        payment_id=f"stripe-{session_id[:32]}",
        owner=str(data.get("customer_email") or data.get("customer") or "unknown"),
        amount_atomic=int(round(amount * 100)),  # fiat minor units (cents)
        amount_display=amount,
        currency=currency,
        provider="stripe",
        provider_evidence=_evidence(
            stripe_session_id=session_id,
            event_type=event_type,
            payment_status=payment_status,
            subscription_id=data.get("subscription_id"),
            invoice_id=data.get("invoice_id"),
        ),
        entitlements=_evidence(
            order_id=f"STRIPE-{session_id[:20]}",
            plan_id=data.get("plan_id"),
            plan_name=data.get("plan_name"),
        ),
    )
    if completed:
        rec.transition(PaymentState.PENDING_VERIFICATION, actor="mapper", reason="stripe_event_received")
        rec.transition(PaymentState.VERIFIED, actor="mapper", reason="stripe_completed_event")
    elif payment_status in ("failed", "canceled", "cancelled"):
        rec.transition(PaymentState.PENDING_VERIFICATION, actor="mapper", reason="stripe_event_received")
        rec.transition(PaymentState.FAILED, actor="mapper", reason=f"stripe_status={payment_status}")
    else:
        rec.transition(PaymentState.PENDING_VERIFICATION, actor="mapper", reason="stripe_event_received")
    return rec


def from_x402_challenge(row: Mapping[str, Any]) -> PaymentRecord:
    """Map an ``x402_challenges`` row to the canonical contract.

    A fulfilled challenge is ``verified`` — work must still wait for
    settlement/finality (see ``require_settled`` in x402_payments.py and the
    SETTLE_BEFORE_SERVE rule), so it is never mapped straight to settled.
    """
    data = dict(row)
    challenge_id = str(data.get("challenge_id") or f"x402-{uuid.uuid4().hex[:12]}")
    status = str(data.get("status") or "pending").lower()
    tx_hash = data.get("tx_hash")

    rec = PaymentRecord(
        payment_id=f"x402-{challenge_id}",
        owner=str(data.get("payer_wallet") or data.get("payer") or "unknown"),
        amount_atomic=int(data.get("amount_atomic") or 0),
        amount_display=float(data.get("amount_display") or 0.0),
        currency=str(data.get("currency") or data.get("token") or "USDC").upper(),
        provider="x402",
        provider_evidence=_evidence(
            challenge_id=challenge_id,
            resource_id=data.get("resource_id"),
            tx_hash=tx_hash,
            access_token=None,  # never mirror access tokens into evidence
        ),
        entitlements=_evidence(
            challenge_id=challenge_id,
            resource_id=data.get("resource_id"),
        ),
    )
    rec.transition(PaymentState.PENDING_VERIFICATION, actor="mapper", reason="x402_challenge_mapped")
    if status == "fulfilled" and tx_hash:
        rec.transition(PaymentState.VERIFIED, actor="mapper", reason="challenge_fulfilled_with_tx")
    elif status == "fulfilled":
        # Fulfilled without a tx hash: cannot verify money movement.
        rec.transition(PaymentState.QUARANTINED, actor="mapper",
                       reason="fulfilled_without_tx_hash")
    elif status in ("expired", "failed", "cancelled", "canceled"):
        rec.transition(PaymentState.FAILED, actor="mapper", reason=f"x402_status={status}")
    return rec


def from_a2a_payment(payment: Mapping[str, Any]) -> PaymentRecord:
    """Map an A2A/auction payment record to the canonical contract.

    Expects the sealed-bid/auction payment shape: ``task_id``,
    ``agent_id``/``bidder``, ``amount_wei`` (or ``amount_atomic``),
    ``token``, ``tx_hash``, ``status``, ``bid_id``.
    """
    data = dict(payment)
    task_id = str(data.get("task_id") or "unknown-task")
    bid_id = str(data.get("bid_id") or data.get("commitment_id") or f"a2a-{uuid.uuid4().hex[:12]}")
    status = str(data.get("status") or "pending").lower()
    tx_hash = data.get("tx_hash")
    amount_wei = int(data.get("amount_wei") or data.get("amount_atomic") or 0)
    token = str(data.get("token") or "AXM").upper()

    rec = PaymentRecord(
        payment_id=f"a2a-{bid_id}",
        owner=str(data.get("agent_id") or data.get("bidder") or data.get("payer") or "unknown"),
        amount_atomic=amount_wei,
        amount_display=amount_wei / 10**18,
        currency=token,
        provider="a2a",
        provider_evidence=_evidence(
            task_id=task_id,
            bid_id=bid_id,
            tx_hash=tx_hash,
            commitment=data.get("commitment"),
            sealed_bid_id=data.get("sealed_bid_id"),
        ),
        entitlements=_evidence(task_id=task_id, bid_id=bid_id),
    )
    rec.transition(PaymentState.PENDING_VERIFICATION, actor="mapper", reason="a2a_payment_mapped")
    if status in ("confirmed", "completed", "settled", "revealed") and tx_hash:
        rec.transition(PaymentState.VERIFIED, actor="mapper", reason=f"a2a_status={status}")
    elif status in ("failed", "slashed", "expired", "ghosted"):
        rec.transition(PaymentState.FAILED, actor="mapper", reason=f"a2a_status={status}")
    return rec


def from_agent_billing(entry: Mapping[str, Any]) -> PaymentRecord:
    """Map an ``agent_billing.record_platform_payment`` JSONL entry to contract.

    These are onchain treasury payments (fee revenue); provider is "manual"
    with the tx hash as evidence.  An entry without a tx hash cannot be
    verified against chain state, so it is quarantined.
    """
    data = dict(entry)
    tx_hash = data.get("tx_hash")
    payment_id = str(data.get("payment_id") or f"billing-{uuid.uuid4().hex[:12]}")
    token = str(data.get("token") or "AXM").upper()

    rec = PaymentRecord(
        payment_id=f"billing-{payment_id}",
        owner=str(data.get("payer") or "unknown"),
        amount_atomic=int(data.get("amount_atomic") or 0),
        amount_display=float(data.get("amount_display") or 0.0),
        currency=token,
        provider="manual",
        provider_evidence=_evidence(
            tx_hash=tx_hash,
            treasury=data.get("treasury"),
            product_name=data.get("product_name"),
            plan_id=data.get("plan_id"),
            entry_ts=data.get("ts"),
        ),
        entitlements=_evidence(plan_id=data.get("plan_id"), product_name=data.get("product_name")),
    )
    rec.transition(PaymentState.PENDING_VERIFICATION, actor="mapper", reason="billing_entry_mapped")
    if tx_hash:
        rec.transition(PaymentState.VERIFIED, actor="mapper", reason="onchain_tx_present")
    else:
        rec.transition(PaymentState.QUARANTINED, actor="mapper",
                       reason="billing_entry_without_tx_hash")
    return rec


def from_checkout_row(row: Mapping[str, Any]) -> PaymentRecord:
    """Map a ``platform_checkouts`` row (token checkout) to the contract."""
    data = dict(row)
    payment_id = str(data.get("payment_id") or f"checkout-{uuid.uuid4().hex[:12]}")
    status = str(data.get("status") or "pending").lower()
    tx_hash = data.get("tx_hash")
    token = str(data.get("token") or "AXM").upper()

    rec = PaymentRecord(
        payment_id=f"checkout-{payment_id}",
        owner=str(data.get("payer_wallet") or data.get("customer_email") or "unknown"),
        amount_atomic=int(data.get("amount_atomic") or 0),
        amount_display=float(data.get("amount_display") or 0.0),
        currency=token,
        provider="checkout",
        provider_evidence=_evidence(
            checkout_payment_id=payment_id,
            tx_hash=tx_hash,
            idempotency_key=data.get("idempotency_key"),
            usd_reference=data.get("usd_reference"),
        ),
        entitlements=_evidence(plan_id=data.get("plan_id"), product_name=data.get("product_name")),
    )
    rec.transition(PaymentState.PENDING_VERIFICATION, actor="mapper", reason="checkout_row_mapped")
    if status == "completed" and tx_hash:
        rec.transition(PaymentState.VERIFIED, actor="mapper", reason="checkout_completed_with_tx")
    elif status == "completed":
        rec.transition(PaymentState.QUARANTINED, actor="mapper",
                       reason="checkout_completed_without_tx_hash")
    elif status in ("expired", "failed"):
        rec.transition(PaymentState.FAILED, actor="mapper", reason=f"checkout_status={status}")
    return rec


def from_legacy_row(row: Mapping[str, Any], *, source: str = "legacy") -> PaymentRecord:
    """Map an unknown/legacy row to the contract.

    ALWAYS lands in ``quarantined``.  The original row is preserved verbatim
    in evidence for human/adjudicator review.  A quarantined payment must
    never be treated as paid: the only exits are back to
    ``pending_verification`` (re-adjudicated as legitimate) or ``failed``.
    """
    data = dict(row)
    rec = PaymentRecord(
        payment_id=f"legacy-{uuid.uuid4().hex}",
        owner=str(data.get("owner") or data.get("customer_email") or data.get("payer") or "unknown"),
        amount_atomic=int(data.get("amount_atomic") or 0),
        amount_display=float(data.get("amount_display") or data.get("amount") or 0.0),
        currency=str(data.get("currency") or data.get("token") or "UNKNOWN").upper(),
        provider="legacy",
        provider_evidence={"source": source, "raw": data},
        entitlements={},
    )
    rec.transition(PaymentState.QUARANTINED, actor="mapper",
                   reason=f"unknown_shape_from_{source}")
    return rec


# ---------------------------------------------------------------------------
# Durable store (SQLite, WAL).  payments + append-only payment_history.
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    payment_id      TEXT PRIMARY KEY,
    owner           TEXT NOT NULL,
    amount_atomic   INTEGER NOT NULL,
    amount_display  REAL NOT NULL,
    currency        TEXT NOT NULL,
    provider        TEXT NOT NULL,
    provider_evidence TEXT NOT NULL DEFAULT '{}',
    entitlements    TEXT NOT NULL DEFAULT '{}',
    refunds         TEXT NOT NULL DEFAULT '{}',
    state           TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 0,
    block_number    INTEGER,
    block_hash      TEXT,
    confirmations   INTEGER,
    finality_confirmed INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    tx_hash         TEXT
);
CREATE TABLE IF NOT EXISTS payment_history (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id    TEXT NOT NULL,
    from_state    TEXT NOT NULL,
    to_state      TEXT NOT NULL,
    ts            TEXT NOT NULL,
    actor         TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_payment_history_payment
    ON payment_history(payment_id, seq);
CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_provider_tx
    ON payments(provider, tx_hash)
    WHERE tx_hash IS NOT NULL AND tx_hash != '';
-- History is append-only: reject UPDATE and DELETE at the SQL layer so that
-- even raw-SQL access cannot rewrite payment history.
CREATE TRIGGER IF NOT EXISTS trg_payment_history_no_update
BEFORE UPDATE ON payment_history
BEGIN
    SELECT RAISE(ABORT, 'payment_history is append-only: UPDATE rejected');
END;
CREATE TRIGGER IF NOT EXISTS trg_payment_history_no_delete
BEFORE DELETE ON payment_history
BEGIN
    SELECT RAISE(ABORT, 'payment_history is append-only: DELETE rejected');
END;
"""


def _default_db_path() -> Path:
    """Return the default sqlite path under the SINCOR data dir."""
    try:
        from sincor2.data_paths import data_dir

        return data_dir() / "payment_ledger.db"
    except Exception:
        return Path(__file__).resolve().parent.parent.parent / "payment_ledger.db"


class PaymentLedgerStore:
    """Durable canonical payment store.

    Args:
        db_path: sqlite file.  WAL mode is enabled so readers never block
            writers and the file survives process restart.

    Thread-safety: each operation opens its own connection with a busy
    timeout; state changes use ``BEGIN IMMEDIATE`` + compare-and-swap on
    the version column so concurrent transitions race safely.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # -- internals --------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _row_to_record(row: sqlite3.Row, history: list[StateTransition]) -> PaymentRecord:
        return PaymentRecord(
            payment_id=row["payment_id"],
            owner=row["owner"],
            amount_atomic=row["amount_atomic"],
            amount_display=row["amount_display"],
            currency=row["currency"],
            provider=row["provider"],
            provider_evidence=json.loads(row["provider_evidence"] or "{}"),
            entitlements=json.loads(row["entitlements"] or "{}"),
            refunds=json.loads(row["refunds"] or "{}"),
            state=PaymentState(row["state"]),
            history=history,
            version=row["version"],
            block_number=row["block_number"],
            block_hash=row["block_hash"],
            confirmations=row["confirmations"],
            finality_confirmed=bool(row["finality_confirmed"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _load_history(self, conn: sqlite3.Connection, payment_id: str) -> list[StateTransition]:
        rows = conn.execute(
            "SELECT payment_id, from_state, to_state, ts, actor, reason"
            " FROM payment_history WHERE payment_id=? ORDER BY seq",
            (payment_id,),
        ).fetchall()
        return [
            StateTransition(
                payment_id=r["payment_id"],
                from_state=r["from_state"],
                to_state=r["to_state"],
                ts=r["ts"],
                actor=r["actor"],
                reason=r["reason"],
            )
            for r in rows
        ]

    # -- writes -------------------------------------------------------------

    def ingest(self, record: PaymentRecord) -> PaymentRecord:
        """Persist a mapped record, replaying its history into the ledger.

        Raises:
            DuplicatePaymentError: if the provider+tx_hash (or payment_id)
                was already ingested — this is the double-ingest guard.
        """
        tx_hash = record.provider_evidence.get("tx_hash")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """INSERT INTO payments
                       (payment_id, owner, amount_atomic, amount_display, currency,
                        provider, provider_evidence, entitlements, refunds, state,
                        version, block_number, block_hash, confirmations,
                        finality_confirmed, created_at, updated_at, tx_hash)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record.payment_id,
                        record.owner,
                        record.amount_atomic,
                        record.amount_display,
                        record.currency,
                        record.provider,
                        json.dumps(record.provider_evidence),
                        json.dumps(record.entitlements),
                        json.dumps(record.refunds),
                        record.state.value,
                        record.version,
                        record.block_number,
                        record.block_hash,
                        record.confirmations,
                        int(record.finality_confirmed),
                        record.created_at,
                        record.updated_at,
                        tx_hash,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise DuplicatePaymentError(
                    f"payment already ingested: {record.payment_id} ({exc})"
                ) from exc
            for h in record.history:
                conn.execute(
                    "INSERT INTO payment_history"
                    " (payment_id, from_state, to_state, ts, actor, reason)"
                    " VALUES (?,?,?,?,?,?)",
                    (h.payment_id, h.from_state, h.to_state, h.ts, h.actor, h.reason),
                )
            conn.commit()
            return record
        finally:
            conn.close()

    def transition_payment(
        self,
        payment_id: str,
        to_state: PaymentState | str,
        *,
        actor: str,
        reason: str = "",
        expected_version: Optional[int] = None,
        allow_finality_bypass: bool = False,
    ) -> PaymentRecord:
        """Atomically transition a payment's state (compare-and-swap).

        Loads the current row, validates the transition against the legal
        table, then applies it with ``UPDATE ... WHERE version=?``.  Exactly
        one concurrent caller wins; the rest get :class:`PaymentConflictError`.

        Raises:
            PaymentNotFound: unknown payment_id.
            IllegalPaymentTransition: transition not in the legal table.
            FinalityNotConfirmed: settling an onchain payment (has tx_hash)
                whose finality was not confirmed by the finality checker,
                unless ``allow_finality_bypass`` is True (used by the
                finality module itself).
            PaymentConflictError: version mismatch — another writer moved
                the payment first.
        """
        target = _as_state(to_state)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM payments WHERE payment_id=?", (payment_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise PaymentNotFound(f"unknown payment_id: {payment_id}")
            history = self._load_history(conn, payment_id)
            record = self._row_to_record(row, history)

            # Skip-finality guard: onchain money may not settle without
            # confirmed finality.  This is the anti-double-spend / anti-reorg
            # gate — settling on a 0-conf tx is how reorgs steal entitlements.
            if (
                target == PaymentState.SETTLED
                and record.provider_evidence.get("tx_hash")
                and not record.finality_confirmed
                and not allow_finality_bypass
            ):
                conn.rollback()
                raise FinalityNotConfirmed(
                    f"payment {payment_id} carries onchain evidence but finality "
                    "is not confirmed; route through payment_finality.check_finality"
                )

            if expected_version is not None and record.version != expected_version:
                conn.rollback()
                raise PaymentConflictError(
                    f"version conflict on {payment_id}: expected {expected_version},"
                    f" found {record.version}"
                )

            entry = record.transition(target, actor=actor, reason=reason)

            updated = conn.execute(
                "UPDATE payments SET state=?, version=?, updated_at=? "
                "WHERE payment_id=? AND version=?",
                (record.state.value, record.version, record.updated_at, payment_id, row["version"]),
            )
            if updated.rowcount != 1:
                # Another writer moved the payment between our read and write.
                conn.rollback()
                raise PaymentConflictError(
                    f"concurrent transition lost the race on {payment_id}"
                )
            conn.execute(
                "INSERT INTO payment_history"
                " (payment_id, from_state, to_state, ts, actor, reason)"
                " VALUES (?,?,?,?,?,?)",
                (entry.payment_id, entry.from_state, entry.to_state, entry.ts, entry.actor, entry.reason),
            )
            conn.commit()
            return record
        finally:
            conn.close()

    def record_chain_progress(
        self,
        payment_id: str,
        *,
        block_number: int,
        block_hash: str,
        confirmations: int,
    ) -> PaymentRecord:
        """Record confirmation progress WITHOUT unlocking settlement.

        Unlike :meth:`mark_finality_confirmed`, this does NOT set the
        ``finality_confirmed`` flag, so the store-level skip-finality guard
        still blocks settlement until the threshold is met.  Used by the
        finality checker on every poll below threshold.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM payments WHERE payment_id=?", (payment_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise PaymentNotFound(f"unknown payment_id: {payment_id}")
            conn.execute(
                "UPDATE payments SET block_number=?, block_hash=?, confirmations=?,"
                " updated_at=? WHERE payment_id=?",
                (block_number, block_hash, confirmations, _utc_now_iso(), payment_id),
            )
            conn.commit()
            history = self._load_history(conn, payment_id)
            fresh = conn.execute(
                "SELECT * FROM payments WHERE payment_id=?", (payment_id,)
            ).fetchone()
            return self._row_to_record(fresh, history)
        finally:
            conn.close()

    def mark_finality_confirmed(
        self,
        payment_id: str,
        *,
        block_number: int,
        block_hash: str,
        confirmations: int,
        actor: str = "finality-checker",
    ) -> PaymentRecord:
        """Record finality evidence (called by payment_finality only).

        Sets the finality evidence + ``finality_confirmed`` flag so the
        store-level skip-finality guard lets the payment settle.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM payments WHERE payment_id=?", (payment_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise PaymentNotFound(f"unknown payment_id: {payment_id}")
            conn.execute(
                "UPDATE payments SET block_number=?, block_hash=?, confirmations=?,"
                " finality_confirmed=1, updated_at=? WHERE payment_id=?",
                (block_number, block_hash, confirmations, _utc_now_iso(), payment_id),
            )
            conn.commit()
            history = self._load_history(conn, payment_id)
            fresh = conn.execute(
                "SELECT * FROM payments WHERE payment_id=?", (payment_id,)
            ).fetchone()
            return self._row_to_record(fresh, history)
        finally:
            conn.close()

    def set_refund(self, payment_id: str, *, refund_id: str, status: str, actor: str) -> PaymentRecord:
        """Attach refund linkage to a payment (does not change state by itself)."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM payments WHERE payment_id=?", (payment_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise PaymentNotFound(f"unknown payment_id: {payment_id}")
            refunds = json.loads(row["refunds"] or "{}")
            refunds.update({"refund_id": refund_id, "status": status, "updated_at": _utc_now_iso()})
            conn.execute(
                "UPDATE payments SET refunds=?, updated_at=? WHERE payment_id=?",
                (json.dumps(refunds), _utc_now_iso(), payment_id),
            )
            conn.commit()
            history = self._load_history(conn, payment_id)
            fresh = conn.execute(
                "SELECT * FROM payments WHERE payment_id=?", (payment_id,)
            ).fetchone()
            return self._row_to_record(fresh, history)
        finally:
            conn.close()

    # -- reads --------------------------------------------------------------

    def get(self, payment_id: str) -> PaymentRecord:
        """Load a payment with its full history.

        Raises:
            PaymentNotFound: unknown payment_id.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM payments WHERE payment_id=?", (payment_id,)
            ).fetchone()
            if row is None:
                raise PaymentNotFound(f"unknown payment_id: {payment_id}")
            return self._row_to_record(row, self._load_history(conn, payment_id))
        finally:
            conn.close()

    def find_by_tx_hash(self, provider: str, tx_hash: str) -> Optional[PaymentRecord]:
        """Find a payment by provider + tx hash (double-spend lookup)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM payments WHERE provider=? AND tx_hash=?",
                (provider, tx_hash),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_record(row, self._load_history(conn, row["payment_id"]))
        finally:
            conn.close()

    def list_by_state(self, state: PaymentState | str) -> list[PaymentRecord]:
        """List payments currently in the given state."""
        target = _as_state(state).value
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM payments WHERE state=? ORDER BY created_at", (target,)
            ).fetchall()
            return [self._row_to_record(r, self._load_history(conn, r["payment_id"])) for r in rows]
        finally:
            conn.close()

    def count(self) -> int:
        """Return the number of payments in the ledger."""
        conn = self._connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0])
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Alert records (used by payment_finality; stored here so alerts are durable)
# ---------------------------------------------------------------------------


@dataclass
class PaymentAlert:
    """A durable alert record (e.g. reorg detected, finality anomaly)."""

    alert_id: str
    payment_id: str
    kind: str
    message: str
    ts: str = field(default_factory=_utc_now_iso)
    details: dict[str, Any] = field(default_factory=dict)


_ALERT_SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_alerts (
    alert_id   TEXT PRIMARY KEY,
    payment_id TEXT NOT NULL,
    kind       TEXT NOT NULL,
    message    TEXT NOT NULL,
    ts         TEXT NOT NULL,
    details    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_payment_alerts_payment
    ON payment_alerts(payment_id, ts);
"""


def record_alert(
    store_or_db: PaymentLedgerStore | str | Path,
    payment_id: str,
    kind: str,
    message: str,
    details: Optional[dict[str, Any]] = None,
) -> PaymentAlert:
    """Persist a payment alert and return it.

    Accepts a :class:`PaymentLedgerStore` or a raw db path so callers that
    only have the path (e.g. background workers) can emit alerts too.
    """
    alert = PaymentAlert(
        alert_id=f"alert-{uuid.uuid4().hex}",
        payment_id=payment_id,
        kind=kind,
        message=message,
        details=details or {},
    )
    db_path = store_or_db.db_path if isinstance(store_or_db, PaymentLedgerStore) else Path(store_or_db)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        conn.executescript(_ALERT_SCHEMA)
        conn.execute(
            "INSERT INTO payment_alerts (alert_id, payment_id, kind, message, ts, details)"
            " VALUES (?,?,?,?,?,?)",
            (
                alert.alert_id,
                alert.payment_id,
                alert.kind,
                alert.message,
                alert.ts,
                json.dumps(alert.details),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return alert


def list_alerts(
    store_or_db: PaymentLedgerStore | str | Path,
    payment_id: Optional[str] = None,
) -> list[PaymentAlert]:
    """List recorded alerts, optionally filtered to one payment."""
    db_path = store_or_db.db_path if isinstance(store_or_db, PaymentLedgerStore) else Path(store_or_db)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_ALERT_SCHEMA)
        if payment_id:
            rows = conn.execute(
                "SELECT * FROM payment_alerts WHERE payment_id=? ORDER BY ts",
                (payment_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM payment_alerts ORDER BY ts").fetchall()
        return [
            PaymentAlert(
                alert_id=r["alert_id"],
                payment_id=r["payment_id"],
                kind=r["kind"],
                message=r["message"],
                ts=r["ts"],
                details=json.loads(r["details"] or "{}"),
            )
            for r in rows
        ]
    finally:
        conn.close()


def allowed_transitions() -> dict[str, list[str]]:
    """Return the legal transition table as plain strings (for docs/UIs)."""
    return {s.value: sorted(t.value for t in targets) for s, targets in _ALLOWED_TRANSITIONS.items()}


def is_paid_state(state: PaymentState | str) -> bool:
    """Return True if the state means money is confirmed in hand.

    Only ``settled`` and ``fulfilled`` count as paid.  ``verified`` is NOT
    paid for onchain flows — verification is not finality.
    """
    return _as_state(state) in (PaymentState.SETTLED, PaymentState.FULFILLED)
