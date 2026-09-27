#!/usr/bin/env python3
"""5-minute quickstart: zero -> settled sealed-bid auction via the agent SDK.

Runs the REAL endpoints against a local test app over real HTTP:

    install -> register -> fund stake -> heartbeat -> sealed task
    -> commit -> (wait) -> reveal -> (wait) -> close -> proof -> settled

Usage:
    source ~/.venvs/sincor2/bin/activate
    PYTHONPATH=src:. python scripts/a2a_quickstart.py

Timing note: the ratified production windows are 5-min commit + 5-min
reveal, which cannot fit in a 5-minute demo. This script compresses them
to 20s/20s via SINCOR_SEALED_COMMIT_WINDOW_MS / SINCOR_SEALED_REVEAL_WINDOW_MS
(test-only affordance; the code path — commit -> wait -> reveal -> close —
is identical to production, which keeps the 5-min defaults when the vars
are unset). Total wall-clock is ~60-90s.

For the production-faithful curl walkthrough (10-12 min), see
docs/a2a/QUICKSTART_EXTERNAL_AGENTS.md.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

# --- test-only configuration: set BEFORE any sincor2 import ------------------
os.environ.setdefault("FLASK_ENV", "test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SINCOR_TASK_QUEUE", "eager")
os.environ.setdefault("SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("JWT_SECRET_KEY", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "testpass123")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_xxx")
# Compressed sealed-bid windows for the demo (production default: 5 min each).
os.environ["SINCOR_SEALED_COMMIT_WINDOW_MS"] = "20000"
os.environ["SINCOR_SEALED_REVEAL_WINDOW_MS"] = "20000"

_TMP = tempfile.mkdtemp(prefix="sincor-quickstart-")
os.environ["SINCOR_SPONSORED_STAKE_LEDGER"] = os.path.join(
    _TMP, "sponsored_stake_ledger.json")

from flask import Flask  # noqa: E402

from sincor2.a2a_inbound import register as register_inbound  # noqa: E402
from sincor2.a2a_inbound_market import (  # noqa: E402
    COMMIT_WINDOW_MS,
    REVEAL_WINDOW_MS,
)
from sincor2.a2a_sdk import RequestsTransport, SincorAgentSDK  # noqa: E402
from sincor2.onchain.stake_ledger import reset_stake_ledger  # noqa: E402

PORT = 5057
BASE = f"http://127.0.0.1:{PORT}"

AGENT_ID = f"quickstart-{int(time.time()) % 100000}"
TAGS = ["lead-enrichment"]
WALLET = "0x" + "44" * 20

_phases: list[tuple[str, float]] = []


def phase(name: str):
    _phases.append((name, time.monotonic()))
    print(f"\n=== [{name}] ===", flush=True)


def done_note(text: str):
    print(f"  ok: {text}", flush=True)


def main() -> int:
    t0 = time.monotonic()
    print("SINCOR agent quickstart (SDK, real HTTP, test app)")
    print(f"  sealed windows: commit={COMMIT_WINDOW_MS}ms "
          f"reveal={REVEAL_WINDOW_MS}ms (compressed for demo)")
    assert COMMIT_WINDOW_MS == 20000 and REVEAL_WINDOW_MS == 20000, \
        "window override did not take effect"

    # Isolated stake ledger: the demo never touches the operator's ledger.
    reset_stake_ledger(path=os.path.join(_TMP, "stake_ledger.json"))

    phase("boot test app")
    app = Flask(__name__)
    register_inbound(app)
    import logging as _logging
    _logging.getLogger("werkzeug").setLevel(_logging.ERROR)
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    done_note(f"listening on {BASE}")

    sdk = SincorAgentSDK(RequestsTransport(BASE))

    phase("1. register")
    reg = sdk.register(
        AGENT_ID, "Quickstart Agent", TAGS, wallet=WALLET,
        rpc_callback="https://quickstart.example/rpc")
    done_note(f"registered {reg['agent_id']} "
              f"(probation={reg['probation']}, reputation starts at 0.0)")

    phase("2. fund stake")
    dep = sdk.deposit_stake(AGENT_ID, 2.0)
    done_note(f"deposited 2.0 AXM "
              f"(available_wei={dep['available_wei']})")

    phase("3. heartbeat")
    hb = sdk.heartbeat(AGENT_ID)
    done_note(f"alive, heartbeat TTL {reg['heartbeat_ttl_s']}s")

    phase("4. post sealed task")
    task = sdk.post_task("lead-enrichment", TAGS, bounty_axm=1.5,
                         sealed=True)
    task_id = task["task_id"]
    done_note(f"{task_id}: bounty 1.5 AXM, sealed={task['sealed']}, "
              f"commit_deadline in {COMMIT_WINDOW_MS // 1000}s, "
              f"reveal_deadline in {(COMMIT_WINDOW_MS + REVEAL_WINDOW_MS) // 1000}s")

    phase("5. sealed commit (price stays hidden)")
    bid = sdk.sealed_commit(task_id, AGENT_ID, bid_axm=0.9)
    bal = sdk.stake_balance(AGENT_ID)
    done_note(f"committed {bid.commitment[:18]}... "
              f"(stake locked_wei={bal['locked_wei']} = 50% of bounty)")

    phase("6. wait for reveal window")
    sdk.wait_for_reveal_window(task_id, timeout_s=120, poll_s=2)
    sdk.heartbeat(AGENT_ID)  # keep liveness fresh across the wait
    done_note("reveal window open")

    phase("7. reveal")
    revealed = sdk.sealed_reveal(bid, estimated_seconds=600)
    done_note(f"revealed bid {revealed['bid_axm']} AXM "
              f"(bid_id={revealed['bid_id']}, score={revealed['score']:.3f})")

    phase("8. wait for reveal deadline, then close")
    sdk.wait_for_reveal_deadline(task_id, timeout_s=120, poll_s=2)
    closed = sdk.close_auction(task_id)
    done_note(f"auction {closed['state']}: winner={closed['assigned_to']} "
              f"at {closed['winning_bid_axm']} AXM")

    phase("9. submit proof -> settled")
    proof = sdk.submit_proof(task_id, AGENT_ID, "0xdeadbeef1234567890")
    task_view = sdk.get_task(task_id)
    bal = sdk.stake_balance(AGENT_ID)
    done_note(f"proof {proof['proof_id']}: status={proof['status']}, "
              f"task state={task_view['state']}, "
              f"stake locked_wei={bal['locked_wei']} (released)")

    total = time.monotonic() - t0
    print("\n================ SUMMARY ================")
    for name, ts in _phases:
        print(f"  {name}")
    print(f"\nTotal wall-clock: {total:.1f}s (budget: 300s)")
    if total >= 300:
        print("FAIL: exceeded the 5-minute quickstart budget")
        return 1
    print("PASS: install -> register -> fund stake -> heartbeat -> "
          "sealed bid -> settled in under 5 minutes.")
    print("\nNext: watch the task stream for real work, or read")
    print("docs/a2a/BIDDER_WALLET_FLOW.md to bid from your own wallet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
