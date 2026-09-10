"""Merkle airdrop quest — proof roundtrip, reject stubs, KYA gate."""
from __future__ import annotations

import os
import tempfile
import unittest

os.environ["SINCOR_DATA_DIR"] = tempfile.mkdtemp(prefix="kya_quest_")

from sincor2.kya.airdrop_quest import AirdropQuest
from sincor2.kya.merkle import MerkleTree, verify_proof
from sincor2.kya import registry as regmod
from sincor2.treasury_hold import standing_order, VAULT_CASH_FLOOR_USDC


class MerkleQuestTests(unittest.TestCase):
    def test_proof_roundtrip(self):
        wallets = [
            "0x1111111111111111111111111111111111111111",
            "0x2222222222222222222222222222222222222222",
            "0x3333333333333333333333333333333333333333",
            "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ]
        tree = MerkleTree(wallets)
        for w in wallets:
            proof = tree.proof(w)
            self.assertIsNotNone(proof)
            self.assertTrue(verify_proof(w, proof or [], tree.hex_root()))
        self.assertFalse(
            verify_proof("0x4444444444444444444444444444444444444444", [], tree.hex_root())
        )

    def test_root_is_order_insensitive(self):
        a = ["0x1111111111111111111111111111111111111111", "0x2222222222222222222222222222222222222222"]
        self.assertEqual(MerkleTree(a).hex_root(), MerkleTree(list(reversed(a))).hex_root())

    def test_claim_requires_verify_and_proof(self):
        q = AirdropQuest()
        wallets = [
            "0x1111111111111111111111111111111111111111",
            "0x2222222222222222222222222222222222222222",
        ]
        q.seed(wallets, source="test")
        reg = regmod.KYARegistry()
        regmod._REG = reg
        listed = reg.list_agent({"agent_id": "bot-1", "wallet": wallets[0]})
        with self.assertRaises(PermissionError):
            q.claim(wallets[0], "bot-1")
        reg.bind("bot-1", wallets[0])
        reg.apply_stake(listed["kya_id"], str(10 * 10**18), "0xstake")
        verified = reg.verify(listed["kya_id"])
        self.assertTrue(verified["verified"])
        rec = q.claim(wallets[0], "bot-1")
        self.assertEqual(rec["status"], "credited_ledger")
        self.assertEqual(rec["reward_axm"], 15)
        again = q.claim(wallets[0], "bot-1")
        self.assertEqual(again["ts"], rec["ts"])
        with self.assertRaises(PermissionError):
            q.claim("0x3333333333333333333333333333333333333333", "bot-1")

    def test_hold_numbers(self):
        order = standing_order()
        self.assertEqual(order["H2_morpho_pct"], 0)
        self.assertEqual(order["H4_vault_before_conversion_pct"], 0)
        self.assertEqual(VAULT_CASH_FLOOR_USDC, 10_000)
        self.assertEqual(order["H1_platform_fee_bps"], 500)
        self.assertFalse(order["H7_execute_live_default"])


if __name__ == "__main__":
    unittest.main()
