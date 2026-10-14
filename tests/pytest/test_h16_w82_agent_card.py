"""W-82: agent-card poisoning — text sanitization + SSRF-hardened _safe_url.

No Flask, no chain, no real DNS (getaddrinfo is mocked where the DNS
path matters; this VM's DNS is untrustworthy).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from sincor2 import kya_registry as kya
from sincor2.a2a_inbound import _normalize_registration, _safe_url
from sincor2.card_sanitize import (
    DESCRIPTION_MAX_LEN,
    NAME_MAX_LEN,
    host_blocked,
    safe_url,
    sanitize_card_text,
)


def _poisoned_card(**over):
    card = {
        "id": "evil-agent",
        "name": "<script>alert(1)</script>",
        "description": "desc\x00\x01\x1bwith controls",
        "version": "1.0.0",
        "skills": [{"id": "s1", "name": "S1", "tags": ["t1"]}],
    }
    card.update(over)
    return {"agent_card": card}


class SanitizeTextTests(unittest.TestCase):
    def test_script_tags_neutralized(self):
        self.assertNotIn("<", sanitize_card_text("<script>alert(1)</script>", 120))
        self.assertNotIn(">", sanitize_card_text("<script>alert(1)</script>", 120))

    def test_overlong_truncated(self):
        self.assertEqual(len(sanitize_card_text("x" * 300, NAME_MAX_LEN)), NAME_MAX_LEN)

    def test_control_chars_stripped(self):
        out = sanitize_card_text("a\x00b\x01c\x1bd\x7fe\x80f", 120)
        self.assertEqual(out, "abcdef")

    def test_legit_whitespace_kept(self):
        self.assertEqual(sanitize_card_text("a\tb\nc", 120), "a\tb\nc")

    def test_none_and_nonstring(self):
        self.assertEqual(sanitize_card_text(None, 120), "")
        self.assertEqual(sanitize_card_text(123, 120), "123")

    def test_img_onerror_neutralized(self):
        out = sanitize_card_text('<img src=x onerror=alert(1)>', 120)
        self.assertNotIn("<", out)
        self.assertNotIn(">", out)


class SafeUrlTests(unittest.TestCase):
    BLOCKED = [
        "http://127.0.0.1:8545/",
        "http://127.1:3000/",
        "http://localhost/",
        "http://localhost:3000/hook",
        "http://LOCALHOST/",
        "http://10.0.0.1/",
        "http://172.16.5.4/",
        "http://172.31.255.255/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[::1]:8080/x",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
        "ftp://example.com/",
        "javascript:alert(1)",
        "http://user:pass@example.com/",
        "http://",
        "",
        None,
    ]
    ALLOWED = [
        "https://8.8.8.8/",
        "https://93.184.216.34/callback",
        "http://93.184.216.34:8080/hook",
    ]

    def test_blocked_hosts_rejected(self):
        for url in self.BLOCKED:
            with self.subTest(url=url):
                self.assertEqual(safe_url(url), "", f"should reject {url!r}")
                self.assertEqual(_safe_url(url), "", f"wrapper should reject {url!r}")

    def test_public_hosts_accepted(self):
        for url in self.ALLOWED:
            with self.subTest(url=url):
                self.assertEqual(safe_url(url), url)
                self.assertEqual(_safe_url(url), url)

    def test_host_blocked_direct(self):
        for h in ["127.0.0.1", "localhost", "x.localhost", "10.1.2.3",
                  "169.254.169.254", "::1", "0.0.0.0"]:
            with self.subTest(host=h):
                self.assertTrue(host_blocked(h))
        self.assertFalse(host_blocked("93.184.216.34"))

    def test_dns_rebinding_naive_case_blocked(self):
        # Attacker domain resolving to the metadata IP must be rejected.
        fake = [("AF_INET", 0, 0, "", ("169.254.169.254", 0))]
        with mock.patch("sincor2.card_sanitize.socket.getaddrinfo", return_value=fake):
            self.assertTrue(host_blocked("evil.example.com"))
            self.assertEqual(safe_url("http://evil.example.com/"), "")

    def test_dns_public_resolution_allowed(self):
        fake = [("AF_INET", 0, 0, "", ("93.184.216.34", 0))]
        with mock.patch("sincor2.card_sanitize.socket.getaddrinfo", return_value=fake):
            self.assertFalse(host_blocked("agent.example.com"))
            self.assertEqual(safe_url("https://agent.example.com/cb"),
                             "https://agent.example.com/cb")

    def test_dns_failure_fails_open_documented(self):
        with mock.patch("sincor2.card_sanitize.socket.getaddrinfo",
                         side_effect=OSError("no dns")):
            self.assertFalse(host_blocked("unresolvable.invalid"))
            self.assertEqual(safe_url("https://unresolvable.invalid/"),
                             "https://unresolvable.invalid/")

    def test_rebinding_toctou_residual_documented(self):
        # The residual risk must stay documented — not silently assumed away.
        import sincor2.card_sanitize as cs
        self.assertIn("TOCTOU", cs.__doc__)
        self.assertIn("re-validate", cs.__doc__)


class NormalizeRegistrationTests(unittest.TestCase):
    def test_card_path_sanitizes(self):
        body = _poisoned_card()
        body["agent_url"] = "http://169.254.169.254/x"
        parsed = _normalize_registration(body)
        self.assertNotIn("<", parsed["name"])
        self.assertNotIn("\x00", parsed["description"])
        self.assertEqual(parsed["rpc_callback"], "")

    def test_card_path_overlong_description_capped(self):
        parsed = _normalize_registration(_poisoned_card(description="y" * 5000))
        self.assertEqual(len(parsed["description"]), DESCRIPTION_MAX_LEN)

    def test_card_path_legit_public_url_kept(self):
        body = _poisoned_card()
        body["agent_url"] = "https://93.184.216.34/cb"
        parsed = _normalize_registration(body)
        self.assertEqual(parsed["rpc_callback"], "https://93.184.216.34/cb")

    def test_noncard_path_sanitizes(self):
        body = {
            "agent_id": "plain-01",
            "name": "<b>evil</b>\x00",
            "description": "d" * 5000,
            "capability_tags": ["t1"],
            "rpc_callback": "http://127.0.0.1:9999/",
        }
        parsed = _normalize_registration(body)
        self.assertNotIn("<", parsed["name"])
        self.assertNotIn("\x00", parsed["name"])
        self.assertEqual(len(parsed["description"]), DESCRIPTION_MAX_LEN)
        self.assertEqual(parsed["rpc_callback"], "")


class KyaListingTests(unittest.TestCase):
    def setUp(self):
        self._prior = os.environ.get("KYA_STORE_PATH")
        fd, self._store_path = tempfile.mkstemp(prefix="kya_w82_", suffix=".json")
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

    def test_list_from_inbound_sanitizes_name(self):
        agent = {
            "agent_id": "w82-01",
            "name": "<script>alert(1)</script>\x00",
            "version": "9.9.9",
            "skills": [],
            "rpc_callback": "http://10.9.9.9/hook",
            "wallet": "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
        }
        rec = kya.list_from_inbound(agent)
        self.assertNotIn("<", rec["name"])
        self.assertNotIn("\x00", rec["name"])
        self.assertEqual(rec["card_url"], "")

    def test_list_from_inbound_keeps_legit(self):
        agent = {
            "agent_id": "w82-02",
            "name": "Honest Agent",
            "version": "1.2.3",
            "skills": [],
            "rpc_callback": "https://93.184.216.34/hook",
            "wallet": "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
        }
        rec = kya.list_from_inbound(agent)
        self.assertEqual(rec["name"], "Honest Agent")
        self.assertEqual(rec["card_url"], "https://93.184.216.34/hook")


if __name__ == "__main__":
    unittest.main()
