#!/usr/bin/env python3
"""Sponsored-stake first-task funnel, end to end, for real.

Proves the exact path an outside agent with ZERO AXM takes:

    register (zero balance) -> admin fronts sponsored stake ->
    stake usable for commit lock -> heartbeat -> sealed commit ->
    reveal -> close -> proof -> settled -> platform recoups the
    front from the agent's earnings.

Usage:
    source ~/.venvs/sincor2/bin/activate
    PYTHONPATH=src:. python scripts/a2a_sponsored_funnel.py

Every step hits the real endpoints over real HTTP against a local test
app. The mechanism is default-OFF; this script opts in via
SINCOR_SPONSORED_STAKE_ENABLED=1 (operator decision required in
production — nothing fronts funds until it is set).
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
# Sponsored-stake operator opt-in (default OFF; this is the mechanism
# the operator enables for the genesis cohort).
os.environ["SINCOR_SPONSORED_STAKE_ENABLED"] = "1"
# Compressed sealed-bid windows for the demo (production: 5 min each).
os.environ["SINCOR_SEALED_COMMIT_WINDOW_MS"] = "20000"
os.environ["SINCOR_SEALED_REVEAL_WINDOW_MS"] = "20000"

_TMP = tempfile.mkdtemp(prefix="sincor-sponsored-funnel-")
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

PORT = 5058
BASE = f"http://127.0.0.1:{PORT}"
ADMIN_HEADERS = {"X-Admin-Key": os.environ["ADMIN_PASSWORD"]}

# Test-only ephemeral identities: fail-closed EIP-191 proofs on both the
# test app and production. Keys never leave this process.
from eth_account import Account  # noqa: E402

_poster_acct = Account.create()
_newcomer_acct = Account.create()

POSTER_ID = f"funnel-poster-{int(time.time()) % 100000}"
AGENT_ID = f"funnel-newcomer-{int(time.time()) % 100000}"
TAGS = ["lead-enrichment"]
FRONT_AXM = 1.0
BOUNTY_AXM = 1.5
BID_AXM = 1.2  # > fronted amount, so settlement fully recoups the front


def note(text: str):
    print(f"  {text}", flush=True)


def main() -> int:
    t0 = time.monotonic()
    print("SINCOR sponsored-stake funnel (zero AXM -> settled, real HTTP)")
    assert COMMIT_WINDOW_MS == 20000 and REVEAL_WINDOW_MS == 20000, \
        "window override did not take effect"

    # Isolated ledgers: the demo never touches the operator's ledgers.
    reset_stake_ledger(path=os.path.join(_TMP, "stake_ledger.json"))
    from sincor2.sponsored_stake import reset_sponsored_ledger
    reset_sponsored_ledger(path=os.path.join(_TMP, "sponsored_stake.json"))

    print("\n[boot test app]")
    app = Flask(__name__)
    register_inbound(app)
    import logging as _logging
    _logging.getLogger("werkzeug").setLevel(_logging.ERROR)
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", PORT, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    note(f"listening on {BASE}")

    sdk = SincorAgentSDK(RequestsTransport(BASE))

    print("\n[1. register poster + newcomer]")
    sdk.register(POSTER_ID, "Funnel Poster", TAGS,
                 wallet=_poster_acct.address, signer=_poster_acct)
    sdk.register(AGENT_ID, "Funnel Newcomer", TAGS,
                 wallet=_newcomer_acct.address, signer=_newcomer_acct)
    bal = sdk.stake_balance(AGENT_ID)
    note(f"newcomer {AGENT_ID} registered with "
         f"{int(bal['available_wei']) / 1e18} AXM (zero, as expected)")

    print("\n[2. admin fronts sponsored stake]")
    r = RequestsTransport(BASE)
    front = r.post("/v1/a2a/admin/sponsored-stake",
                   {"agent_id": AGENT_ID, "amount_axm": FRONT_AXM},
                   headers=ADMIN_HEADERS)
    note(f"fronted {FRONT_AXM} AXM (status={front['status']}, "
         f"record fronted_wei={front['fronted_wei']})")
    bal = sdk.stake_balance(AGENT_ID)
    note(f"newcomer stake available_wei={bal['available_wei']} "
         f"(= {FRONT_AXM} AXM, immediately usable)")

    print("\n[3. duplicate front refused (one active sponsorship per agent)]")
    try:
        r.post("/v1/a2a/admin/sponsored-stake",
               {"agent_id": AGENT_ID, "amount_axm": FRONT_AXM},
               headers=ADMIN_HEADERS)
        print("FAIL: duplicate front accepted")
        return 1
    except Exception as err:
        note(f"duplicate front rejected as expected: {err}")

    print("\n[4. poster lists a sealed task]")
    sdk.heartbeat(POSTER_ID, signer=_poster_acct)
    task = sdk.post_task("lead-enrichment", TAGS, bounty_axm=BOUNTY_AXM,
                         sealed=True)
    task_id = task["task_id"]
    note(f"{task_id}: bounty {BOUNTY_AXM} AXM")

    print("\n[5. newcomer commits with zero self-funding]")
    sdk.heartbeat(AGENT_ID, signer=_newcomer_acct)
    bid = sdk.sealed_commit(task_id, AGENT_ID, bid_axm=BID_AXM)
    bal = sdk.stake_balance(AGENT_ID)
    note(f"committed {bid.commitment[:18]}... "
         f"(locked_wei={bal['locked_wei']} = 50% of bounty, "
         f"covered by sponsored front)")

    print("\n[6. reveal -> close]")
    sdk.wait_for_reveal_window(task_id, timeout_s=120, poll_s=2)
    sdk.heartbeat(AGENT_ID, signer=_newcomer_acct)
    revealed = sdk.sealed_reveal(bid, estimated_seconds=600)
    note(f"revealed {revealed['bid_axm']} AXM")
    sdk.wait_for_reveal_deadline(task_id, timeout_s=120, poll_s=2)
    closed = sdk.close_auction(task_id)
    note(f"winner={closed['assigned_to']} at {closed['winning_bid_axm']} AXM")

    print("\n[7. proof -> settled, front recoups from earnings]")
    # Content-bound proof (item 77): a bound deliverable is
    # adjudicator-retrievable; deliverable_uri is required.
    proof = sdk.submit_proof(task_id, AGENT_ID, "0xfeedface1234567890",
                             deliverable={"report": "funnel deliverable"},
                             deliverable_uri="https://funnel.example/r/1")
    note(f"proof {proof['proof_id']}: status={proof['status']} "
         f"(content_bound={proof.get('content_bound')})")
    # Admin status route requires the X-Admin-Key header; the SDK GET
    # transport carries none, so use requests directly here.
    import requests as _req
    status = _req.get(
        f"{BASE}/v1/a2a/admin/sponsored-stake/{AGENT_ID}",
        headers=ADMIN_HEADERS).json()
    note(f"sponsorship status={status['status']} "
         f"(recouped_wei={status['recouped_wei']} of "
         f"fronted_wei={status['fronted_wei']})")
    assert status["status"] == "settled", \
        f"expected sponsorship settled, got {status['status']}"
    bal = sdk.stake_balance(AGENT_ID)
    note(f"final stake locked_wei={bal['locked_wei']} (released)")

    total = time.monotonic() - t0
    print(f"\nPASS: zero-AXM agent -> sponsored -> bid -> settled -> "
          f"front recouped, in {total:.1f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
