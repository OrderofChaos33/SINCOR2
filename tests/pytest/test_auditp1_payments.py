"""P1 audit remediation — Batch B (Payments).

Findings covered
---------------
6. ``0xSIMULATED`` bypass in ``sincor2.payment_verifier``: the production
   verifier returned success for any ``0xSIMULATED...`` hash. Fixed by
   removing every simulated-success shortcut; simulated hashes are
   fail-closed by default and accepted only with an explicit per-call
   ``allow_simulated=True`` (test-only). No environment variable or global
   toggle can enable it.
7. x402 entitlement reuse in ``sincor2.x402_payments.access_granted``:
   tokens were checked without expiry or consumption. Fixed: every check
   enforces ``token_expires_at`` and single-use consumption
   (``token_consumed_at``), persisted in the module's SQLite store.
8. tx-hash reuse across x402 challenges in ``verify_challenge``: one
   on-chain payment could fulfill many challenges. Fixed: UNIQUE(tx_hash)
   index + atomic claim (BEGIN IMMEDIATE; same-transaction pre-check +
   fulfill UPDATE; fulfill only if the claim won).
"""
from __future__ import annotations

import inspect
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sincor2.x402_payments as x402
from sincor2 import payment_verifier as pv
from sincor2.payment_verifier import PaymentVerifier


class _FakeKVStore:
    """In-memory stand-in for the persistent KV store.

    The verifier caches successful verifications in a process dict AND in
    the on-disk persistent store. Tests must never touch the real on-disk
    store: a cached entry would leak across test files that reuse the same
    tx hash (this actually broke test_payment_amount_reconciliation.py
    during development of these tests).
    """

    def __init__(self):
        self.kv: dict = {}

    def kv_get(self, key):
        return self.kv.get(key)

    def kv_set(self, key, value):
        self.kv[key] = value


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def production_env(monkeypatch):
    """Force the verifier down its production path (no dev-env bypass)."""
    monkeypatch.setenv("FLASK_ENV", "production")
    # Isolate the in-process caches between tests.
    PaymentVerifier._verified.clear()
    PaymentVerifier._amounts.clear()
    # Redirect the on-disk KV cache to a per-test in-memory fake so cached
    # verifications can never leak across test files via the real store.
    import sincor2.persistent_store as ps
    monkeypatch.setattr(ps, "get_store", lambda: _FakeKVStore())
    yield
    PaymentVerifier._verified.clear()
    PaymentVerifier._amounts.clear()


@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    """Point the x402 SQLite store at a throwaway DB for the test."""
    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def mock_onchain(monkeypatch):
    """Pretend every treasury-transfer check succeeds on-chain."""
    def _ok(tx_hash, **kwargs):
        return {"ok": True, "payer_wallet": "0x" + "ab" * 20}
    monkeypatch.setattr(x402, "verify_treasury_transfer", _ok)


def _real_hash(seed: str = "aa") -> str:
    return "0x" + seed * 32


def _pad(addr: str) -> str:
    return "0x" + "00" * 12 + addr[2:].lower()


def _fulfill(resource_id: str = "agent_task", tx_hash: str | None = None,
             payer_wallet: str = "") -> dict:
    ch = x402.create_challenge(resource_id, payer_wallet=payer_wallet)
    assert ch["ok"] is False and ch["http_status"] == 402, ch  # challenge shape
    return x402.verify_challenge(
        ch["challenge_id"], tx_hash or _real_hash(), payer_wallet=payer_wallet)


# ---------------------------------------------------------------------------
# Finding 6 — 0xSIMULATED bypass
# ---------------------------------------------------------------------------

class TestSimulatedBypass:
    def test_simulated_hash_rejected_by_default(self, production_env):
        """Attack test: a forged '0xSIMULATED...' hash must NOT verify."""
        assert PaymentVerifier.is_verified("0xSIMULATED-ATTACK-1", 1) is False

    def test_simulated_variants_never_succeed(self, production_env, monkeypatch):
        """Adversarial: case/format variants must fail closed, never succeed."""
        # Lowercase variant misses the canonical prefix -> must go down the
        # real RPC path. With no RPC URLs configured that must raise
        # PaymentRpcError (fail closed), never return True.
        monkeypatch.setattr(
            PaymentVerifier, "_rpc_urls", classmethod(lambda cls: []))
        with pytest.raises(PaymentVerifier.PaymentRpcError):
            PaymentVerifier.is_verified("0xsimulated-sneaky", 1)
        # Wrong prefix shape entirely -> invalid hash -> False.
        assert PaymentVerifier.is_verified("0XSIMULATED-1", 1) is False
        assert PaymentVerifier.is_verified("", 1) is False

    def test_simulated_not_enabled_by_environment(self, production_env, monkeypatch):
        """No environment variable may re-enable the simulated bypass."""
        for var in ("ALLOW_SIMULATED", "PAYMENT_ALLOW_SIMULATED",
                    "SINCOR_ALLOW_SIMULATED", "FLASK_ENV"):
            monkeypatch.setenv(var, "1")
        monkeypatch.setenv("FLASK_ENV", "production")  # keep prod path
        assert PaymentVerifier.is_verified("0xSIMULATED-ENV-1", 1) is False

    def test_simulated_opt_in_requires_explicit_flag(self, production_env):
        """Test-only escape hatch: explicit per-call opt-in accepts it."""
        assert PaymentVerifier.is_verified(
            "0xSIMULATED-TEST-9", 1, allow_simulated=True) is True

    def test_allow_simulated_defaults_false(self):
        """The flag defaults to closed on every verifier entrypoint."""
        for meth in ("is_verified", "verified_tx_data", "verified_amount_wei"):
            param = inspect.signature(
                getattr(PaymentVerifier, meth)).parameters["allow_simulated"]
            assert param.default is False, meth

    def test_verified_tx_data_rejects_simulated_by_default(self, production_env):
        assert PaymentVerifier.verified_tx_data(
            "0xSIMULATED-123", 10 ** 18) is None

    def test_verified_tx_data_simulated_opt_in_is_honestly_marked(
            self, production_env):
        """Opt-in record is labeled simulated_test — never 'onchain'."""
        rec = PaymentVerifier.verified_tx_data(
            "0xSIMULATED-123", 10 ** 18, allow_simulated=True)
        assert rec is not None
        assert rec["verification"] == "simulated_test"
        assert rec["verification"] != "onchain"
        assert rec["verified_transfer"] is False

    def test_verified_amount_wei_rejects_simulated_always(self, production_env):
        """No amount can be read from a simulated tx — always None."""
        assert PaymentVerifier.verified_amount_wei("0xSIMULATED-123") is None
        assert PaymentVerifier.verified_amount_wei(
            "0xSIMULATED-123", allow_simulated=True) is None

    def test_bootstrap_never_enables_simulation(self):
        """The bootstrapped production verifier cannot opt into simulation."""
        repo_root = Path(pv.__file__).resolve().parent.parent.parent
        bootstrap_src = (repo_root / "src" / "sincor2"
                         / "a2a_bootstrap.py").read_text()
        assert "allow_simulated" not in bootstrap_src
        # And no production call site anywhere passes the opt-in flag
        # (the defining module itself is excluded — it only documents it).
        hits = [p for p in (repo_root / "src").rglob("*.py")
                if p.name != "payment_verifier.py"
                and "allow_simulated=True" in p.read_text()]
        assert hits == [], hits

    def test_real_shaped_hash_still_verifies(self, production_env, monkeypatch):
        """Happy path: a real-shaped hash with a qualifying receipt verifies."""
        tx = _real_hash("b7")
        receipt = {
            "status": "0x1",
            "blockNumber": "0x1234",
            "logs": [{
                "address": pv.AXIOM_CONTRACT,
                "topics": [PaymentVerifier._TRANSFER_TOPIC,
                           _pad("0x" + "11" * 20), _pad(pv.TREASURY_WALLET)],
                "data": hex(2 * 10 ** 18),
            }],
        }
        monkeypatch.setattr(PaymentVerifier, "_fetch_receipt",
                            classmethod(lambda cls, url, h: receipt))
        assert PaymentVerifier.is_verified(tx, 10 ** 18) is True
        assert PaymentVerifier.verified_amount_wei(tx) == 2 * 10 ** 18
        data = PaymentVerifier.verified_tx_data(tx, 10 ** 18)
        assert data is not None and data["verification"] == "onchain"
        assert data["verified_transfer"] is True


# ---------------------------------------------------------------------------
# Finding 7 — x402 entitlement reuse
# ---------------------------------------------------------------------------

class TestEntitlementSingleUse:
    def test_fresh_token_grants_access_once(
            self, x402_db, mock_onchain):
        """Happy path: first dispatch succeeds, second is rejected."""
        res = _fulfill()
        assert res["ok"] is True, res
        token = res["access_token"]
        assert x402.access_granted(token, "agent_task") is True
        assert x402.access_granted(token, "agent_task") is False

    def test_token_expiry_stamped_on_fulfill(self, x402_db, mock_onchain):
        res = _fulfill()
        exp = datetime.fromisoformat(res["token_expires_at"])
        assert exp > datetime.now(timezone.utc)

    def test_expired_token_rejected(self, x402_db, mock_onchain):
        """Attack test: an expired token must not grant access."""
        res = _fulfill()
        token = res["access_token"]
        past = (datetime.now(timezone.utc)
                - timedelta(seconds=10)).isoformat()
        with x402._conn() as conn:
            conn.execute(
                "UPDATE x402_challenges SET token_expires_at=? "
                "WHERE access_token=?", (past, token))
            conn.commit()
        assert x402.access_granted(token, "agent_task") is False

    def test_legacy_token_without_expiry_rejected(self, x402_db, mock_onchain):
        """Pre-P1-7 rows (NULL token_expires_at) fail closed."""
        res = _fulfill()
        token = res["access_token"]
        with x402._conn() as conn:
            conn.execute(
                "UPDATE x402_challenges SET token_expires_at=NULL "
                "WHERE access_token=?", (token,))
            conn.commit()
        assert x402.access_granted(token, "agent_task") is False

    def test_token_bound_to_resource(self, x402_db, mock_onchain):
        res = _fulfill()
        assert x402.access_granted(
            res["access_token"], "bi_report_preview") is False

    def test_unknown_or_empty_token_rejected(self, x402_db):
        assert x402.access_granted("nope", "agent_task") is False
        assert x402.access_granted("", "agent_task") is False

    def test_consume_false_precheck_does_not_burn(
            self, x402_db, mock_onchain):
        """Informational pre-checks (the /x402 status route) don't consume."""
        res = _fulfill()
        token = res["access_token"]
        assert x402.access_granted(token, "agent_task", consume=False) is True
        assert x402.access_granted(token, "agent_task", consume=False) is True
        # First real dispatch still works, then the token is spent.
        assert x402.access_granted(token, "agent_task") is True
        assert x402.access_granted(token, "agent_task") is False

    def test_concurrent_consume_single_winner(self, x402_db, mock_onchain):
        """Adversarial race: exactly one concurrent dispatch may win."""
        res = _fulfill()
        token = res["access_token"]
        results = []
        def _try():
            results.append(x402.access_granted(token, "agent_task"))
        threads = [threading.Thread(target=_try) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(True) == 1, results
        assert results.count(False) == 7

    def test_consumption_survives_reopen(self, x402_db, mock_onchain, tmp_path):
        """Consumption is durable: a fresh connection still sees it spent."""
        res = _fulfill()
        token = res["access_token"]
        assert x402.access_granted(token, "agent_task") is True
        # New connection (simulates a later process/request).
        assert x402.access_granted(token, "agent_task") is False


# ---------------------------------------------------------------------------
# Finding 8 — tx-hash reuse across challenges
# ---------------------------------------------------------------------------

class TestTxHashUniqueness:
    def test_unique_index_exists(self, x402_db):
        with x402._conn() as conn:
            idx = conn.execute(
                "PRAGMA index_list(x402_challenges)").fetchall()
        uniq = [r for r in idx
                if r["name"] == "x402_challenges_tx_hash_uniq" and r["unique"]]
        assert uniq, [dict(r) for r in idx]

    def test_same_tx_hash_rejected_on_second_challenge(
            self, x402_db, mock_onchain):
        """Attack test: one payment cannot fulfill two challenges."""
        tx = _real_hash("cc")
        first = _fulfill(tx_hash=tx)
        assert first["ok"] is True, first

        ch2 = x402.create_challenge("agent_task")
        second = x402.verify_challenge(ch2["challenge_id"], tx)
        assert second["ok"] is False
        assert second["error"] == "tx_hash_already_used"

        # The second challenge is not poisoned: a distinct hash fulfills it.
        third = x402.verify_challenge(ch2["challenge_id"], _real_hash("dd"))
        assert third["ok"] is True, third

    def test_tx_hash_case_variant_rejected(self, x402_db, mock_onchain):
        """Adversarial: same hash in different case is the same payment."""
        tx = _real_hash("ee")
        assert _fulfill(tx_hash=tx)["ok"] is True
        ch2 = x402.create_challenge("agent_task")
        second = x402.verify_challenge(ch2["challenge_id"], tx.upper())
        assert second["ok"] is False
        assert second["error"] == "tx_hash_already_used"

    def test_distinct_hashes_fulfill_distinct_challenges(
            self, x402_db, mock_onchain):
        """Happy path: different payments fulfill different challenges."""
        r1 = _fulfill(tx_hash=_real_hash("f1"))
        r2 = _fulfill(tx_hash=_real_hash("f2"))
        assert r1["ok"] is True and r2["ok"] is True
        assert r1["access_token"] != r2["access_token"]
        assert x402.access_granted(r1["access_token"], "agent_task") is True
        assert x402.access_granted(r2["access_token"], "agent_task") is True

    def test_expired_challenge_still_rejected(self, x402_db, mock_onchain):
        ch = x402.create_challenge("agent_task")
        past = (datetime.now(timezone.utc)
                - timedelta(seconds=5)).isoformat()
        with x402._conn() as conn:
            conn.execute(
                "UPDATE x402_challenges SET expires_at=? WHERE challenge_id=?",
                (past, ch["challenge_id"]))
            conn.commit()
        res = x402.verify_challenge(ch["challenge_id"], _real_hash("ab"))
        assert res == {"ok": False, "error": "challenge_expired"}

    def test_concurrent_double_fulfill_single_winner(
            self, x402_db, mock_onchain):
        """Adversarial race: two challenges, one payment — exactly one wins."""
        ch1 = x402.create_challenge("agent_task")
        ch2 = x402.create_challenge("agent_task")
        tx = _real_hash("99")
        results = []

        def _try(cid):
            results.append(x402.verify_challenge(cid, tx))

        threads = [threading.Thread(target=_try, args=(ch1["challenge_id"],)),
                   threading.Thread(target=_try, args=(ch2["challenge_id"],))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        oks = [r for r in results if r.get("ok") is True]
        rejs = [r for r in results
                if r.get("error") == "tx_hash_already_used"]
        assert len(oks) == 1, results
        assert len(rejs) == 1, results
