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
    sanitize_skills,
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

    def test_bidi_and_zero_width_stripped(self):
        # Display-spoofing format chars must not survive in a trust directory.
        out = sanitize_card_text("a\u202eb\u200bc", 120)
        self.assertEqual(out, "abc")
        out = sanitize_card_text("x\ufeffy", 120)
        self.assertEqual(out, "xy")
        out = sanitize_card_text("\u2066n\u2069", 120)
        self.assertEqual(out, "n")

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

    def test_ipv4_mapped_ipv6_rejected(self):
        # W-82 rework: ipaddress containment is version-strict, so
        # ::ffff:127.0.0.1 was NOT "in" 127.0.0.0/8 and slipped through.
        # On dual-stack hosts these connect to loopback/private/metadata.
        for url in [
            "http://[::ffff:127.0.0.1]/",
            "http://[::ffff:10.0.0.1]/",
            "http://[::ffff:169.254.169.254]/",
        ]:
            with self.subTest(url=url):
                self.assertEqual(safe_url(url), "", f"should reject {url!r}")
                self.assertEqual(_safe_url(url), "", f"wrapper should reject {url!r}")
        # Direct host checks, incl. a 6to4 form embedding 10.0.0.1.
        for h in ["::ffff:127.0.0.1", "::ffff:192.168.1.1", "2002:0a00:0001::1"]:
            with self.subTest(host=h):
                self.assertTrue(host_blocked(h))
        # Plain public v6 still passes.
        self.assertEqual(safe_url("http://[2606:2800:220:1:248:1893:25c8:1946]/"), "http://[2606:2800:220:1:248:1893:25c8:1946]/")

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


class SkillSanitizationTests(unittest.TestCase):
    """W-82 rework: skill id/name/tags were stored raw in three sinks."""

    POISON = [
        {"id": "<script>alert(1)</script>", "name": "<img src=x onerror=alert(1)>",
         "tags": ["<b>bold</b>", "ok-tag"]},
    ]

    def _assert_skills_clean(self, skills):
        self.assertTrue(skills, "skills must survive sanitization")
        for s in skills:
            for field in ("id", "name"):
                v = s.get(field, "")
                self.assertNotIn("<", v, f"poison in skill {field}: {v!r}")
                self.assertNotIn(">", v, f"poison in skill {field}: {v!r}")
            for t in s.get("tags", []):
                self.assertNotIn("<", t, f"poison in skill tag: {t!r}")
                self.assertNotIn(">", t, f"poison in skill tag: {t!r}")

    def test_card_path_skills_sanitized(self):
        body = _poisoned_card(skills=self.POISON)
        parsed = _normalize_registration(body)
        self._assert_skills_clean(parsed["skills"])

    def test_flat_body_tags_and_skills_sanitized(self):
        body = {
            "agent_id": "plain-02",
            "name": "Plain",
            "capability_tags": ["<script>alert(1)</script>", "legit-tag"],
        }
        parsed = _normalize_registration(body)
        for t in parsed["capability_tags"]:
            self.assertNotIn("<", t, f"poison in capability_tags: {t!r}")
        self._assert_skills_clean(parsed["skills"])
        self.assertIn("legit-tag", parsed["capability_tags"])

    def test_kya_list_from_inbound_skills_sanitized(self):
        prior = os.environ.get("KYA_STORE_PATH")
        fd, store_path = tempfile.mkstemp(prefix="kya_w82_sk_", suffix=".json")
        os.close(fd)
        os.environ["KYA_STORE_PATH"] = store_path
        try:
            kya.reset()
            if os.path.exists(store_path):
                os.unlink(store_path)
            agent = {
                "agent_id": "w82-sk",
                "name": "Skill Poison",
                "skills": self.POISON,
                "wallet": "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
            }
            rec = kya.list_from_inbound(agent)
            self._assert_skills_clean(rec["skills"])
            for sid in rec["scope"]["allowed_skills"]:
                self.assertNotIn("<", sid, f"poison in allowed_skills: {sid!r}")
        finally:
            if os.path.exists(store_path):
                os.unlink(store_path)
            if prior is None:
                os.environ.pop("KYA_STORE_PATH", None)
            else:
                os.environ["KYA_STORE_PATH"] = prior


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


def _scan_no_markup(testcase, obj, path="root"):
    """Recursively assert no <> survives in any string leaf OR dict key."""
    if isinstance(obj, str):
        testcase.assertNotIn("<", obj, f"markup at {path}: {obj!r}")
        testcase.assertNotIn(">", obj, f"markup at {path}: {obj!r}")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _scan_no_markup(testcase, k, f"{path}.key")
            _scan_no_markup(testcase, v, f"{path}[{k!r}]")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _scan_no_markup(testcase, v, f"{path}[{i}]")


class Nat64Tests(unittest.TestCase):
    """Reviewer non-blocking rec: block the NAT64 well-known prefix."""

    def test_nat64_prefix_blocked(self):
        # 64:ff9b::/96 embeds an IPv4 address a NAT64 gateway translates;
        # 64:ff9b::7f00:1 would reach 127.0.0.1 through NAT64.
        self.assertTrue(host_blocked("64:ff9b::7f00:1"))
        self.assertTrue(host_blocked("64:ff9b::a9fe:a9fe"))
        self.assertEqual(safe_url("http://[64:ff9b::7f00:1]/"), "")
        # sanity: a genuine public v6 address still passes
        self.assertFalse(host_blocked("2606:4700:4700::1111"))


class SkillSchemaExhaustivenessTests(unittest.TestCase):
    """Round 3: stop whack-a-mole. sanitize_skills must cover the WHOLE
    AgentSkill schema — description, examples, modes, JSON-Schema leaves,
    unknown/future fields — not just id/name/tags.

    test_every_schema_field_sanitized walks AgentSkill's fields
    dynamically: a new str-ish field added to the schema tomorrow is
    automatically poisoned and fails unless the sanitizer handles it.
    """

    @staticmethod
    def _camel(snake):
        parts = snake.split("_")
        return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])

    @staticmethod
    def _poison_for(ann):
        import typing
        origin = typing.get_origin(ann)
        args = typing.get_args(ann)
        if ann is str:
            return "<script>alert(1)</script>"
        if origin is typing.Union:  # Optional[str]
            non_none = [a for a in args if a is not type(None)]
            inner = non_none[0] if non_none else str
            return SkillSchemaExhaustivenessTests._poison_for(inner)
        if origin is list:
            item = args[0] if args else typing.Any
            if item is str or item is typing.Any:
                return ["<img src=x onerror=alert(1)>", "clean"]
            return [SkillSchemaExhaustivenessTests._poison_for(item)]
        if origin is dict:
            return {
                "title": "<script>alert(1)</script>",
                "<b>evilkey</b>": "v",
                "properties": {
                    "<i>prop</i>": {
                        "type": "string",
                        "description": "<u>d</u>",
                    }
                },
            }
        # int and anything else: poison with a string anyway — the
        # sanitizer must never let <> through a string leaf, whatever
        # the declared type.
        return "<script>alert(1)</script>"

    def test_every_schema_field_sanitized(self):
        import typing
        from sincor2.a2a_integration import AgentSkill
        hints = typing.get_type_hints(AgentSkill)
        self.assertIn("description", hints, "schema walk must see description")
        self.assertIn("examples", hints, "schema walk must see examples")
        skill = {}
        for fname, ann in hints.items():
            poison = self._poison_for(ann)
            skill[fname] = poison
            camel = self._camel(fname)
            if camel != fname:
                skill[camel] = poison
        # Served-only keys from AgentSkill.to_dict() that are not dataclass
        # fields (an attacker can also just send them):
        skill["quoteUrl"] = "http://169.254.169.254/latest/meta-data/"
        skill["axmPriceDisplay"] = "<script>alert(1)</script>"
        # Unknown future fields — the default must be SANITIZE, never
        # passthrough:
        skill["mysteryFutureField"] = "<script>alert('future')</script>"
        skill["nested_unknown"] = {"deep": ["<img src=x onerror=alert(9)>"]}
        out = sanitize_skills([skill])
        self.assertEqual(len(out), 1)
        _scan_no_markup(self, out[0])
        # the internal-URL quoteUrl must be killed by the SSRF policy
        self.assertEqual(out[0]["quoteUrl"], "")

    def test_description_examples_through_card_path(self):
        """The round-2 finding, pinned at both levels: description and
        examples must not survive the registration choke point."""
        body = _poisoned_card(skills=[{
            "id": "s1",
            "name": "S1",
            "description": "<script>alert('desc')</script>",
            "examples": ["<b>evil example</b>", "clean example"],
            "inputSchema": {"title": "<i>evil title</i>"},
        }])
        parsed = _normalize_registration(body)
        self.assertTrue(parsed["skills"], "skills must survive")
        _scan_no_markup(self, parsed["skills"])
        # and at the sanitize_skills unit level:
        _scan_no_markup(self, sanitize_skills(body["agent_card"]["skills"]))

    def test_url_typed_skill_field_gets_ssrf_policy(self):
        out = sanitize_skills(
            [{"id": "s1", "name": "S1",
              "quoteUrl": "http://169.254.169.254/x"}])[0]
        self.assertEqual(out["quoteUrl"], "")
        out = sanitize_skills(
            [{"id": "s1", "name": "S1",
              "quoteUrl": "https://93.184.216.34/q"}])[0]
        self.assertEqual(out["quoteUrl"], "https://93.184.216.34/q")

    def test_legit_skill_schema_survives(self):
        skill = {
            "id": "translator",
            "name": "Translator",
            "description": "Translates text between languages.",
            "tags": ["nlp", "translation"],
            "examples": ["Translate 'hello' to French."],
            "inputModes": ["text/plain"],
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
            "axmPriceWei": 1000,
        }
        out = sanitize_skills([skill])[0]
        self.assertEqual(out["description"], "Translates text between languages.")
        self.assertEqual(out["examples"], ["Translate 'hello' to French."])
        self.assertEqual(out["inputSchema"]["properties"]["text"]["type"], "string")
        self.assertEqual(out["axmPriceWei"], 1000)


class NormalizeRecordExhaustivenessTests(unittest.TestCase):
    """The normalized registration record is the stored trust anchor.
    Recursively scans the ENTIRE output record: any string field added in
    the future without sanitization fails this test. agent_id/wallet are
    allowlist-shaped (regex-validated downstream in register_agent_record
    and kya_registry); everything else must be markup-free."""

    def test_record_has_no_passthrough_string_field(self):
        body = _poisoned_card(
            id="<script>alert('id')</script>",
            wallet="0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
            skills=[{
                "id": "s1",
                "name": "S1",
                "description": "<script>d</script>",
                "examples": ["<b>e</b>"],
                "inputSchema": {"title": "<i>t</i>"},
                "futureField": "<u>f</u>",
            }],
        )
        parsed = _normalize_registration(body)
        self.assertNotIn("<", parsed["agent_id"],
                         f"markup in agent_id: {parsed['agent_id']!r}")
        for key, val in parsed.items():
            if key in ("agent_id", "wallet"):
                continue  # allowlist-validated identifier/address fields
            _scan_no_markup(self, val, f"record[{key}]")

    def test_flat_body_record_has_no_passthrough(self):
        body = {
            "agent_id": "<script>alert('x')</script>",
            "name": "<b>evil</b>",
            "description": "<i>evil desc</i>",
            "capability_tags": ["<u>evil-tag</u>"],
            "wallet": "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
        }
        parsed = _normalize_registration(body)
        self.assertNotIn("<", parsed["agent_id"])
        for key, val in parsed.items():
            if key in ("agent_id", "wallet"):
                continue
            _scan_no_markup(self, val, f"record[{key}]")


if __name__ == "__main__":
    unittest.main()
