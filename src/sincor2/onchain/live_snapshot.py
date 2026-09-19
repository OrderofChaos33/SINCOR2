"""Live token holder / transfer snapshot for the public money surface.

Canon lock files store addresses, floor policy, and a dated snapshot.
This module is the runtime source for holder/transfer counts so
``/api/price/official`` never broadcasts the 2026-09-01 lock-date
explorer numbers as if they were current.

Refresh order:
1. Etherscan API V2 ``tokenholdercount`` when ``ETHERSCAN_API_KEY`` is set.
2. Basescan token-page scrape (holder count is public in the meta description).
3. Last committed snapshot in ``TOKEN_CANON.json`` marked ``stale=true``.

Never invent transfer volume. Basescan's SINC token overview has historically
shown ``Transfers: 0`` even after a multi-thousand holder airdrop; we report
the explorer figure and flag the indexer caveat instead of guessing.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sincor2.onchain.constants import AXIOM_TOKEN, SINC_TOKEN

logger = logging.getLogger(__name__)

BASE_CHAIN_ID = 8453
ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
CACHE_TTL_S = int(os.getenv("SINCOR_ONCHAIN_CACHE_TTL_S", "300"))
REQUEST_TIMEOUT_S = 12

_lock = threading.Lock()
_cache: dict[str, Any] = {"payload": None, "fetched_at": 0.0}

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CANON_PATH = _REPO_ROOT / "TOKEN_CANON.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _http_get(url: str, timeout: int = REQUEST_TIMEOUT_S) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SINCOR2-live-snapshot/1.0 (+https://getsincor.com)",
            "Accept": "text/html,application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _parse_int(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    text = str(raw).strip().replace(",", "")
    if not text.isdigit():
        return None
    return int(text)


def load_canon() -> dict[str, Any]:
    try:
        return json.loads(_CANON_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("TOKEN_CANON.json unreadable: %s", exc)
        return {}


def locked_snapshot() -> dict[str, Any]:
    """Dated snapshot stored in canon. Used as fallback, never as silent live."""
    canon = load_canon()
    sinc = (canon.get("sinc") or {}).get("onchain_live") or (canon.get("sinc") or {}).get("onchain_as_of_lock") or {}
    axm = (canon.get("axiom") or {}).get("onchain_live") or {}
    return {
        "sinc": {
            "address": (canon.get("sinc") or {}).get("address", SINC_TOKEN),
            "holders": _parse_int(sinc.get("holders")) or 0,
            "transfers": _parse_int(sinc.get("transfers")),
            "source": "token_canon_lock",
        },
        "axiom": {
            "address": (canon.get("axiom") or {}).get("address", AXIOM_TOKEN),
            "holders": _parse_int(axm.get("holders")) or _parse_int((canon.get("axiom") or {}).get("holders")),
            "transfers": _parse_int(axm.get("transfers")),
            "source_verified_basescan": bool((canon.get("axiom") or {}).get("source_verified_basescan")),
            "source": "token_canon_lock",
        },
        "lock_version": canon.get("lock_version"),
        "verified_at": sinc.get("verified_at") or canon.get("verified_at"),
    }


def _etherscan_holder_count(address: str) -> int | None:
    key = (os.getenv("ETHERSCAN_API_KEY") or os.getenv("BASESCAN_API_KEY") or "").strip()
    if not key:
        return None
    url = (
        f"{ETHERSCAN_V2}?chainid={BASE_CHAIN_ID}&module=token&action=tokenholdercount"
        f"&contractaddress={address}&apikey={key}"
    )
    try:
        raw = json.loads(_http_get(url))
    except Exception as exc:
        logger.info("etherscan tokenholdercount failed for %s: %s", address, exc)
        return None
    if str(raw.get("status")) != "1":
        logger.info("etherscan tokenholdercount rejected for %s: %s", address, raw.get("message"))
        return None
    return _parse_int(raw.get("result"))


def _scrape_basescan_token(address: str) -> dict[str, Any]:
    url = f"https://basescan.org/token/{address}"
    html = _http_get(url)
    holders = None
    meta = re.search(r"Holders:\s*([\d,]+)\s*\|", html)
    if meta:
        holders = _parse_int(meta.group(1))
    if holders is None:
        block = re.search(
            r'id="ContentPlaceHolder1_tr_tokenHolders"[\s\S]{0,400}?([\d,]{2,})',
            html,
        )
        if block:
            holders = _parse_int(block.group(1))
    transfers = None
    tx_meta = re.search(r"Transfers:\s*([\d,]+)", html)
    if tx_meta:
        transfers = _parse_int(tx_meta.group(1))
    verified = "Contract Source Code Verified" in html and "Similar Match" not in html
    similar = "Similar Match" in html
    return {
        "holders": holders,
        "transfers": transfers,
        "source_verified_basescan": verified,
        "source_similar_match_only": similar,
        "source": "basescan_html",
        "explorer": url,
    }


def _merge_token(address: str, locked: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "address": address,
        "holders": locked.get("holders"),
        "transfers": locked.get("transfers"),
        "source": "token_canon_lock",
        "stale": True,
        "explorer": f"https://basescan.org/token/{address}",
    }
    holders = _etherscan_holder_count(address)
    source = "etherscan_v2"
    if holders is None:
        try:
            scraped = _scrape_basescan_token(address)
        except Exception as exc:
            logger.warning("basescan scrape failed for %s: %s", address, exc)
            scraped = {}
        if scraped.get("holders") is not None:
            holders = scraped["holders"]
            source = "basescan_html"
            if scraped.get("transfers") is not None:
                out["transfers"] = scraped["transfers"]
            if "source_verified_basescan" in scraped:
                out["source_verified_basescan"] = scraped["source_verified_basescan"]
            if scraped.get("source_similar_match_only"):
                out["source_match"] = "similar"
    if holders is not None:
        out["holders"] = holders
        out["source"] = source
        out["stale"] = False
    return out


def fetch_live_onchain(*, force: bool = False) -> dict[str, Any]:
    """Return a public-safe on-chain snapshot. Never raises to callers."""
    now = time.time()
    with _lock:
        cached = _cache["payload"]
        if cached is not None and not force and now - float(_cache["fetched_at"]) < CACHE_TTL_S:
            return cached

    locked = locked_snapshot()
    try:
        sinc = _merge_token(SINC_TOKEN, locked.get("sinc") or {})
        axiom = _merge_token(AXIOM_TOKEN, locked.get("axiom") or {})
        canon = load_canon()
        axiom["source_verified_basescan"] = bool(
            (canon.get("axiom") or {}).get("source_verified_basescan")
        )
        payload = {
            "chain": "base",
            "chain_id": BASE_CHAIN_ID,
            "verified_at": _now_iso(),
            "cache_ttl_s": CACHE_TTL_S,
            "sinc": sinc,
            "axiom": axiom,
            "notes": [
                "Holder counts are explorer-indexed, not an audited circulating float.",
                "SINC Basescan transfer-count widget has lagged holder growth after airdrop; do not treat a 0 as no activity without checking the holders tab.",
                "AXM source_verified_basescan stays false until a live Basescan checkmark exists.",
            ],
        }
    except Exception as exc:
        logger.exception("live onchain snapshot failed: %s", exc)
        payload = {
            "chain": "base",
            "chain_id": BASE_CHAIN_ID,
            "verified_at": locked.get("verified_at") or _now_iso(),
            "stale": True,
            "error": str(exc),
            "sinc": locked.get("sinc"),
            "axiom": locked.get("axiom"),
            "source": "token_canon_lock",
        }

    with _lock:
        _cache["payload"] = payload
        _cache["fetched_at"] = time.time()
    return payload


def attach_official_price_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Mutate the official price payload with live on-chain facts."""
    snap = fetch_live_onchain()
    payload["onchain"] = snap
    sinc = snap.get("sinc") or {}
    payload["sinc_holders"] = sinc.get("holders")
    payload["sinc_transfers"] = sinc.get("transfers")
    payload["onchain_verified_at"] = snap.get("verified_at")
    payload["canon_lock_version"] = load_canon().get("lock_version")
    return payload
