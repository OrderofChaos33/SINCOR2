"""Live SINC/AXM pointers must be the canonical contracts.

Retired SINC 0x9C8c… is kept on a denylist so a forgotten .env cannot
resurrect it. It must never appear as a live pointer in templates, metadata,
or runtime modules.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sincor2.onchain.constants import (  # noqa: E402
    AXIOM_TOKEN,
    SINC_TOKEN,
    STALE_ADDRESSES,
    is_stale,
)

RETIRED_SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
RETIRED_AXM = "0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822"

# Files that MUST keep the retired hex so env overrides are rejected.
DENYLIST_REQUIRED = {
    "src/sincor2/onchain/constants.py",
    "tests/pytest/test_onchain_constants.py",
    "marketplace/registry_sim.py",
    "CANONICAL_ADDRESSES.md",
}

# Files allowed to mention retired addresses as retired/stale (never as live).
DENYLIST_ALLOWED = DENYLIST_REQUIRED | {
    "tests/pytest/test_live_pointer_hygiene.py",
    "README.md",
    "TOA_4TIER_CODEBASE_MEMORY.md",
    "docs/SINC_INTEGRATION.md",
    "docs/architecture/registry.md",
    "docs/token/README.md",
    "docs/A2A_PRODUCTION_CHECKLIST.md",
    "docs/CEO_TOKEN_PIVOT_AXIOM_2026-08-16.md",
    "onchain/script/Deploy.s.sol",
    "onchain/script/06_DeployAxiom.s.sol",
    "onchain/src/Axiom.sol",
    "contracts/legacy/SINCPlatformAccess.sol",
}

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "out",
    "cache",
    ".mypy_cache",
    ".pytest_cache",
}

SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".zip",
    ".pyc",
    ".so",
    ".bin",
}

RETIRED_SINC_RE = re.compile(r"0x9[Cc]8[Cc]d8[dD]3961[Ff]445[Dd]653713[Dd][Ee]65[Cc]6578[Bb][Ee]11668[eE]7")
RETIRED_AXM_RE = re.compile(r"0x[fF]{2}7[aA][fF]6[fF]{2}ca25[aA]9[Dd][Cc]0[Ff][Cc]990[dD]998[aA]c[fF]24[Cc]{2}60[Bb]7822")
SINC_TRUNC_RE = re.compile(r"0x9[Cc]8[Cc]")

LIVE_SURFACE_PREFIXES = (
    "templates/",
    "static/",
    "scripts/",
    "src/",
    "content/",
)


def _iter_text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_denylist_still_blocks_retired_sinc() -> None:
    assert is_stale(RETIRED_SINC)
    assert RETIRED_SINC.lower() in STALE_ADDRESSES
    assert not is_stale(SINC_TOKEN)
    assert SINC_TOKEN.lower() == "0xe1d836087f6573b665d25ce088793e916d7892f8"


def test_denylist_files_keep_retired_sinc() -> None:
    missing = []
    for rel in sorted(DENYLIST_REQUIRED):
        text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        if not RETIRED_SINC_RE.search(text):
            missing.append(rel)
    assert missing == [], f"denylist lost retired SINC (would allow .env resurrection): {missing}"


def test_retired_sinc_is_not_a_live_pointer() -> None:
    offenders = []
    for path in _iter_text_files():
        rel = _rel(path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not RETIRED_SINC_RE.search(text):
            continue
        if rel in DENYLIST_ALLOWED:
            continue
        offenders.append(rel)
    assert offenders == [], (
        "retired SINC must be replaced with "
        f"{SINC_TOKEN}, not left as a live pointer: {offenders}"
    )


def test_templates_and_metadata_have_no_truncated_retired_sinc() -> None:
    offenders = []
    for path in _iter_text_files():
        rel = _rel(path)
        if not rel.startswith(("templates/", "static/", "scripts/", "content/")):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if SINC_TRUNC_RE.search(text):
            offenders.append(rel)
    assert offenders == [], f"truncated retired SINC still on a live surface: {offenders}"


def test_live_surfaces_do_not_advertise_dead_axm() -> None:
    offenders = []
    for path in _iter_text_files():
        rel = _rel(path)
        if not rel.startswith(LIVE_SURFACE_PREFIXES) and rel not in {
            "onchain/README.md",
            "docs/api/README.md",
            "docs/funding/artifacts/base-builder-grants-self-nomination.md",
            "docs/CEO_DAILY_BRIEF_2026-08-17.md",
        }:
            continue
        if rel in DENYLIST_ALLOWED:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if RETIRED_AXM_RE.search(text):
            offenders.append(rel)
    assert offenders == [], (
        "dead PumpClaw AXM must be replaced with "
        f"{AXIOM_TOKEN} on live surfaces: {offenders}"
    )


def test_token_metadata_json_is_canonical() -> None:
    meta = json.loads((ROOT / "scripts" / "token_metadata.json").read_text(encoding="utf-8"))
    assert meta["address"].lower() == SINC_TOKEN.lower()
    assert meta["decimals"] == 8
    assert meta["totalSupply"] == "1000000000"
    assert SINC_TOKEN in meta["explorer"]
    assert SINC_TOKEN in meta["security"]["sourcify"]
    blob = json.dumps(meta)
    assert RETIRED_SINC.lower() not in blob.lower()


def test_tokenlist_is_canonical() -> None:
    listing = json.loads(
        (ROOT / "static" / "tokenlists" / "sincor.tokenlist.json").read_text(encoding="utf-8")
    )
    tokens = listing["tokens"]
    assert len(tokens) == 1
    assert tokens[0]["address"].lower() == SINC_TOKEN.lower()
    assert tokens[0]["decimals"] == 8


def test_metadata_route_relocks_address_after_json_merge() -> None:
    src = (ROOT / "src" / "sincor2" / "mvp_blueprints" / "sinc.py").read_text(encoding="utf-8")
    update_idx = src.find("payload.update(json.load(f))")
    lock_idx = src.rfind("payload['address'] = SINC_TOKEN")
    assert update_idx != -1, "metadata route must still merge token_metadata.json"
    assert lock_idx > update_idx, (
        "canonical SINC address must be re-applied AFTER JSON merge so a stale "
        "scripts/token_metadata.json cannot clobber /.well-known/sinc-token.json"
    )


def test_live_pages_render_canonical_addresses() -> None:
    pytest.importorskip("flask")
    from flask import Flask, render_template
    from sincor2.social_links import SOCIAL_LINKS

    app = Flask(
        __name__,
        template_folder=str(ROOT / "templates"),
        static_folder=str(ROOT / "static"),
    )

    @app.context_processor
    def _inject_onchain_addresses():
        return {
            "sinc_token": SINC_TOKEN,
            "axiom_token": AXIOM_TOKEN,
            "treasury_address": "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
            "social_links": SOCIAL_LINKS,
            "is_admin": False,
            "admin_username": "",
            "is_customer": False,
            "username": "",
        }

    pages = (
        "home.html",
        "sinc_gateway.html",
        "refer.html",
        "axiom.html",
        "whitepaper.html",
    )
    with app.app_context():
        for name in pages:
            body = render_template(
                name,
                sinc_spot_usd=None,
                sinc_spot_label="$1.50 floor",
                walletconnect_project_id="",
            )
            if name != "axiom.html":
                assert SINC_TOKEN in body, name
            if name in ("home.html", "axiom.html", "whitepaper.html"):
                assert AXIOM_TOKEN.lower() in body.lower(), name
            assert RETIRED_SINC.lower() not in body.lower(), name
            assert RETIRED_AXM.lower() not in body.lower(), name
            assert "0x9C8c" not in body, name
            assert "0x9c8c" not in body, name
