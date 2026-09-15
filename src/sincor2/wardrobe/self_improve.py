"""TOA-facing helper: open one internal improve job on contract-net."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sincor2.contract_net import ContractNetEvaluator, MemoryHashStore, calculate_bid_score

DEFAULT_BIDS = {
    "E-toa-44": {"bid_amount": 1.0, "estimated_seconds": 120, "reputation": 0.92},
    "E-strategist-45": {"bid_amount": 0.8, "estimated_seconds": 180, "reputation": 0.80},
    "E-critic-46": {"bid_amount": 0.4, "estimated_seconds": 90, "reputation": 0.88},
}


def open_improve_job(
    title: str,
    *,
    job_class: str = "improve",
    eligible: list[str] | None = None,
    store: MemoryHashStore | None = None,
    ledger_dir: Path | None = None,
) -> dict[str, Any]:
    store = store or MemoryHashStore()
    ev = ContractNetEvaluator(store)
    task_id = f"IMP-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    store.hset(f"task:{task_id}:meta", mapping={"status": "open", "title": title, "job_class": job_class})
    pool = eligible or list(DEFAULT_BIDS)
    scored = []
    for agent_id in pool:
        spec = DEFAULT_BIDS.get(agent_id) or {
            "bid_amount": 1.0,
            "estimated_seconds": 180,
            "reputation": 0.5,
        }
        store.hset(f"task:{task_id}:bids", mapping={agent_id: json.dumps({
            "bid_amount": spec["bid_amount"],
            "estimated_seconds": spec["estimated_seconds"],
        })})
        store.hset(f"agent:{agent_id}:stats", mapping={"reputation": spec["reputation"]})
        scored.append((
            agent_id,
            calculate_bid_score(spec["bid_amount"], spec["estimated_seconds"], spec["reputation"]),
        ))
    winner = ev.evaluate_task_bids(task_id)
    meta = store.hgetall(f"task:{task_id}:meta")
    row = {
        "task_id": task_id,
        "title": title,
        "job_class": job_class,
        "assigned": meta.get("assigned_agent"),
        "status": meta.get("status"),
        "winning_bid_axm": meta.get("winning_bid_axm"),
        "ranking": sorted(scored, key=lambda x: -x[1]),
        "winner_payload": winner,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if ledger_dir:
        ledger_dir = Path(ledger_dir)
        ledger_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with (ledger_dir / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    return row
