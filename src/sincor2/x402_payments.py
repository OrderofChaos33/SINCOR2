"""HTTP 402 micropayments (spec §5.4).

Challenges are denominated per resource config; platform policy prefers AXM
for new flows (SINC is legacy for residual subscription renewals only).

Design constraint (dev-watch item 79, Payload Tools postmortem 2026-10-07):
``verify`` is an authorization-shape check, NOT payment. ``settle`` is the
money movement. Work must NEVER be served on a verify receipt alone — the
postmortem documents sellers doing unpaid work on credit because verify
passed on every attempt while settle rejected everything with
``invalid_payload`` (token ``name`` had to be exactly "USD Coin", not
"USDC"; a $0.001 amount minimum — neither was in the spec). The
``SETTLE_BEFORE_SERVE`` constant is True, and ``require_settled()`` is the
single guard every serve path must call before doing paid work. The
batch-settlement voucher section below covers offchain promises only:
vouchers are promises, not settlement, and batch settlement does NOT relax
settle-before-serve.
"""

# ---------------------------------------------------------------------------
# CDP SDK integration notes (dev-watch item 78, 2026-10-08).
#
# CDP_SDK_PIN: there is NO cdp-sdk in requirements.txt / requirements.lock.
# This module speaks the x402 wire protocol directly (challenge payloads)
# and does not import the CDP SDK, so nothing is pinned here.  Pin cdp-sdk
# in requirements BEFORE any live CdpX402Client/facilitator integration,
# then re-run the explicit-network tests in
# tests/pytest/test_cdp_explicit_networks.py against the pinned version.
#
# CDP 1.58.0 notes:
#   * revoke-delegation moved DELETE -> POST; the old routes are deprecated
#     and stop working 2026-10-22.  Any delegation-revocation flow built
#     later must use POST.
#   * new Mandates API (createMandate/getMandate/listMandates/
#     cancelMandate/approveWalletMandate/revokeWalletMandate/
#     authorizeMandatePaymentSession).  See MANDATES_API_WATCH below.
# ---------------------------------------------------------------------------

from __future__ import annotations

import copy
import json
import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final, Optional, Sequence

import yaml

from sincor2.agent_billing import record_platform_payment
from sincor2.platform_payments import (
    CHAIN_ID,
    CONSUMED_TX_DDL,
    atomic_to_display,
    display_to_atomic,
    token_address,
    token_decimals,
    TREASURY,
    verify_treasury_transfer,
)
from sincor2.treasury_inflow import record_inflow

_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG = _ROOT / "config" / "x402_pricing.yaml"


def _db_path() -> Path:
    from sincor2.data_paths import orders_db_path

    return orders_db_path()


#: The only migrations ``_ensure_column`` may perform (dev-watch 304,
#: CVE-2026-118378 hedge): column name -> expected DDL type. PRAGMA and
#: ALTER cannot bind identifiers as parameters, so identifiers are
#: selected from this allowlist and the SQL executed below is a constant
#: string per entry — a caller-supplied table/column/ddl value is never
#: interpolated into a query string.
_X402_ENSURE_COLUMN_SQL = {
    "token_expires_at": (
        "TEXT",
        "ALTER TABLE x402_challenges ADD COLUMN token_expires_at TEXT",
    ),
    "token_consumed_at": (
        "TEXT",
        "ALTER TABLE x402_challenges ADD COLUMN token_consumed_at TEXT",
    ),
    # WREN PR 4 (merged #596): minting source (wallet when known, else
    # client IP). Without this entry init_x402_db() raises ValueError on
    # every _conn() call, degrading X402_AVAILABLE=False.
    "source_key": (
        "TEXT",
        "ALTER TABLE x402_challenges ADD COLUMN source_key TEXT",
    ),
}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Add a column if missing (migration for DBs created before this patch).

    ``table``/``column``/``ddl`` must match the allowlist exactly; any
    other value raises ValueError without touching the database.
    """
    entry = _X402_ENSURE_COLUMN_SQL.get(column)
    if table != "x402_challenges" or entry is None or entry[0] != ddl:
        raise ValueError(
            f"migration not allowlisted: table={table!r} column={column!r} ddl={ddl!r}"
        )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(x402_challenges)").fetchall()}
    if column not in cols:
        conn.execute(entry[1])


def init_x402_db() -> None:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """CREATE TABLE IF NOT EXISTS x402_challenges (
            challenge_id TEXT PRIMARY KEY,
            resource_id TEXT NOT NULL,
            amount_atomic TEXT NOT NULL,
            amount_display REAL NOT NULL,
            payer_wallet TEXT,
            source_key TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            tx_hash TEXT,
            access_token TEXT,
            token_expires_at TEXT,
            token_consumed_at TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            fulfilled_at TEXT
        )"""
    )
    # P1-7/P1-8 migrations for DBs created before this patch.
    _ensure_column(conn, "x402_challenges", "token_expires_at", "TEXT")
    _ensure_column(conn, "x402_challenges", "token_consumed_at", "TEXT")
    # WREN PR 4: minting source (wallet when known, else client IP) so
    # open-unbound challenges can be capped per source.
    _ensure_column(conn, "x402_challenges", "source_key", "TEXT")
    try:
        # P1-8: one on-chain payment may fulfill at most one challenge.
        # NULL tx_hash rows (pending challenges) never conflict in SQLite,
        # so this only binds fulfilled rows.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS x402_challenges_tx_hash_uniq "
            "ON x402_challenges(tx_hash)"
        )
    except Exception as err:
        # Rows written under the vulnerable scheme may already contain
        # duplicate tx_hash values, which blocks index creation. Log and
        # continue: the atomic-claim UPDATE below still performs a
        # same-transaction pre-check as a best-effort defense.
        logger.warning("x402: tx_hash UNIQUE index not created: %s", err)
    # D2: shared consumed_onchain_txs registry (canonical DDL lives in
    # platform_payments). Created here too so the x402 atomic claim in
    # verify_challenge can INSERT into it on the same connection /
    # transaction as the challenge fulfill.
    conn.execute(CONSUMED_TX_DDL)
    conn.commit()
    conn.close()


def _conn() -> sqlite3.Connection:
    init_x402_db()
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    return c


logger = logging.getLogger("sincor.x402")

#: Entitlement-token lifetime in seconds. AUDIT P1-7: every fulfilled
#: challenge stamps ``token_expires_at``; access_granted() rejects expired
#: tokens and consumes single-use tokens. Tokens issued before the P1-7
#: migration have NULL ``token_expires_at`` and are treated as expired
#: (fail closed).
ENTITLEMENT_TTL_SECONDS = int(os.getenv("X402_ENTITLEMENT_TTL_SECONDS", "3600"))


def load_pricing() -> dict[str, Any]:
    if not _CONFIG.is_file():
        return {"resources": {}, "defaults": {}}
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}


def get_resource(resource_id: str) -> dict[str, Any] | None:
    cfg = load_pricing()
    res = (cfg.get("resources") or {}).get(resource_id)
    if not res:
        return None
    defaults = cfg.get("defaults") or {}
    token = str(res.get("token") or defaults.get("token") or "SINC").upper()
    if token == "AXIOM":
        token = "AXM"
    amount = float(
        res.get(f"amount_{token.lower()}")
        or res.get("amount")
        or res.get("amount_display")
        or res.get("amount_sinc")
        or 1
    )
    ttl = int(defaults.get("challenge_ttl_seconds", 900))
    resource = {
        "id": resource_id,
        "label": res.get("label", resource_id),
        "description": res.get("description", ""),
        "amount_display": amount,
        "amount_atomic": str(display_to_atomic(amount, token)),
        "token": token,
        "token_address": token_address(token),
        "token_decimals": token_decimals(token),
        "treasury": res.get("treasury", defaults.get("treasury", TREASURY)),
        "chain_id": int(res.get("chain_id", defaults.get("chain_id", CHAIN_ID))),
        "ttl_seconds": ttl,
        "skill_id": str(res.get("skill_id") or ""),
    }
    if token == "SINC":
        resource["amount_sinc"] = amount
    if token == "USDC":
        resource["amount_usdc"] = amount
    return resource


def list_resources() -> list[dict[str, Any]]:
    cfg = load_pricing()
    return [
        get_resource(rid)  # type: ignore
        for rid in (cfg.get("resources") or {}).keys()
    ]


#: Fail-closed network allowlist for x402 payment routes.  From CDP SDK
#: 1.56.0, a route with scheme "upto" and NO explicit networks expands to
#: BOTH Base and Solana (previously Base-only).  Every route we construct
#: must therefore carry an explicit networks list; :func:`build_route_config`
#: is the only sanctioned constructor and it raises on None/empty.
X402_NETWORKS_EXPLICIT: Final = ("base",)


class MANDATES_API_WATCH:
    """Design note (no code yet): the CDP Mandates API (1.58.0) is the
    wallet-layer payment-session authorization primitive candidate for
    the session-key work item.  When that work starts, prefer
    authorizeMandatePaymentSession-scoped mandates over long-lived
    delegation grants: sessions bound to a mandate are revocable in one
    call (revokeWalletMandate) and carry explicit spend/network bounds,
    which composes with the X402_NETWORKS_EXPLICIT allowlist above.
    Tracked here so the primitive is evaluated, not silently skipped."""


class EIP7702_DELEGATION_LIFECYCLE:
    """Design constraint (dev-watch item 312, 2026-10-30): any EIP-7702
    delegation SINCOR ever issues or accepts must follow this lifecycle.

    Why: a signed 7702 authorization is a STANDING capability, not a
    one-shot approval. Whoever holds the signed authorization can attach
    it later, and the delegating account keeps executing the delegate's
    code until the delegation is explicitly cleared (the sven.eth drain,
    2026-10-14: one signature, reused as a standing capability).

    Current state: there is NO 7702 surface anywhere in this codebase --
    no delegation is issued, accepted, or executed today. This note is
    the gate for the day that changes. Guarded by
    tests/pytest/test_devwatch312_delegation_guard.py.

    The rule, for any future 7702 delegation:

    1. Short-lived and scoped: every authorization carries an explicit
       expiry (block or timestamp) and names ONE specific implementation
       address. No open-ended delegations, no wildcard implementation.
    2. Revocable by a single clearing transaction: the delegating
       account can revoke the delegation with one clearing transaction
       that restores it to a plain account, and the clearing path is
       tested before the delegation is ever used.
    3. Inventoried: every active delegation is recorded in a delegation
       inventory (account, implementation, expiry, purpose) at issuance
       time. An active delegation that is not in the inventory is
       treated as a compromise until cleared -- clear first, investigate
       second.
    4. Re-verified at use time: no money-path code may accept an action
       from a 7702-delegated account on the strength of a previously
       signed authorization. The current delegate address must be
       re-verified onchain at use time, and the action refused if the
       onchain delegation does not match the inventoried, unexpired
       scope.
    """


def build_route_config(
    *,
    scheme: str = "exact",
    networks: Optional[Sequence[str]] = X402_NETWORKS_EXPLICIT,
) -> dict[str, Any]:
    """Build an x402 route config with explicit networks, fail closed.

    ``networks`` defaults to :data:`X402_NETWORKS_EXPLICIT`; passing
    ``None`` or an empty sequence raises ``ValueError`` -- we never rely
    on SDK defaults, which from CDP 1.56.0 expand an "upto" route with no
    networks to Base AND Solana.  All production route construction must
    go through this function.
    """
    if networks is None or len(list(networks)) == 0:
        raise ValueError(
            "x402 route networks must be explicit (got None/empty); "
            "refusing to rely on CDP SDK default network expansion"
        )
    seen: list[str] = []
    for net in networks:
        net = str(net).strip().lower()
        if not net or net in seen:
            continue
        seen.append(net)
    if not seen:
        raise ValueError("x402 route networks must be explicit (all blank)")
    return {"scheme": str(scheme), "networks": seen}


def facilitator_route(
    *,
    scheme: str = "exact",
    networks: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Seam for the future CdpX402Client/facilitator integration.

    Intercepts ``networks=None`` BEFORE it reaches the SDK and substitutes
    :data:`X402_NETWORKS_EXPLICIT`, so the SDK's default-expansion (Base +
    Solana from 1.56.0 on an "upto" route with no networks) can never fire
    on our behalf.  Callers pass the returned ``networks`` list explicitly
    to the SDK call.
    """
    resolved: Optional[Sequence[str]] = (
        X402_NETWORKS_EXPLICIT if networks is None else networks
    )
    return build_route_config(scheme=scheme, networks=resolved)


# ---------------------------------------------------------------------------
# Dev-watch 317 — Bazaar discovery extension completeness + settle-payload
# attachment (SWARM seller log, 2026-09-28, updated 2026-10-02).
#
# SWARM settled twelve test purchases that never appeared in Coinbase's
# Bazaar. Two seller-side causes: (1) its discovery extension had
# ``info.input`` but NO ``schema`` — Coinbase's validator rejects that as
# an invalid discovery configuration; (2) the Bazaar indexer reads
# discovery data from ``PaymentPayload.resource`` and
# ``PaymentPayload.extensions`` at settle time, and the official client
# does not copy them there — an adapter forwarding only ``x402Version`` /
# ``accepted`` / ``payload`` silently drops the indexing data.
#
# Our rules, enforced here so the shape cannot drift per-resource:
#   * EVERY 402 payload built by build_payment_payload carries
#     ``extensions.bazaar`` generated by the ONE helper
#     build_bazaar_discovery_extension(): ``info.input`` describing the
#     paid call AND a full ``schema`` whose
#     ``properties.input.properties.body`` is the resource's real input
#     schema (BAZAAR_INPUT_SCHEMAS — field names mirror what each serve
#     handler in mvp_blueprints.billing._paid_skill_dispatch reads).
#   * validate_bazaar_extension() checks the documented validator
#     requirements locally (schema present, types sane).
#   * build_facilitator_payload() — the payload handed to the
#     facilitator/settle step when that integration lands — copies
#     ``resource`` and ``extensions`` explicitly and FAILS CLOSED when
#     either is missing or invalid, never silently dropping them.
# Payload shape + local validation only: no listing attempt, no live
# facilitator call (the CDP facilitator route decision is founder-gated).
# ---------------------------------------------------------------------------

#: JSON Schema primitive/composite types accepted in an input schema.
_JSON_SCHEMA_TYPES: Final = frozenset(
    {"string", "integer", "number", "boolean", "array", "object", "null"}
)

#: Per-resource input schemas for the paid call body. Only resources
#: whose serve handler reads business input are listed; every other
#: resource takes no business input beyond the payment itself and gets
#: the freeform-object default in bazaar_input_schema().
BAZAAR_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "wallet-sanctions-screen": {
        "type": "object",
        "properties": {
            "address": {
                "type": "string",
                "description": "Wallet address to screen (0x...); "
                "'wallet' and 'wallet_address' are accepted aliases",
            },
            "wallet": {"type": "string"},
            "wallet_address": {"type": "string"},
        },
    },
    "kya-trust-score": {
        "type": "object",
        "properties": {
            "agent_id": {"type": "string"},
            "wallet": {
                "type": "string",
                "description": "Agent wallet address",
            },
        },
    },
    "web-search-read": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "description": "'search' (requires query) or 'read' "
                "(requires url)",
            },
            "query": {"type": "string"},
            "url": {"type": "string"},
        },
        "required": ["mode"],
    },
    "local-business-site-check": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "business_name": {"type": "string"},
        },
        "required": ["url"],
    },
    "market-data-read": {
        "type": "object",
        "properties": {
            "pair": {
                "type": "string",
                "description": "Chainlink pair, e.g. ETH/USD",
            },
            "mode": {
                "type": "string",
                "description": "Read mode; default 'price'",
            },
        },
        "required": ["pair"],
    },
    "polymarket-whale-signal": {
        "type": "object",
        "properties": {
            "wallet": {"type": "string"},
            "market_id": {"type": "string"},
            "window_minutes": {"type": "integer"},
        },
    },
    "toa-micro-decision": {
        "type": "object",
        "properties": {
            "options": {"type": "array", "items": {"type": "object"}},
            "context": {"type": "object"},
            "decision_id": {"type": "string"},
        },
        "required": ["options"],
    },
}


def bazaar_input_schema(resource_id: str) -> dict[str, Any]:
    """The resource's paid-call body schema (deep copy; safe to embed)."""
    schema = BAZAAR_INPUT_SCHEMAS.get(resource_id)
    if schema is None:
        return {"type": "object", "properties": {}}
    return copy.deepcopy(schema)


def _bazaar_example_value(name: str, prop: dict[str, Any]) -> Any:
    """Type-valid placeholder for one input-schema property."""
    if name in ("address", "wallet", "wallet_address", "agent_wallet"):
        return "0x0000000000000000000000000000000000000000"
    if name == "url":
        return "https://example.com"
    ptype = prop.get("type")
    if ptype == "integer" or ptype == "number":
        return 0
    if ptype == "boolean":
        return False
    if ptype == "array":
        return []
    if ptype == "object":
        return {}
    return ""


def build_bazaar_discovery_extension(
    resource: dict[str, Any], resource_id: str
) -> dict[str, Any]:
    """Build the complete ``extensions`` block for a 402 payload.

    The single generator for the Bazaar discovery extension: every paid
    resource gets the identical shape — ``info.input`` describing the
    paid call (with a type-valid example body) and a full ``schema``
    (the JSON Schema the info block must validate against, embedding
    the resource's real input schema at
    ``properties.input.properties.body``). A resource can no longer ship
    the SWARM shape (info.input with no schema) because no code path
    builds the extension by hand.
    """
    body_schema = bazaar_input_schema(resource_id)
    example_body = {
        name: _bazaar_example_value(name, prop)
        for name, prop in (body_schema.get("properties") or {}).items()
        if isinstance(prop, dict)
    }
    return {
        "bazaar": {
            "info": {
                "input": {
                    "type": "http",
                    "method": "POST",
                    "bodyType": "json",
                    "body": example_body,
                },
                "output": {
                    "type": "json",
                    "example": {"ok": True, "resource": resource_id},
                },
            },
            "schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {
                    "input": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["http"],
                            },
                            "method": {
                                "type": "string",
                                "enum": ["GET", "POST"],
                            },
                            "bodyType": {
                                "type": "string",
                                "enum": ["json", "form-data", "text"],
                            },
                            "body": body_schema,
                            "queryParams": {"type": "object"},
                            "headers": {
                                "type": "object",
                                "additionalProperties": {"type": "string"},
                            },
                        },
                        "required": ["type", "method"],
                        "additionalProperties": False,
                    },
                    "output": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["json"],
                            },
                            "example": {"type": "object"},
                        },
                        "required": ["type"],
                    },
                },
                "required": ["input"],
            },
        }
    }


def validate_bazaar_extension(extension: Any) -> list[str]:
    """Local check of the documented Bazaar validator requirements.

    Returns a list of problems (empty = valid). Structural and
    dependency-free: the extension must carry ``info.input`` (type
    http, a real method) AND a full ``schema`` (object schema requiring
    ``input``, embedding an object body schema whose every property has
    a sane JSON Schema type). This is the check that would have caught
    the SWARM shape — info.input present, schema absent — before any
    listing attempt.
    """
    if not isinstance(extension, dict) or not isinstance(
        extension.get("bazaar"), dict
    ):
        return ["extensions.bazaar missing or not an object"]
    bazaar = extension["bazaar"]
    errors: list[str] = []
    info = bazaar.get("info")
    if not isinstance(info, dict):
        errors.append("bazaar.info missing or not an object")
        info = {}
    info_input = info.get("input")
    if not isinstance(info_input, dict):
        errors.append("bazaar.info.input missing or not an object")
    else:
        if info_input.get("type") != "http":
            errors.append("bazaar.info.input.type must be 'http'")
        if info_input.get("method") not in (
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        ):
            errors.append("bazaar.info.input.method missing or unsupported")
    schema = bazaar.get("schema")
    if not isinstance(schema, dict):
        errors.append("bazaar.schema missing or not an object")
        return errors
    if schema.get("type") != "object":
        errors.append("bazaar.schema.type must be 'object'")
    if "input" not in (schema.get("required") or []):
        errors.append("bazaar.schema.required must include 'input'")
    props = schema.get("properties")
    input_schema = props.get("input") if isinstance(props, dict) else None
    if not isinstance(input_schema, dict):
        errors.append("bazaar.schema.properties.input missing")
        return errors
    body_schema = (input_schema.get("properties") or {}).get("body")
    if not isinstance(body_schema, dict) or body_schema.get("type") != "object":
        errors.append(
            "bazaar.schema input body schema missing or not an object schema"
        )
        return errors
    for name, prop in (body_schema.get("properties") or {}).items():
        if not isinstance(prop, dict) or prop.get("type") not in _JSON_SCHEMA_TYPES:
            errors.append(
                f"bazaar input schema property {name!r} has insane type "
                f"{(prop or {}).get('type')!r}"
            )
    return errors


def build_facilitator_payload(
    payment: dict[str, Any],
    *,
    payload_body: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble the PaymentPayload handed to the facilitator/settle step.

    Item 317: the Bazaar indexer reads ``resource`` and ``extensions``
    from THIS payload at settle time, and a client/adapter that forwards
    only ``x402Version`` / ``accepted`` / ``payload`` silently drops
    them. This builder copies both explicitly from the 402 payment
    payload and FAILS CLOSED (ValueError) when either is missing or the
    extension is invalid — the drop becomes a loud local error, not a
    silent unlisting. No facilitator call is made here; this is the
    canonical adapter the founder-gated facilitator integration will
    use when it lands.
    """
    if not isinstance(payment, dict):
        raise ValueError("facilitator payload source must be a dict")
    resource_ref = payment.get("resource")
    if not resource_ref:
        raise ValueError(
            "facilitator payload missing resource (item 317: the "
            "indexer reads PaymentPayload.resource at settle time)"
        )
    errors = validate_bazaar_extension(payment.get("extensions"))
    if errors:
        raise ValueError(
            "facilitator payload extensions invalid (item 317): "
            + "; ".join(errors)
        )
    # The accepted-requirements block is SELECTED from the 402 payment
    # payload, never rebuilt here: route fields (scheme/network/...)
    # originate only in build_route_config/build_payment_payload (the
    # dev-watch 78 explicit-network invariant), and this adapter must
    # not become a second route constructor.
    accepted = {
        key: payment[key]
        for key in (
            "scheme",
            "network",
            "networks",
            "chainId",
            "maxAmountRequired",
            "payTo",
            "asset",
        )
        if key in payment
    }
    return {
        "x402Version": payment.get("x402Version", 1),
        "accepted": accepted,
        "resource": resource_ref,
        "extensions": payment["extensions"],
        "payload": dict(payload_body or {}),
    }


def build_payment_payload(
    resource: dict[str, Any],
    resource_id: str,
    challenge_id: str,
    *,
    scheme: str = "exact",
) -> dict[str, Any]:
    """Build the 402 payment payload for a resource.

    The route (scheme + explicit networks) always comes from
    :func:`build_route_config`; no code path constructs a route without
    it.  ``networks`` (the full explicit list) is included alongside the
    legacy singular ``network`` field so facilitators honoring the x402
    v2 route format see the binding too.
    """
    route = build_route_config(scheme=scheme)
    return {
        "x402Version": 1,
        "scheme": route["scheme"],
        "network": route["networks"][0],
        "networks": route["networks"],
        "chainId": resource["chain_id"],
        "maxAmountRequired": resource["amount_atomic"],
        "resource": f"/x402/{resource_id}",
        "description": resource["description"],
        "payTo": resource["treasury"],
        "asset": resource["token_address"],
        "extra": {
            "challenge_id": challenge_id,
            "token": resource["token"],
            "amount_display": resource["amount_display"],
            "decimals": resource["token_decimals"],
            "skill_id": resource.get("skill_id", ""),
        },
        # Dev-watch 317: complete Bazaar discovery extension on every
        # 402 (info.input AND full schema), helper-generated only.
        "extensions": build_bazaar_discovery_extension(resource, resource_id),
    }


# ---------------------------------------------------------------------------
# W-102 round 3 — creation-time wallet-ownership proof.
#
# Round 2 bound every challenge to a payer_wallet, but the binding itself
# was a bare address string: create_challenge() accepted ANY payer_wallet
# with no proof the caller owned it. The red-team r3 PoC minted a challenge
# naming the VICTIM's wallet, watched the public treasury for the victim's
# payment, and front-ran /api/x402/verify — binding check and on-chain
# payer check both passed truthfully, and UNIQUE(tx_hash) first-claim-wins
# handed the attacker's challenge the victim's entitlement (access_token),
# leaving the victim's own verify to fail tx_hash_already_used. Cost: zero.
#
# Fix: a bare address string is no longer a capability. Challenge creation
# is now two-phase:
#   1. create_challenge(resource_id) mints an UNBOUND draft (payer_wallet="").
#      An unbound draft can never be fulfilled through the public verify
#      route (require_bound_challenge=True -> challenge_unbound), so minting
#      is harmless.
#   2. bind_challenge_payer(challenge_id, payer_wallet, signature) verifies
#      an EIP-191 personal_sign signature by payer_wallet over
#      challenge_bind_message() — (challenge_id | resource_id | payer_wallet)
#      — with require_low_s canonicality, and only then writes the binding.
#      Without the wallet's private key, an attacker cannot bind the
#      victim's wallet to their own challenge.
#
# Why creation-time binding (not a verify-time fresh signature, the listed
# alternative): the signature covers the SERVER-MINTED challenge_id, so it
# authorizes exactly one binding on exactly one challenge — no
# consumed-signature/replay database is needed, and a leaked signature can
# never be replayed onto a different challenge. Binding at creation also
# kills the front-run race at mint instead of at verify: under the
# verify-time design the attacker could still mint unlimited victim-named
# challenges for free and race the victim's verify call; here the attacker
# can mint only unbound drafts, which are unredeemable and unclaimable.
# ---------------------------------------------------------------------------

#: EIP-191 personal_sign domain for challenge-ownership binding.
X402_BIND_DOMAIN = "SINCOR-X402-CHALLENGE-BIND"


def challenge_bind_message(
    challenge_id: str, resource_id: str, payer_wallet: str
) -> str:
    """Canonical message the payer signs to prove wallet ownership.

    Public so clients/SDKs can build byte-identical messages; the server
    always rebuilds it from its own stored challenge_id/resource_id, so a
    caller cannot substitute a different challenge, resource, or wallet
    into a signature obtained elsewhere.
    """
    return "|".join(
        [
            X402_BIND_DOMAIN,
            str(challenge_id),
            str(resource_id),
            str(payer_wallet).strip().lower(),
        ]
    )


def _recover_bind_signer(message: str, signature: str) -> str:
    """Recover the signer address of a challenge-binding signature.

    Mirrors a2a_inbound_market._recover_eip191: require_low_s canonicality
    first (high-s twins are rejected even though they recover to the same
    address — the codebase standard), then eth_account EIP-191 recovery.
    Raises ValueError on any failure; callers fail closed.
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        from sincor2.sig_canonical import require_low_s
    except Exception as exc:
        raise ValueError("signature verification unavailable") from exc
    try:
        raw = require_low_s(signature)
        return str(
            Account.recover_message(encode_defunct(text=message), signature=raw)
        )
    except Exception as exc:
        raise ValueError("bad signature") from exc


def _valid_wallet_format(wallet: str) -> bool:
    """Format-only 0x-address check (mirrors mvp_app.validate_wallet)."""
    import re

    return bool(
        isinstance(wallet, str) and re.match(r"^0x[a-fA-F0-9]{40}$", wallet.strip())
    )


#: WREN PR 4: per-source cap on OPEN UNBOUND challenges. Checkout mints
#: one DB row per call; without a cap a single source can grow the
#: x402_challenges table (and the 402 surface) without bound. "Open
#: unbound" = status 'pending', no payer bound yet, not yet expired.
MAX_OPEN_UNBOUND_CHALLENGES_PER_SOURCE = 20


def create_challenge(resource_id: str, *, source_key: str = "") -> dict[str, Any]:
    """Mint an UNBOUND challenge draft (W-102 round 3).

    The draft records NO payer binding — a bare address string is no longer
    accepted at creation. The caller must complete
    bind_challenge_payer() with an EIP-191 ownership signature before the
    challenge can be fulfilled through the public verify route
    (require_bound_challenge=True rejects unbound drafts as
    challenge_unbound).

    ``source_key`` names the minting source (payer wallet when the caller
    knows it, else the client IP). When given, minting is refused with
    ``error="rate_limited"`` / ``http_status=429`` once the source already
    holds MAX_OPEN_UNBOUND_CHALLENGES_PER_SOURCE open unbound challenges.
    Callers that pass no source (legacy internal mints) are uncapped.
    """
    resource = get_resource(resource_id)
    if not resource:
        return {"ok": False, "error": "unknown_resource"}

    now = datetime.now(timezone.utc)
    challenge_id = f"x402-{secrets.token_hex(8)}"
    expires = now + timedelta(seconds=resource["ttl_seconds"])
    source = (source_key or "").strip().lower()

    with _conn() as conn:
        if source:
            (open_unbound,) = conn.execute(
                """SELECT COUNT(*) FROM x402_challenges
                   WHERE source_key = ? AND status = 'pending'
                     AND (payer_wallet = '' OR payer_wallet IS NULL)
                     AND expires_at > ?""",
                (source, now.isoformat()),
            ).fetchone()
            if open_unbound >= MAX_OPEN_UNBOUND_CHALLENGES_PER_SOURCE:
                logger.warning(
                    "x402: challenge mint refused for source %s "
                    "(%d open unbound)", source, open_unbound)
                return {"ok": False, "error": "rate_limited",
                        "http_status": 429}
        conn.execute(
            """INSERT INTO x402_challenges
               (challenge_id, resource_id, amount_atomic, amount_display, payer_wallet,
                source_key, status, created_at, expires_at)
               VALUES (?, ?, ?, ?, '', ?, 'pending', ?, ?)""",
            (
                challenge_id,
                resource_id,
                resource["amount_atomic"],
                resource["amount_display"],
                source or None,
                now.isoformat(),
                expires.isoformat(),
            ),
        )
        conn.commit()

    payload = build_payment_payload(resource, resource_id, challenge_id)

    return {
        "ok": False,
        "http_status": 402,
        "error": "ownership_proof_required",
        "challenge_id": challenge_id,
        "resource_id": resource_id,
        "expires_at": expires.isoformat(),
        "payment": payload,
        "bind": {
            "bind_url": "/api/x402/challenge-bind",
            "sign_message_format": (
                "SINCOR-X402-CHALLENGE-BIND|<challenge_id>|<resource_id>|"
                "<payer_wallet_lowercase> — sign with EIP-191 personal_sign "
                "using the payer wallet, then POST "
                "{challenge_id, payer_wallet, signature} to /api/x402/challenge-bind"
            ),
        },
        "message": (
            f"Send {resource['amount_display']} {resource['token']} "
            f"to {resource['treasury']} on Base after binding your wallet"
        ),
        "note": (
            "Ownership proof required: this challenge is unbound and cannot "
            "be fulfilled until the payer wallet signs the bind message. "
            "The minting route returns the exact 'sign_message' for the "
            "wallet you supplied."
        ),
    }


def purge_expired_challenges() -> int:
    """Delete expired, never-fulfilled challenge rows (WREN PR 4).

    Only ``status='pending'`` rows past their ``expires_at`` are removed:
    fulfilled rows are the audit/replay record for a completed payment
    and are never purged here. Called from the daily-ops scheduler job
    so the table cannot grow without bound. Returns rows deleted.
    """
    now = datetime.now(timezone.utc)
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM x402_challenges "
            "WHERE status = 'pending' AND expires_at <= ?",
            (now.isoformat(),),
        )
        conn.commit()
        return cur.rowcount


def challenge_status(challenge_id: str) -> str | None:
    """Read-only status lookup for one challenge (WREN PR 4 repair).

    Returns the row's ``status`` ('pending', 'fulfilled', ...) or None
    when no such challenge exists. The /rail/settle rate limiter's
    dynamic limit uses this to give proven-duplicate acks for
    fulfilled challenges a bounded 600/hour exemption; it never
    writes.
    """
    cid = (challenge_id or "").strip()
    if not cid:
        return None
    with _conn() as conn:
        row = conn.execute(
            "SELECT status FROM x402_challenges WHERE challenge_id=?",
            (cid,),
        ).fetchone()
    return str(row["status"]) if row else None


def bind_challenge_payer(
    challenge_id: str, payer_wallet: str, signature: str
) -> dict[str, Any]:
    """Bind a payer wallet to a challenge draft after ownership proof.

    Verifies the EIP-191 personal_sign signature over
    challenge_bind_message(challenge_id, resource_id, payer_wallet) BEFORE
    the binding is written; any unsigned, malformed, high-s, or
    wrong-signer signature is rejected and the row is left unbound.
    A draft can be bound exactly once (challenge_already_bound).
    """
    cid = (challenge_id or "").strip()
    if not cid:
        return {"ok": False, "error": "challenge_id_required"}
    wallet = (payer_wallet or "").strip().lower()
    if not _valid_wallet_format(wallet):
        return {"ok": False, "error": "payer_wallet_required"}
    if not signature:
        return {"ok": False, "error": "ownership_signature_required"}

    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM x402_challenges WHERE challenge_id=?", (cid,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "challenge_not_found"}
        if row["status"] == "fulfilled":
            return {"ok": False, "error": "challenge_already_bound"}
        expires = row["expires_at"]
        if expires and _parse_iso(expires) < datetime.now(timezone.utc):
            return {"ok": False, "error": "challenge_expired"}
        if (row["payer_wallet"] or "").strip():
            return {"ok": False, "error": "challenge_already_bound"}

        resource_id = str(row["resource_id"])
        resource = get_resource(resource_id)
        if not resource:
            return {"ok": False, "error": "unknown_resource"}
        message = challenge_bind_message(cid, resource_id, wallet)
        try:
            recovered = _recover_bind_signer(message, signature)
        except ValueError:
            logger.warning(
                "x402: bind rejected for challenge %s: bad ownership signature",
                cid,
            )
            return {"ok": False, "error": "ownership_proof_failed"}
        if recovered.lower() != wallet:
            logger.warning(
                "x402: bind rejected for challenge %s: signature recovers to "
                "%s, not the claimed payer",
                cid,
                recovered,
            )
            return {"ok": False, "error": "ownership_proof_failed"}

        # Atomic bind: the UPDATE itself is the claim. The conditional
        # WHERE closes the race between the SELECT above and this write —
        # two concurrent binds cannot both succeed; the loser sees
        # rowcount 0 and gets challenge_already_bound.
        cur = conn.execute(
            "UPDATE x402_challenges SET payer_wallet=? WHERE challenge_id=? "
            "AND status='pending' AND (payer_wallet='' OR payer_wallet IS NULL)",
            (wallet, cid),
        )
        if cur.rowcount != 1:
            conn.rollback()
            logger.warning(
                "x402: bind lost race for challenge %s; already bound", cid
            )
            return {"ok": False, "error": "challenge_already_bound"}
        conn.commit()

    payload = build_payment_payload(resource, resource_id, cid)

    return {
        "ok": False,
        "http_status": 402,
        "error": "payment_required",
        "challenge_id": cid,
        "resource_id": resource_id,
        "payer_wallet": wallet,
        "ownership_proven": True,
        "expires_at": row["expires_at"],
        "payment": payload,
        "message": (
            f"Send {resource['amount_display']} {resource['token']} "
            f"to {resource['treasury']} on Base"
        ),
    }


def bind_server_issued_challenge(
    challenge_id: str, payer_wallet: str
) -> dict[str, Any]:
    """Bind a SERVER-ISSUED challenge to the wallet it was issued for.

    Restricted counterpart to bind_challenge_payer() for flows where the
    server itself mints the challenge for a specific wallet it has
    already recorded in its own issuance table, and where fulfillment
    accrues EXCLUSIVELY to that wallet — currently only rail
    subscription renewals (rail_subscriptions.issue_renewal_challenge):
    the wallet is required at issuance, recorded in
    rail_sub_challenges, and settle_renewal extends only that wallet's
    subscription, so a settler can never redirect the benefit to
    themselves. This is what lets subscription settle pass
    require_bound_challenge=True consistently with the pack rail.

    This must NEVER be used for pack challenges: pack fulfillment
    (download URL / intake) is delivered to whoever settles, so a
    server-side string binding would let an attacker bind the victim's
    wallet to their own challenge and front-run the victim's payment
    (the W-102 round-3 attack). Pack challenges are bound only via
    bind_challenge_payer() with an EIP-191 ownership signature.

    Same atomic claim semantics as bind_challenge_payer(): the
    conditional UPDATE is the claim; an already-bound or non-pending
    challenge is refused (challenge_already_bound).
    """
    cid = (challenge_id or "").strip()
    if not cid:
        return {"ok": False, "error": "challenge_id_required"}
    wallet = (payer_wallet or "").strip().lower()
    if not _valid_wallet_format(wallet):
        return {"ok": False, "error": "payer_wallet_required"}

    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM x402_challenges WHERE challenge_id=?", (cid,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "challenge_not_found"}
        if row["status"] != "pending" or (row["payer_wallet"] or "").strip():
            return {"ok": False, "error": "challenge_already_bound"}
        cur = conn.execute(
            "UPDATE x402_challenges SET payer_wallet=? WHERE challenge_id=? "
            "AND status='pending' AND (payer_wallet='' OR payer_wallet IS NULL)",
            (wallet, cid),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return {"ok": False, "error": "challenge_already_bound"}
        conn.commit()
    return {"ok": True, "challenge_id": cid, "payer_wallet": wallet,
            "ownership_proven": False, "server_issued": True}


def _parse_iso(ts: str) -> datetime:
    """Parse the ISO timestamps this module stores (tolerates a Z suffix)."""
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


# W-103: fulfilled-challenge replay must be authenticated. A re-issue of an
# already-fulfilled challenge's entitlement token requires a fresh EIP-191
# signature over the canonical replay message from the wallet recorded on
# the original challenge. Mirrors the heartbeat proof convention
# (SINCOR-HEARTBEAT\n<agent_id>\n<ts_ms>) in a2a_inbound_ext.py.
REPLAY_PROOF_DOMAIN = "SINCOR-X402-REPLAY"
REPLAY_MAX_SKEW_MS = 600_000  # 10-minute freshness window
REPLAY_AUTH_FAILED = {"ok": False, "error": "replay_auth_failed"}


def build_replay_message(challenge_id: str, ts_ms: int) -> str:
    """Canonical EIP-191 message a payer signs to re-issue a fulfilled
    challenge's token. Binds the canonical challenge_id and a millisecond
    timestamp so the signature cannot be reused for another challenge or
    outside the freshness window."""
    return f"{REPLAY_PROOF_DOMAIN}\n{challenge_id}\n{int(ts_ms)}"


def _replay_auth_ok(
    recorded_wallet: str,
    challenge_id: str,
    replay_signature: str,
    replay_ts_ms: Any,
) -> bool:
    """W-103 gate for BOTH fulfilled paths of verify_challenge.

    Checks, in order: (1) signature and timestamp present and well-formed;
    (2) |now_ms - ts_ms| <= 10 minutes; (3) signer recovered via
    encode_defunct; (4) recorded payer_wallet non-blank and matching the
    recovered address case-insensitively. ANY failure returns False — the
    caller maps every failure to the same generic replay_auth_failed error
    so no existence oracle or distinguishing behavior exists."""
    if not replay_signature or replay_ts_ms is None:
        return False
    try:
        ts_ms = int(str(replay_ts_ms).strip())
    except (TypeError, ValueError):
        return False
    if ts_ms < 0:
        return False
    if abs(int(time.time() * 1000) - ts_ms) > REPLAY_MAX_SKEW_MS:
        return False
    wallet = (recorded_wallet or "").strip()
    if not wallet:
        return False
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        from sincor2.sig_canonical import require_low_s
        require_low_s(replay_signature)  # reject (r, n-s) malleation twins
        signer = str(Account.recover_message(
            encode_defunct(text=build_replay_message(challenge_id, ts_ms)),
            signature=replay_signature))
    except Exception:
        return False
    return signer.lower() == wallet.lower()


# ---------------------------------------------------------------------------
# D2 item 3 — high-value rail settlements: finality + OFAC gate.
#
# Threshold encoding: the gate is keyed on the RESOURCE CLASS, using the
# existing pack/subscription resource distinction in
# config/x402_pricing.yaml, not on a raw amount (amounts are denominated
# per-resource and the class already encodes the value tier):
#   * "rail-pack-*" — B2B / digital skill packs, $97–$2,997 one-off
#     entitlements (intake/download/subscription-grade fulfillment).
#   * "rail-sub-*"  — rail subscriptions, $297–$2,997 per 30-day period.
# These are HIGH-VALUE rail settlements: verify_challenge refuses to
# fulfill them below finality_confirmations() (12 on Base, the platform
# checkout depth) and screens the recovered payer against the vendored
# OFAC SDN snapshot via platform_payments.screen_payer_sanctions, with
# the platform path's fail behavior mirrored exactly: a sanctions HIT
# always refuses; a screening outage / stale (>48h) snapshot refuses in
# strict mode (PLATFORM_OFAC_STRICT, default true) and proceeds with a
# warning otherwise. Platform checkouts themselves are already gated
# inside platform_payments (verify_checkout/_maybe_finalize).
#   * Every other resource (per-call skill serves: agent_task,
#     web-search-read, wallet-sanctions-screen, ... — $0.005–$5 reads)
#     is NOT high-value: no 12-confirmation wait and no OFAC gate is
#     imposed here, so a small serve is never delayed for finality.
# Both high-value entry routes funnel through verify_challenge —
# POST /rail/settle (rail_routes.settle_rail_order) and
# POST /api/x402/verify (mvp_blueprints.billing) — so this one gate
# covers both surfaces.
HIGH_VALUE_RESOURCE_PREFIXES: Final = ("rail-pack-", "rail-sub-")


def is_high_value_rail_resource(resource_id: str) -> bool:
    """True for pack/subscription resources (the D2 high-value class)."""
    rid = str(resource_id or "")
    return rid.startswith(HIGH_VALUE_RESOURCE_PREFIXES)


def _high_value_gate(vr: dict[str, Any], resource_id: str) -> dict[str, Any] | None:
    """Finality + OFAC gate for high-value rail settlements.

    ``vr`` is a successful verify_treasury_transfer result (it already
    carries confirmations / required_confirmations). Returns None when
    the settle may proceed, or the refusal dict verify_challenge should
    return (nothing is fulfilled and no registry claim is made).
    """
    conf = int(vr.get("confirmations") or 0)
    required = int(vr.get("required_confirmations") or 12)
    if conf < required:
        return {
            "ok": False,
            "error": "awaiting_finality",
            "status": "awaiting_finality",
            "confirmations": conf,
            "required_confirmations": required,
            "resource_id": resource_id,
            "detail": (
                f"high-value rail settlement requires {required} "
                f"confirmations, tx has {conf}"),
        }
    # OFAC: reuse the platform path's screening entry point and strict
    # mode verbatim (resolved through the module so operator/test
    # patching of platform_payments applies here too).
    from sincor2 import platform_payments as _pp

    payer = str(vr.get("payer_wallet") or "")
    screen = _pp.screen_payer_sanctions(payer)
    verdict = screen.get("verdict")
    if verdict == "hit":
        logger.warning(
            "x402: high-value settle refused: OFAC hit for payer %s "
            "resource %s", payer, resource_id)
        return {"ok": False, "error": "sanctions_hit",
                "ofac_verdict": verdict,
                "ofac_detail": screen.get("detail", ""),
                "resource_id": resource_id}
    if verdict == "unavailable" and _pp.ofac_strict_mode():
        logger.warning(
            "x402: high-value settle refused: OFAC screening unavailable "
            "(strict) for resource %s: %s", resource_id,
            screen.get("detail", ""))
        return {"ok": False, "error": "ofac_screening_unavailable",
                "ofac_verdict": verdict,
                "ofac_detail": screen.get("detail", ""),
                "resource_id": resource_id}
    if verdict == "unavailable":
        logger.warning(
            "x402: high-value settle proceeding with OFAC screening "
            "unavailable (PLATFORM_OFAC_STRICT=false) for resource %s: %s",
            resource_id, screen.get("detail", ""))
    return None


def verify_challenge(
    challenge_id: str,
    tx_hash: str,
    *,
    payer_wallet: str = "",
    require_bound_challenge: bool = False,
    replay_signature: str = "",
    replay_ts_ms: Any = None,
) -> dict[str, Any]:
    if not challenge_id or not tx_hash:
        return {"ok": False, "error": "challenge_id_and_tx_hash_required"}

    # P1-8: normalize before any comparison/storage so case variants of the
    # same hash collide on the UNIQUE index instead of slipping through.
    tx_hash = str(tx_hash).strip().lower()

    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM x402_challenges WHERE challenge_id=?", (challenge_id,)
        ).fetchone()
        if not row:
            # W-103: no existence oracle on the replay path. A caller that
            # supplied replay credentials gets the same generic failure as
            # any other replay-auth failure, whether or not the challenge
            # exists.
            if replay_signature or replay_ts_ms is not None:
                return dict(REPLAY_AUTH_FAILED)
            return {"ok": False, "error": "challenge_not_found"}
        if row["status"] == "fulfilled":
            # W-103: the victim's live token is re-issued ONLY with a valid
            # EIP-191 replay signature from the recorded payer wallet.
            if not _replay_auth_ok(
                str(row["payer_wallet"] or ""),
                str(row["challenge_id"]),
                replay_signature,
                replay_ts_ms,
            ):
                return dict(REPLAY_AUTH_FAILED)
            return {
                "ok": True,
                "status": "already_fulfilled",
                "access_token": row["access_token"],
                "resource_id": row["resource_id"],
            }

        expires = row["expires_at"]
        if expires and _parse_iso(expires) < datetime.now(timezone.utc):
            return {"ok": False, "error": "challenge_expired"}

        # W-102: bind the claim to the challenge's recorded payer. The
        # challenge row records payer_wallet at bind_challenge_payer time
        # (W-102 round 3: only after an EIP-191 ownership signature by that
        # wallet — a bare address string is no longer a capability). A
        # claim naming any other wallet is a front-run attempt: the on-chain
        # payer check alone cannot stop it, because the attacker truthfully
        # names the victim's wallet while claiming under their own
        # challenge_id. A claim with no wallet on a bound challenge is
        # likewise rejected (anonymous claim of someone else's challenge).
        #
        # W-102 round 2: a challenge with no recorded payer is a bearer
        # instrument — anyone observing a confirmed treasury transfer could
        # claim the victim's tx_hash under it (first-claim-wins), even while
        # truthfully naming the victim's wallet. Public verify callers pass
        # require_bound_challenge=True, so such claims are rejected here,
        # before any on-chain call. Helper-level callers (existing tests)
        # keep the previous behavior: blank-recorded challenges are bound
        # only to the on-chain payer.
        challenge_payer = (row["payer_wallet"] or "").strip().lower()
        supplied_payer = (payer_wallet or "").strip().lower()
        if require_bound_challenge and not challenge_payer:
            return {"ok": False, "error": "challenge_unbound"}
        if challenge_payer and supplied_payer != challenge_payer:
            return {"ok": False, "error": "challenge_payer_mismatch"}

    resource = get_resource(str(row["resource_id"]))
    vr = verify_treasury_transfer(
        tx_hash,
        token=str((resource or {}).get("token", "SINC")),
        expected_atomic=int(row["amount_atomic"]),
        treasury=(resource or {}).get("treasury"),
        payer_wallet=payer_wallet,
    )
    if not vr.get("ok"):
        return vr

    # D2 item 3: high-value rail settlements (rail-pack-* / rail-sub-*)
    # must clear finality (12 confirmations) + OFAC screening before any
    # fulfillment. Small per-call skill serves skip this gate entirely.
    if is_high_value_rail_resource(str(row["resource_id"])):
        gate_refusal = _high_value_gate(vr, str(row["resource_id"]))
        if gate_refusal is not None:
            return gate_refusal

    access_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    # P1-7: every issued entitlement token carries its own expiry.
    token_expires = (now + timedelta(seconds=ENTITLEMENT_TTL_SECONDS)).isoformat()

    # P1-8 atomic claim: the fulfill UPDATE is itself the claim. It runs in
    # one IMMEDIATE transaction that (a) rechecks the challenge is still
    # pending and unexpired, (b) best-effort rejects a tx_hash already
    # claimed by another challenge (covers DBs where the UNIQUE index could
    # not be created), and (c) relies on UNIQUE(tx_hash) as the
    # authoritative guard under races — insert-or-fail, fulfill only if the
    # claim won.
    with _conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            return {"ok": False, "error": "store_busy"}
        try:
            fresh = conn.execute(
                "SELECT * FROM x402_challenges WHERE challenge_id=?",
                (challenge_id,),
            ).fetchone()
            if not fresh:
                conn.rollback()
                # W-103: no existence oracle on the replay path (mirrors the
                # pre-transaction branch above).
                if replay_signature or replay_ts_ms is not None:
                    return dict(REPLAY_AUTH_FAILED)
                return {"ok": False, "error": "challenge_not_found"}
            if fresh["status"] == "fulfilled":
                conn.rollback()
                # W-103: the lost-the-race branch must authenticate the
                # replayer too — this is where the W-102 bypass used to
                # re-mint the victim's live token with zero auth.
                if not _replay_auth_ok(
                    str(fresh["payer_wallet"] or ""),
                    str(fresh["challenge_id"]),
                    replay_signature,
                    replay_ts_ms,
                ):
                    return dict(REPLAY_AUTH_FAILED)
                return {
                    "ok": True,
                    "status": "already_fulfilled",
                    "access_token": fresh["access_token"],
                    "resource_id": fresh["resource_id"],
                }
            fresh_exp = fresh["expires_at"]
            if fresh_exp and _parse_iso(fresh_exp) < datetime.now(timezone.utc):
                conn.rollback()
                return {"ok": False, "error": "challenge_expired"}
            claimed = conn.execute(
                "SELECT challenge_id FROM x402_challenges WHERE tx_hash=?",
                (tx_hash,),
            ).fetchone()
            if claimed:
                conn.rollback()
                logger.warning(
                    "x402: tx_hash %s already claimed by challenge %s; "
                    "rejecting double-fulfillment of %s",
                    tx_hash, claimed["challenge_id"], challenge_id)
                return {"ok": False, "error": "tx_hash_already_used"}
            # D2 item 1: claim the tx in the shared consumed_onchain_txs
            # registry, inside THIS atomic transaction, so the fulfill
            # and the cross-ledger claim commit or roll back together.
            # A tx already held under a DIFFERENT claim_ref (a platform
            # payment_id, or another challenge) refuses the fulfill; a
            # claim under THIS challenge_id (a rail settle for this
            # same challenge claimed first) is the same entitlement and
            # proceeds idempotently.
            try:
                conn.execute(
                    "INSERT INTO consumed_onchain_txs "
                    "(tx_hash, claim_system, claim_ref, payer_wallet, "
                    " claimed_at) VALUES (?, 'x402', ?, ?, ?)",
                    (tx_hash, challenge_id, vr["payer_wallet"], now_iso),
                )
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    "SELECT claim_ref, claim_system FROM "
                    "consumed_onchain_txs WHERE tx_hash=?",
                    (tx_hash,),
                ).fetchone()
                if existing is None or existing["claim_ref"] != challenge_id:
                    conn.rollback()
                    logger.warning(
                        "x402: tx_hash %s already consumed system-wide "
                        "by %s:%s; rejecting fulfillment of %s",
                        tx_hash,
                        existing["claim_system"] if existing else "?",
                        existing["claim_ref"] if existing else "?",
                        challenge_id)
                    return {"ok": False, "error": "tx_hash_already_used"}
            cur = conn.execute(
                """UPDATE x402_challenges
                   SET status='fulfilled', tx_hash=?, payer_wallet=?,
                       access_token=?, token_expires_at=?, fulfilled_at=?
                   WHERE challenge_id=?""",
                (tx_hash, vr["payer_wallet"], access_token,
                 token_expires, now_iso, challenge_id),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return {"ok": False, "error": "fulfill_conflict"}
            conn.commit()
        except sqlite3.IntegrityError:
            # UNIQUE(tx_hash) fired: this on-chain payment already fulfilled
            # another challenge. Double-fulfillment rejected.
            conn.rollback()
            logger.warning(
                "x402: tx_hash %s replay rejected for challenge %s "
                "(UNIQUE constraint)", tx_hash, challenge_id)
            return {"ok": False, "error": "tx_hash_already_used"}

    return {
        "ok": True,
        "status": "fulfilled",
        "challenge_id": challenge_id,
        "resource_id": row["resource_id"],
        "access_token": access_token,
        "token_expires_at": token_expires,
        "tx_hash": tx_hash,
        "payer_wallet": vr["payer_wallet"],
    }


def finalize_challenge_payment(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok") or result.get("status") != "fulfilled":
        return {}
    resource = get_resource(str(result.get("resource_id") or ""))
    if not resource:
        return {}
    token = resource["token"]
    amount_display = float(result.get("amount_display") or resource["amount_display"])
    payment = record_platform_payment(
        tx_hash=str(result.get("tx_hash") or ""),
        payer_wallet=str(result.get("payer_wallet") or ""),
        token=token,
        amount_atomic=int(result.get("amount_atomic") or resource["amount_atomic"]),
        product_name=f"x402:{resource['id']}",
        plan_id="x402",
        payment_id=str(result.get("challenge_id") or ""),
    )
    inflow = record_inflow(
        amount_display,
        asset=token,
        source="x402_payment",
        usd_estimate=amount_display,
        tx_hash=str(result.get("tx_hash") or ""),
        note=f"x402:{resource['id']}",
        projected=False,
    )
    return {
        "payment_log": payment,
        "treasury_inflow": inflow.to_dict() if hasattr(inflow, "to_dict") else {},
    }


def execute_paid_resource(resource_id: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    resource = get_resource(resource_id)
    if not resource:
        return 404, {"ok": False, "error": "unknown_resource"}
    if resource.get("skill_id"):
        from verticals.loader import instantiate_vertical_agents
        from sincor2.vertical_dispatch import dispatch_vertical_task

        input_payload = payload or {}
        input_text = json.dumps({"payload": input_payload})
        dispatched = dispatch_vertical_task(
            resource["skill_id"],
            input_text,
            {"vertical_agents": instantiate_vertical_agents()},
        )
        if not dispatched:
            return 503, {
                "ok": False,
                "error": "skill_unavailable",
                "resource": resource_id,
                "skill_id": resource["skill_id"],
            }
        output, error = dispatched
        if error:
            return 502, {
                "ok": False,
                "error": "skill_execution_failed",
                "resource": resource_id,
                "skill_id": resource["skill_id"],
                "detail": error,
            }
        try:
            execution = json.loads(output)
        except json.JSONDecodeError:
            execution = {"status": "success", "result": {"raw": output}}
        return 200, {
            "ok": True,
            "resource": resource_id,
            "skill_id": resource["skill_id"],
            "execution": execution,
        }
    return 200, {
        "ok": True,
        "resource": resource_id,
        "message": "Access granted. Resource handler may be extended per config/x402_pricing.yaml.",
    }


def access_granted(access_token: str, resource_id: str, *, consume: bool = True) -> bool:
    """Paid-resource gate with single-use, expiring entitlement tokens.

    P1-7: every access check rejects expired tokens (``token_expires_at``)
    and already-consumed tokens (``token_consumed_at``). With
    ``consume=True`` (default — the serve path), a successful check
    atomically stamps ``token_consumed_at`` via a conditional UPDATE, so
    the token grants access exactly once even under concurrency. Pass
    ``consume=False`` for informational pre-checks that must not burn the
    token. Tokens issued before the P1-7 migration have NULL
    ``token_expires_at`` and are treated as expired (fail closed).
    """
    if not access_token or not resource_id:
        return False
    now = datetime.now(timezone.utc)
    with _conn() as conn:
        row = conn.execute(
            """SELECT challenge_id, token_expires_at, token_consumed_at
               FROM x402_challenges
               WHERE access_token=? AND resource_id=? AND status='fulfilled'""",
            (access_token, resource_id),
        ).fetchone()
        if not row:
            return False
        exp = row["token_expires_at"]
        if not exp:
            # Legacy token (no expiry stamped): fail closed.
            return False
        if _parse_iso(exp) < now:
            return False
        if row["token_consumed_at"]:
            return False
        if consume:
            cur = conn.execute(
                """UPDATE x402_challenges SET token_consumed_at=?
                   WHERE access_token=? AND resource_id=? AND status='fulfilled'
                   AND token_consumed_at IS NULL""",
                (now.isoformat(), access_token, resource_id),
            )
            conn.commit()
            if cur.rowcount != 1:
                # Lost the consumption race: another dispatch won.
                return False
    return True

# ---------------------------------------------------------------------------
# Settle-before-serve guard (dev-watch item 79)
# ---------------------------------------------------------------------------

#: Hard rule: a serve path may only proceed when a settle RECEIPT — not a
#: verify receipt — exists for the payment reference. Item 79 (Payload Tools
#: postmortem, 2026-10-07): sellers who served work on ``verify`` alone did
#: unpaid work on credit, because verify passed on every attempt while settle
#: rejected everything with ``invalid_payload``. Root causes were (a) the
#: token ``name`` field had to be exactly "USD Coin", not "USDC", and (b) a
#: $0.001 amount minimum — neither was in the spec (x402-foundation/x402#961).
#: Every serve path MUST call :func:`require_settled` before doing paid work.
SETTLE_BEFORE_SERVE = True

_SETTLE_OK_STATUSES = {"settled", "confirmed", "success"}

# intent_hash -> "settled" | "rejected": process-wide memory of settle
# outcomes, consulted by require_settled(). This is the nonce-reuse trap
# hedge: when settle rejects with nonce_already_used, the intent is marked
# rejected. Minting a fresh authorization for the SAME intent afterwards
# must NOT be treated as a new payment unless the fresh authorization has
# its own confirmed settle receipt — otherwise the "fix" converts a replay
# rejection into a double payment.
_INTENT_OUTCOMES: dict[str, str] = {}


def register_settle_outcome(intent_hash: str, outcome: str) -> None:
    """Record a terminal settle outcome for an intent.

    ``outcome`` must be ``"settled"`` or ``"rejected"``. A "rejected"
    intent refuses verify-only serve attempts until a fresh authorization
    for the same intent produces its own confirmed settle receipt.
    """
    if not intent_hash:
        raise ValueError("intent_hash required")
    if outcome not in ("settled", "rejected"):
        raise ValueError("outcome must be 'settled' or 'rejected'")
    _INTENT_OUTCOMES[intent_hash] = outcome


def intent_outcome(intent_hash: str) -> str | None:
    """Return the recorded settle outcome for an intent, or None."""
    return _INTENT_OUTCOMES.get(intent_hash)


def reset_intent_registry() -> None:
    """Clear the intent-outcome registry. Test helper only."""
    _INTENT_OUTCOMES.clear()


def require_settled(payment: Any) -> bool:
    """Serve-path guard: return True only if a settle receipt exists.

    ``payment`` is a payment reference dict with keys::

        {"intent_hash": str,
         "verify": {"ok": bool, ...},
         "settle": {"ok": bool, "status": str, "reason": str, ...}}

    Rules:
    - ``SETTLE_BEFORE_SERVE`` must be True (fail closed if ever weakened).
    - verify alone is NEVER enough — verify passed on every attempt in the
      item-79 postmortem while settle rejected everything.
    - A confirmed settle receipt (``ok`` True + settled/confirmed/success
      status) approves and records the intent as settled.
    - A rejected settle marks the intent rejected and returns False.
    - A verify-only reference whose intent was previously rejected (the
      nonce-reuse trap: fresh auth minted after ``nonce_already_used``
      without confirming the first settle) returns False. It can only be
      approved by producing its own confirmed settle receipt.
    """
    if SETTLE_BEFORE_SERVE is not True:
        return False
    if not isinstance(payment, dict):
        return False
    intent = str(payment.get("intent_hash") or "")
    verify = payment.get("verify") or {}
    if not isinstance(verify, dict) or verify.get("ok") is not True:
        return False
    settle = payment.get("settle") or {}
    if isinstance(settle, dict) and settle:
        status = str(settle.get("status") or "").lower()
        if settle.get("ok") is True and status in _SETTLE_OK_STATUSES:
            if intent:
                _INTENT_OUTCOMES[intent] = "settled"
            return True
        # A settle receipt exists but is not successful: record the
        # rejection so the intent cannot be re-served on verify alone.
        if intent:
            _INTENT_OUTCOMES[intent] = "rejected"
        return False
    # Verify-only reference: blocked by the intent registry if a previous
    # settle for this intent was rejected (nonce-reuse trap); blocked in
    # all other cases too — verify is not settlement.
    if intent and _INTENT_OUTCOMES.get(intent) == "rejected":
        return False
    return False


# ---------------------------------------------------------------------------
# Batch-settlement vouchers (dev-watch item 81)
# ---------------------------------------------------------------------------

# VOUCHERS ARE PROMISES, NOT SETTLEMENT.
#
# Coinbase-style x402 batch settlement (2026-10-06): buyers deposit ERC-20
# into onchain escrow and sign offchain vouchers per request; sellers verify
# vouchers, serve, and redeem batched later. This section covers the offchain
# voucher half ONLY. Batch settlement does NOT relax settle-before-serve:
# ``redeem_voucher()`` records an intent to include a voucher in a future
# batch — it creates NO settle receipt. A serve path must still call
# ``require_settled()`` and see a confirmed batch-settlement receipt before
# serving voucher-backed work.
#
# Replay surface: a voucher replayed across redemption batches, and
# voucher-vs-settlement races. Mitigations: nonce uniqueness enforced
# process-wide (``_REDEEMED_NONCES``), idempotent redemption (re-redeeming
# returns the original record and never double-pays), expiry enforcement.

@dataclass
class Voucher:
    """Offchain payment voucher for batch settlement (a promise, not money)."""

    voucher_id: str
    payer: str
    pay_to: str
    amount: float
    token: str
    nonce: str
    expiry_ts: float
    intent_hash: str


# nonce -> redeemed: process-wide redemption memory. A nonce may be redeemed
# exactly once across all batches.
_REDEEMED_NONCES: set[str] = set()

# voucher_id -> redemption record: idempotency memory for redeem_voucher().
_REDEMPTION_RECORDS: dict[str, dict[str, Any]] = {}


def reset_voucher_registry() -> None:
    """Clear voucher redemption memory. Test helper only."""
    _REDEEMED_NONCES.clear()
    _REDEMPTION_RECORDS.clear()


def validate_voucher(voucher: Any) -> dict[str, Any]:
    """Validate a voucher without redeeming it.

    Enforces: nonce present and never previously redeemed (across all
    batches), expiry_ts in the future, amount > 0, payer/pay_to/intent_hash
    present. Returns ``{"ok": True}`` or ``{"ok": False, "error": ...}``.
    """
    if not isinstance(voucher, Voucher):
        return {"ok": False, "error": "not_a_voucher"}
    if not voucher.nonce:
        return {"ok": False, "error": "nonce_required"}
    if voucher.nonce in _REDEEMED_NONCES:
        return {"ok": False, "error": "nonce_already_redeemed"}
    if not voucher.payer:
        return {"ok": False, "error": "payer_required"}
    if not voucher.pay_to:
        return {"ok": False, "error": "pay_to_required"}
    if not voucher.intent_hash:
        return {"ok": False, "error": "intent_hash_required"}
    if voucher.amount is None or voucher.amount <= 0:
        return {"ok": False, "error": "invalid_amount"}
    if voucher.expiry_ts <= time.time():
        return {"ok": False, "error": "voucher_expired"}
    return {"ok": True}


def redeem_voucher(voucher: Voucher, *, batch_id: str = "") -> dict[str, Any]:
    """Idempotently redeem a voucher for batch settlement.

    First redemption of a ``voucher_id`` validates the voucher, consumes its
    nonce, and records the redemption. Re-redeeming the same voucher (e.g.
    replayed across batches) returns the ORIGINAL redemption record with
    ``error: "already_redeemed"`` and ``double_pay: False`` — it never
    double-pays.

    The redemption record carries ``"settled": False`` deliberately:
    redemption is batch inclusion intent, NOT settlement. Serving the
    voucher-backed work still requires a confirmed batch-settlement receipt
    via :func:`require_settled`.
    """
    if not isinstance(voucher, Voucher):
        return {"ok": False, "error": "not_a_voucher", "double_pay": False}
    if voucher.voucher_id in _REDEMPTION_RECORDS:
        original = _REDEMPTION_RECORDS[voucher.voucher_id]
        return {
            "ok": False,
            "error": "already_redeemed",
            "double_pay": False,
            "redemption": dict(original),
        }
    check = validate_voucher(voucher)
    if not check.get("ok"):
        return {"ok": False, "error": check["error"], "double_pay": False}
    record = {
        "voucher_id": voucher.voucher_id,
        "payer": voucher.payer,
        "pay_to": voucher.pay_to,
        "amount": voucher.amount,
        "token": voucher.token,
        "nonce": voucher.nonce,
        "intent_hash": voucher.intent_hash,
        "batch_id": batch_id,
        "redeemed_at": datetime.now(timezone.utc).isoformat(),
        "settled": False,  # redemption is NOT settlement; see module docstring
    }
    _REDEMPTION_RECORDS[voucher.voucher_id] = record
    _REDEEMED_NONCES.add(voucher.nonce)
    return {"ok": True, "redemption": dict(record), "settle_pending": True}
