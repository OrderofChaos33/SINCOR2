"""KYA v0 tests against inbound fabric agent shape. No Flask, no chain."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("KYA_STORE_PATH", os.path.join(tempfile.gettempdir(), "kya_test_records.json"))

from sincor2 import kya_registry as kya


FABRIC_AGENT = {
    "agent_id": "scout-lead-01",
    "name": "Scout Lead",
    "description": "Inbound fabric shaped agent",
    "version": "0.1.0",
    "capability_tags": ["lead-enrichment"],
    "skills": [{"id": "lead-enrichment", "name": "Lead Enrichment"}],
    "rpc_callback": "https://getsincor.com/api/a2a",
    "wallet": "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
    "chain_id": 8453,
    "sinc_staked": 0,
    "last_heartbeat": 0,
}


class KyaFabricTests(unittest.TestCase):
    def setUp(self):
        kya.reset()
        path = Path(os.environ["KYA_STORE_PATH"])
        if path.exists():
            path.unlink()

    def test_list_from_fabric_shape(self):
        rec = kya.list_from_inbound(FABRIC_AGENT)
        self.assertTrue(rec["kya_id"].startswith("kya_"))
        self.assertEqual(rec["status"], "listed")
        self.assertEqual(rec["agent_id"], "scout-lead-01")
        self.assertEqual(rec["chain_id"], 8453)
        self.assertEqual(rec["agent_wallet"].lower(), FABRIC_AGENT["wallet"].lower())

    def test_hook_listed_never_raises(self):
        rec = kya.hook_listed(FABRIC_AGENT)
        self.assertIsNotNone(rec)
        self.assertIsNone(kya.hook_listed({}))

    def test_bind_stake_verify_needs_heartbeat(self):
        rec = kya.list_from_inbound(FABRIC_AGENT)
        principal = FABRIC_AGENT["wallet"]
        bound = kya.bind(rec["agent_id"], principal, "0xsig", "SINCOR KYA bind", recovered=principal)
        self.assertEqual(bound["status"], "bound")
        staked = kya.apply_stake(rec["kya_id"], str(kya.MIN_STAKE_WEI), "0x" + "ab" * 32)
        self.assertEqual(staked["status"], "staked")
        with self.assertRaises(ValueError):
            kya.verify(rec["kya_id"])
        kya.heartbeat(rec["agent_id"], ok=True)
        verified = kya.verify(rec["kya_id"])
        self.assertEqual(verified["status"], "verified")
        self.assertGreaterEqual(verified["score"], 600)

    def test_revoke_blocks_verify(self):
        rec = kya.list_from_inbound(FABRIC_AGENT)
        kya.revoke(rec["kya_id"], "test")
        with self.assertRaises(ValueError):
            kya.bind(rec["agent_id"], FABRIC_AGENT["wallet"], "0x", "m", recovered=FABRIC_AGENT["wallet"])

    def test_dead_token_not_in_module(self):
        self.assertEqual(kya.AXM.lower(), "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a")
        self.assertNotIn("ff7af6ffca25a9dc0fc990d998acf24cc60b7822", kya.AXM.lower())
