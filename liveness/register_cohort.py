#!/usr/bin/env python3
"""Register the liveness cohort + fund stake deposits. Idempotent.

For each agent in liveness/cohort.json:
  1. GET /v1/a2a/agents — skip registration if already present.
  2. POST /v1/a2a/register (disclosure baked into the description).
  3. GET /v1/a2a/stake/<agent_id> — skip deposit if available >= target.
  4. POST /v1/a2a/stake/deposit {agent_id, amount_axm} (self-service).

No admin key needed. Safe to re-run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from net import HttpError, get_json, post_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STAKE_TARGET_AXM = 50.0


def load_cohort():
    with open(os.path.join(HERE, "cohort.json"), encoding="utf-8") as fh:
        return json.load(fh)["agents"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="https://getsincor.com")
    ap.add_argument("--stake-axm", type=float, default=STAKE_TARGET_AXM)
    args = ap.parse_args()

    agents = load_cohort()
    try:
        live = {a.get("agent_id") for a in
                get_json(args.base_url, "/v1/a2a/agents").get("agents", [])}
    except Exception as e:
        print(f"FATAL: cannot reach {args.base_url}: {e}")
        return 1

    for a in agents:
        aid = a["agent_id"]
        if aid in live:
            print(f"  SKIP register {aid}: already registered")
        else:
            body = {
                "agent_id": aid,
                "name": a["name"],
                "description": a["description"],
                "version": a.get("version", "1.0.0"),
                "capability_tags": a["capability_tags"],
                "skills": a.get("skills", []),
                "wallet": a["wallet"],
                "chain_id": a.get("chain_id", 8453),
            }
            try:
                post_json(args.base_url, "/v1/a2a/register", body)
                print(f"  OK register {aid}")
            except HttpError as e:
                print(f"  FAIL register {aid}: {e}")
                continue
        # stake top-up (self-service; heartbeat not required for deposit)
        try:
            bal = get_json(args.base_url, f"/v1/a2a/stake/{aid}")
            # balance_of returns wei strings: available_wei = deposited-locked-bonded
            avail = int(bal.get("available_wei") or 0) / 1e18
        except HttpError as e:
            if e.status == 404:
                avail = 0.0
            else:
                print(f"  FAIL stake check {aid}: {e}")
                continue
        if avail >= args.stake_axm:
            print(f"  SKIP deposit {aid}: available {avail:g} AXM")
            continue
        try:
            post_json(args.base_url, "/v1/a2a/stake/deposit",
                      {"agent_id": aid,
                       "amount_axm": round(args.stake_axm - avail, 6)})
            print(f"  OK deposit {aid}: topped to {args.stake_axm:g} AXM")
        except HttpError as e:
            print(f"  FAIL deposit {aid}: {e}")
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
