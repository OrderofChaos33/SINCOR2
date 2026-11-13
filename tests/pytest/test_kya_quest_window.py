"""Airdrop quest claim window — env-gated start/end enforcement."""
from __future__ import annotations

import os
import tempfile
import time
import unittest

os.environ["SINCOR_DATA_DIR"] = tempfile.mkdtemp(prefix="kya_quest_window_")

from sincor2.kya.airdrop_quest import AirdropQuest
from sincor2.kya import registry as regmod

WALLETS = [
    "0x1111111111111111111111111111111111111111",
    "0x2222222222222222222222222222222222222222",
]

WINDOW_ENV = ("KYA_QUEST_START_TS", "KYA_QUEST_END_TS")


def _now() -> int:
    return int(time.time())


class QuestWindowTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in WINDOW_ENV}
        for k in WINDOW_ENV:
            os.environ.pop(k, None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for k in WINDOW_ENV:
            os.environ.pop(k, None)
            if self._saved[k] is not None:
                os.environ[k] = self._saved[k]

    def _seed(self) -> AirdropQuest:
        q = AirdropQuest()
        q.seed(list(WALLETS), source="test-window")
        return q

    def _verified_agent(self, agent_id: str, wallet: str) -> None:
        reg = regmod.KYARegistry()
        regmod._REG = reg
        listed = reg.list_agent({"agent_id": agent_id, "wallet": wallet})
        reg.bind(agent_id, wallet)
        reg.apply_stake(listed["kya_id"], str(10 * 10**18), "0xstake")
        verified = reg.verify(listed["kya_id"])
        self.assertTrue(verified["verified"])

    def test_claim_before_start_raises_not_open(self):
        now = _now()
        os.environ["KYA_QUEST_START_TS"] = str(now + 3600)
        os.environ["KYA_QUEST_END_TS"] = str(now + 7200)
        q = self._seed()
        with self.assertRaises(PermissionError) as ctx:
            q.claim(WALLETS[0], "no-such-agent")
        self.assertIn("quest not open", str(ctx.exception))

    def test_claim_after_end_raises_closed(self):
        now = _now()
        os.environ["KYA_QUEST_START_TS"] = str(now - 7200)
        os.environ["KYA_QUEST_END_TS"] = str(now - 3600)
        q = self._seed()
        with self.assertRaises(PermissionError) as ctx:
            q.claim(WALLETS[0], "no-such-agent")
        self.assertIn("quest closed", str(ctx.exception))

    def test_window_checked_before_identity_lookup(self):
        # unlisted agent_id would raise KeyError("agent not listed") — the
        # window must fire first with PermissionError.
        now = _now()
        os.environ["KYA_QUEST_START_TS"] = str(now + 3600)
        os.environ["KYA_QUEST_END_TS"] = str(now + 7200)
        q = self._seed()
        with self.assertRaises(PermissionError) as ctx:
            q.claim(WALLETS[0], "unknown-agent-xyz")
        self.assertIn("quest not open", str(ctx.exception))

    def test_claim_inside_window_proceeds(self):
        now = _now()
        os.environ["KYA_QUEST_START_TS"] = str(now - 3600)
        os.environ["KYA_QUEST_END_TS"] = str(now + 3600)
        q = self._seed()
        self._verified_agent("bot-window", WALLETS[0])
        rec = q.claim(WALLETS[0], "bot-window")
        self.assertEqual(rec["status"], "credited_ledger")
        self.assertEqual(rec["reward_axm"], 15)

    def test_unset_window_claims_normally(self):
        q = self._seed()
        self._verified_agent("bot-nowindow", WALLETS[0])
        rec = q.claim(WALLETS[0], "bot-nowindow")
        self.assertEqual(rec["status"], "credited_ledger")

    def test_start_only_window(self):
        now = _now()
        os.environ["KYA_QUEST_START_TS"] = str(now - 10)
        q = self._seed()
        self._verified_agent("bot-startonly", WALLETS[0])
        rec = q.claim(WALLETS[0], "bot-startonly")
        self.assertEqual(rec["status"], "credited_ledger")

    def test_end_only_window_closed(self):
        now = _now()
        os.environ["KYA_QUEST_END_TS"] = str(now - 10)
        q = self._seed()
        with self.assertRaises(PermissionError) as ctx:
            q.claim(WALLETS[0], "no-such-agent")
        self.assertIn("quest closed", str(ctx.exception))

    def test_stats_window_open_true_inside(self):
        now = _now()
        os.environ["KYA_QUEST_START_TS"] = str(now - 3600)
        os.environ["KYA_QUEST_END_TS"] = str(now + 3600)
        q = self._seed()
        w = q.stats()["window"]
        self.assertEqual(w["start"], now - 3600)
        self.assertEqual(w["end"], now + 3600)
        self.assertTrue(w["open"])

    def test_stats_window_open_false_outside(self):
        now = _now()
        os.environ["KYA_QUEST_START_TS"] = str(now - 7200)
        os.environ["KYA_QUEST_END_TS"] = str(now - 3600)
        q = self._seed()
        w = q.stats()["window"]
        self.assertFalse(w["open"])
        self.assertEqual(w["start"], now - 7200)
        self.assertEqual(w["end"], now - 3600)

    def test_stats_window_none_when_unset(self):
        q = self._seed()
        w = q.stats()["window"]
        self.assertIsNone(w["start"])
        self.assertIsNone(w["end"])
        self.assertTrue(w["open"])

    def test_window_read_at_claim_time_not_init(self):
        # Set the window AFTER the quest object is constructed; claim must
        # still enforce it (env is read at claim time, not cached at init).
        q = self._seed()
        now = _now()
        os.environ["KYA_QUEST_START_TS"] = str(now + 3600)
        os.environ["KYA_QUEST_END_TS"] = str(now + 7200)
        with self.assertRaises(PermissionError) as ctx:
            q.claim(WALLETS[0], "no-such-agent")
        self.assertIn("quest not open", str(ctx.exception))
        # And clearing the env again re-opens the window immediately.
        os.environ.pop("KYA_QUEST_START_TS", None)
        os.environ.pop("KYA_QUEST_END_TS", None)
        self._verified_agent("bot-late", WALLETS[0])
        rec = q.claim(WALLETS[0], "bot-late")
        self.assertEqual(rec["status"], "credited_ledger")


if __name__ == "__main__":
    unittest.main()
