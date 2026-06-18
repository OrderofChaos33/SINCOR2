"""On-chain security signals and explorer certification status for SINC."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
CHAIN_ID = 8453
DEPLOYER = "0xC184EcEfFaf6392951e4C7b042d61774497B5dC5"
BONDING_CURVE = "0x75dE341a2BC81806198364F125d4Cde36527619C"
ROGUE_V2_PAIR = "0x85372932f9b151a076815d92cf71a97980ffd667"


def _http_get(url: str, timeout: int = 25) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SINCOR-security/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None


def _http_json(url: str, timeout: int = 25) -> dict | None:
    body = _http_get(url, timeout=timeout)
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def fetch_goplus(addr: str = SINC) -> dict | None:
    data = _http_json(
        f"https://api.gopluslabs.io/api/v1/token_security/{CHAIN_ID}"
        f"?contract_addresses={addr}"
    )
    if not data or data.get("code") != 1:
        return None
    result = data.get("result") or {}
    return result.get(addr.lower()) or result.get(addr) or next(iter(result.values()), None)


def fetch_blockscout(addr: str = SINC) -> dict | None:
    return _http_json(f"https://base.blockscout.com/api/v2/addresses/{addr}")


def _flag(value: str | int | None) -> bool:
    return str(value or "0") == "1"


def diagnose(addr: str = SINC) -> dict:
    goplus = fetch_goplus(addr) or {}
    blockscout = fetch_blockscout(addr) or {}
    token = blockscout.get("token") if isinstance(blockscout.get("token"), dict) else {}

    dex_pools = goplus.get("dex") or []
    rogue_listed = any(
        (p.get("pair") or "").lower() == ROGUE_V2_PAIR.lower() for p in dex_pools if isinstance(p, dict)
    )

    contract_clean = not any(
        _flag(goplus.get(k))
        for k in (
            "is_honeypot",
            "is_blacklisted",
            "cannot_sell_all",
            "hidden_owner",
            "selfdestruct",
            "transfer_pausable",
            "honeypot_with_same_creator",
            "fake_token",
        )
    )

    certified = bool(token.get("certified"))
    icon_url = token.get("icon_url")
    is_scam = bool(blockscout.get("is_scam"))
    reputation = blockscout.get("reputation")

    reasons: list[str] = []
    if not certified:
        reasons.append("Blockscout token is not certified (no deployer-verified icon/metadata).")
    if not icon_url:
        reasons.append("Blockscout icon_url is empty — wallets show generic/unverified UI.")
    if is_scam:
        reasons.append("Blockscout is_scam flag is set.")
    if rogue_listed:
        reasons.append(
            f"GoPlus indexes rogue UniV2 pair {ROGUE_V2_PAIR} with negligible liquidity; "
            "some scanners treat thin DEX listings as risky."
        )
    if reputation not in (None, "ok"):
        reasons.append(f"Blockscout reputation is {reputation!r}.")
    if not contract_clean:
        reasons.append("GoPlus reports one or more contract risk flags.")

    actions: list[dict] = [
        {
            "priority": 1,
            "who": "deployer",
            "title": "Verify deployer on Blockscout",
            "url": "https://base.blockscout.com/my-account/verified-addresses",
            "wallet": DEPLOYER,
            "detail": "Sign as deployer, set logo https://getsincor.com/static/tokenlists/assets/logo-256.png",
        },
        {
            "priority": 2,
            "who": "deployer",
            "title": "Submit Blockscout public tag",
            "url": "https://base.blockscout.com/public-tags/submit",
            "detail": "Tag SINC as official SINCOR utility token on Base.",
        },
        {
            "priority": 3,
            "who": "anyone",
            "title": "Register on TKN (Blockscout icon source)",
            "url": f"https://tkn.xyz/token/base/{addr}",
            "detail": "Add logo, website, and token list URL.",
        },
        {
            "priority": 4,
            "who": "deployer",
            "title": "Basescan Update Token Info",
            "url": f"https://basescan.org/token/{addr}#writeContract",
            "detail": "Verify ownership + submit logo/links (clears UNKNOWN reputation in MetaMask).",
        },
    ]

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "token": addr,
        "chain_id": CHAIN_ID,
        "verdict": "suspicious_ui" if reasons and contract_clean else ("risky_contract" if not contract_clean else "ok"),
        "contract_clean": contract_clean,
        "reasons": reasons,
        "actions": actions,
        "goplus": {
            "is_honeypot": goplus.get("is_honeypot"),
            "is_blacklisted": goplus.get("is_blacklisted"),
            "is_open_source": goplus.get("is_open_source"),
            "is_mintable": goplus.get("is_mintable"),
            "is_in_dex": goplus.get("is_in_dex"),
            "dex_pools": dex_pools,
            "rogue_pair_detected": rogue_listed,
            "official_buy": f"https://getsincor.com/sinc",
            "bonding_curve": BONDING_CURVE,
            "do_not_route_pool": ROGUE_V2_PAIR,
        },
        "blockscout": {
            "reputation": reputation,
            "is_scam": is_scam,
            "is_verified": blockscout.get("is_verified"),
            "certified": certified,
            "icon_url": icon_url,
            "creator": blockscout.get("creator_address_hash") or DEPLOYER,
            "token_url": f"https://base.blockscout.com/token/{addr}",
        },
        "canonical": {
            "deployer": DEPLOYER,
            "bonding_curve": BONDING_CURVE,
            "rogue_v2_pair": ROGUE_V2_PAIR,
            "token_list": "https://getsincor.com/tokenlists/sincor.tokenlist.json",
            "metadata": "https://getsincor.com/.well-known/sinc-token.json",
        },
    }