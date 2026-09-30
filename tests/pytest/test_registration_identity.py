"""First-registration squatting control (wave 32): verified wallet binding,
protected names, ownership on re-registration, and signed transfers.

Exercises the genuine EIP-191 path (real eth_account signatures), not mocks.
Fabric persistence is redirected to a temp SINCOR_DATA_DIR; no chain, no server.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest

from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import a2a_identity as ident
from sincor2 import a2a_inbound_ext as ext
from sincor2.a2a_inbound import reset_fabric

_OWNER = Account.create()
_OWNER_WALLET = _OWNER.address
_ATTACKER = Account.create()
_ATTACKER_WALLET = _ATTACKER.address


def _sign(text: str, signer=_OWNER) -> str:
    sig = signer.sign_message(encode_defunct(text=text)).signature.hex()
    return sig if sig.startswith("0x") else "0x" + sig


def _body(agent_id: str, **extra) -> dict:
    base = {
        "agent_id": agent_id,
        "name": agent_id,
        "capability_tags": ["lead-enrichment"],
        "wallet": _OWNER_WALLET,
    }
    base.update(extra)
    return base


def _proof_body(agent_id: str, signer=_OWNER, wallet=None) -> dict:
    ts = int(time.time() * 1000)
    wallet = wallet or signer.address
    sig = _sign(ident.register_message(agent_id, ts), signer)
    return _body(agent_id,
                 registration_signature=sig,
                 registration_wallet=wallet,
                 registration_ts=ts)


class RegistrationIdentityTests(unittest.TestCase):
    def setUp(self):
        self._prior_data = os.environ.get("SINCOR_DATA_DIR")
        self._prior_req = os.environ.get("SINCOR_REGISTRATION_PROOF_REQUIRED")
        self._data_dir = tempfile.mkdtemp(prefix="reg_identity_")
        os.environ["SINCOR_DATA_DIR"] = self._data_dir
        os.environ.pop("SINCOR_REGISTRATION_PROOF_REQUIRED", None)
        reset_fabric()

    def tearDown(self):
        shutil.rmtree(self._data_dir, ignore_errors=True)
        if self._prior_data is None:
            os.environ.pop("SINCOR_DATA_DIR", None)
        else:
            os.environ["SINCOR_DATA_DIR"] = self._prior_data
        if self._prior_req is None:
            os.environ.pop("SINCOR_REGISTRATION_PROOF_REQUIRED", None)
        else:
            os.environ["SINCOR_REGISTRATION_PROOF_REQUIRED"] = self._prior_req
        reset_fabric()

    # -- acceptance: verified-wallet claim succeeds -------------------------
    def test_verified_claim_binds_owner(self):
        snap = ext.register_agent_record(_proof_body("reg-verified-01"))
        self.assertEqual(snap["identity"], "verified")
        self.assertEqual(snap["owner_wallet"], _OWNER_WALLET.lower())

    # -- acceptance: unverified claim is marked untrusted, not silently trusted
    def test_unverified_claim_marked_untrusted(self):
        snap = ext.register_agent_record(_body("reg-unverified-01"))
        self.assertEqual(snap["identity"], "unverified")
        self.assertEqual(snap["owner_wallet"], "")

    def test_unverified_claim_rejected_when_proof_required(self):
        os.environ["SINCOR_REGISTRATION_PROOF_REQUIRED"] = "1"
        with self.assertRaises(PermissionError):
            ext.register_agent_record(_body("reg-strict-01"))

    # -- acceptance: protected names blocked --------------------------------
    def test_protected_names_blocked(self):
        for name in ("sincor", "admin", "treasury", "sincor-official",
                     "SINCOR-support", "axiom", "root"):
            with self.assertRaises(ValueError, msg=name):
                ext.register_agent_record(_proof_body(name))
            with self.assertRaises(ValueError, msg=name):
                ext.register_agent_record(_body(name))

    def test_ordinary_names_allowed(self):
        snap = ext.register_agent_record(_proof_body("my-cool-agent-01"))
        self.assertEqual(snap["agent_id"], "my-cool-agent-01")

    # -- acceptance: duplicate claim by different wallet rejected ------------
    def test_owner_re_registration_requires_owner_signature(self):
        ext.register_agent_record(_proof_body("reg-owned-01"))
        attacker_body = _proof_body("reg-owned-01", signer=_ATTACKER)
        with self.assertRaises(PermissionError):
            ext.register_agent_record(attacker_body)
        # And a bare (unsigned) re-registration is rejected too.
        with self.assertRaises(PermissionError):
            ext.register_agent_record(_body("reg-owned-01"))

    def test_owner_can_re_register_with_fresh_signature(self):
        ext.register_agent_record(_proof_body("reg-rereg-01"))
        snap = ext.register_agent_record(
            _proof_body("reg-rereg-01", wallet=_OWNER_WALLET))
        self.assertEqual(snap["identity"], "verified")
        self.assertEqual(snap["owner_wallet"], _OWNER_WALLET.lower())

    def test_wallet_claim_mismatch_rejected(self):
        # Signature from the attacker, wallet claim of the owner: the
        # mandatory-claim check must refuse to mint the owner's identity.
        ts = int(time.time() * 1000)
        sig = _sign(ident.register_message("reg-mismatch-01", ts), _ATTACKER)
        body = _body("reg-mismatch-01", registration_signature=sig,
                     registration_wallet=_OWNER_WALLET, registration_ts=ts)
        snap = ext.register_agent_record(body)
        self.assertEqual(snap["identity"], "unverified")
        self.assertEqual(snap["owner_wallet"], "")

    def test_stale_timestamp_rejected(self):
        ts = int(time.time() * 1000) - 60 * 60 * 1000  # 1h old
        sig = _sign(ident.register_message("reg-stale-01", ts), _OWNER)
        body = _body("reg-stale-01", registration_signature=sig,
                     registration_wallet=_OWNER_WALLET, registration_ts=ts)
        snap = ext.register_agent_record(body)
        self.assertEqual(snap["identity"], "unverified")

    # -- acceptance: grandfathered IDs still work ----------------------------
    def test_grandfathered_record_keeps_working_unsigned(self):
        snap = ext.register_agent_record(_body("reg-grandfather-01"))
        self.assertEqual(snap["identity"], "unverified")
        # Re-registration without a proof still works (grace period).
        snap2 = ext.register_agent_record(
            _body("reg-grandfather-01", description="updated"))
        self.assertEqual(snap2["description"], "updated")
        self.assertEqual(snap2["identity"], "unverified")

    def test_grandfathered_record_claims_ownership_with_proof(self):
        ext.register_agent_record(_body("reg-claim-01"))
        snap = ext.register_agent_record(_proof_body("reg-claim-01"))
        self.assertEqual(snap["identity"], "verified")
        self.assertEqual(snap["owner_wallet"], _OWNER_WALLET.lower())
        # Now the attacker is locked out.
        with self.assertRaises(PermissionError):
            ext.register_agent_record(_proof_body("reg-claim-01", signer=_ATTACKER))

    # -- transfer policy ------------------------------------------------------
    def _transfer_sig(self, agent_id, new_wallet, signer, ts=None):
        ts = ts if ts is not None else int(time.time() * 1000)
        sig = _sign(ident.transfer_message(agent_id, new_wallet, ts), signer)
        return sig, ts

    def test_transfer_by_owner_succeeds(self):
        ext.register_agent_record(_proof_body("reg-xfer-01"))
        new_wallet = Account.create().address
        sig, ts = self._transfer_sig("reg-xfer-01", new_wallet, _OWNER)
        snap = ext.transfer_agent_record({
            "agent_id": "reg-xfer-01", "new_wallet": new_wallet,
            "transfer_signature": sig, "transfer_ts": ts})
        self.assertEqual(snap["owner_wallet"], new_wallet.lower())
        self.assertEqual(snap["identity"], "verified")

    def test_transfer_by_non_owner_rejected(self):
        ext.register_agent_record(_proof_body("reg-xfer-02"))
        new_wallet = Account.create().address
        sig, ts = self._transfer_sig("reg-xfer-02", new_wallet, _ATTACKER)
        with self.assertRaises(PermissionError):
            ext.transfer_agent_record({
                "agent_id": "reg-xfer-02", "new_wallet": new_wallet,
                "transfer_signature": sig, "transfer_ts": ts})

    def test_transfer_without_owner_rejected(self):
        ext.register_agent_record(_body("reg-xfer-03"))  # grandfathered, no owner
        new_wallet = Account.create().address
        sig, ts = self._transfer_sig("reg-xfer-03", new_wallet, _OWNER)
        with self.assertRaises(PermissionError):
            ext.transfer_agent_record({
                "agent_id": "reg-xfer-03", "new_wallet": new_wallet,
                "transfer_signature": sig, "transfer_ts": ts})

    def test_transfer_unknown_agent(self):
        with self.assertRaises(KeyError):
            ext.transfer_agent_record({"agent_id": "no-such-agent",
                                       "new_wallet": _OWNER_WALLET})

    def test_old_owner_locked_out_after_transfer(self):
        ext.register_agent_record(_proof_body("reg-xfer-04"))
        new_acct = Account.create()
        sig, ts = self._transfer_sig("reg-xfer-04", new_acct.address, _OWNER)
        ext.transfer_agent_record({
            "agent_id": "reg-xfer-04", "new_wallet": new_acct.address,
            "transfer_signature": sig, "transfer_ts": ts})
        # Old owner's signature no longer controls the record.
        with self.assertRaises(PermissionError):
            ext.register_agent_record(_proof_body("reg-xfer-04", signer=_OWNER))
        # New owner can re-register.
        snap = ext.register_agent_record(_proof_body("reg-xfer-04", signer=new_acct))
        self.assertEqual(snap["owner_wallet"], new_acct.address.lower())

    # -- HTTP route surface ----------------------------------------------------
    def test_register_route_returns_identity_fields(self):
        from flask import Flask
        app = Flask(__name__)
        ext.mount(app)
        client = app.test_client()
        r = client.post("/v1/a2a/register", json=_proof_body("reg-http-01"))
        self.assertEqual(r.status_code, 201)
        payload = r.get_json()
        self.assertEqual(payload["identity"], "verified")
        self.assertEqual(payload["owner_wallet"], _OWNER_WALLET.lower())

    def test_register_route_403_on_hijack(self):
        from flask import Flask
        app = Flask(__name__)
        ext.mount(app)
        client = app.test_client()
        client.post("/v1/a2a/register", json=_proof_body("reg-http-02"))
        r = client.post("/v1/a2a/register",
                        json=_proof_body("reg-http-02", signer=_ATTACKER))
        self.assertEqual(r.status_code, 403)

    def test_register_route_400_on_protected(self):
        from flask import Flask
        app = Flask(__name__)
        ext.mount(app)
        client = app.test_client()
        r = client.post("/v1/a2a/register", json=_body("sincor"))
        self.assertEqual(r.status_code, 400)

    def test_transfer_route(self):
        from flask import Flask
        app = Flask(__name__)
        ext.mount(app)
        client = app.test_client()
        client.post("/v1/a2a/register", json=_proof_body("reg-http-03"))
        new_wallet = Account.create().address
        sig, ts = self._transfer_sig("reg-http-03", new_wallet, _OWNER)
        r = client.post("/v1/a2a/transfer", json={
            "agent_id": "reg-http-03", "new_wallet": new_wallet,
            "transfer_signature": sig, "transfer_ts": ts})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["owner_wallet"], new_wallet.lower())


if __name__ == "__main__":
    unittest.main()
