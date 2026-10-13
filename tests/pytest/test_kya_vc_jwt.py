"""KYA JWT verifiable-credential tests. No Flask, no chain."""
from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import unittest
import uuid

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from sincor2 import kya_registry
from sincor2.kya import credentials
from sincor2.kya.credentials import (
    CredentialError,
    InvalidCredential,
    issue_credential,
    verify_credential,
)


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _test_issuer_pem() -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _agent(agent_id: str, wallet: str) -> dict:
    return {
        "agent_id": agent_id,
        "name": f"Agent {agent_id}",
        "description": "vc test agent",
        "version": "0.1.0",
        "capability_tags": ["probe"],
        "skills": [{"id": "probe", "name": "Probe"}],
        "rpc_callback": "https://getsincor.com/api/a2a",
        "wallet": wallet,
        "chain_id": 8453,
    }


WALLET = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"


class KyaVcJwtTests(unittest.TestCase):
    def setUp(self):
        self._prior_store = os.environ.get("KYA_STORE_PATH")
        self._prior_key = os.environ.get("KYA_VC_ISSUER_KEY")
        self._prior_kid = os.environ.get("KYA_VC_KID")
        fd, self._store_path = tempfile.mkstemp(prefix="kya_vc_test_", suffix=".json")
        os.close(fd)
        os.environ["KYA_STORE_PATH"] = self._store_path
        self._pem = _test_issuer_pem()
        os.environ["KYA_VC_ISSUER_KEY"] = self._pem.decode("utf-8")
        os.environ.pop("KYA_VC_KID", None)
        kya_registry.reset()
        credentials._reset_issuer_state()
        if os.path.exists(self._store_path):
            os.unlink(self._store_path)

    def tearDown(self):
        credentials._reset_issuer_state()
        kya_registry.reset()
        if os.path.exists(self._store_path):
            os.unlink(self._store_path)
        for name, prior in (
            ("KYA_STORE_PATH", self._prior_store),
            ("KYA_VC_ISSUER_KEY", self._prior_key),
            ("KYA_VC_KID", self._prior_kid),
        ):
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior

    def _listed(self, agent_id: str, wallet: str = WALLET) -> dict:
        return kya_registry.list_from_inbound(_agent(agent_id, wallet))

    def _kid_of(self, token: str) -> str:
        return jwt.get_unverified_header(token)["kid"]

    def _craft(self, kid: str, **overrides) -> str:
        """Mint a hand-built token with the test issuer key (adversarial)."""
        now = int(time.time())
        payload = {
            "iss": "sincor-kya",
            "sub": overrides.pop("sub", "kya_test"),
            "agent_id": overrides.pop("agent_id", "vc-test-01"),
            "wallet": WALLET.lower(),
            "iat": now,
            "exp": now + 3600,
            "jti": uuid.uuid4().hex,
        }
        payload.update(overrides)
        return jwt.encode(
            payload, self._pem, algorithm="ES256", headers={"kid": kid, "typ": "JWT"}
        )

    # 1. roundtrip ---------------------------------------------------------
    def test_issue_verify_roundtrip(self):
        rec = self._listed("vc-test-01")
        token = issue_credential("vc-test-01", WALLET, rec["kya_id"])
        self.assertIsInstance(token, str)
        self.assertEqual(len(token.split(".")), 3)
        claims = verify_credential(token)
        self.assertEqual(claims["iss"], "sincor-kya")
        self.assertEqual(claims["sub"], rec["kya_id"])
        self.assertEqual(claims["agent_id"], "vc-test-01")
        self.assertEqual(claims["wallet"], WALLET.lower())
        self.assertIn("jti", claims)
        self.assertLessEqual(claims["iat"], int(time.time()))
        self.assertGreater(claims["exp"], claims["iat"])

    # 2. expired -----------------------------------------------------------
    def test_expired_token_rejected(self):
        rec = self._listed("vc-test-02")
        token = issue_credential("vc-test-02", WALLET, rec["kya_id"], ttl_s=-10)
        with self.assertRaises(CredentialError):
            verify_credential(token)

    # 3. tampered payload --------------------------------------------------
    def test_tampered_payload_rejected(self):
        rec = self._listed("vc-test-03")
        token = issue_credential("vc-test-03", WALLET, rec["kya_id"])
        parts = token.split(".")
        p = parts[1]
        p = ("A" if p[0] != "A" else "B") + p[1:]
        tampered = ".".join([parts[0], p, parts[2]])
        with self.assertRaises(CredentialError):
            verify_credential(tampered)

    # 4. wrong issuer ------------------------------------------------------
    def test_wrong_issuer_rejected(self):
        rec = self._listed("vc-test-04")
        token = issue_credential("vc-test-04", WALLET, rec["kya_id"])
        bad = self._craft(self._kid_of(token), sub=rec["kya_id"], iss="evil-issuer")
        with self.assertRaises(CredentialError):
            verify_credential(bad)

    # 5. revoked agent -----------------------------------------------------
    def test_revoked_agent_rejected(self):
        rec = self._listed("vc-test-05")
        token = issue_credential("vc-test-05", WALLET, rec["kya_id"])
        verify_credential(token)  # valid before revocation
        kya_registry.revoke(rec["kya_id"], reason="vc test revoke")
        with self.assertRaises(InvalidCredential):
            verify_credential(token)

    # 6. unknown kya_id ----------------------------------------------------
    def test_unknown_kya_id_rejected(self):
        token = issue_credential("vc-test-06", WALLET, "kya_doesnotexist000")
        with self.assertRaises(InvalidCredential):
            verify_credential(token)

    # 7. rotation ----------------------------------------------------------
    def test_rotated_key_token_verifies_under_old_kid(self):
        rec = self._listed("vc-test-07")
        token_a = issue_credential("vc-test-07", WALLET, rec["kya_id"])
        kid_a = self._kid_of(token_a)

        new_pem = _test_issuer_pem()
        kid_b = credentials.rotate_issuer(new_pem)
        self.assertNotEqual(kid_a, kid_b)

        # old-kid token still verifies during the grace window
        claims = verify_credential(token_a)
        self.assertEqual(claims["sub"], rec["kya_id"])

        # new tokens mint under the new key
        token_b = issue_credential("vc-test-07", WALLET, rec["kya_id"])
        self.assertEqual(self._kid_of(token_b), kid_b)
        verify_credential(token_b)

        # active key cannot be retired; old key can, ending its grace
        self.assertFalse(credentials.retire_key(kid_b))
        self.assertTrue(credentials.retire_key(kid_a))
        with self.assertRaises(CredentialError):
            verify_credential(token_a)
        verify_credential(token_b)  # new key unaffected

    # 8. future iat --------------------------------------------------------
    def test_future_iat_rejected(self):
        rec = self._listed("vc-test-08")
        token = issue_credential("vc-test-08", WALLET, rec["kya_id"])
        now = int(time.time())
        bad = self._craft(
            self._kid_of(token), sub=rec["kya_id"], iat=now + 3600, exp=now + 7200
        )
        with self.assertRaises(CredentialError):
            verify_credential(bad)

    # 9. alg=none ----------------------------------------------------------
    def test_alg_none_rejected(self):
        rec = self._listed("vc-test-09")
        now = int(time.time())
        payload = {
            "iss": "sincor-kya",
            "sub": rec["kya_id"],
            "agent_id": "vc-test-09",
            "wallet": WALLET.lower(),
            "iat": now,
            "exp": now + 3600,
            "jti": uuid.uuid4().hex,
        }
        header = _b64u(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        body = _b64u(json.dumps(payload).encode())
        with self.assertRaises(CredentialError):
            verify_credential(f"{header}.{body}.")

    # 10. wrong-key signature under our kid --------------------------------
    def test_wrong_key_signature_rejected(self):
        rec = self._listed("vc-test-10")
        token = issue_credential("vc-test-10", WALLET, rec["kya_id"])
        attacker_pem = _test_issuer_pem()
        forged = jwt.encode(
            {
                "iss": "sincor-kya",
                "sub": rec["kya_id"],
                "agent_id": "vc-test-10",
                "wallet": WALLET.lower(),
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
                "jti": uuid.uuid4().hex,
            },
            attacker_pem,
            algorithm="ES256",
            headers={"kid": self._kid_of(token), "typ": "JWT"},
        )
        with self.assertRaises(CredentialError):
            verify_credential(forged)

    # 11. ephemeral fallback ------------------------------------------------
    def test_ephemeral_key_when_env_unset(self):
        os.environ.pop("KYA_VC_ISSUER_KEY", None)
        credentials._reset_issuer_state()
        rec = self._listed("vc-test-11")
        token = issue_credential("vc-test-11", WALLET, rec["kya_id"])
        claims = verify_credential(token)  # works inside this process
        self.assertEqual(claims["sub"], rec["kya_id"])


if __name__ == "__main__":
    unittest.main()
