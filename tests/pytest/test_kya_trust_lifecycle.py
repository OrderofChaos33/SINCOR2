"""KYA trust-lifecycle tests: heartbeat expiry and revocation propagation.

Proves the trust-stack claim "verify is not a badge — it is a state machine
with a revoke" against the live registry (sincor2/kya_registry.py), and pins
the remaining known gaps as failing tests so they cannot be forgotten.

No Flask server, no chain. Registry persistence is redirected to temp files.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import kya_registry as kya

# Throwaway signer: bind() verifies a real EIP-191 signature, so the tests
# exercise the genuine path instead of depending on eth_account being absent.
_SIGNER = Account.create()
WALLET = _SIGNER.address
_BIND_MESSAGE = "SINCOR KYA bind (test)"


def _bind(agent_id: str) -> dict:
    sig = _SIGNER.sign_message(encode_defunct(text=_BIND_MESSAGE)).signature.hex()
    sig = sig if sig.startswith("0x") else "0x" + sig
    return kya.bind(agent_id, WALLET, sig, _BIND_MESSAGE)


FABRIC_AGENT = {
    "agent_id": "trust-probe-01",
    "name": "Trust Probe",
    "description": "Heartbeat/revocation lifecycle probe",
    "version": "0.1.0",
    "capability_tags": ["lead-enrichment"],
    "skills": [{"id": "lead-enrichment", "name": "Lead Enrichment"}],
    "rpc_callback": "https://getsincor.com/api/a2a",
    "wallet": WALLET,
    "chain_id": 8453,
    "last_heartbeat": 0,
}


def _stale_heartbeat_ms() -> int:
    """A last_heartbeat_ms old enough that _is_live() must return False."""
    return kya._now_ms() - (kya.HEARTBEAT_TTL_MS * kya.LIVE_GRACE + 60_000)


def _verify_lifecycle(agent_id: str = "trust-probe-01") -> dict:
    """Drive one agent all the way to verified. Returns the record."""
    agent = dict(FABRIC_AGENT, agent_id=agent_id)
    rec = kya.list_from_inbound(agent)
    _bind(agent_id)
    kya.apply_stake(rec["kya_id"], str(kya.MIN_STAKE_WEI), "0x" + "ab" * 32)
    kya.heartbeat(agent_id, ok=True)
    return kya.verify(rec["kya_id"])


class KyaHeartbeatExpiryTests(unittest.TestCase):
    def setUp(self):
        self._prior_kya = os.environ.get("KYA_STORE_PATH")
        self._prior_data = os.environ.get("SINCOR_DATA_DIR")
        fd, self._store_path = tempfile.mkstemp(prefix="kya_trust_", suffix=".json")
        os.close(fd)
        self._data_dir = tempfile.mkdtemp(prefix="kya_trust_data_")
        os.environ["KYA_STORE_PATH"] = self._store_path
        os.environ["SINCOR_DATA_DIR"] = self._data_dir
        kya.reset()
        if os.path.exists(self._store_path):
            os.unlink(self._store_path)

    def tearDown(self):
        if os.path.exists(self._store_path):
            os.unlink(self._store_path)
        import shutil
        shutil.rmtree(self._data_dir, ignore_errors=True)
        for key, prior in (("KYA_STORE_PATH", self._prior_kya),
                           ("SINCOR_DATA_DIR", self._prior_data)):
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior

    def test_verified_agent_expires_on_stale_heartbeat(self):
        """A verified agent whose heartbeat goes stale must transition to expired."""
        rec = _verify_lifecycle()
        self.assertEqual(rec["status"], "verified")

        live = kya.get(rec["kya_id"])
        live["sla"]["last_heartbeat_ms"] = _stale_heartbeat_ms()
        refreshed = kya.refresh_status(live)

        self.assertFalse(kya._is_live(refreshed))
        self.assertEqual(refreshed["status"], "expired")

    def test_expiry_drops_liveness_from_score(self):
        """The canonical score() must stop counting a stale agent as live."""
        rec = _verify_lifecycle()
        live_score = kya.score(kya.get(rec["kya_id"]))

        stale = kya.get(rec["kya_id"])
        stale["sla"]["last_heartbeat_ms"] = _stale_heartbeat_ms()
        stale_score = kya.score(kya.refresh_status(stale))

        self.assertGreater(live_score, stale_score)

    def test_heartbeat_revives_expired_agent(self):
        """A fresh heartbeat restores a verified agent that had expired."""
        rec = _verify_lifecycle()
        stale = kya.get(rec["kya_id"])
        stale["sla"]["last_heartbeat_ms"] = _stale_heartbeat_ms()
        self.assertEqual(kya.refresh_status(stale)["status"], "expired")

        revived = kya.heartbeat(rec["agent_id"], ok=True)
        self.assertEqual(revived["status"], "verified")

    def test_verify_rejected_when_heartbeat_stale(self):
        """verify() must refuse when the heartbeat is stale, even with stake."""
        rec = _verify_lifecycle()
        stale = kya.get(rec["kya_id"])
        stale["sla"]["last_heartbeat_ms"] = _stale_heartbeat_ms()
        with self.assertRaises(ValueError):
            kya.verify(rec["kya_id"])

    def test_expiry_applies_only_to_verified_status(self):
        """DOCUMENTED GAP: a staked-but-unverified agent with a stale heartbeat
        never transitions to expired — refresh_status only expires 'verified'.
        The Genesis narrative says 'miss the window and the record expires'
        without that qualification."""
        agent = dict(FABRIC_AGENT, agent_id="trust-probe-staked")
        rec = kya.list_from_inbound(agent)
        _bind(agent["agent_id"])
        staked = kya.apply_stake(rec["kya_id"], str(kya.MIN_STAKE_WEI), "0x" + "ab" * 32)
        self.assertEqual(staked["status"], "staked")

        stale = kya.get(rec["kya_id"])
        stale["sla"]["last_heartbeat_ms"] = _stale_heartbeat_ms()
        refreshed = kya.refresh_status(stale)
        # Current behavior: stays 'staked'. If this ever becomes 'expired',
        # the gap is closed — update this test to assert the fixed behavior.
        self.assertEqual(refreshed["status"], "staked")


class KyaRevocationTests(unittest.TestCase):
    def setUp(self):
        self._prior_kya = os.environ.get("KYA_STORE_PATH")
        self._prior_data = os.environ.get("SINCOR_DATA_DIR")
        fd, self._store_path = tempfile.mkstemp(prefix="kya_trust_", suffix=".json")
        os.close(fd)
        self._data_dir = tempfile.mkdtemp(prefix="kya_trust_data_")
        os.environ["KYA_STORE_PATH"] = self._store_path
        os.environ["SINCOR_DATA_DIR"] = self._data_dir
        kya.reset()
        if os.path.exists(self._store_path):
            os.unlink(self._store_path)

    def tearDown(self):
        if os.path.exists(self._store_path):
            os.unlink(self._store_path)
        import shutil
        shutil.rmtree(self._data_dir, ignore_errors=True)
        for key, prior in (("KYA_STORE_PATH", self._prior_kya),
                           ("SINCOR_DATA_DIR", self._prior_data)):
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior

    def test_revoke_flags_record_and_blocks_lifecycle(self):
        rec = _verify_lifecycle()
        revoked = kya.revoke(rec["kya_id"], "test revocation")

        self.assertTrue(revoked["revoked"])
        self.assertEqual(revoked["revoke_reason"], "test revocation")
        self.assertEqual(revoked["status"], "revoked")
        # refresh_status must never clear a revocation, even with a live heartbeat
        kya.heartbeat(rec["agent_id"], ok=True)
        self.assertEqual(kya.refresh_status(kya.get(rec["kya_id"]))["status"], "revoked")

        with self.assertRaises(ValueError):
            _bind(rec["agent_id"])
        with self.assertRaises(ValueError):
            kya.apply_stake(rec["kya_id"], str(kya.MIN_STAKE_WEI), "0x" + "cd" * 32)
        with self.assertRaises(ValueError):
            kya.verify(rec["kya_id"])

    def test_revoke_visible_in_lookup_and_snapshot(self):
        rec = _verify_lifecycle()
        kya.revoke(rec["kya_id"], "test revocation")

        found = kya.lookup_wallet(WALLET)
        self.assertTrue(any(r.get("revoked") for r in found))
        self.assertEqual(kya.snapshot()["revoked"], 1)
        self.assertEqual(kya.snapshot()["verified"], 0)


class KyaRevocationPropagationTests(unittest.TestCase):
    """The public A2A directory must not keep serving a revoked agent as live."""

    def setUp(self):
        self._prior_kya = os.environ.get("KYA_STORE_PATH")
        self._prior_data = os.environ.get("SINCOR_DATA_DIR")
        fd, self._store_path = tempfile.mkstemp(prefix="kya_trust_", suffix=".json")
        os.close(fd)
        self._data_dir = tempfile.mkdtemp(prefix="kya_trust_data_")
        os.environ["KYA_STORE_PATH"] = self._store_path
        os.environ["SINCOR_DATA_DIR"] = self._data_dir
        kya.reset()
        if os.path.exists(self._store_path):
            os.unlink(self._store_path)
        from sincor2.a2a_inbound import reset_fabric
        reset_fabric()

    def tearDown(self):
        if os.path.exists(self._store_path):
            os.unlink(self._store_path)
        import shutil
        shutil.rmtree(self._data_dir, ignore_errors=True)
        for key, prior in (("KYA_STORE_PATH", self._prior_kya),
                           ("SINCOR_DATA_DIR", self._prior_data)):
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior
        from sincor2.a2a_inbound import reset_fabric
        reset_fabric()

    def test_revoked_agent_excluded_from_directory(self):
        """Revoking an agent removes it from list_agents() on the very next request."""
        from sincor2 import a2a_inbound_ext as ext

        agent_id = "trust-probe-revoked"
        snapshot = ext.register_agent_record(dict(FABRIC_AGENT, agent_id=agent_id))
        kya_id = snapshot.get("kya_id")
        self.assertTrue(kya_id, "registration should create a KYA record")

        before = {a["agent_id"] for a in ext.list_agents()}
        self.assertIn(agent_id, before, "agent should be listed before revocation")

        kya.revoke(kya_id, "test revocation")

        rows = {a["agent_id"]: a for a in ext.list_agents()}
        self.assertNotIn(
            agent_id, rows,
            "revoked agent must disappear from the directory immediately",
        )

    def test_revoked_agent_excluded_with_live_only(self):
        """Exclusion holds under live_only filtering (not just the default listing)."""
        from sincor2 import a2a_inbound_ext as ext

        agent_id = "trust-probe-revoked-live"
        ext.register_agent_record(dict(FABRIC_AGENT, agent_id=agent_id))
        ext.heartbeat_agent(agent_id)  # make the fabric row live
        kya_id = kya.get_by_agent(agent_id)["kya_id"]

        live_rows = {a["agent_id"] for a in ext.list_agents(live_only=True)}
        self.assertIn(agent_id, live_rows)

        kya.revoke(kya_id, "test revocation")

        rows = {a["agent_id"] for a in ext.list_agents(live_only=True)}
        self.assertNotIn(agent_id, rows)

    def test_expired_kya_status_surfaced_in_directory(self):
        """A verified agent whose KYA heartbeat goes stale is listed with honest kya_status."""
        from sincor2 import a2a_inbound_ext as ext

        agent_id = "trust-probe-expired"
        snapshot = ext.register_agent_record(dict(FABRIC_AGENT, agent_id=agent_id))
        kya_id = snapshot.get("kya_id")
        self.assertTrue(kya_id, "registration should create a KYA record")

        _bind(agent_id)
        kya.apply_stake(kya_id, str(kya.MIN_STAKE_WEI), "0x" + "ab" * 32)
        kya.heartbeat(agent_id, ok=True)
        self.assertEqual(kya.verify(kya_id)["status"], "verified")

        live = kya.get(kya_id)
        live["sla"]["last_heartbeat_ms"] = _stale_heartbeat_ms()

        rows = {a["agent_id"]: a for a in ext.list_agents()}
        row = rows.get(agent_id)
        self.assertIsNotNone(row, "expired (not revoked) agent stays listed")
        self.assertEqual(
            row.get("kya_status"), "expired",
            f"directory must surface live KYA status, got {row.get('kya_status')!r}",
        )

    def test_directory_survives_kya_outage(self):
        """If KYA is down, list_agents() fails open with stored values — no exception."""
        from unittest import mock

        from sincor2 import a2a_inbound_ext as ext

        agent_id = "trust-probe-kya-down"
        ext.register_agent_record(dict(FABRIC_AGENT, agent_id=agent_id))

        with mock.patch(
            "sincor2.kya_registry.live_statuses",
            side_effect=RuntimeError("kya down"),
        ):
            rows = {a["agent_id"]: a for a in ext.list_agents()}
        self.assertIn(agent_id, rows, "directory must fail open when KYA is down")


if __name__ == "__main__":
    unittest.main()
