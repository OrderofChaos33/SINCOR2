"""Cleanup wave 2, Finding A: the $0.15 / $150M FDV claim class is purged.

The live GET /api/price/official endpoint used to assert an official SINC
price ("Official SINC price $0.15 ($150M FDV / 1B tokens)") with payload
fields display_price_usd / official_floor_usd / hook_floor_usd /
minimum_buy_usd_per_sinc / spot_usd all 0.15, sourced from
SINC_FLOOR_USD / SINC_FDV_AT_FLOOR_USD constants and copy in six modules.
These tests pin:

  * the endpoint and the payload builder publish NO price-assertion
    fields and no price/floor/FDV claim strings, while keeping factual
    fields (fixed supply, contract address);
  * the price constants are gone from onchain.constants and the catalog
    role copy is clean;
  * the six cleaned modules' source and drafted copy carry none of the
    banned claim strings;
  * claim_scan covers the six modules, catches a planted claim, and
    passes over the cleaned tree with zero false positives.
"""
from __future__ import annotations

import json
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SIX_MODULES = [
    os.path.join("src", "sincor2", "mvp_blueprints", "sinc.py"),
    os.path.join("src", "sincor2", "onchain", "constants.py"),
    os.path.join("src", "sincor2", "value_engine.py"),
    os.path.join("launch_content_engine", "onchain_stats.py"),
    os.path.join("launch_content_engine", "agent_spotlight.py"),
    os.path.join("launch_content_engine", "seo_compare_pipeline.py"),
]

BANNED_KEYS = {
    "display_price_usd",
    "official_floor_usd",
    "hook_floor_usd",
    "minimum_buy_usd_per_sinc",
    "spot_usd",
    "price_usd",
}

BANNED_STRINGS = ["$0.15", "$150M", "FDV", "official price"]


def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_keys(v)


def _assert_payload_clean(payload):
    blob = json.dumps(payload)
    low = blob.lower()
    keys = set(_all_keys(payload))
    assert not (keys & BANNED_KEYS), keys & BANNED_KEYS
    assert not any("floor" in k for k in keys), keys
    for banned in BANNED_STRINGS:
        assert banned.lower() not in low, f"banned string in payload: {banned}"
    assert not re.search(r"\bfdv\b", low)
    assert "0.15" not in blob


class TestOfficialPriceEndpoint:
    def test_endpoint_publishes_no_price_assertion(self):
        pytest.importorskip("flask")
        from sincor2.mvp_app import app

        client = app.test_client()
        resp = client.get("/api/price/official")
        assert resp.status_code == 200, resp.data
        payload = resp.get_json() or {}
        _assert_payload_clean(payload)
        # Genuinely factual fields survive.
        token = payload.get("token") or {}
        assert token.get("total_supply") == 1_000_000_000
        assert token.get("address") == "0xe1D836087F6573b665d25CE088793E916D7892f8"
        assert payload.get("buy_url") == "https://getsincor.com/buy"

    def test_payload_builder_ignores_legacy_price_fields(self, monkeypatch):
        from sincor2.onchain import live_snapshot
        from launch_content_engine.onchain_stats import build_official_price_payload

        fake = {
            "verified_at": "2026-09-19T14:20:00Z",
            "sinc": {"holders": 3455, "transfers": 0},
            "axiom": {"holders": 3354, "transfers": 3},
        }
        monkeypatch.setattr(
            live_snapshot, "fetch_live_onchain", lambda force=False: fake)
        payload = build_official_price_payload({
            "official_floor_usd": 0.15,
            "hook_floor_usd": 0.15,
            "spot_usd": 0.15,
            "eth_usd": 3000,
            "price_note": "SINC is the platform access token with 1B fixed supply.",
            "sinc_token": "0xe1D836087F6573b665d25CE088793E916D7892f8",
            "sinc_total_supply": 1_000_000_000,
            "rogue_v2_pair": "0x85372932f9b151a076815d92cf71a97980ffd667",
        })
        _assert_payload_clean(payload)
        assert payload["token"]["total_supply"] == 1_000_000_000
        assert payload["sinc_holders"] == 3455

    def test_fetch_stats_has_no_price_fields(self, monkeypatch):
        from launch_content_engine import onchain_stats

        monkeypatch.setattr(onchain_stats, "_safe_call", lambda *a, **k: 0.0)
        stats = onchain_stats.fetch_stats()
        _assert_payload_clean(stats)
        assert stats["sinc_total_supply"] == 1_000_000_000
        assert "fixed supply" in stats["price_note"]


class TestConstantsPurged:
    def test_price_constants_removed(self):
        from sincor2.onchain import constants

        assert not hasattr(constants, "SINC_FLOOR_USD")
        assert not hasattr(constants, "SINC_FDV_AT_FLOOR_USD")
        assert constants.SINC_TOTAL_SUPPLY == 1_000_000_000

    def test_catalog_role_copy_clean(self):
        from sincor2.onchain import constants

        role = constants.catalog()["sinc"]["role"]
        assert "$0.15" not in role
        assert "floor" not in role.lower()

    def test_onchain_stats_defines_no_floor_constant(self):
        from launch_content_engine import onchain_stats

        assert not hasattr(onchain_stats, "SINC_FLOOR_USD")


class TestModuleCopyClean:
    def test_six_modules_have_no_banned_strings(self):
        for rel in SIX_MODULES:
            text = open(os.path.join(REPO_ROOT, rel), encoding="utf-8").read()
            low = text.lower()
            for banned in BANNED_STRINGS:
                assert banned.lower() not in low, f"{rel}: {banned}"
            assert "sinc_floor_usd" not in low, rel
            assert "sinc_fdv" not in low, rel

    def test_drafted_copy_is_clean(self, monkeypatch):
        from launch_content_engine import agent_spotlight, seo_compare_pipeline
        from launch_content_engine import onchain_stats

        _, referral = agent_spotlight.draft_referral_cta()
        texts = [referral]
        texts += [c["body"] for c in seo_compare_pipeline.COMPARISONS]
        monkeypatch.setattr(onchain_stats, "fetch_stats", lambda: {
            "sinc_in_hook_pm_m": 20.0,
            "sinc_in_safe": 1000.0,
            "sinc_token": "0xe1D836087F6573b665d25CE088793E916D7892f8",
            "buy_url": "https://getsincor.com/buy",
        })
        texts.append(onchain_stats.draft_post())
        for text in texts:
            low = text.lower()
            for banned in BANNED_STRINGS:
                assert banned.lower() not in low, text
            assert not re.search(r"\bfloor\b", low), text

    def test_value_engine_social_pack_is_clean(self, monkeypatch):
        from sincor2 import value_engine

        monkeypatch.setattr(value_engine, "fetch_value_summary", lambda: {
            "sinc_in_hook_pm_m": 20.0,
        })
        pack = value_engine.social_pack()
        for key in ("twitter", "telegram", "farcaster"):
            low = pack[key].lower()
            for banned in BANNED_STRINGS:
                assert banned.lower() not in low, pack[key]
            assert not re.search(r"\bfloor\b", low), pack[key]


class TestClaimScanCoverage:
    def test_six_modules_are_scanned_sources(self):
        from sincor2 import claim_scan

        files = claim_scan.source_files(REPO_ROOT)
        for rel in SIX_MODULES:
            assert os.path.join(REPO_ROOT, rel) in files, rel

    def test_new_patterns_registered(self):
        from sincor2 import claim_scan

        assert "fdv" in claim_scan.FORBIDDEN_PATTERNS
        assert "price-floor" in claim_scan.FORBIDDEN_PATTERNS

    def test_cleaned_tree_has_no_hits_in_six_modules(self):
        from sincor2 import claim_scan

        hits = [h for h in claim_scan.scan(REPO_ROOT)
                if h["file"] in SIX_MODULES]
        assert hits == [], hits

    def test_no_new_pattern_hits_anywhere(self):
        from sincor2 import claim_scan

        hits = [h for h in claim_scan.scan(REPO_ROOT)
                if h["pattern"] in ("fdv", "price-floor")]
        assert hits == [], hits

    def test_scanner_catches_planted_claim(self, tmp_path):
        from sincor2 import claim_scan

        rel = os.path.join("launch_content_engine", "onchain_stats.py")
        planted = tmp_path / rel
        planted.parent.mkdir(parents=True)
        planted.write_text(
            'NOTE = "Official SINC price $0.15 ($150M FDV / 1B tokens)."\n',
            encoding="utf-8",
        )
        hits = [h for h in claim_scan.scan(str(tmp_path))
                if h["file"] == rel]
        assert hits, "planted token-price claim was not caught"
        patterns = {h["pattern"] for h in hits}
        assert "price-floor" in patterns, patterns
        assert "fdv" in patterns, patterns
