"""KYA package lifecycle tests: revoke(), expiry TTL, heartbeat refresh.

Exercises sincor2.kya.registry.KYARegistry (the kya/ PACKAGE registry -- NOT
sincor2.kya_registry). No Flask, no chain. SINCOR_DATA_DIR is pointed at a fresh
temp dir per test; KYA_VERIFY_TTL_S / KYA_HEARTBEAT_EXTEND_S are tuned small
where TTL behaviour is under test.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest import mock

import sincor2.kya.registry as kya_reg
from sincor2.kya.registry import KYARegistry, MIN_STAKE_WEI

AGENT = {"agent_id": "lifecycle-probe", "wallet": "0x1111111111111111111111111111111111111111"}


def _verify_ready(reg: KYARegistry, agent: dict) -> dict:
    listed = reg.list_agent(dict(agent))
    kid = listed["kya_id"]
    reg.bind(agent["agent_id"], agent["wallet"])
    reg.apply_stake(kid, str(MIN_STAKE_WEI), "0x" + "ab" * 32)
    return reg.verify(kid)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self._prior_data = os.environ.get("SINCOR_DATA_DIR")
        self._prior_ttl = os.environ.get("KYA_VERIFY_TTL_S")
        self._prior_ext = os.environ.get("KYA_HEARTBEAT_EXTEND_S")
        self._tmp = tempfile.TemporaryDirectory(prefix="kya_pkg_lifecycle_")
        os.environ["SINCOR_DATA_DIR"] = self._tmp.name
        os.environ["KYA_VERIFY_TTL_S"] = "3600"
        os.environ["KYA_HEARTBEAT_EXTEND_S"] = "600"

    def tearDown(self):
        self._tmp.cleanup()
        for key, val in (
            ("SINCOR_DATA_DIR", self._prior_data),
            ("KYA_VERIFY_TTL_S", self._prior_ttl),
            ("KYA_HEARTBEAT_EXTEND_S", self._prior_ext),
        ):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_revoke_flips_status_and_blocks_verify(self):
        reg = KYARegistry()
        rec = _verify_ready(reg, AGENT)
        kid = rec["kya_id"]
        self.assertTrue(reg.is_live(kid))

        revoked = reg.revoke(kid, reason="compromised key")
        self.assertEqual(revoked["status"], "revoked")
        self.assertFalse(revoked["verified"])
        self.assertGreater(revoked["revoked_at"], 0)
        self.assertEqual(revoked["revoke_reason"], "compromised key")
        self.assertFalse(reg.is_live(kid))
        self.assertEqual(reg.status_of(kid), "revoked")

        with self.assertRaises(PermissionError):
            reg.verify(kid)

    def test_revoked_kya_id_cannot_be_reverified_after_relist(self):
        reg = KYARegistry()
        rec = _verify_ready(reg, AGENT)
        old_kid = rec["kya_id"]
        reg.revoke(old_kid, reason="fraud")

        # Re-listing the same agent_id creates a NEW kya_id; the old one stays revoked.
        listed = reg.list_agent(dict(AGENT))
        new_kid = listed["kya_id"]
        self.assertNotEqual(new_kid, old_kid)
        self.assertTrue(new_kid.startswith(old_kid + "-r"))

        with self.assertRaises(PermissionError):
            reg.verify(old_kid)
        # And the new kya_id is a clean identity: it can bind/stake/verify.
        reg.bind(AGENT["agent_id"], AGENT["wallet"])
        reg.apply_stake(new_kid, str(MIN_STAKE_WEI), "0x" + "cd" * 32)
        fresh = reg.verify(new_kid)
        self.assertEqual(fresh["status"], "verified")
        self.assertTrue(reg.is_live(new_kid))

    def test_expired_record_reports_expired_via_status_of(self):
        reg = KYARegistry()
        base = 1_000_000
        with mock.patch.object(kya_reg, "_now", return_value=base):
            rec = _verify_ready(reg, AGENT)
            kid = rec["kya_id"]
            self.assertEqual(reg.status_of(kid), "verified")
        # Past the 3600s TTL: effective status reports expired...
        with mock.patch.object(kya_reg, "_now", return_value=base + 3601):
            self.assertEqual(reg.status_of(kid), "expired")
            self.assertFalse(reg.is_live(kid))
            # ...but the stored record is NOT mutated, so lookup() callers
            # see the record as-is.
            self.assertEqual(reg.lookup(kya_id=kid)["status"], "verified")

    def test_fresh_heartbeat_extends_expiry(self):
        reg = KYARegistry()
        base = 2_000_000
        with mock.patch.object(kya_reg, "_now", return_value=base):
            rec = _verify_ready(reg, AGENT)
            kid = rec["kya_id"]
            before = reg.lookup(kya_id=kid)["expires_at"]
            # Heartbeat at the same instant as verify: the cap (now + TTL)
            # equals the current expiry, so nothing moves.
            reg.heartbeat(AGENT["agent_id"])
            self.assertEqual(reg.lookup(kya_id=kid)["expires_at"], before)
        # 1000s later the record is still live: expiry extends by the
        # 600s KYA_HEARTBEAT_EXTEND_S, capped at now + TTL.
        with mock.patch.object(kya_reg, "_now", return_value=base + 1000):
            reg.heartbeat(AGENT["agent_id"])
            after = reg.lookup(kya_id=kid)["expires_at"]
            self.assertEqual(after, before + 600)
            self.assertEqual(reg.status_of(kid), "verified")
            self.assertTrue(reg.is_live(kid))

    def test_stale_record_expires_without_heartbeat(self):
        os.environ["KYA_VERIFY_TTL_S"] = "1"
        reg = KYARegistry()
        rec = _verify_ready(reg, AGENT)
        kid = rec["kya_id"]
        self.assertTrue(reg.is_live(kid))
        # Wait deterministically until the wall clock passes expires_at
        # (avoids int-second boundary flakiness of a fixed sleep).
        deadline = reg.lookup(kya_id=kid)["expires_at"]
        while int(time.time()) <= deadline:
            time.sleep(0.2)
        self.assertFalse(reg.is_live(kid))
        self.assertEqual(reg.status_of(kid), "expired")

    def test_heartbeat_does_not_revive_expired_record(self):
        reg = KYARegistry()
        base = 3_000_000
        with mock.patch.object(kya_reg, "_now", return_value=base):
            rec = _verify_ready(reg, AGENT)
            kid = rec["kya_id"]
            exp_before = reg.lookup(kya_id=kid)["expires_at"]
        # Past the 3600s TTL: heartbeat must not extend a dead record.
        with mock.patch.object(kya_reg, "_now", return_value=base + 3601):
            self.assertFalse(reg.is_live(kid))
            reg.heartbeat(AGENT["agent_id"])
            self.assertEqual(reg.lookup(kya_id=kid)["expires_at"], exp_before)
            self.assertFalse(reg.is_live(kid))
            self.assertEqual(reg.status_of(kid), "expired")
            # The way back is re-verify, which refreshes the TTL from now.
            reg.apply_stake(kid, str(MIN_STAKE_WEI), "0x" + "ab" * 32)
            revived = reg.verify(kid)
            self.assertEqual(revived["expires_at"], base + 3601 + 3600)
            self.assertTrue(reg.is_live(kid))
            self.assertEqual(reg.status_of(kid), "verified")

    def test_tombstone_persists_across_registry_reload(self):
        reg = KYARegistry()
        rec = _verify_ready(reg, AGENT)
        kid = rec["kya_id"]
        reg.revoke(kid, reason="double-spend")

        # New KYARegistry() instance on the same store: tombstone survives.
        reg2 = KYARegistry()
        self.assertEqual(reg2.lookup(kya_id=kid)["status"], "revoked")
        with self.assertRaises(PermissionError):
            reg2.verify(kid)
        # ...and re-listing still derives a successor, not the tombstoned id.
        listed = reg2.list_agent(dict(AGENT))
        self.assertNotEqual(listed["kya_id"], kid)

    def test_revoke_unknown_kya_id_raises_keyerror(self):
        reg = KYARegistry()
        with self.assertRaises(KeyError):
            reg.revoke("kya_doesnotexist00")
        with self.assertRaises(KeyError):
            reg.status_of("kya_doesnotexist00")

    def test_history_records_revoke_event(self):
        reg = KYARegistry()
        rec = _verify_ready(reg, AGENT)
        kid = rec["kya_id"]
        revoked = reg.revoke(kid, reason="policy violation")
        events = [h["event"] for h in revoked.get("history", [])]
        self.assertIn("listed", events)
        self.assertIn("verified", events)
        self.assertIn("revoked", events)
        revoke_entry = next(h for h in revoked["history"] if h["event"] == "revoked")
        self.assertEqual(revoke_entry["reason"], "policy violation")
        self.assertGreater(revoke_entry["at"], 0)

    def test_verify_sets_expires_at_from_env_ttl(self):
        reg = KYARegistry()
        rec = _verify_ready(reg, AGENT)
        exp = rec["expires_at"]
        now = int(time.time())
        self.assertGreater(exp, now + 3590)
        self.assertLessEqual(exp, now + 3600 + 5)
        self.assertTrue(reg.is_live(rec["kya_id"]))


if __name__ == "__main__":
    unittest.main()
