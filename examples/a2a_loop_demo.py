#!/usr/bin/env python3
"""Run the minimal SINCOR2 A2A discovery -> task -> settlement loop."""

from __future__ import annotations

import json
import os
import urllib.request

BASE_URL = os.getenv("SINCOR2_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
SKILL_ID = os.getenv("SINCOR2_SKILL_ID", "lead-enrichment-outbound")


def fetch_json(url: str, payload: dict | None = None) -> dict:
    if payload is None:
        request = urllib.request.Request(url)
    else:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    card = fetch_json(f"{BASE_URL}/.well-known/agent-card.json")
    print("=== Agent Card ===")
    print(
        json.dumps(
            {"name": card["name"], "skills": [skill["id"] for skill in card["skills"][:10]]},
            indent=2,
        )
    )

    send_payload = {
        "jsonrpc": "2.0",
        "id": "demo-loop",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [
                    {
                        "kind": "text",
                        "text": json.dumps(
                            {
                                "task_type": "lead_enrichment",
                                "payload": {
                                    "company": "Acme Corp",
                                    "segment": "B2B SaaS",
                                },
                            }
                        ),
                    }
                ],
                "metadata": {
                    "skillId": SKILL_ID,
                    "callerId": "examples/a2a_loop_demo.py",
                    "txHash": "0xSIMULATEDTASK",
                    "axmPaidWei": "1000000000000000000",
                },
            }
        },
    }
    task_response = fetch_json(f"{BASE_URL}/api/a2a", send_payload)
    print("=== Task Response ===")
    print(json.dumps(task_response, indent=2))

    settlements = fetch_json(f"{BASE_URL}/api/marketplace/settlement/records")
    print("=== Settlement Snapshot ===")
    print(json.dumps(settlements, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
