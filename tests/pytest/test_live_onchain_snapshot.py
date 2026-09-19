"""Official money surface must not broadcast lock-date dead holder counts."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANON = json.loads((ROOT / "TOKEN_CANON.json").read_text(encoding="utf-8"))


def test_canon_lock_no_longer_claims_one_holder() -> None:
    live = CANON["sinc"]["onchain_as_of_lock"]
    assert int(live["holders"]) >= 1000
    assert "verified_at" in live
    assert CANON["axiom"]["source_verified_basescan"] is False
    axm = CANON["axiom"]["onchain_live"]
    assert int(axm["holders"]) >= 1000
    assert CANON["lock_version"].startswith("2026-09-19")
    assert CANON.get("verified_at")


def test_locked_snapshot_fallback_reads_canon() -> None:
    from sincor2.onchain.live_snapshot import locked_snapshot

    snap = locked_snapshot()
    assert snap["sinc"]["holders"] >= 1000
    assert snap["axiom"]["holders"] >= 1000


def test_attach_official_price_fields_uses_injected_snapshot(monkeypatch) -> None:
    from sincor2.onchain import live_snapshot

    fake = {
        "chain": "base",
        "chain_id": 8453,
        "verified_at": "2026-09-19T14:20:00Z",
        "sinc": {"address": CANON["sinc"]["address"], "holders": 3455, "transfers": 0, "stale": False},
        "axiom": {"address": CANON["axiom"]["address"], "holders": 3354, "transfers": 3, "stale": False},
    }
    monkeypatch.setattr(live_snapshot, "fetch_live_onchain", lambda force=False: fake)
    payload = {"official_floor_usd": 0.15}
    live_snapshot.attach_official_price_fields(payload)
    assert payload["sinc_holders"] == 3455
    assert payload["onchain"]["axiom"]["holders"] == 3354
    assert payload["onchain_verified_at"] == "2026-09-19T14:20:00Z"


def test_build_official_price_payload_includes_onchain(monkeypatch) -> None:
    from sincor2.onchain import live_snapshot
    from launch_content_engine.onchain_stats import build_official_price_payload

    fake = {
        "verified_at": "2026-09-19T14:20:00Z",
        "sinc": {"holders": 3455, "transfers": 0},
        "axiom": {"holders": 3354, "transfers": 3},
    }
    monkeypatch.setattr(live_snapshot, "fetch_live_onchain", lambda force=False: fake)
    payload = build_official_price_payload({"official_floor_usd": 0.15, "eth_usd": 3000, "price_note": "test"})
    assert payload["official_floor_usd"] == 0.15
    assert payload["sinc_holders"] == 3455
    assert payload["onchain"]["axiom"]["holders"] == 3354


def test_public_docs_do_not_freeze_one_holder_as_current() -> None:
    md = (ROOT / "TOKEN_CANON.md").read_text(encoding="utf-8")
    html = (ROOT / "templates/token_canon.html").read_text(encoding="utf-8")
    assert "3,455 holders" in md
    assert "explorer: 1 holder, 0 transfers" not in html
