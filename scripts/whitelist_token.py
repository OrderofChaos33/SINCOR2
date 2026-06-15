#!/usr/bin/env python3
"""SINC token whitelist orchestrator — prepare PRs, token lists, and form payloads."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
META_PATH = Path(__file__).resolve().parent / "token_metadata.json"
OUT = ROOT / "tokenlists"
ASSETS = OUT / "assets"
PR = OUT / "pr-packages"
STAGING = OUT / "_staging"

CHAIN_ID = 8453
CHECKSUM = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
LOWER = CHECKSUM.lower()

TARGETS = [
    {
        "id": "balancer",
        "name": "Balancer token list",
        "type": "pr",
        "repo": "https://github.com/balancer/tokenlists.git",
        "path_hint": "src/tokenlists/balancer/tokens/base.ts",
        "docs": "https://github.com/balancer/tokenlists",
        "fee": False,
        "notes": "SINC may already be listed — script checks live file.",
    },
    {
        "id": "superchain",
        "name": "Superchain / Coinbase Wallet token list",
        "type": "pr",
        "repo": "https://github.com/ethereum-optimism/ethereum-optimism.github.io.git",
        "path_hint": "data/SINC/data.json + logo.svg",
        "docs": "https://github.com/ethereum-optimism/ethereum-optimism.github.io",
        "fee": False,
        "notes": "High impact for Base + Coinbase Wallet. Use nobridge:true for native Base token.",
    },
    {
        "id": "trustwallet",
        "name": "Trust Wallet assets",
        "type": "pr",
        "repo": "https://github.com/trustwallet/assets.git",
        "path_hint": f"blockchains/base/assets/{CHECKSUM}/",
        "docs": "https://developer.trustwallet.com/developer/new-asset",
        "fee": True,
        "notes": "Requires BNB/TWT processing fee. Needs 10k+ holders for approval.",
    },
    {
        "id": "cowswap",
        "name": "CoW Swap token list",
        "type": "issue",
        "url": "https://github.com/cowprotocol/token-lists/issues/new?template=1-addTokenForm.yml",
        "docs": "https://github.com/cowprotocol/token-lists",
        "fee": False,
    },
    {
        "id": "cowswap_image",
        "name": "CoW Swap token image only",
        "type": "issue",
        "url": "https://github.com/cowprotocol/token-lists/issues/new?template=2-addImageForm.yml",
        "docs": "https://github.com/cowprotocol/token-lists",
        "fee": False,
    },
    {
        "id": "geckoterminal",
        "name": "GeckoTerminal token info",
        "type": "form",
        "url": "https://www.geckoterminal.com",
        "docs": "https://support.coingecko.com/hc/en-us/articles/7291312302617",
        "fee": False,
        "notes": "Search SINC on Base, then Update token info.",
    },
    {
        "id": "dexscreener",
        "name": "DexScreener enhanced token info",
        "type": "form",
        "url": "https://dexscreener.com/base/0x9C8cd8d3961F445D653713dE65C6578bE11668e7",
        "docs": "https://docs.dexscreener.com/token-listing",
        "fee": True,
        "notes": "Enhanced Token Info is paid; basic indexing is free with liquidity+trades.",
    },
    {
        "id": "coingecko",
        "name": "CoinGecko listing request",
        "type": "form",
        "url": "https://www.coingecko.com/en/coins/new",
        "docs": "https://support.coingecko.com/hc/en-us/articles/7291312302617",
        "fee": False,
        "notes": "Unlocks Rainbow verification path on Base.",
    },
    {
        "id": "coinmarketcap",
        "name": "CoinMarketCap listing request",
        "type": "form",
        "url": "https://coinmarketcap.com/request/",
        "docs": "https://support.coinmarketcap.com/hc/en-us/articles/360043685031",
        "fee": False,
    },
    {
        "id": "dextools",
        "name": "DEXTools token info update",
        "type": "form",
        "url": "https://www.dextools.io/app/en/base/pair-explorer",
        "fee": True,
    },
    {
        "id": "birdeye",
        "name": "Birdeye token info",
        "type": "form",
        "url": "https://birdeye.so/token/0x9C8cd8d3961F445D653713dE65C6578bE11668e7?chain=base",
        "fee": False,
    },
    {
        "id": "defillama",
        "name": "DefiLlama adapter / protocol listing",
        "type": "pr",
        "repo": "https://github.com/DefiLlama/DefiLlama-Adapters.git",
        "docs": "https://docs.llama.fi/list-your-project/submit-a-project",
        "fee": False,
        "notes": "For TVL tracking once pools have measurable liquidity.",
    },
    {
        "id": "sincor_tokenlist",
        "name": "Self-hosted Uniswap token list (MetaMask / Rabby / 1inch import)",
        "type": "host",
        "url": "https://getsincor.com/tokenlists/sincor.tokenlist.json",
        "fee": False,
        "notes": "Served by Flask at /tokenlists/ and /.well-known/tokenlist.json",
    },
    {
        "id": "blockscout",
        "name": "Blockscout verified address + public tag",
        "type": "form",
        "url": "https://base.blockscout.com/my-account/verified-addresses",
        "docs": "https://docs.blockscout.com/using-blockscout/my-account/verified-addresses",
        "fee": False,
        "notes": "Deployer signs to verify ownership → update token icon/name. Also submit public tag.",
    },
    {
        "id": "blockscout_public_tag",
        "name": "Blockscout public tag request",
        "type": "form",
        "url": "https://base.blockscout.com/public-tags/submit",
        "fee": False,
        "notes": "Marks official SINC contract; clears unverified/suspicious UI for explorers.",
    },
    {
        "id": "tkn",
        "name": "Token Name Service (TKN) — Blockscout icon source",
        "type": "form",
        "url": "https://tkn.xyz/token/base/0x9C8cd8d3961F445D653713dE65C6578bE11668e7",
        "docs": "https://docs.tkn.xyz/usage/organizations",
        "fee": False,
        "notes": "Blockscout pulls token metadata from TKN. Submit logo, website, description.",
    },
]

# GitHub raw is what explorers/aggregators trust; getsincor.com mirrors the same file.
PUBLIC_LOGO_URL = "https://raw.githubusercontent.com/OrderofChaos33/SINCOR2/main/static/tokenlists/assets/logo-256.png"
PUBLIC_LOGO_URL_MIRROR = "https://getsincor.com/static/tokenlists/assets/logo-256.png"


def load_meta() -> dict:
    with META_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def ensure_dirs() -> None:
    for d in (OUT, ASSETS, PR, STAGING):
        d.mkdir(parents=True, exist_ok=True)


def resize_logo(meta: dict) -> Path:
    src = ROOT / meta["logoSource"]
    if not src.exists():
        raise FileNotFoundError(f"Logo not found: {src}")
    if Image is None:
        raise RuntimeError("Pillow required: pip install pillow")

    out = ASSETS / "logo-256.png"
    img = Image.open(src).convert("RGBA")
    img = img.resize((256, 256), Image.Resampling.LANCZOS)
    img.save(out, format="PNG", optimize=True)
    shutil.copy2(out, ASSETS / "logo.png")
    return out


def write_svg_logo(meta: dict) -> Path:
    """Minimal SVG for Superchain list (embeds hosted PNG)."""
    out = ASSETS / "logo.svg"
    png_url = PUBLIC_LOGO_URL
    svg = textwrap.dedent(
        f"""\
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
          <image href="{png_url}" width="256" height="256"/>
        </svg>
        """
    )
    out.write_text(svg, encoding="utf-8")
    return out


def uniswap_token_list(meta: dict) -> dict:
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    logo = PUBLIC_LOGO_URL
    return {
        "name": "SINCOR Token List",
        "logoURI": logo,
        "keywords": ["sincor", "sinc", "base", "ai"],
        "timestamp": ts,
        "version": {"major": 1, "minor": 0, "patch": 2},
        "tokens": [
            {
                "chainId": meta["chainId"],
                "address": meta["address"],
                "name": meta["name"],
                "symbol": meta["symbol"],
                "decimals": meta["decimals"],
                "logoURI": logo,
                "tags": meta.get("tags", []),
                "extensions": {
                    "logoURIMirror": PUBLIC_LOGO_URL_MIRROR,
                    "website": meta.get("website", "https://getsincor.com"),
                },
            }
        ],
    }


def write_token_lists(meta: dict) -> None:
    tl = uniswap_token_list(meta)
    paths = [
        OUT / "sincor.tokenlist.json",
        ROOT / "static" / "tokenlists" / "sincor.tokenlist.json",
    ]
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(tl, indent=2) + "\n", encoding="utf-8")


def trustwallet_package(meta: dict) -> None:
    folder = PR / "trustwallet" / "blockchains" / "base" / "assets" / CHECKSUM
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ASSETS / "logo.png", folder / "logo.png")
    info = {
        "name": meta["name"],
        "website": meta["website"],
        "description": meta["descriptionLong"],
        "explorer": meta["explorer"],
        "type": "ERC20",
        "symbol": meta["symbol"],
        "decimals": meta["decimals"],
        "status": "active",
        "id": CHECKSUM,
        "tags": ["defi"],
        "links": [
            {"name": "twitter", "url": meta["twitter"]},
            {"name": "telegram", "url": meta["telegram"]},
            {"name": "github", "url": "https://github.com/getsincor"},
        ],
    }
    (folder / "info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")


def superchain_package(meta: dict) -> None:
    folder = PR / "superchain" / "data" / "SINC"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ASSETS / "logo.svg", folder / "logo.svg")
    data = {
        "name": meta["name"],
        "symbol": meta["symbol"],
        "decimals": meta["decimals"],
        "description": meta["descriptionLong"][:999],
        "website": meta["website"],
        "twitter": "@getsincor",
        "nobridge": True,
        "tokens": {"base": {"address": meta["address"]}},
    }
    (folder / "data.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def balancer_patch_note(meta: dict) -> None:
    line = f"  '{meta['address']}', // SINC"
    note = PR / "balancer" / "ADD_IF_MISSING.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        textwrap.dedent(
            f"""\
            File: src/tokenlists/balancer/tokens/base.ts
            Add this line if SINC is not already present:

            {line}

            Then open PR: https://github.com/balancer/tokenlists/compare
            """
        ),
        encoding="utf-8",
    )


def cowswap_issue(meta: dict) -> None:
    folder = PR / "cowswap"
    folder.mkdir(parents=True, exist_ok=True)
    body = textwrap.dedent(
        f"""\
        ## Add Token — copy into GitHub issue form

        - **Network**: Base (8453)
        - **Token address**: `{meta['address']}`
        - **Token symbol**: {meta['symbol']}
        - **Token name**: {meta['name']}
        - **Decimals**: {meta['decimals']}
        - **Logo**: attach `tokenlists/assets/logo-256.png`
        - **Website**: {meta['website']}
        - **Description**: {meta['descriptionShort']}

        Open: https://github.com/cowprotocol/token-lists/issues/new?template=1-addTokenForm.yml
        """
    )
    (folder / "ADD_TOKEN_ISSUE.md").write_text(body, encoding="utf-8")
    shutil.copy2(ASSETS / "logo-256.png", folder / "logo-256.png")


def form_payloads(meta: dict) -> None:
    folder = PR / "forms"
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "copyPasteBlock": "\n".join(
            [
                f"Name: {meta['name']}",
                f"Symbol: {meta['symbol']}",
                f"Contract: {meta['address']}",
                f"Chain: Base ({meta['chainId']})",
                f"Decimals: {meta['decimals']}",
                f"Total Supply: {meta['totalSupply']}",
                f"Website: {meta['website']}",
                f"Twitter: {meta['twitter']}",
                f"Telegram: {meta['telegram']}",
                f"Explorer: {meta['explorer']}",
                f"Description (short): {meta['descriptionShort']}",
                f"Description (long): {meta['descriptionLong']}",
                "Trust: " + " · ".join(meta["trustSignals"]),
            ]
        ),
        "logoPath": str(ASSETS / "logo-256.png"),
        "targets": [t for t in TARGETS if t["type"] == "form"],
    }
    (folder / "FORM_PAYLOAD.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (folder / "FORM_PAYLOAD.txt").write_text(payload["copyPasteBlock"] + "\n", encoding="utf-8")


def write_manifest(meta: dict) -> None:
    manifest = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "token": meta,
        "targets": TARGETS,
        "quickStart": [
            "1. python scripts/whitelist_token.py prepare   # already run if you see this",
            "2. python scripts/whitelist_token.py check     # see what's already listed",
            "3. python scripts/whitelist_token.py launch    # clone repos + stage PR files",
            "4. Push branches / open PRs / submit forms using tokenlists/pr-packages/",
            "5. Host static/tokenlists/ on getsincor.com for wallet import URL",
        ],
        "walletImportUrl": "https://getsincor.com/tokenlists/sincor.tokenlist.json",
    }
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    readme = textwrap.dedent(
        f"""\
        # SINC whitelist package

        Generated for `{meta['address']}` on Base ({meta['chainId']}).

        ## Immediate (no approval needed)

        - **Wallet import URL** (MetaMask → Settings → Token lists → Add):
          `https://getsincor.com/static/tokenlists/sincor.tokenlist.json`
          (Deploy `static/tokenlists/` to production first.)

        ## PR-ready folders

        | Target | Folder | Action |
        |--------|--------|--------|
        | Superchain / Coinbase Wallet | `pr-packages/superchain/` | PR to ethereum-optimism/ethereum-optimism.github.io |
        | Trust Wallet | `pr-packages/trustwallet/` | PR to trustwallet/assets (+ fee) |
        | Balancer | `pr-packages/balancer/` | Check if already listed |
        | CoW Swap | `pr-packages/cowswap/` | GitHub issue form |
        | Forms (CG, CMC, DexScreener…) | `pr-packages/forms/` | Copy `FORM_PAYLOAD.txt` |

        ## Commands

        ```powershell
        python scripts/whitelist_token.py check
        python scripts/whitelist_token.py launch
        python scripts/whitelist_token.py open-forms
        ```
        """
    )
    (OUT / "README.md").write_text(readme, encoding="utf-8")


def http_get(url: str, timeout: int = 20) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sincor-whitelist/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError):
        return None


def cmd_prepare(_: argparse.Namespace) -> int:
    meta = load_meta()
    ensure_dirs()
    print("Resizing logo → 256×256 PNG …")
    resize_logo(meta)
    write_svg_logo(meta)
    write_token_lists(meta)
    trustwallet_package(meta)
    superchain_package(meta)
    balancer_patch_note(meta)
    cowswap_issue(meta)
    form_payloads(meta)
    write_manifest(meta)
    shutil.copy2(ASSETS / "logo-256.png", ASSETS / "logo.png")
    host_assets = ROOT / "static" / "tokenlists" / "assets"
    host_assets.mkdir(parents=True, exist_ok=True)
    for name in ("logo-256.png", "logo.png", "logo.svg"):
        shutil.copy2(ASSETS / name, host_assets / name)
    print(f"OK — package written to {OUT}")
    print(f"Wallet list: {ROOT / 'static' / 'tokenlists' / 'sincor.tokenlist.json'}")
    return 0


PENDING_SUBMISSIONS = [
    {
        "name": "Superchain PR",
        "url": "https://github.com/ethereum-optimism/ethereum-optimism.github.io/pull/1329",
        "status_url": "https://api.github.com/repos/ethereum-optimism/ethereum-optimism.github.io/pulls/1329",
    },
    {
        "name": "CoW Swap issue",
        "url": "https://github.com/cowprotocol/token-lists/issues/1444",
        "status_url": "https://api.github.com/repos/cowprotocol/token-lists/issues/1444",
    },
    {
        "name": "Li.Fi issue",
        "url": "https://github.com/lifinance/customized-token-list/issues/595",
        "status_url": "https://api.github.com/repos/lifinance/customized-token-list/issues/595",
    },
]

PRODUCTION_URLS = [
    "https://getsincor.com/tokenlists/sincor.tokenlist.json",
    "https://getsincor.com/static/tokenlists/assets/logo-256.png",
    "https://getsincor.com/.well-known/sinc-token.json",
]


def http_head_ok(url: str, timeout: int = 20) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "sincor-whitelist/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except (urllib.error.URLError, TimeoutError):
        return False


def github_issue_state(status_url: str) -> str | None:
    body = http_get(status_url)
    if not body:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    state = data.get("state")
    if state == "open":
        return "open"
    if state == "closed":
        return "merged" if data.get("merged") else "closed"
    return state


def github_pat() -> str | None:
    env_path = ROOT / "onchain" / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("GITHUB_PAT="):
            return line.split("=", 1)[1].strip() or None
    return None


def github_post_comment(api_url: str, body: str) -> tuple[bool, str]:
    token = github_pat()
    if not token:
        return False, "GITHUB_PAT missing in onchain/.env"
    payload = json.dumps({"body": body}).encode()
    req = urllib.request.Request(
        api_url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "sincor-whitelist/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            return True, data.get("html_url", "comment posted")
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="replace")
        return False, f"HTTP {e.code}: {err[:300]}"


def cmd_check(_: argparse.Namespace) -> int:
    meta = load_meta()
    addr = meta["address"]
    checks: list[tuple[str, str, bool]] = []

    bal = http_get(
        "https://raw.githubusercontent.com/balancer/tokenlists/main/src/tokenlists/balancer/tokens/base.ts"
    )
    checks.append(("Balancer base.ts", "listed" if bal and addr.lower() in bal.lower() else "missing", bool(bal and addr.lower() in bal.lower())))

    sc = http_get("https://raw.githubusercontent.com/ethereum-optimism/ethereum-optimism.github.io/master/data/SINC/data.json")
    sc_pr = http_get("https://raw.githubusercontent.com/OrderofChaos33/ethereum-optimism.github.io/add-sinc-20260612/data/SINC/data.json")
    sc_listed = bool(sc and addr.lower() in sc.lower())
    sc_status = "listed" if sc_listed else ("PR pending" if sc_pr and addr.lower() in sc_pr.lower() else "missing")
    checks.append(("Superchain list", sc_status, sc_listed))

    tw = http_get(f"https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/base/assets/{addr}/info.json")
    checks.append(("Trust Wallet", "listed" if tw and addr.lower() in tw.lower() else "missing", bool(tw)))

    rainbow = http_get("https://metadata.p.rainbow.me/token-list/rainbow-token-list.json")
    checks.append(("Rainbow", "listed" if rainbow and addr.lower() in rainbow.lower() else "missing", bool(rainbow and addr.lower() in rainbow.lower())))

    cow = http_get("https://raw.githubusercontent.com/cowprotocol/token-lists/main/src/public/CowSwap.json")
    checks.append(("CoW Swap default", "listed" if cow and addr.lower() in cow.lower() else "missing", bool(cow and addr.lower() in cow.lower())))

    lifi = http_get("https://raw.githubusercontent.com/lifinance/customized-token-list/main/tokens/BAS.json")
    checks.append(("Li.Fi Base list", "listed" if lifi and addr.lower() in lifi.lower() else "missing", bool(lifi and addr.lower() in lifi.lower())))

    cg = http_get(f"https://api.coingecko.com/api/v3/coins/base/contract/{addr.lower()}")
    checks.append(("CoinGecko", "listed" if cg and '"id"' in cg and "error" not in cg.lower() else "missing", bool(cg and '"id"' in cg and "error" not in cg.lower())))

    bs = http_get(f"https://base.blockscout.com/api/v2/addresses/{addr}")
    bs_certified = bool(bs and '"certified":true' in bs.replace(" ", ""))
    bs_scam = bool(bs and '"is_scam":true' in bs.replace(" ", ""))
    bs_icon = bool(bs and '"icon_url":null' not in bs.replace(" ", ""))
    bs_status = "certified" if bs_certified else ("scam-flagged" if bs_scam else ("has-icon" if bs_icon else "uncertified"))
    checks.append(("Blockscout certified", bs_status, bs_certified))

    tkn = http_get(f"https://tkn.xyz/token/base/{addr}")
    checks.append(("TKN registry", "listed" if tkn and "SINCOR" in (tkn or "") else "missing", bool(tkn and "SINCOR" in (tkn or ""))))

    prod_ok = all(http_head_ok(u) for u in PRODUCTION_URLS)
    checks.append(("Production URLs (getsincor)", "live" if prod_ok else "check deploy", prod_ok))

    print(f"\n=== SINC whitelist status ({addr}) ===\n")
    for name, status, ok in checks:
        mark = "✓" if ok else "○"
        print(f"  {mark} {name}: {status}")

    print("\n=== Open submissions ===\n")
    for sub in PENDING_SUBMISSIONS:
        state = github_issue_state(sub["status_url"]) or "unknown"
        mark = "✓" if state in ("merged", "closed") else "○"
        print(f"  {mark} {sub['name']}: {state} — {sub['url']}")
    print()
    return 0


def cmd_bump(_: argparse.Namespace) -> int:
    meta = load_meta()
    addr = meta["address"]
    curve = meta.get("relatedContracts", {}).get("bondingCurve", "0x75dE341a2BC81806198364F125d4Cde36527619C")
    rogue = "0x85372932f9b151a076815d92cf71a97980ffd667"
    results: list[tuple[str, bool, str]] = []

    cow_body = textwrap.dedent(
        f"""\
        Completing this submission — the original issue body was truncated.

        - **Network**: Base (8453)
        - **Token address**: `{addr}`
        - **Token symbol**: {meta['symbol']}
        - **Token name**: {meta['name']}
        - **Decimals**: {meta['decimals']}
        - **Logo**: {PUBLIC_LOGO_URL_MIRROR}
        - **Website**: {meta['website']}
        - **Description**: {meta['descriptionShort']} Official buy venue: bonding curve `{curve}` (not rogue V2 `{rogue}`).

        Self-hosted token list: https://getsincor.com/tokenlists/sincor.tokenlist.json
        Metadata: https://getsincor.com/.well-known/sinc-token.json
        """
    )
    ok, msg = github_post_comment(
        "https://api.github.com/repos/cowprotocol/token-lists/issues/1444/comments",
        cow_body,
    )
    results.append(("CoW #1444", ok, msg))

    sc_body = textwrap.dedent(
        f"""\
        Friendly bump (Jun 15) — PR remains mergeable. Self-hosted infrastructure is live:

        - Token list: https://getsincor.com/tokenlists/sincor.tokenlist.json
        - Metadata: https://getsincor.com/.well-known/sinc-token.json
        - Logo: {PUBLIC_LOGO_URL_MIRROR}

        Balancer already lists SINC. Li.Fi issue #595 completed. Official price from bonding curve `{curve}` only — do not use rogue V2 `{rogue}`.

        Happy to address any review feedback.
        """
    )
    ok, msg = github_post_comment(
        "https://api.github.com/repos/ethereum-optimism/ethereum-optimism.github.io/issues/1329/comments",
        sc_body,
    )
    results.append(("Superchain PR #1329", ok, msg))

    print("\n=== Submission bumps ===\n")
    for name, success, detail in results:
        mark = "✓" if success else "✗"
        print(f"  {mark} {name}: {detail}")
    print()
    return 0 if all(r[1] for r in results) else 1


def git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def stage_repo(repo_url: str, repo_name: str, copy_fn, *, sparse: str | None = None) -> str | None:
    dest = STAGING / repo_name
    if dest.exists():
        shutil.rmtree(dest)
    print(f"Cloning {repo_url} …")
    if sparse:
        r = subprocess.run(
            ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", repo_url, str(dest)],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            print(f"  sparse clone failed: {r.stderr.strip()}")
            return None
        r2 = subprocess.run(["git", "sparse-checkout", "set", sparse], cwd=dest, capture_output=True, text=True)
        if r2.returncode != 0:
            print(f"  sparse-checkout failed: {r2.stderr.strip()}")
            return None
    else:
        r = subprocess.run(["git", "clone", "--depth", "1", repo_url, str(dest)], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  clone failed: {r.stderr.strip()}")
            return None
    copy_fn(dest)
    branch = f"add-sinc-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    subprocess.run(["git", "checkout", "-b", branch], cwd=dest, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, capture_output=True)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=dest, capture_output=True, text=True)
    if not status.stdout.strip():
        print(f"  no changes needed in {repo_name}")
        return None
    subprocess.run(
        ["git", "commit", "-m", "Add SINC token on Base (0x9C8cd8d3961F445D653713dE65C6578bE11668e7)"],
        cwd=dest,
        capture_output=True,
    )
    print(f"  staged branch {branch} in {dest}")
    print(f"  next: cd {dest} && git push -u origin {branch}")
    print(f"  then open PR on GitHub")
    return str(dest)


def cmd_launch(_: argparse.Namespace) -> int:
    if not git_available():
        print("git not found — PR folders are in tokenlists/pr-packages/ for manual upload")
        return 1

    meta = load_meta()
    ensure_dirs()
    if not (PR / "superchain").exists():
        cmd_prepare(argparse.Namespace())

    def copy_superchain(dest: Path) -> None:
        src = PR / "superchain" / "data" / "SINC"
        dst = dest / "data" / "SINC"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

    def copy_trust(dest: Path) -> None:
        src = PR / "trustwallet" / "blockchains" / "base" / "assets" / CHECKSUM
        dst = dest / "blockchains" / "base" / "assets" / CHECKSUM
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

    stage_repo(
        "https://github.com/ethereum-optimism/ethereum-optimism.github.io.git",
        "ethereum-optimism.github.io",
        copy_superchain,
    )
    stage_repo(
        "https://github.com/trustwallet/assets.git",
        "trustwallet-assets",
        copy_trust,
        sparse="blockchains/base",
    )

    bal = http_get(
        "https://raw.githubusercontent.com/balancer/tokenlists/main/src/tokenlists/balancer/tokens/base.ts"
    )
    if bal and meta["address"].lower() in bal.lower():
        print("Balancer: already listed — skip PR")
    else:
        print("Balancer: add line from pr-packages/balancer/ADD_IF_MISSING.txt manually")

    sc = STAGING / "ethereum-optimism.github.io"
    if sc.exists():
        print(f"\nSuperchain PR branch ready: {sc}")
        print("  git push -u origin add-sinc-20260612  (from that folder, after forking)")
        print("  PR target: https://github.com/ethereum-optimism/ethereum-optimism.github.io/compare")

    print("\nTrust Wallet fallback: upload tokenlists/pr-packages/trustwallet/ via GitHub web UI")
    print("CoW Swap: use pr-packages/cowswap/ADD_TOKEN_ISSUE.md")
    print("Forms: use pr-packages/forms/FORM_PAYLOAD.txt")
    return 0


def cmd_open_forms(_: argparse.Namespace) -> int:
    import webbrowser

    for t in TARGETS:
        if t.get("type") == "form" and t.get("url"):
            print(f"Opening {t['name']}: {t['url']}")
            webbrowser.open(t["url"])
    form_txt = PR / "forms" / "FORM_PAYLOAD.txt"
    if form_txt.exists():
        print(f"\nForm copy-paste block: {form_txt}")
        try:
            subprocess.run(["clip"], input=form_txt.read_text(encoding="utf-8"), text=True, check=True, shell=True)
            print("(copied to clipboard on Windows)")
        except Exception:
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SINC token whitelist orchestrator")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="Generate logos, token list, PR packages")
    sub.add_parser("check", help="Check live list presence")
    sub.add_parser("launch", help="Clone repos and stage git branches for PRs")
    sub.add_parser("open-forms", help="Open form URLs and copy payload to clipboard")
    sub.add_parser("bump", help="Comment on open Superchain PR and CoW issue")
    args = parser.parse_args()
    handlers = {
        "prepare": cmd_prepare,
        "check": cmd_check,
        "launch": cmd_launch,
        "open-forms": cmd_open_forms,
        "bump": cmd_bump,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())