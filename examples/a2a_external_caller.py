#!/usr/bin/env python3
"""
SINCOR External A2A Caller — production-ready reference client
=============================================================
Any external agent (Claude, Hermes, CrewAI, OpenAI tools, custom swarm)
can use this to discover → quote → submit → poll against a live or local
SINCOR instance.

Safety:
  --simulate   never posts real payment intents; prints the full flow
  Default base URL is local. Override with SINCOR_A2A_BASE or --base.

Usage:
  python examples/a2a_external_caller.py --simulate
  python examples/a2a_external_caller.py --base https://your-sincor.example --skill lead-generation
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


def _http(
    method: str,
    url: str,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    data = None
    headers = {"Accept": "application/json", "User-Agent": "sincor-external-a2a/1.0"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"HTTP {e.code} {url}: {err_body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"URL error {url}: {e}") from e


def discover(base: str) -> Dict[str, Any]:
    """A2A discovery: Agent Card (v1.0.1 preferred, legacy fallback)."""
    for path in ("/.well-known/agent-card.json", "/.well-known/agent.json", "/api/a2a/agents"):
        try:
            return _http("GET", base.rstrip("/") + path)
        except RuntimeError:
            continue
    raise RuntimeError("No Agent Card endpoint responded")


def quote(base: str, skill_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Request a quote. Expects treasury_fee_split / platform_fee fields when server is current."""
    body = {"skill": skill_id, "input": payload or {"query": "sample external task"}}
    return _http("POST", base.rstrip("/") + "/api/a2a/quote", body)


def submit_task(
    base: str,
    skill_id: str,
    payload: Dict[str, Any],
    payment_intent: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Submit via JSON-RPC message/send or legacy tasks/send."""
    # Prefer JSON-RPC 2.0
    rpc = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": json.dumps(payload)}],
                "metadata": {"skill": skill_id, "payment": payment_intent or {}},
            }
        },
    }
    try:
        return _http("POST", base.rstrip("/") + "/api/a2a", rpc)
    except RuntimeError:
        # Legacy fallback
        return _http(
            "POST",
            base.rstrip("/") + "/api/a2a/tasks/send",
            {"skill": skill_id, "input": payload, "payment": payment_intent or {}},
        )


def get_task(base: str, task_id: str) -> Dict[str, Any]:
    try:
        rpc = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tasks/get",
            "params": {"id": task_id},
        }
        return _http("POST", base.rstrip("/") + "/api/a2a", rpc)
    except RuntimeError:
        return _http("GET", base.rstrip("/") + f"/api/a2a/tasks/{task_id}")


def run_flow(
    base: str,
    skill: str,
    simulate: bool,
    poll_seconds: float = 2.0,
    max_polls: int = 15,
) -> int:
    print(f"[1] Discover  base={base}")
    card = discover(base)
    skills = card.get("skills") or card.get("agents") or []
    print(f"    skills visible: {len(skills) if isinstance(skills, list) else 'n/a'}")

    print(f"[2] Quote     skill={skill}")
    q = quote(base, skill)
    print("    quote keys:", sorted(q.keys())[:12])
    fee_split = q.get("treasury_fee_split") or q.get("fee_split") or {}
    if fee_split:
        print("    treasury_fee_split:", json.dumps(fee_split, default=str)[:200])
    else:
        print("    WARNING: no treasury_fee_split in quote — server may predate fee-split PR")

    if simulate:
        print("[3] Submit    SIMULATE — not posting payment intent")
        print("    Would submit skill=%s with payment commitment from quote" % skill)
        print("[4] Poll      skipped in simulate mode")
        print("OK — external A2A flow validated (simulate)")
        return 0

    print("[3] Submit")
    payment = {
        "token": q.get("token") or "AXM",
        "amount": q.get("axm_price_wei") or q.get("amount"),
        "treasury_fee": fee_split,
    }
    result = submit_task(base, skill, {"query": "external caller live task"}, payment)
    task_id = (
        result.get("result", {}).get("id")
        or result.get("id")
        or result.get("task_id")
    )
    if not task_id:
        print("    submit response:", json.dumps(result, default=str)[:400])
        raise RuntimeError("No task id returned")
    print(f"    task_id={task_id}")

    print("[4] Poll")
    for i in range(max_polls):
        t = get_task(base, str(task_id))
        state = (
            t.get("result", {}).get("status", {}).get("state")
            or t.get("status")
            or t.get("state")
            or "unknown"
        )
        print(f"    poll {i+1}: state={state}")
        if str(state).lower() in ("completed", "failed", "canceled", "rejected"):
            print("    final:", json.dumps(t, default=str)[:500])
            return 0 if str(state).lower() == "completed" else 1
        time.sleep(poll_seconds)
    print("TIMEOUT")
    return 2


def main() -> int:
    p = argparse.ArgumentParser(description="SINCOR external A2A reference client")
    p.add_argument("--base", default=os.getenv("SINCOR_A2A_BASE", "http://127.0.0.1:8080"))
    p.add_argument("--skill", default="lead-generation")
    p.add_argument("--simulate", action="store_true", help="Discover+quote only; no payment")
    args = p.parse_args()
    try:
        return run_flow(args.base, args.skill, args.simulate)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
