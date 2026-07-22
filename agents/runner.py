#!/usr/bin/env python3
"""SINCOR agent-ops runner — 24/7 task dispatch + treasury scoreboard.

Zero-dependency (stdlib only). Designed for cron:
    */15 * * * *  cd /path/to/SINCOR2 && python3 agents/runner.py --once
    0 * * * *     cd /path/to/SINCOR2 && python3 agents/runner.py --metrics

What it does:
  1. Loads agents/departments.json + agents/tasks/*.json.
  2. Runs due tasks (per interval_minutes), writing task envelopes to agents/outbox/.
  3. If SINCOR_LLM_ENDPOINT is set (OpenAI-compatible /v1/chat/completions),
     dispatches the envelope to that worker and stores the result in agents/results/.
     If unset, envelopes sit in outbox/ for external workers (Hermes/Claude/etc.)
     to consume — the outbox IS the queue.
  4. Appends everything to agents/ledger/YYYY-MM-DD.jsonl (the audit trail).
  5. --metrics snapshots treasury balances on Base (SINC, AXM, USDC) via JSON-RPC.

Everything is measured by treasury inflow. The ledger is the single record.
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TASKS_DIR = ROOT / "tasks"
OUTBOX = ROOT / "outbox"
RESULTS = ROOT / "results"
LEDGER = ROOT / "ledger"
STATE = ROOT / "state"

TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
TOKENS = {
    "SINC": ("0x9C8cd8d3961F445D653713dE65C6578bE11668e7", 8),
    "AXM": ("0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822", 18),
    "USDC": ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
}
BALANCE_OF = "0x70a08231"  # balanceOf(address)

for d in (OUTBOX, RESULTS, LEDGER, STATE):
    d.mkdir(exist_ok=True)


def now() -> float:
    return time.time()


def ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(p: Path):
    return json.loads(p.read_text())


def ledger_write(entry: dict) -> None:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(LEDGER / f"{day}.jsonl", "a") as f:
        f.write(json.dumps({"ts": ts(), **entry}) + "\n")


def state_load() -> dict:
    p = STATE / "state.json"
    return load_json(p) if p.exists() else {"last_run": {}}


def state_save(s: dict) -> None:
    (STATE / "state.json").write_text(json.dumps(s, indent=2))


# ---------------------------------------------------------------- treasury metrics

def rpc_call(method: str, params: list):
    url = os.environ.get("BASE_RPC_URL", "https://base-rpc.publicnode.com")
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def balance_of(token: str, holder: str) -> int:
    data = BALANCE_OF + holder.lower().replace("0x", "").rjust(64, "0")
    res = rpc_call("eth_call", [{"to": token, "data": data}, "latest"])
    return int(res.get("result", "0x0"), 16)


def treasury_snapshot() -> dict:
    snap = {"ts": ts(), "treasury": TREASURY}
    for sym, (addr, dec) in TOKENS.items():
        try:
            raw = balance_of(addr, TREASURY)
            snap[sym] = round(raw / 10**dec, 4)
        except Exception as e:  # metrics must never kill the runner
            snap[sym] = None
            snap[f"{sym}_error"] = str(e)
    return snap


# ---------------------------------------------------------------- dispatch

def llm_dispatch(envelope: dict) -> dict | None:
    """POST to an OpenAI-compatible worker if configured. None when unconfigured."""
    endpoint = os.environ.get("SINCOR_LLM_ENDPOINT")
    if not endpoint:
        return None
    body = {
        "model": os.environ.get("SINCOR_LLM_MODEL", "default"),
        "messages": [
            {"role": "system", "content": envelope["system"]},
            {"role": "user", "content": envelope["prompt"]},
        ],
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('SINCOR_LLM_KEY', '')}",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def run_task(dept_cfg: dict, dept_name: str, task: dict, force: bool, state: dict) -> None:
    last = state["last_run"].get(task["id"], 0)
    due = force or (now() - last >= task["interval_minutes"] * 60)
    if not due:
        return

    envelope = {
        "id": task["id"],
        "ts": ts(),
        "department": dept_name,
        "title": task["title"],
        "system": (
            f"You are the SINCOR {dept_name} department. Mission: {dept_cfg['mission']} "
            f"Prime directive: {dept_cfg.get('prime', 'Every task is measured by treasury inflow.')}"
        ),
        "prompt": task["prompt"],
        "kpis": task["kpis"],
        "treasury": treasury_snapshot() if "metrics" in task["id"] or dept_name == "toa" else None,
    }
    (OUTBOX / f"{task['id']}-{int(now())}.json").write_text(json.dumps(envelope, indent=2))

    status = "queued"
    try:
        resp = llm_dispatch(envelope)
        if resp is not None:
            (RESULTS / f"{task['id']}-{int(now())}.json").write_text(json.dumps(resp, indent=2))
            status = "dispatched"
    except Exception as e:
        status = f"dispatch_error: {e}"

    ledger_write({"type": "task", "id": task["id"], "dept": dept_name, "status": status})
    state["last_run"][task["id"]] = now()


def main() -> None:
    args = set(sys.argv[1:])
    force_task = None
    if "--task" in sys.argv:
        force_task = sys.argv[sys.argv.index("--task") + 1]

    if "--metrics" in args:
        snap = treasury_snapshot()
        ledger_write({"type": "metrics", **snap})
        print(json.dumps(snap, indent=2))
        return

    departments = load_json(ROOT / "departments.json")
    state = state_load()

    task_files = sorted(TASKS_DIR.glob("*.json"))
    for tf in task_files:
        cfg = load_json(tf)
        dept = cfg["department"]
        dept_cfg = departments["departments"].get(dept, {"mission": dept})
        for task in cfg["tasks"]:
            if force_task and task["id"] != force_task:
                continue
            run_task(dept_cfg, dept, task, force=bool(force_task), state=state)

    state_save(state)

    if "--loop" in args:
        while True:
            time.sleep(60)
            state = state_load()
            for tf in task_files:
                cfg = load_json(tf)
                dept = cfg["department"]
                dept_cfg = departments["departments"].get(dept, {"mission": dept})
                for task in cfg["tasks"]:
                    run_task(dept_cfg, dept, task, force=False, state=state)
            state_save(state)


if __name__ == "__main__":
    main()
