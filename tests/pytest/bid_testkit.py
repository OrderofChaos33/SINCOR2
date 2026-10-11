"""Shared test kit for W-77 sealed-bid auth.

Sealed-bid commit/reveal routes require bidder authentication: an EIP-191
``personal_sign`` signature by the agent's REGISTERED wallet over the exact
bid message (or the operator heartbeat token).  These helpers register an
agent with a wallet claim + proof and produce signed commit/reveal bodies.

Key registry: ``register_wallet_agent`` stashes the agent's key by agent_id
so per-file ``_commit``/``_reveal`` helpers can sign without changing
their call signatures.
"""

from __future__ import annotations

import secrets
import time

from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2.a2a_identity import register_message
from sincor2.a2a_inbound import _now_ms
from sincor2.a2a_integration import _auth_bid_message

_KEYS: dict[str, Account] = {}


def register_wallet_agent(client, agent_id: str, **extra) -> Account:
    """Register ``agent_id`` with a fresh wallet + EIP-191 proof.

    Returns the signing key; also stashes it for :func:`key_for`.
    ``extra`` fields are merged into the registration body.
    """
    key = Account.create()
    ts = _now_ms()
    message = register_message(agent_id, ts)
    sig = key.sign_message(encode_defunct(text=message)).signature
    body = {
        "agent_id": agent_id,
        "capability_tags": ["lead-enrichment"],
        "rpc_callback": "https://agent.example/rpc",
        "wallet": key.address,
        "registration_wallet": key.address,
        "registration_ts": ts,
        "registration_signature": "0x" + bytes(sig).hex(),
    }
    body.update(extra)
    r = client.post("/v1/a2a/register", json=body)
    assert r.status_code in (200, 201), r.get_json()
    _KEYS[agent_id] = key
    return key


def key_for(agent_id: str) -> Account:
    try:
        return _KEYS[agent_id]
    except KeyError:
        raise AssertionError(
            f"no test key for {agent_id!r}: register via register_wallet_agent first"
        )


def clear_keys() -> None:
    _KEYS.clear()


def bid_auth_fields(key: Account, purpose: str, task_id: str,
                    agent_id: str, payload: str) -> dict:
    """EIP-191 bid auth fields for the commit/reveal routes (W-77)."""
    ts = int(time.time())
    nonce = secrets.token_hex(8)
    message = _auth_bid_message(purpose, task_id, agent_id, payload, ts, nonce)
    sig = key.sign_message(encode_defunct(text=message)).signature
    return {
        "authSignature": "0x" + bytes(sig).hex(),
        "authTimestamp": ts,
        "authNonce": nonce,
    }


def signed_commit_body(key: Account, task_id: str, agent_id: str,
                       commitment: str) -> dict:
    body = {"task_id": task_id, "agent_id": agent_id, "commitment": commitment}
    body.update(bid_auth_fields(key, "commit", task_id, agent_id, commitment))
    return body


def signed_reveal_body(key: Account, task_id: str, agent_id: str,
                       bid_axm: float, nonce_hex: str,
                       estimated_seconds: int = 300) -> dict:
    payload = f"{float(bid_axm)}:{nonce_hex}"
    body = {"task_id": task_id, "agent_id": agent_id, "bid_axm": bid_axm,
            "nonce": nonce_hex, "estimated_seconds": estimated_seconds}
    body.update(bid_auth_fields(key, "reveal", task_id, agent_id, payload))
    return body
