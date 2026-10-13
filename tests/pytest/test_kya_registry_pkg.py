"""KYA package registry hardening: identity-change guard, wallet index, snapshot paging."""
from __future__ import annotations

import os
import tempfile
import unittest

os.environ["SINCOR_DATA_DIR"] = tempfile.mkdtemp(prefix="kya_registry_pkg_")

from sincor2.kya.registry import KYARegistry, MIN_STAKE_WEI

W1 = "0x1111111111111111111111111111111111111111"
W2 = "0x2222222222222222222222222222222222222222"


def fresh_registry() -> KYARegistry:
    # KYARegistry reads SINCOR_DATA_DIR at construction; give each test a clean
    # directory so no store state leaks between tests.
    os.environ["SINCOR_DATA_DIR"] = tempfile.mkdtemp(prefix="kya_reg_pkg_one_")
    return KYARegistry()


class KYARegistryPkgTests(unittest.TestCase):
    def setUp(self):
        self.reg = fresh_registry()

    # --- identity-change guard -------------------------------------------
    def test_relist_same_wallet_idempotent(self):
        a = self.reg.list_agent({"agent_id": "bot-1", "wallet": W1})
        b = self.reg.list_agent({"agent_id": "bot-1", "wallet": W1})
        self.assertEqual(a["kya_id"], b["kya_id"])
        self.assertEqual(b["status"], "listed")
        self.assertEqual(b["wallet"], W1)
        self.assertNotIn("history", b)
        # wallet index still resolves
        self.assertEqual(self.reg.lookup(wallet=W1)["agent_id"], "bot-1")

    def test_relist_different_wallet_resets_and_records_history(self):
        self.reg.list_agent({"agent_id": "bot-1", "wallet": W1})
        self.reg.bind("bot-1", W1)
        rec = self.reg.list_agent({"agent_id": "bot-1", "wallet": W2})
        self.assertEqual(rec["status"], "listed")
        self.assertFalse(rec["verified"])
        self.assertEqual(rec["wallet"], W2)
        history = rec.get("history") or []
        self.assertEqual(len(history), 1)
        ev = history[0]
        self.assertEqual(ev["event"], "identity_changed")
        self.assertEqual(ev["old_wallet"], W1)
        self.assertIn("at", ev)
        # old wallet no longer resolves, new one does
        self.assertIsNone(self.reg.lookup(wallet=W1))
        self.assertEqual(self.reg.lookup(wallet=W2)["agent_id"], "bot-1")

    def test_stake_reset_on_identity_change(self):
        first = self.reg.list_agent({"agent_id": "bot-1", "wallet": W1})
        self.reg.bind("bot-1", W1)
        self.reg.apply_stake(first["kya_id"], str(MIN_STAKE_WEI), "0xstake")
        self.assertEqual(self.reg.lookup(agent_id="bot-1")["stake_axm_wei"], str(MIN_STAKE_WEI))
        rec = self.reg.list_agent({"agent_id": "bot-1", "wallet": W2})
        self.assertEqual(rec["stake_axm_wei"], "0")
        self.assertEqual(rec["status"], "listed")
        self.assertFalse(rec["verified"])

    def test_identity_change_history_accumulates_and_survives_refresh(self):
        self.reg.list_agent({"agent_id": "bot-1", "wallet": W1})
        self.reg.list_agent({"agent_id": "bot-1", "wallet": W2})
        rec = self.reg.list_agent({"agent_id": "bot-1", "wallet": W1})
        history = rec.get("history") or []
        self.assertEqual([e["event"] for e in history], ["identity_changed", "identity_changed"])
        self.assertEqual(history[0]["old_wallet"], W1)
        self.assertEqual(history[1]["old_wallet"], W2)
        # same-wallet refresh keeps the trail intact
        again = self.reg.list_agent({"agent_id": "bot-1", "wallet": W1})
        self.assertEqual(again.get("history"), history)

    # --- wallet lookup index ----------------------------------------------
    def test_wallet_lookup_finds_via_index(self):
        self.reg.list_agent({"agent_id": "bot-a", "wallet": W1})
        self.reg.list_agent({"agent_id": "bot-b", "wallet": W2})
        rec = self.reg.lookup(wallet=W2)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["agent_id"], "bot-b")
        self.assertEqual(rec["kya_id"], self.reg.by_wallet[W2.lower()])

    def test_wallet_lookup_case_insensitive_and_unknown(self):
        self.reg.list_agent({"agent_id": "bot-a", "wallet": W1})
        self.assertIsNotNone(self.reg.lookup(wallet=W1.upper()))
        self.assertIsNone(self.reg.lookup(wallet="0xdead"))

    def test_bind_updates_wallet_index(self):
        rec = self.reg.list_agent({"agent_id": "bot-1", "wallet": W1})
        self.reg.bind("bot-1", W1)
        self.assertEqual(self.reg.by_wallet[W1.lower()], rec["kya_id"])
        self.assertEqual(self.reg.lookup(wallet=W1)["status"], "bound")

    # --- snapshot pagination / filter --------------------------------------
    def test_snapshot_default_args_match_old_behavior(self):
        for i in range(3):
            self.reg.list_agent({"agent_id": f"bot-{i}", "wallet": f"0x{i:040x}"})
        snap = self.reg.snapshot()
        self.assertEqual(snap["listed"], 3)
        self.assertEqual(snap["verified"], 0)
        self.assertEqual(len(snap["records"]), 3)

    def test_snapshot_pagination_limit_offset(self):
        for i in range(5):
            self.reg.list_agent({"agent_id": f"bot-{i}", "wallet": f"0x{i:040x}"})
        p1 = self.reg.snapshot(limit=2, offset=0)
        p2 = self.reg.snapshot(limit=2, offset=2)
        p3 = self.reg.snapshot(limit=2, offset=4)
        self.assertEqual(p1["listed"], 5)  # counts stay global
        self.assertEqual(len(p1["records"]), 2)
        self.assertEqual(len(p2["records"]), 2)
        self.assertEqual(len(p3["records"]), 1)
        ids = [r["kya_id"] for r in p1["records"] + p2["records"] + p3["records"]]
        self.assertEqual(len(set(ids)), 5)  # no dupes, no gaps

    def test_snapshot_verified_only_filter(self):
        for i in range(3):
            rec = self.reg.list_agent({"agent_id": f"bot-{i}", "wallet": f"0x{i:040x}"})
            self.reg.bind(f"bot-{i}", f"0x{i:040x}")
            self.reg.apply_stake(rec["kya_id"], str(MIN_STAKE_WEI), "0xstake")
            self.reg.verify(rec["kya_id"])
        self.reg.list_agent({"agent_id": "unverified", "wallet": W2})
        snap = self.reg.snapshot(verified_only=True)
        self.assertEqual(snap["listed"], 4)
        self.assertEqual(snap["verified"], 3)
        self.assertEqual(len(snap["records"]), 3)
        self.assertTrue(all(r["verified"] for r in snap["records"]))

    def test_snapshot_records_are_copies(self):
        self.reg.list_agent({"agent_id": "bot-1", "wallet": W1})
        snap = self.reg.snapshot()
        snap["records"][0]["status"] = "tampered"
        snap["records"][0]["history"] = [{"event": "fake"}]
        stored = self.reg.lookup(agent_id="bot-1")
        self.assertEqual(stored["status"], "listed")
        self.assertNotIn("history", stored)


if __name__ == "__main__":
    unittest.main()
