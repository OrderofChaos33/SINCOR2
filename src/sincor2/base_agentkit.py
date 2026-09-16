from __future__ import annotations

import os
import re
from typing import Any, Iterable

from sincor2.onchain.constants import BASE_CHAIN_ID, TREASURY

_SAFE_ENV_RE = re.compile(r"[^A-Z0-9]+")


def _env_key(agent_name: str) -> str:
    return _SAFE_ENV_RE.sub("_", (agent_name or "agent").upper()).strip("_") or "AGENT"


def resolve_agent_wallet(agent_name: str, wallet: str | None = None) -> str:
    if wallet:
        return wallet
    key = _env_key(agent_name)
    for env_name in (
        f"{key}_WALLET_ADDRESS",
        f"{key}_AGENTKIT_WALLET_ADDRESS",
        "AGENTKIT_WALLET_ADDRESS",
        "COINBASE_AGENTKIT_WALLET_ADDRESS",
        "TREASURY_ADDRESS",
    ):
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return value
    return TREASURY


def build_base_commerce_profile(
    agent_name: str,
    *,
    wallet: str | None = None,
    skill_ids: Iterable[str] = (),
    accepted_tokens: Iterable[str] = ("USDC", "SINC"),
) -> dict[str, Any]:
    skill_ids = [str(skill_id) for skill_id in skill_ids if skill_id]
    wallet_address = resolve_agent_wallet(agent_name, wallet)
    tokens = [str(token).upper() for token in accepted_tokens if token]
    action_providers = [
        {"id": "wallet", "network": "base-mainnet"},
        {"id": "erc20", "network": "base-mainnet", "tokens": tokens},
    ]
    if skill_ids:
        action_providers.append(
            {"id": "x402", "network": "base-mainnet", "skills": skill_ids}
        )
    return {
        "wallet": wallet_address,
        "chain_id": BASE_CHAIN_ID,
        "payment_methods": [
            {
                "scheme": "x402",
                "network": "base",
                "chainId": BASE_CHAIN_ID,
                "acceptedTokens": tokens,
                "payTo": wallet_address,
            }
        ],
        "agentkit": {
            "provider": "coinbase-agentkit",
            "network": "base-mainnet",
            "walletAddress": wallet_address,
            "walletCreation": {
                "mode": "server-managed",
                "cdpApiKeyIdEnv": "CDP_API_KEY_ID",
                "cdpApiKeySecretEnv": "CDP_API_KEY_SECRET",
                "walletSecretEnv": "AGENTKIT_WALLET_SECRET",
            },
            "actionProviders": action_providers,
            "sessionPolicy": {"sponsored": True, "gasless": True},
            "typescriptPackage": "@coinbase/agentkit",
            "pythonPackage": "coinbase-agentkit",
        },
        "base_commerce": {
            "network": "base",
            "chainId": BASE_CHAIN_ID,
            "wallet": wallet_address,
            "treasury": TREASURY,
            "x402": {"enabled": True, "acceptedTokens": tokens, "skillIds": skill_ids},
            "sessionPolicy": {"sponsored": True, "gasless": True},
        },
    }
