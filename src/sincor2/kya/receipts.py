"""Polyclaw performance receipts. Publish now. Vault later.

No external capital until calibration window is met.
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


class ReceiptBook:
    def __init__(self) -> None:
        self.store = JsonStore("polyclaw_receipts")
        raw = self.store.load() or {}
        self.lock = threading.Lock()
        self.days: List[Dict[str, Any]] = raw.get("days") or []

    def _persist(self) -> None:
        self.store.save({"days": self.days[-400:], "saved_at": _now()})

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
        row["attestation_hash"] = record_hash(row)
        with self.lock:
            self.days = [d for d in self.days if d.get("date") != row["date"]]
            self.days.append(row)
            self.days.sort(key=lambda d: d["date"])
            self._persist()
        return row

    def scorecard(self) -> Dict[str, Any]:
        with self.lock:
            days = list(self.days)
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
