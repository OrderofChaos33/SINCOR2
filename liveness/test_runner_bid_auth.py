#!/usr/bin/env python3
"""Hermetic tests for the runner's W-77 EIP-191 bid signing.

Builds commit/reveal bodies with runner.build_bid_auth using a throwaway
test key (never a cohort key), then verifies them with the SERVER-side
verification logic imported from a fresh origin/main worktree
(sincor2.a2a_integration._server_bound_bidder_wallet +
_auth_bid_message). Run:

    SINCOR_MAIN_SRC=/tmp/sincor-main-wt/src \
    ~/.venvs/sincor2/bin/python -m pytest liveness/test_runner_bid_auth.py -q

(run from ~/workspace/sincor2-liveness-wt; SINCOR_MAIN_SRC must point at
a checkout of origin/main.)
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_MAIN_SRC = os.environ.get("SINCOR_MAIN_SRC", "/tmp/sincor-main-wt/src")
if os.path.isdir(_MAIN_SRC):
    sys.path.insert(0, _MAIN_SRC)
    sys.path.insert(0, os.path.dirname(_MAIN_SRC))

import pytest  # noqa: E402
from eth_account import Account  # noqa: E402

import runner  # noqa: E402

server = pytest.importorskip(
    "sincor2.a2a_integration",
    reason="origin/main worktree not on path (set SINCOR_MAIN_SRC)")

AGENT = "sincor-liveness-test"
TASK = "tsk_bidauthtest1"
COMMITMENT = "0x" + "ab" * 32


@pytest.fixture()
def wallet():
    acct = Account.create()
    return acct, {AGENT: acct.key.hex()}


def _verify(body, *, purpose, payload, task=TASK, agent=AGENT):
    return server._server_bound_bidder_wallet(
        body, purpose=purpose, task_id=task, agent_id=agent, payload=payload)


def test_message_format_matches_server(wallet):
    acct, _ = wallet
    mine = runner.auth_bid_message("commit", TASK, AGENT, COMMITMENT, 123, "n1")
    theirs = server._auth_bid_message("commit", TASK, AGENT, COMMITMENT, 123, "n1")
    assert mine == theirs
    assert mine.startswith("SINCOR-A2A-MARKET bid-commit\n")


def test_commit_signature_recovers_to_agent_wallet(wallet):
    acct, keys = wallet
    auth = runner.build_bid_auth(keys, AGENT, purpose="commit",
                                 task_id=TASK, payload=COMMITMENT)
    assert auth and auth["authSignature"].startswith("0x")
    body = {"task_id": TASK, "agent_id": AGENT, "commitment": COMMITMENT, **auth}
    assert _verify(body, purpose="commit", payload=COMMITMENT) == acct.address.lower()


def test_reveal_signature_recovers_to_agent_wallet(wallet):
    acct, keys = wallet
    bid_axm, salt = 1.275, "cd" * 32
    payload = f"{float(bid_axm)}:{salt}"  # exactly the server route's format
    auth = runner.build_bid_auth(keys, AGENT, purpose="reveal",
                                 task_id=TASK, payload=payload)
    body = {"task_id": TASK, "agent_id": AGENT, "bid_axm": bid_axm,
            "nonce": salt, **auth}
    assert _verify(body, purpose="reveal", payload=payload) == acct.address.lower()


def test_tampered_payload_is_rejected(wallet):
    # _server_bound_bidder_wallet returns ANY well-formed recovery; the
    # rejection happens in _require_bid_auth, which compares the recovered
    # wallet against the agent's REGISTERED wallet. So a signature over a
    # tampered payload must recover to some OTHER wallet, never the agent's.
    acct, keys = wallet
    auth = runner.build_bid_auth(keys, AGENT, purpose="commit",
                                 task_id=TASK, payload=COMMITMENT)
    body = {"task_id": TASK, "agent_id": AGENT, "commitment": COMMITMENT, **auth}
    other = "0x" + "ef" * 32
    assert _verify(body, purpose="commit", payload=other) != acct.address.lower()
    # a commit signature cannot be replayed as a reveal
    assert _verify(body, purpose="reveal", payload=COMMITMENT) != acct.address.lower()


def test_signature_is_single_use(wallet):
    acct, keys = wallet
    auth = runner.build_bid_auth(keys, AGENT, purpose="commit",
                                 task_id=TASK, payload=COMMITMENT)
    body = {"task_id": TASK, "agent_id": AGENT, "commitment": COMMITMENT, **auth}
    assert _verify(body, purpose="commit", payload=COMMITMENT) == acct.address.lower()
    assert _verify(body, purpose="commit", payload=COMMITMENT) is None  # replay


def test_stale_timestamp_is_rejected(wallet):
    _, keys = wallet
    stale = int(time.time()) - 3600
    auth = runner.build_bid_auth(keys, AGENT, purpose="commit", task_id=TASK,
                                 payload=COMMITMENT, timestamp=stale)
    body = {"task_id": TASK, "agent_id": AGENT, "commitment": COMMITMENT, **auth}
    assert _verify(body, purpose="commit", payload=COMMITMENT) is None


def test_no_key_returns_none():
    assert runner.build_bid_auth({}, AGENT, purpose="commit",
                                 task_id=TASK, payload=COMMITMENT) is None
    assert runner.build_bid_auth(None, AGENT, purpose="commit",
                                 task_id=TASK, payload=COMMITMENT) is None


def test_load_cohort_keys_shape(tmp_path):
    import json
    p = tmp_path / "keys.json"
    fake = "0x" + "11" * 32
    p.write_text(json.dumps({"a1": {"key": fake}, "a2": fake}))
    loaded = runner.load_cohort_keys(str(p))
    assert loaded == {"a1": fake, "a2": fake}
    assert runner.load_cohort_keys(str(tmp_path / "missing.json")) == {}
