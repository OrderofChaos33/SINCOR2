#!/usr/bin/env python3
"""
SINCOR External A2A Caller — production-ready minimal client
===========================================================
Discover → Quote → (Pay) → Submit → Poll.

This is the reference implementation external agents should use or adapt.
Works against https://getsincor.com or any local SINCOR2 instance.

Usage:
  python examples/a2a_external_caller.py --skill lead-enrichment --input "Enrich Acme Corp, B2B SaaS"
  python examples/a2a_external_caller.py --skill market-intelligence --input "Competitor landscape for Base DeFi" --simulate

Environment:
  SINCOR_URL          (default https://getsincor.com)
  A2A_SIMULATE=1      allows 0xSIMULATED* payment hashes for testing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, Optional

import urllib.request
import urllib.error

PLATFORM = os.getenv("SINCOR_URL", "https://getsincor.com").rstrip("/")
SIMULATE = os.getenv("A2A_SIMULATE", "0") == "1" or "--simulate" in sys.argv


def _post(path: str, body: Dict[str, Any], timeout: int = 60) -> Dict[str, Any]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{PLATFORM}{path}",
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _get(path: str, timeout: int = 30) -> Dict[str, Any]:
    req = urllib.request.Request(
        f"{PLATFORM}{path}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def discover() -> Dict[str, Any]:
    print(f"[1] Discovering Agent Card at {PLATFORM}/.well-known/agent-card.json …")
    card = _get("/.well-known/agent-card.json")
    skills = card.get("skills", [])
    print(f"    Platform: {card.get('name')} v{card.get('version')}")
    print(f"    Skills advertised: {len(skills)}")
    return card


def quote(skill_id: str) -> Dict[str, Any]:
    print(f"[2] Quoting skill '{skill_id}' …")
    q = _post("/api/a2a/quote", {"skill_id": skill_id})
    if "error" in q:
        raise RuntimeError(q["error"])
    print(f"    Primary token : {q.get('primary_token', 'SINC')}")
    print(f"    SINC amount   : {q.get('sinc_amount')}")
    print(f"    AXM (legacy)  : {q.get('axm_price_display')}")
    print(f"    Pay to        : {q.get('pay_to')}")
    print(f"    Chain ID      : {q.get('chain_id')}")
    return q


def submit(skill_id: str, input_text: str, tx_hash: Optional[str] = None) -> Dict[str, Any]:
    print(f"[3] Submitting task …")
    body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"text": input_text}],
                "metadata": {
                    "skillId": skill_id,
                    "callerId": "external-demo-agent",
                    "txHash": tx_hash,
                },
            }
        },
    }
    if tx_hash:
        body["params"]["txHash"] = tx_hash
        body["params"]["axmPaidWei"] = "1000000000000000000"  # 1 AXM placeholder

    result = _post("/api/a2a", body)
    if "error" in result:
        raise RuntimeError(result["error"])
    task = result.get("result", {})
    print(f"    Task ID : {task.get('id')}")
    print(f"    State   : {task.get('status', {}).get('state')}")
    return task


def poll(task_id: str, max_wait: int = 120) -> Dict[str, Any]:
    print(f"[4] Polling task {task_id} …")
    start = time.time()
    while time.time() - start < max_wait:
        body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tasks/get",
            "params": {"id": task_id},
        }
        result = _post("/api/a2a", body)
        task = result.get("result", {})
        state = task.get("status", {}).get("state")
        print(f"    State: {state}")
        if state in ("completed", "failed", "canceled", "rejected"):
            return task
        time.sleep(3)
    raise TimeoutError(f"Task {task_id} did not finish within {max_wait}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="SINCOR External A2A Caller")
    parser.add_argument("--skill", default="lead-enrichment")
    parser.add_argument("--input", default="Enrich and score Acme Corp (B2B SaaS, 50 employees)")
    parser.add_argument("--simulate", action="store_true")
    args = parser.parse_args()

    try:
        card = discover()
        q = quote(args.skill)

        tx_hash = None
        if args.simulate or SIMULATE:
            tx_hash = "0xSIMULATED" + uuid.uuid4().hex[:56]
            print(f"[PAY] Using simulated tx: {tx_hash}")
        else:
            print("[PAY] In production, send the exact SINC/AXM amount to the pay_to address")
            print("      then pass the real tx hash. Continuing with no payment for demo…")

        task = submit(args.skill, args.input, tx_hash=tx_hash)
        final = poll(task["id"])

        print("\n=== FINAL RESULT ===")
        print(json.dumps(final, indent=2)[:2000])
        if final.get("artifacts"):
            for art in final["artifacts"]:
                for part in art.get("parts", []):
                    if "text" in part:
                        print("\n--- Artifact text ---")
                        print(part["text"][:1500])
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
