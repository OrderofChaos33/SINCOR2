"""W-105 (NEW, VERIFIED): the x402 payment path skips OFAC sanctions screening.

The platform-checkout path (``platform_payments.verify_checkout`` ->
``_maybe_finalize``) screens every payer via ``screen_payer_sanctions``:
a sanctions HIT always blocks, and an unavailable oracle blocks in
strict mode (the default). The x402 micropayment path
(``POST /api/x402/verify`` -> ``x402_payments.verify_challenge`` ->
``verify_treasury_transfer``) never calls ``screen_payer_sanctions`` at
all — ``grep`` finds exactly one call site in the whole codebase
(platform_payments.py:1109), and it is not on the x402 path.

Consequence: a wallet on the OFAC SDN list can pay SINC/USDC into the
treasury through x402 and receive a live entitlement token. Receiving
funds from a sanctioned party is itself the sanctions touchpoint. The
P20 fail-closed posture ("stale snapshot => self-halt") is also applied
inconsistently: the checkout path halts while x402 keeps accepting.

Severity: Medium (compliance/legal exposure on a live real-money
intake path; no fund theft).

Status: VERIFIED-OPEN (deferred). Wiring the screen into
``verify_challenge`` is a small change, but with the snapshot stale
since 2026-10-11 the strict-mode screen would SELF-HALT x402 today —
disabling a live money path is a founder decision, not an overnight
unilateral change.

Demonstrated below (hermetic: tmp DB/data dir/ledger, mocked RPC):
  1. Oracle reports a sanctions HIT for the payer; x402 verify still
     fulfills and issues a live entitlement token.
  2. ``screen_payer_sanctions`` is never invoked anywhere on the x402
     verify path (call counter stays 0).
"""
from __future__ import annotations

import pytest
from flask import Flask

# Production import order: mvp_app first so _bind_mvp() binds X402_AVAILABLE.
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing

import sincor2.platform_payments as pp
import sincor2.treasury_inflow as inflow_mod
import sincor2.x402_payments as x402

SANCTIONED = "0x" + "de" * 20


def _tx(seed: str = "ab") -> str:
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
        # The sanctioned wallet really did pay the treasury on-chain.
        return {"ok": True, "payer_wallet": SANCTIONED}

    monkeypatch.setattr(x402, "verify_treasury_transfer", _ok)

    calls = {"n": 0}

    def _hit(payer_wallet):
        calls["n"] += 1
        return {"verdict": "hit", "detail": "sdn_list_match"}

    # Even if the platform screen ran, it would report a HIT here.
    monkeypatch.setattr(pp, "screen_payer_sanctions", _hit)

    app = Flask(__name__)
    app.register_blueprint(billing.bp)
    app.config.update(TESTING=True)
    return app.test_client(), calls


class TestX402SkipsSanctionsScreening:
    def test_sanctioned_payer_fulfills_x402(self, hermetic):
        """A sanctions-hit payer still gets a live entitlement via x402."""
        client, calls = hermetic
        ch = x402.create_challenge("agent_task", payer_wallet=SANCTIONED)
        r = client.post(
            "/api/x402/verify",
            json={"challenge_id": ch["challenge_id"],
                  "tx_hash": _tx(),
                  "payer_wallet": SANCTIONED},
        )
        body = r.get_json()
        # The money path completed for a sanctioned payer: fulfilled +
        # a live, spendable entitlement token.
        assert r.status_code == 200, body
        assert body["status"] == "fulfilled", body
        assert body.get("access_token"), body
        # And the sanctions screen was never consulted on this path.
        assert calls["n"] == 0, (
            "screen_payer_sanctions must not run on the x402 path "
            "(gap demonstration)")
