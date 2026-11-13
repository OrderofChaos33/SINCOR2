"""Polyclaw performance receipts. Publish now. Vault later.

No external capital until calibration window is met.

Chain-of-receipts (migration note): each day row carries ``prev_hash`` —
the ``attestation_hash`` of the chronologically previous day ("" for the
first day) — and ``attestation_hash`` = ``record_hash`` of the whole row
*minus* ``attestation_hash`` itself (so ``prev_hash`` is covered by the
hash). Rewriting any past day silently is impossible: the recomputed hash
no longer matches the ``prev_hash`` stored on the next day, and
``verify_chain()`` reports the break.

Stores written before chaining carry rows without ``prev_hash``.
``verify_chain()`` treats such a legacy row as a valid chain-start ONLY
when it is the earliest date in the book — a legacy row sitting behind a
newer day is reported as a break (honest: old rows predate chaining, so
their ordering guarantees cannot be audited). Writing any day via
``record_day()`` re-chains the full book, upgrading legacy rows on write.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from sincor2.kya.registry import record_hash
from sincor2.kya.store import JsonStore

CALIBRATION_DAYS = 60


def _now() -> int:
    return int(time.time())


def _chain_input(row: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the attestation hash covers (prev_hash included, hash excluded)."""
    return {k: v for k, v in row.items() if k != "attestation_hash"}


class ReceiptBook:
    def __init__(self) -> None:
        self.store = JsonStore("polyclaw_receipts")
        raw = self.store.load() or {}
        self.lock = threading.Lock()
        self.days: List[Dict[str, Any]] = raw.get("days") or []

    def _persist(self) -> None:
        self.store.save({"days": self.days[-400:], "saved_at": _now()})

    def _rechain_locked(self) -> None:
        """Sort by date, then re-link prev_hash / attestation_hash end to end.

        Called on every record_day so a same-date replacement in the middle
        of history re-chains all successors instead of silently breaking.
        """
        ordered = sorted(self.days, key=lambda d: d.get("date") or "")
        prev = ""
        for d in ordered:
            d["prev_hash"] = prev
            d["attestation_hash"] = record_hash(_chain_input(d))
            prev = d["attestation_hash"]
        self.days = ordered

    def record_day(self, body: Dict[str, Any]) -> Dict[str, Any]:
        row = {
            "date": str(body.get("date") or time.strftime("%Y-%m-%d")),
            "trades": int(body.get("trades") or 0),
            "wins": int(body.get("wins") or 0),
            "pnl": float(body.get("pnl") or 0),
            "max_dd": float(body.get("max_dd") or 0),
            "simulated": bool(body.get("simulated", True)),
            "notes": str(body.get("notes") or "")[:300],
            "ts": _now(),
        }
        row["win_rate"] = round(row["wins"] / row["trades"], 4) if row["trades"] else None
        with self.lock:
            self.days = [d for d in self.days if d.get("date") != row["date"]]
            self.days.append(row)
            self._rechain_locked()
            self._persist()
        return row

    def verify_chain(self) -> Dict[str, Any]:
        """Recompute every link in date order. O(n), no mutation.

        Returns {"ok": bool, "days": n, "break_at": date-or-None} where
        break_at is the first date whose link fails.
        """
        with self.lock:
            days = sorted(self.days, key=lambda d: d.get("date") or "")
        n = len(days)
        prev = ""
        for i, d in enumerate(days):
            date = d.get("date")
            if "prev_hash" not in d:
                # Legacy row (pre-chaining store): only valid as chain start.
                if i != 0:
                    return {"ok": False, "days": n, "break_at": date}
                if d.get("attestation_hash") != record_hash(_chain_input(d)):
                    return {"ok": False, "days": n, "break_at": date}
            else:
                if d.get("prev_hash") != prev:
                    return {"ok": False, "days": n, "break_at": date}
                if d.get("attestation_hash") != record_hash(_chain_input(d)):
                    return {"ok": False, "days": n, "break_at": date}
            prev = d.get("attestation_hash")
        return {"ok": True, "days": n, "break_at": None}

    def scorecard(self) -> Dict[str, Any]:
        with self.lock:
            days = list(self.days)
        chain = self.verify_chain()
        live = [d for d in days if not d.get("simulated")]
        use = live or days
        n = len(use)
        pnl = sum(float(d.get("pnl") or 0) for d in use)
        trades = sum(int(d.get("trades") or 0) for d in use)
        wins = sum(int(d.get("wins") or 0) for d in use)
        first = use[0]["date"] if use else None
        last = use[-1]["date"] if use else None
        span_days = n
        vault_ready = (not any(d.get("simulated") for d in use)) and span_days >= CALIBRATION_DAYS and trades >= 80
        return {
            "days": n,
            "span_first": first,
            "span_last": last,
            "trades": trades,
            "wins": wins,
            "win_rate": round(wins / trades, 4) if trades else None,
            "pnl": round(pnl, 4),
            "live_days": len(live),
            "simulated_days": n - len(live),
            "calibration_days_required": CALIBRATION_DAYS,
            "vault_product": "blocked" if not vault_ready else "eligible",
            "reason": "need 60 live days + 80 trades, no simulated mix" if not vault_ready else "calibration met",
            "chain_ok": bool(chain["ok"]),
        }


_R: Optional[ReceiptBook] = None
_LOCK = threading.Lock()


def get_receipts() -> ReceiptBook:
    global _R
    if _R is None:
        with _LOCK:
            if _R is None:
                _R = ReceiptBook()
    return _R
