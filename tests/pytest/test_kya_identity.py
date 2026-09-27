"""KYA identity-persistence tests: tombstones, whitewash detection,
wallet cardinality, and clean-exit unstake. No Flask, no chain."""
from __future__ import annotations

import os
import tempfile
import unittest

from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import kya_registry as kya


def _agent(agent_id: str, wallet: str):
    return {
        "agent_id": agent_id,
        "name": f"Agent {agent_id}",
        "description": "identity persistence probe",
        "version": "0.1.0",
        "capability_tags": ["probe"],
        "skills": [{"id": "probe", "name": "Probe"}],
        "rpc_callback": "https://getsincor.com/api/a2a",
        "wallet": wallet,
        "chain_id": 8453,
    }


SHARED_CARD = {
    "id": "shared-card",
    "name": "Same Card",
    "description": "identical card, different agent_id",
    "version": "1.0.0",
    "skills": ["probe"],
}


def _bind(agent_id: str, acct) -> dict:
    msg = f"SINCOR KYA bind {agent_id}"
    sig = acct.sign_message(encode_defunct(text=msg)).signature.hex()
    return kya.bind(agent_id, acct.address, sig, msg)


class IdentityPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._prior = os.environ.get("KYA_STORE_PATH")
        fd, self._store_path = tempfile.mkstemp(prefix="kya_identity_test_", suffix=".json")
        os.close(fd)
        os.environ["KYA_STORE_PATH"] = self._store_path
        kya.reset()
        if os.path.exists(self._store_path):
            os.unlink(self._store_path)

    def tearDown(self):
        if os.path.exists(self._store_path):
            os.unlink(self._store_path)
        if self._prior is None:
            os.environ.pop("KYA_STORE_PATH", None)
        else:
            os.environ["KYA_STORE_PATH"] = self._prior
        kya.reset()

    def test_revoke_writes_tombstone(self):
        acct = Account.create()
        rec = kya.list_from_inbound(_agent("ghost-a", acct.address))
        kya.revoke(rec["kya_id"], reason="test revoke")
        snap = kya.tombstones_snapshot()
        self.assertEqual(snap["count"], 1)
        self.assertEqual(snap["tombstones"][0]["wallet"], acct.address.lower())
        self.assertEqual(snap["tombstones"][0]["reason"], "test revoke")

    def test_tombstoned_wallet_flags_rebirth(self):
        acct = Account.create()
        rec = kya.list_from_inbound(_agent("dead-1", acct.address))
        kya.revoke(rec["kya_id"], reason="misconduct")
        reborn = kya.list_from_inbound(_agent("reborn-1", acct.address))
        risk = reborn.get("identity_risk") or {}
        self.assertIn("tombstoned_wallet", risk)
        self.assertEqual(risk["tombstoned_wallet"]["prior_agent_id"], "dead-1")

    def test_card_reuse_flags_whitewash(self):
        w1, w2 = Account.create(), Account.create()
        kya.list_from_inbound(_agent("orig-1", w1.address), card=dict(SHARED_CARD))
        copy = kya.list_from_inbound(_agent("copy-1", w2.address), card=dict(SHARED_CARD))
        risk = copy.get("identity_risk") or {}
        self.assertIn("card_reuse", risk)
        self.assertIn("orig-1", risk["card_reuse"]["other_agent_ids"])

    def test_flag_ghost_tombstones_without_revoke(self):
        acct = Account.create()
        rec = kya.list_from_inbound(_agent("ghoster", acct.address))
        tomb = kya.flag_ghost("ghoster")
        self.assertIsNotNone(tomb)
        self.assertEqual(tomb["reason"], "ghosting")
        # record itself is not revoked — the market layer owns reputation
        self.assertFalse(kya.get(rec["kya_id"]).get("revoked"))
        # ...but a rebirth behind the same wallet is flagged
        reborn = kya.list_from_inbound(_agent("ghoster-2", acct.address))
        self.assertIn("tombstoned_wallet", reborn.get("identity_risk") or {})

    def test_wallet_identity_cap(self):
        acct = Account.create()
        for i in range(kya.MAX_IDENTITIES_PER_WALLET):
            kya.list_from_inbound(_agent(f"multi-{i}", acct.address))
            _bind(f"multi-{i}", acct)
        kya.list_from_inbound(_agent("multi-over", acct.address))
        with self.assertRaises(ValueError):
            _bind("multi-over", acct)
        # revoking one frees a slot (and tombstones the wallet)
        kya.revoke(kya.get_by_agent("multi-0")["kya_id"], reason="slot freed")
        bound = _bind("multi-over", acct)
        self.assertEqual(bound["status"], "bound")

    def test_unstake_clean_exit(self):
        acct = Account.create()
        rec = kya.list_from_inbound(_agent("leaver", acct.address))
        _bind("leaver", acct)
        kya.apply_stake(rec["kya_id"], str(10 * 10**18), "0xtx")
        req = kya.request_unstake(rec["kya_id"])
        self.assertIsNotNone(req["unstake_requested_at"])
        # timelock still active -> finalize refuses
        with self.assertRaises(ValueError):
            kya.finalize_unstake(rec["kya_id"])
        # backdate past the timelock -> finalize releases
        rec2 = kya.get(rec["kya_id"])
        rec2["unstake_requested_at"] = kya._now_ms() - kya.UNSTAKE_TIMELOCK_MS - 1000
        out = kya.finalize_unstake(rec["kya_id"])
        self.assertEqual(out["released_axm_wei"], str(10 * 10**18))
        self.assertEqual(kya.get(rec["kya_id"])["stake_axm_wei"], "0")

    def test_unstake_blocked_by_dispute_hold(self):
        acct = Account.create()
        rec = kya.list_from_inbound(_agent("disputed", acct.address))
        _bind("disputed", acct)
        kya.apply_stake(rec["kya_id"], str(10 * 10**18), "0xtx")
        kya.request_unstake(rec["kya_id"])
        kya.get(rec["kya_id"])["unstake_requested_at"] = kya._now_ms() - kya.UNSTAKE_TIMELOCK_MS - 1000
        kya.set_dispute_hold(rec["kya_id"], True)
        with self.assertRaises(ValueError):
            kya.finalize_unstake(rec["kya_id"])
        kya.set_dispute_hold(rec["kya_id"], False)
        out = kya.finalize_unstake(rec["kya_id"])
        self.assertEqual(out["released_axm_wei"], str(10 * 10**18))

    def test_tombstones_survive_save_load(self):
        acct = Account.create()
        rec = kya.list_from_inbound(_agent("persist-a", acct.address))
        kya.revoke(rec["kya_id"], reason="persistence check")
        kya.save()
        kya.reset()
        kya.load()
        self.assertEqual(kya.tombstones_snapshot()["count"], 1)
        reborn = kya.list_from_inbound(_agent("persist-b", acct.address))
        self.assertIn("tombstoned_wallet", reborn.get("identity_risk") or {})


if __name__ == "__main__":
    unittest.main()
