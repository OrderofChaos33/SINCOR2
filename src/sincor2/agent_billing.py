"""Record SINC platform revenue; historical burn audit (spec §5.3).
Locked policy: 5% platform fee to treasury, converted to USDC/WETH before
deposit. No burn — burn counters below are historical-only."""

from __future__ import annotations

import contextlib
import json
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sincor2.platform_payments import SINC, TREASURY, _rpc_call, atomic_to_display

BURN_ADDRESS = "0x000000000000000000000000000000000000dEaD"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _log_path() -> Path:
    from sincor2.data_paths import agent_burn_log_path

    return agent_burn_log_path()


_log_lock = threading.Lock()


@contextlib.contextmanager
def _log_file_lock(log_file: Path):
    """OS flock beside the JSONL log, held across dedupe-check + append.

    _log_lock only serializes threads in THIS process (NEW-4): two
    processes released together could both miss the dedupe check and
    append duplicates. The OS releases the lock automatically when a
    holder dies, so a crashed writer never wedges the log. Degrades to
    the threading lock alone where flock is unavailable.
    """
    lock_path = str(log_file) + ".lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        yield
        return
    locked = False
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            locked = True
        except (ImportError, OSError):
            locked = False
        yield
    finally:
        if locked:
            try:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
        os.close(fd)


def _find_platform_payment(log_file: Path, *, tx_hash: str,
                           payment_id: str, amount_atomic: int,
                           product_name: str) -> Optional[dict[str, Any]]:
    """Return the existing platform-payment row for this payment, if any.

    Dedupe key: (tx_hash, payment_id) — payment_id is the challenge /
    order id that uniquely names one payment for one tx. Rows written
    without a payment_id fall back to (tx_hash, product_name,
    amount_atomic): a different amount under the same tx + product is
    a DIFFERENT payment and must not dedupe into the first row
    (NEW-6). Known limitation: the fallback key stays payer-blind for
    equal amounts — a same-amount retry whose recovered payer differs
    (e.g. by case) still dedupes, which is the safe direction for an
    append-only revenue log.

    Trade-off, stated both ways (R3-3): the amount-exact side cuts the
    other direction. If one economic payment is re-presented under the
    same tx + product with a RECOMPUTED different amount (e.g. a spot
    re-price between a crash and its retry), it does not match the
    first row and a second row IS appended — the log can double-count
    that payment. That is accepted deliberately: the alternative
    (amount-blind dedupe on tx + product) would swallow a genuinely
    distinct second payment that happens to share one tx, silently
    LOSING revenue records, which is worse for an append-only log
    than an auditable duplicate.
    """
    if not log_file.is_file():
        return None
    tx = (tx_hash or "").lower()
    for line in log_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") != "platform_payment":
            continue
        if (row.get("tx_hash") or "").lower() != tx:
            continue
        if payment_id:
            if row.get("payment_id") == payment_id:
                return row
        elif row.get("product_name") == product_name:
            try:
                if int(row.get("amount_atomic") or 0) != amount_atomic:
                    continue
            except (TypeError, ValueError):
                continue
            return row
    return None


def record_platform_payment(
    *,
    tx_hash: str,
    payer_wallet: str,
    token: str,
    amount_atomic: int,
    product_name: str = "",
    plan_id: str = "",
    payment_id: str = "",
) -> dict[str, Any]:
    """Append treasury payment to the platform payment log.

    Crash-safe idempotency (hunt-29 R1): the log is append-only and a
    caller can crash after this append but before its own commit /
    flag write lands, then retry the whole payment record. Keyed on
    (tx_hash, payment_id), a retry of the same payment returns the
    existing row instead of appending a duplicate — each payment is
    recorded at most once across any crash+retry sequence.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "platform_payment",
        "tx_hash": tx_hash,
        "payer": payer_wallet.lower(),
        "token": token.upper(),
        "amount_atomic": amount_atomic,
        "amount_display": atomic_to_display(amount_atomic, token),
        "treasury": TREASURY.lower(),
        "product_name": product_name,
        "plan_id": plan_id,
        "payment_id": payment_id,
        "burn_policy": "5% platform fee to treasury, converted to USDC/WETH before deposit. No burn.",
        "burn_tx": None,
    }

    log_file = _log_path()
    with _log_lock:
        with _log_file_lock(log_file):
            if tx_hash:
                existing = _find_platform_payment(
                    log_file, tx_hash=tx_hash, payment_id=payment_id,
                    amount_atomic=amount_atomic,
                    product_name=product_name)
                if existing is not None:
                    return existing
            with log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    return entry


def _chain_burn_total() -> float:
    """Sum SINC sent to dead address from treasury (on-chain audit)."""
    try:
        latest_hex = _rpc_call("eth_blockNumber", [])
        latest = int(latest_hex, 16)
        from_block = max(0, latest - 500_000)
        logs = _rpc_call(
            "eth_getLogs",
            [
                {
                    "fromBlock": hex(from_block),
                    "toBlock": "latest",
                    "address": SINC,
                    "topics": [
                        TRANSFER_TOPIC,
                        f"0x{'0' * 24}{TREASURY.lower().replace('0x', '')}",
                        f"0x{'0' * 24}{BURN_ADDRESS.lower().replace('0x', '')}",
                    ],
                }
            ],
        )
        total = 0
        for log in logs or []:
            total += int(log.get("data", "0x0"), 16)
        return atomic_to_display(total, "SINC")
    except Exception:
        return 0.0


def fetch_burn_stats() -> dict[str, Any]:
    """Internal-only historical stats. The public /api/sinc/burn-stats route
    is retired; on-chain burn totals are historical. Locked policy: no burn."""
    log_file = _log_path()
    payments: list[dict[str, Any]] = []
    sinc_volume = 0.0
    axm_volume = 0.0
    if log_file.is_file():
        for line in log_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            payments.append(row)
            tok = (row.get("token") or "").upper()
            amt = float(row.get("amount_display") or 0)
            if tok == "SINC":
                sinc_volume += amt
            elif tok == "AXM":
                axm_volume += amt

    return {
        "treasury": TREASURY,
        "sinc_token": SINC,
        "log_path": str(log_file.relative_to(_root())) if log_file.is_file() else None,
        "platform_payments_count": len(payments),
        "sinc_platform_volume": round(sinc_volume, 4),
        "axm_platform_volume": round(axm_volume, 4),
        "on_chain_treasury_burn_sinc": _chain_burn_total(),
        "recent": payments[-10:],
        "burn_address": BURN_ADDRESS,
    }