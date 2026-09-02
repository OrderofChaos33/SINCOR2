"""A2A adoption telemetry for growth and monetization KPIs."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_LOCK = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_path() -> Path:
    try:
        from sincor2.data_paths import data_dir

        return data_dir() / "a2a_adoption_events.jsonl"
    except Exception:
        repo_root = Path(__file__).resolve().parents[2]
        return repo_root / "data" / "a2a_adoption_events.jsonl"


def _append_event(event: Dict[str, Any]) -> None:
    path = _event_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, separators=(",", ":")) + "\n"
    with _LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)


def _read_events(max_age_seconds: float | None = None) -> List[Dict[str, Any]]:
    path = _event_path()
    if not path.exists():
        return []
    cutoff = (time.time() - max_age_seconds) if max_age_seconds else None
    events: List[Dict[str, Any]] = []
    with _LOCK:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    ts = str(event.get("ts") or "")
                    if cutoff is not None and ts:
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if dt.timestamp() < cutoff:
                                continue
                        except ValueError:
                            pass
                    events.append(event)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
    return events


def record_agent_registered(*, agent_id: str, wallet: str, tags: List[str], probation: bool) -> None:
    _append_event(
        {
            "ts": _utc_now_iso(),
            "event_type": "agent_registered",
            "agent_id": (agent_id or "").strip(),
            "wallet": (wallet or "").strip().lower(),
            "tags": [str(tag).strip().lower() for tag in tags if tag],
            "probation": bool(probation),
        }
    )


def record_paid_settlement(
    *,
    task_id: str,
    caller_id: str,
    skill_id: str,
    tx_hash: str,
    axm_paid_wei: int,
    platform_fee_axm: float,
) -> None:
    axm_paid_axm = float(axm_paid_wei) / 10**18 if axm_paid_wei > 0 else 0.0
    _append_event(
        {
            "ts": _utc_now_iso(),
            "event_type": "paid_settlement",
            "task_id": (task_id or "").strip(),
            "caller_id": (caller_id or "").strip(),
            "skill_id": (skill_id or "").strip(),
            "tx_hash": (tx_hash or "").strip(),
            "axm_paid_wei": int(axm_paid_wei or 0),
            "axm_paid_axm": axm_paid_axm,
            "platform_fee_axm": float(platform_fee_axm or 0.0),
        }
    )


def weekly_adoption_kpi(window_days: int = 7) -> Dict[str, Any]:
    events = _read_events(max_age_seconds=max(1, int(window_days)) * 86400)
    settled = [evt for evt in events if evt.get("event_type") == "paid_settlement"]
    registered = [evt for evt in events if evt.get("event_type") == "agent_registered"]

    blocked_callers = {"", "anonymous", "sincor-agent-swarm"}
    active_callers = {
        str(evt.get("caller_id") or "").strip().lower()
        for evt in settled
        if str(evt.get("caller_id") or "").strip().lower() not in blocked_callers
    }

    volume_axm = sum(float(evt.get("axm_paid_axm") or 0.0) for evt in settled)
    fees_axm = sum(float(evt.get("platform_fee_axm") or 0.0) for evt in settled)

    try:
        from sincor2.treasury_inflow import ledger_summary_24h

        ledger = ledger_summary_24h()
        realized_usd_24h = float(ledger.get("usd_realized_24h") or 0.0)
        a2a_source_usd_24h = float((ledger.get("by_source") or {}).get("a2a_settlement") or 0.0)
    except Exception:
        realized_usd_24h = 0.0
        a2a_source_usd_24h = 0.0

    return {
        "window_days": int(window_days),
        "north_star_metric": "weekly_active_external_agents_settling_paid_axm_tasks",
        "weekly_active_external_agents": len(active_callers),
        "paid_a2a_tasks": len(settled),
        "paid_a2a_volume_axm": round(volume_axm, 8),
        "platform_fee_axm": round(fees_axm, 8),
        "registered_external_agents": len(registered),
        "treasury_realized_usd_24h": round(realized_usd_24h, 6),
        "treasury_a2a_settlement_usd_24h": round(a2a_source_usd_24h, 6),
    }


def launch_surface_contract() -> Dict[str, Any]:
    return {
        "contract_version": "a2a-v1.0.1-p0",
        "endpoints": {
            "discover": "/.well-known/agent-card.json",
            "register": "/v1/a2a/register",
            "stream": "/v1/a2a/stream",
            "quote": "/api/a2a/quote",
            "send": "/api/a2a",
            "settlement": "/api/metrics/treasury",
        },
        "tokens": {
            "settlement_token": "AXM",
            "utility_token": "SINC",
        },
    }
