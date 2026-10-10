"""Finality and reorg-aware settlement (PAY-03).

Extends the payment state machine with ``awaiting_finality`` between
``verified`` and ``settled``.  Verification says "the money movement looks
right"; finality says "the chain will not take it back".  Entitlements are
granted only on settlement — never on verification alone.

Components:
  * :class:`FinalityPolicy` — per-chain confirmation thresholds.
  * :class:`ChainClient` — injected chain interface; production wires a real
    RPC client later.  Tests use :class:`FakeChainClient`.
  * :func:`check_finality` — poll-style finality check; settles only when
    confirmations >= threshold.
  * :func:`on_reorg` — reorg handler: disputes the payment, revokes
    entitlements, emits a durable alert.
  * Entitlement grant/revoke hooks — registered callbacks so the ledger
    itself never assumes how access is granted in production.

Reorg rule: if a block hash previously recorded at a given height changes,
the payment is moved to ``disputed``, every linked entitlement is revoked
via the registered revoker, and a ``reorg_detected`` alert is persisted via
``payment_ledger.record_alert``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Protocol

from sincor2.payment_ledger import (
    PaymentConflictError,
    PaymentLedgerStore,
    PaymentNotFound,
    PaymentRecord,
    PaymentState,
    is_paid_state,
    record_alert,
)


# ---------------------------------------------------------------------------
# Chain client interface
# ---------------------------------------------------------------------------


class ChainClient(Protocol):
    """Injected chain interface.  Production wires a real RPC client later."""

    def get_receipt(self, tx_hash: str) -> Optional[Mapping[str, Any]]:
        """Return the tx receipt mapping, or None if not found.

        Expected keys: ``blockNumber`` (int), ``blockHash`` (str),
        ``status`` (int, 1 = success).
        """
        ...

    def get_block(self, height: int) -> Optional[Mapping[str, Any]]:
        """Return the block mapping at ``height``, or None if unavailable.

        Expected keys: ``number`` (int), ``hash`` (str).
        """
        ...

    def head_height(self) -> int:
        """Return the current chain head height."""
        ...


# ---------------------------------------------------------------------------
# Finality policy
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLDS: dict[int, int] = {
    1: 12,          # Ethereum mainnet
    8453: 12,       # Base mainnet
    11155111: 6,    # Sepolia
    84532: 6,       # Base Sepolia
}
_DEFAULT_THRESHOLD = 12


class FinalityPolicy:
    """Per-chain confirmation thresholds.

    Args:
        thresholds: mapping of chain_id -> required confirmations.
            Defaults cover Ethereum/Base mainnets and Sepolia testnets.
        default: fallback threshold for unlisted chains.

    The ``SINCOR_FINALITY_CONFIRMATIONS`` env var (JSON object of
    ``{"<chain_id>": <confirmations>}``) merges over the defaults so
    operators can tune without code changes.
    """

    def __init__(
        self,
        thresholds: Optional[Mapping[int, int]] = None,
        default: int = _DEFAULT_THRESHOLD,
    ) -> None:
        merged: dict[int, int] = dict(_DEFAULT_THRESHOLDS)
        if thresholds:
            merged.update({int(k): int(v) for k, v in thresholds.items()})
        env_raw = os.environ.get("SINCOR_FINALITY_CONFIRMATIONS", "").strip()
        if env_raw:
            try:
                env_map = json.loads(env_raw)
                merged.update({int(k): int(v) for k, v in env_map.items()})
            except (ValueError, AttributeError):
                pass  # malformed env must not break the policy
        self.thresholds = merged
        self.default = int(default)

    def confirmations_for(self, chain_id: int) -> int:
        """Return the required confirmations for a chain id."""
        return self.thresholds.get(int(chain_id), self.default)


# ---------------------------------------------------------------------------
# Entitlement hooks
# ---------------------------------------------------------------------------

EntitlementHook = Callable[[PaymentRecord], Mapping[str, Any]]

_granters: list[EntitlementHook] = []
_revokers: list[EntitlementHook] = []


def register_entitlement_granter(fn: EntitlementHook) -> EntitlementHook:
    """Register a callback invoked once when a payment settles.

    The callback receives the settled :class:`PaymentRecord` and returns a
    mapping describing what was granted (for the audit trail).
    """
    _granters.append(fn)
    return fn


def register_entitlement_revoker(fn: EntitlementHook) -> EntitlementHook:
    """Register a callback invoked when a payment is disputed (reorg etc.).

    The callback receives the disputed :class:`PaymentRecord` and returns a
    mapping describing what was revoked.
    """
    _revokers.append(fn)
    return fn


def clear_entitlement_hooks() -> None:
    """Remove all registered hooks (tests use this to reset)."""
    _granters.clear()
    _revokers.clear()


def _grant_entitlements(record: PaymentRecord) -> list[Mapping[str, Any]]:
    """Run all granters; failures are captured, never raised."""
    results = []
    for fn in _granters:
        try:
            results.append({"ok": True, "result": dict(fn(record))})
        except Exception as exc:  # noqa: BLE001 — hook isolation
            results.append({"ok": False, "error": str(exc)})
    return results


def _revoke_entitlements(record: PaymentRecord) -> list[Mapping[str, Any]]:
    """Run all revokers; failures are captured, never raised."""
    results = []
    for fn in _revokers:
        try:
            results.append({"ok": True, "result": dict(fn(record))})
        except Exception as exc:  # noqa: BLE001 — hook isolation
            results.append({"ok": False, "error": str(exc)})
    return results


# ---------------------------------------------------------------------------
# Finality check
# ---------------------------------------------------------------------------


@dataclass
class FinalityResult:
    """Outcome of :func:`check_finality`."""

    ok: bool
    status: str
    payment_id: str
    confirmations: Optional[int] = None
    required: Optional[int] = None
    detail: str = ""


def _chain_id_of(record: PaymentRecord) -> int:
    """Extract the chain id from evidence; default to Base mainnet."""
    raw = record.provider_evidence.get("chain_id")
    try:
        return int(raw) if raw is not None else 8453
    except (TypeError, ValueError):
        return 8453


def check_finality(
    store: PaymentLedgerStore,
    payment_id: str,
    chain_client: ChainClient,
    policy: FinalityPolicy,
    *,
    actor: str = "finality-checker",
) -> FinalityResult:
    """Check onchain finality for a payment; settle only at threshold.

    Flow:
      * No tx_hash in evidence (fiat/offchain) -> settle directly from
        ``verified`` (finality is not applicable); grants entitlements.
      * Receipt missing or chain unreachable -> stays ``awaiting_finality``,
        no state change, no crash.
      * Receipt reverted (status 0) -> ``failed``.
      * Recorded block hash changed at the same height -> reorg path
        (:func:`on_reorg`): ``disputed`` + entitlements revoked + alert.
      * confirmations < threshold -> record progress, stay
        ``awaiting_finality``, no entitlement.
      * confirmations >= threshold -> ``finality_confirmed`` flag set,
        transition to ``settled``, entitlements granted exactly once.

    Idempotent: calling again on an already-settled payment returns
    ``already_settled`` without re-granting entitlements or double-settling.
    """
    record = store.get(payment_id)
    tx_hash = record.provider_evidence.get("tx_hash")

    # -- terminal / idempotent fast paths ---------------------------------
    if is_paid_state(record.state):
        # A deep reorg can orphan even a settled tx: verify the recorded
        # block hash against the chain's current view before declaring
        # everything fine.  Dispute ONLY on positive conflicting evidence
        # (both hashes present and different); chain outages or missing
        # blocks must never trigger a dispute.
        if record.block_number is not None and record.block_hash:
            try:
                block = chain_client.get_block(int(record.block_number))
                chain_hash = str((block or {}).get("hash") or "")
                if chain_hash and chain_hash != record.block_hash:
                    return on_reorg(
                        store, payment_id,
                        block_number=int(record.block_number),
                        old_block_hash=record.block_hash,
                        new_block_hash=chain_hash,
                        actor=actor,
                    )
            except Exception:  # noqa: BLE001 — outage is not a reorg
                pass
        return FinalityResult(
            ok=True, status="already_settled", payment_id=payment_id,
            detail=f"payment already in paid state {record.state.value}; no re-settle",
        )
    if record.state in (PaymentState.FAILED, PaymentState.REFUNDED, PaymentState.QUARANTINED):
        return FinalityResult(
            ok=False, status="terminal_state", payment_id=payment_id,
            detail=f"payment is {record.state.value}; finality not applicable",
        )
    if record.state == PaymentState.DISPUTED:
        return FinalityResult(
            ok=False, status="disputed", payment_id=payment_id,
            detail="payment is disputed; resolve the dispute before finality",
        )

    # -- offchain path: no tx_hash means no chain finality to await --------
    if not tx_hash:
        if record.state == PaymentState.CREATED:
            store.transition_payment(
                payment_id, PaymentState.PENDING_VERIFICATION,
                actor=actor, reason="finality_check_started",
            )
            record = store.get(payment_id)
        if record.state == PaymentState.PENDING_VERIFICATION:
            store.transition_payment(
                payment_id, PaymentState.VERIFIED,
                actor=actor, reason="offchain_provider_verified",
            )
            record = store.get(payment_id)
        try:
            record = store.transition_payment(
                payment_id, PaymentState.SETTLED,
                actor=actor, reason="finality_not_applicable_offchain",
            )
        except PaymentConflictError:
            return FinalityResult(
                ok=True, status="already_settled", payment_id=payment_id,
                detail="lost the settlement race to a concurrent poller; no re-settle",
            )
        granted = _grant_entitlements(record)
        return FinalityResult(
            ok=True, status="settled", payment_id=payment_id,
            detail=f"offchain payment settled; entitlements granted={granted}",
        )

    # -- onchain path ------------------------------------------------------
    # Concurrent pollers may race these advances; on a lost race, reload
    # and continue with the fresh state instead of crashing.
    if record.state == PaymentState.VERIFIED:
        try:
            record = store.transition_payment(
                payment_id, PaymentState.AWAITING_FINALITY,
                actor=actor, reason="awaiting_chain_finality",
            )
        except PaymentConflictError:
            record = store.get(payment_id)
    if record.state == PaymentState.CREATED:
        try:
            record = store.transition_payment(
                payment_id, PaymentState.PENDING_VERIFICATION,
                actor=actor, reason="finality_check_started",
            )
        except PaymentConflictError:
            record = store.get(payment_id)
        return FinalityResult(
            ok=False, status="not_verified", payment_id=payment_id,
            detail="payment not yet verified; verify before finality",
        )
    if record.state == PaymentState.PENDING_VERIFICATION:
        return FinalityResult(
            ok=False, status="not_verified", payment_id=payment_id,
            detail="payment not yet verified; verify before finality",
        )

    # receipt lookup — chain outage or missing receipt must not crash
    try:
        receipt = chain_client.get_receipt(str(tx_hash))
    except Exception as exc:  # noqa: BLE001 — chain client isolation
        return FinalityResult(
            ok=False, status="chain_unavailable", payment_id=payment_id,
            detail=f"get_receipt raised: {exc}; payment stays awaiting_finality",
        )
    if receipt is None:
        return FinalityResult(
            ok=False, status="receipt_not_found", payment_id=payment_id,
            detail="receipt not found; payment stays awaiting_finality",
        )
    if int(receipt.get("status", 0)) != 1:
        record = store.transition_payment(
            payment_id, PaymentState.FAILED,
            actor=actor, reason="tx_reverted_onchain",
        )
        return FinalityResult(
            ok=False, status="tx_reverted", payment_id=payment_id,
            detail="transaction reverted onchain; payment failed, no entitlement",
        )

    try:
        block_number = int(receipt["blockNumber"])
    except (KeyError, TypeError, ValueError):
        return FinalityResult(
            ok=False, status="malformed_receipt", payment_id=payment_id,
            detail="receipt missing blockNumber; payment stays awaiting_finality",
        )
    receipt_block_hash = str(receipt.get("blockHash") or "")

    # reorg check: the chain's view of this height must match what we saw
    try:
        block = chain_client.get_block(block_number)
        head = chain_client.head_height()
    except Exception as exc:  # noqa: BLE001 — chain client isolation
        return FinalityResult(
            ok=False, status="chain_unavailable", payment_id=payment_id,
            detail=f"block/head lookup raised: {exc}; payment stays awaiting_finality",
        )
    chain_block_hash = str((block or {}).get("hash") or "")

    # If we previously recorded a block hash at this height and the chain
    # now disagrees (or the receipt itself disagrees), that is a reorg.
    for seen_hash, label in (
        (record.block_hash, "recorded"),
        (receipt_block_hash, "receipt"),
    ):
        if seen_hash and chain_block_hash and seen_hash != chain_block_hash:
            return on_reorg(
                store,
                payment_id,
                block_number=block_number,
                old_block_hash=seen_hash,
                new_block_hash=chain_block_hash,
                actor=actor,
            )

    seen_hash = receipt_block_hash or chain_block_hash
    confirmations = max(0, int(head) - block_number + 1)
    required = policy.confirmations_for(_chain_id_of(record))

    store.record_chain_progress(
        payment_id,
        block_number=block_number,
        block_hash=seen_hash,
        confirmations=confirmations,
    )

    if confirmations < required:
        return FinalityResult(
            ok=False, status="awaiting_finality", payment_id=payment_id,
            confirmations=confirmations, required=required,
            detail=f"{confirmations}/{required} confirmations; no entitlement granted",
        )

    # Threshold met: unlock settlement, transition, grant entitlements once.
    # Two pollers can race here; the CAS loser gets PaymentConflictError —
    # translate that into a clean already_settled instead of crashing.
    store.mark_finality_confirmed(
        payment_id,
        block_number=block_number,
        block_hash=seen_hash,
        confirmations=confirmations,
        actor=actor,
    )
    try:
        record = store.transition_payment(
            payment_id, PaymentState.SETTLED,
            actor=actor,
            reason=f"finality_confirmed_{confirmations}_of_{required}",
            allow_finality_bypass=True,  # flag was just set by mark_finality_confirmed
        )
    except PaymentConflictError:
        return FinalityResult(
            ok=True, status="already_settled", payment_id=payment_id,
            detail="lost the settlement race to a concurrent poller; no re-settle",
        )
    granted = _grant_entitlements(record)
    return FinalityResult(
        ok=True, status="settled", payment_id=payment_id,
        confirmations=confirmations, required=required,
        detail=f"finality confirmed; entitlements granted={granted}",
    )


def on_reorg(
    store: PaymentLedgerStore,
    payment_id: str,
    *,
    block_number: int,
    old_block_hash: str,
    new_block_hash: str,
    actor: str = "reorg-watcher",
) -> FinalityResult:
    """Handle a detected chain reorganization for a payment.

    Moves the payment to ``disputed``, revokes all linked entitlements via
    the registered revokers, and persists a ``reorg_detected`` alert.
    Idempotent: a payment already in ``disputed`` is left alone.
    """
    record = store.get(payment_id)
    if record.state == PaymentState.DISPUTED:
        return FinalityResult(
            ok=False, status="already_disputed", payment_id=payment_id,
            detail="payment already disputed; no duplicate alert",
        )

    record = store.transition_payment(
        payment_id, PaymentState.DISPUTED,
        actor=actor,
        reason=(
            f"reorg at height {block_number}: "
            f"{old_block_hash} -> {new_block_hash}"
        ),
        allow_finality_bypass=True,
    )
    revoked = _revoke_entitlements(record)
    alert = record_alert(
        store,
        payment_id,
        kind="reorg_detected",
        message=(
            f"Reorg detected for payment {payment_id}: block {block_number} "
            f"hash changed {old_block_hash} -> {new_block_hash}. "
            "Payment disputed; entitlements revoked."
        ),
        details={
            "block_number": block_number,
            "old_block_hash": old_block_hash,
            "new_block_hash": new_block_hash,
            "revoked": revoked,
            "previous_state": record.history[-1].from_state if record.history else None,
        },
    )
    return FinalityResult(
        ok=False, status="disputed", payment_id=payment_id,
        detail=f"reorg -> disputed; alert={alert.alert_id}; revoked={revoked}",
    )
