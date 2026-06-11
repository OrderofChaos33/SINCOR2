from __future__ import annotations

"""SINC token API endpoints — balance, quotes, credits, staking, and history.

All monetary amounts are in whole SINC tokens (decimals = 0).
"""

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required

from sincor2.error_handling import ApiError
from sincor2.sinc_access import (
    COST_LISTING_FEE,
    COST_PER_AGENT_CALL,
    COST_PER_SWARM_HOUR,
    SINC_CONTRACT,
    SINC_PLATFORM_ACCESS,
    TIER_ADVANCED_HOLD,
    TIER_ENTERPRISE_STAKE,
    TIER_LIST_STAKE,
    TIER_PRIORITY_STAKE,
    SINCAccessManager,
    SINCMeter,
)

logger = logging.getLogger(__name__)

sinc_bp = Blueprint("sinc", __name__, url_prefix="/api/sinc")

# Mapping: action_type → base SINC cost
_ACTION_COSTS: Dict[str, int] = {
    "agent_call": COST_PER_AGENT_CALL,
    "swarm_hour": COST_PER_SWARM_HOUR,
    "marketplace_listing": COST_LISTING_FEE,
    "a2a_task": COST_PER_AGENT_CALL,
    "advanced_agent": COST_PER_AGENT_CALL * 2,
    "priority_routing": 0,  # free with staking
}


def _manager() -> SINCAccessManager:
    mgr = current_app.extensions.get("sinc_access")
    if mgr is None:
        raise ApiError("sinc_unavailable", "SINC access manager not initialized", status=503)
    return mgr


def _meter() -> SINCMeter:
    meter = current_app.extensions.get("sinc_meter")
    if meter is None:
        raise ApiError("sinc_unavailable", "SINC meter not initialized", status=503)
    return meter


def _validate_wallet(wallet: str) -> str:
    addr = wallet.strip().lower()
    if not addr.startswith("0x") or len(addr) != 42:
        raise ApiError("invalid_wallet", "Provide a valid 0x Ethereum wallet address", status=400)
    return addr


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------

@sinc_bp.get("/balance")
def get_balance():
    """Return on-chain SINC balance, credits, staked, and access tier.

    Query params:
        wallet (str): 0x Ethereum address
    """
    wallet = request.args.get("wallet", "").strip()
    if not wallet:
        raise ApiError("wallet_required", "wallet query parameter is required", status=400)
    wallet = _validate_wallet(wallet)
    status = _manager().get_full_status(wallet)
    return jsonify({
        "success": True,
        "data": {
            **status,
            "sinc_contract": SINC_CONTRACT,
            "platform_access_contract": SINC_PLATFORM_ACCESS or None,
            "basescan_url": f"https://basescan.org/token/{SINC_CONTRACT}?a={wallet}",
            "tiers": {
                "advanced_hold": TIER_ADVANCED_HOLD,
                "list_stake": TIER_LIST_STAKE,
                "priority_stake": TIER_PRIORITY_STAKE,
                "enterprise_stake": TIER_ENTERPRISE_STAKE,
            },
        },
    })


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------

@sinc_bp.post("/quote")
def get_quote():
    """Return a SINC cost estimate for a platform action.

    Body:
        action_type (str): one of agent_call | swarm_hour | marketplace_listing |
                           a2a_task | advanced_agent | priority_routing
        quantity (int, optional): multiplier (e.g. hours for swarm_hour). Default 1.
        wallet (str, optional): if provided, applies staking discount and checks balance.
    """
    body = request.get_json(silent=True) or {}
    action_type = str(body.get("action_type", "")).strip().lower()
    if not action_type:
        raise ApiError("missing_field", "action_type is required", status=400)
    if action_type not in _ACTION_COSTS:
        raise ApiError(
            "unknown_action",
            f"Unknown action_type. Valid types: {sorted(_ACTION_COSTS)}",
            status=400,
        )

    try:
        quantity = max(1, int(body.get("quantity", 1)))
    except (TypeError, ValueError):
        raise ApiError("invalid_quantity", "quantity must be a positive integer", status=400)

    base_cost = _ACTION_COSTS[action_type] * quantity
    wallet = body.get("wallet", "").strip()

    discount_applied = False
    effective_cost = base_cost
    current_balance = None
    can_afford = None

    if wallet:
        try:
            wallet = _validate_wallet(wallet)
        except ApiError:
            wallet = ""

    if wallet:
        mgr = _manager()
        staked = mgr.get_staked(wallet)
        if staked >= TIER_PRIORITY_STAKE:
            discount_bps = 2000  # 20% staking discount
            effective_cost = base_cost - (base_cost * discount_bps) // 10_000
            discount_applied = True

        current_balance = mgr.get_balance(wallet)
        credits = mgr.get_credits(wallet)
        can_afford = credits >= effective_cost

    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    return jsonify({
        "success": True,
        "quote": {
            "action_type": action_type,
            "quantity": quantity,
            "base_cost_sinc": base_cost,
            "effective_cost_sinc": effective_cost,
            "discount_applied": discount_applied,
            "expires_at": expires_at,
            "wallet": wallet or None,
            "current_balance": current_balance,
            "can_afford": can_afford,
            "buy_sinc_url": "/sinc",
            "purchase_credits_url": "/api/sinc/credits/purchase",
        },
    })


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------

@sinc_bp.post("/credits/purchase")
def initiate_credit_purchase():
    """Return the calldata and instructions for purchasing SINC platform credits.

    The frontend should use this calldata to build a MetaMask transaction:
    1. ``approve(platform_access_contract, amount)`` on the SINC token.
    2. ``purchaseCredits(amount)`` on SINCPlatformAccess.

    Body:
        wallet (str): buyer's wallet address
        amount (int): SINC credits to purchase (minimum 10)
    """
    body = request.get_json(silent=True) or {}
    wallet = _validate_wallet(body.get("wallet", ""))
    try:
        amount = int(body.get("amount", 0))
    except (TypeError, ValueError):
        raise ApiError("invalid_amount", "amount must be an integer", status=400)
    if amount < 10:
        raise ApiError("amount_too_low", "Minimum credit purchase is 10 SINC", status=400)

    if not SINC_PLATFORM_ACCESS:
        raise ApiError(
            "contract_not_deployed",
            "SINCPlatformAccess contract address not configured. "
            "Credits will be available once the contract is deployed.",
            status=503,
        )

    # purchaseCredits(uint256) selector: keccak256("purchaseCredits(uint256)") = 0x5e00e854
    purchase_selector = "0x5e00e854"
    amount_hex = hex(amount)[2:].zfill(64)
    purchase_calldata = purchase_selector + amount_hex

    # approve(address,uint256) selector: 0x095ea7b3
    approve_selector = "0x095ea7b3"
    spender_hex = SINC_PLATFORM_ACCESS.lower().replace("0x", "").zfill(64)
    approve_calldata = approve_selector + spender_hex + amount_hex

    return jsonify({
        "success": True,
        "instructions": {
            "step_1": {
                "description": f"Approve {amount} SINC for the platform contract",
                "to": SINC_CONTRACT,
                "data": approve_calldata,
                "chain_id": 8453,
            },
            "step_2": {
                "description": f"Purchase {amount} SINC credits",
                "to": SINC_PLATFORM_ACCESS,
                "data": purchase_calldata,
                "chain_id": 8453,
            },
            "amount": amount,
            "wallet": wallet,
            "note": "After confirmation, your credit balance will update within ~15 seconds.",
        },
    }), 201


@sinc_bp.post("/credits/spend")
@jwt_required()
def spend_credits():
    """Deduct credits from a user wallet for a metered platform action.

    This endpoint is internal (JWT-protected) and called by the platform
    backend when billing for agent calls, swarm usage, etc.

    Body:
        wallet (str): user wallet address
        action_type (str): action being billed
        quantity (int, optional): multiplier (default 1)
        task_id (str, optional): task/session identifier for logging
    """
    body = request.get_json(silent=True) or {}
    wallet = _validate_wallet(body.get("wallet", ""))
    action_type = str(body.get("action_type", "")).strip().lower()
    if not action_type or action_type not in _ACTION_COSTS:
        raise ApiError("invalid_action", f"Valid action types: {sorted(_ACTION_COSTS)}", status=400)

    try:
        quantity = max(1, int(body.get("quantity", 1)))
    except (TypeError, ValueError):
        raise ApiError("invalid_quantity", "quantity must be a positive integer", status=400)

    task_id = str(body.get("task_id", "")).strip() or ""
    base_cost = _ACTION_COSTS[action_type] * quantity

    # Apply staking discount if eligible
    mgr = _manager()
    staked = mgr.get_staked(wallet)
    effective_cost = base_cost
    if staked >= TIER_PRIORITY_STAKE:
        effective_cost = base_cost - (base_cost * 2000) // 10_000

    # Record in meter (actual on-chain deduction happens via SINCPlatformAccess)
    meter = _meter()
    meter.record(
        wallet=wallet,
        action_type=action_type,
        sinc_amount=effective_cost,
        task_id=task_id,
        direction="debit",
    )

    # Invalidate cached credits so next read is fresh
    mgr.invalidate_cache(wallet)

    return jsonify({
        "success": True,
        "debited": {
            "wallet": wallet,
            "action_type": action_type,
            "quantity": quantity,
            "base_cost": base_cost,
            "effective_cost": effective_cost,
            "task_id": task_id,
        },
    })


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

@sinc_bp.get("/history")
def get_history():
    """Return paginated SINC usage history for a wallet.

    Query params:
        wallet (str): 0x address
        limit (int, optional): max events to return (default 50, max 100)
        direction (str, optional): "debit" | "credit" filter
    """
    wallet = request.args.get("wallet", "").strip()
    if not wallet:
        raise ApiError("wallet_required", "wallet query parameter is required", status=400)
    wallet = _validate_wallet(wallet)

    try:
        limit = min(int(request.args.get("limit", 50)), 100)
    except (TypeError, ValueError):
        limit = 50

    direction = request.args.get("direction")
    if direction and direction not in ("debit", "credit"):
        raise ApiError("invalid_direction", "direction must be 'debit' or 'credit'", status=400)

    meter = _meter()
    events = meter.get_events(wallet=wallet, limit=limit, direction=direction or None)
    total_spent = meter.total_spent(wallet)

    return jsonify({
        "success": True,
        "wallet": wallet,
        "events": events,
        "count": len(events),
        "total_spent_sinc": total_spent,
        "basescan_token_url": f"https://basescan.org/token/{SINC_CONTRACT}?a={wallet}",
    })


# ---------------------------------------------------------------------------
# Staking helpers (return calldata for frontend to sign)
# ---------------------------------------------------------------------------

@sinc_bp.post("/stake")
def initiate_stake():
    """Return calldata for staking SINC in SINCPlatformAccess.

    Body:
        wallet (str): staker's wallet
        amount (int): SINC to stake
    """
    body = request.get_json(silent=True) or {}
    wallet = _validate_wallet(body.get("wallet", ""))
    try:
        amount = int(body.get("amount", 0))
    except (TypeError, ValueError):
        raise ApiError("invalid_amount", "amount must be an integer", status=400)
    if amount <= 0:
        raise ApiError("invalid_amount", "amount must be greater than zero", status=400)

    if not SINC_PLATFORM_ACCESS:
        raise ApiError("contract_not_deployed", "SINCPlatformAccess not configured", status=503)

    # stake(uint256) selector: keccak256("stake(uint256)") = 0xa694fc3a
    stake_selector = "0xa694fc3a"
    amount_hex = hex(amount)[2:].zfill(64)
    stake_calldata = stake_selector + amount_hex

    approve_selector = "0x095ea7b3"
    spender_hex = SINC_PLATFORM_ACCESS.lower().replace("0x", "").zfill(64)
    approve_calldata = approve_selector + spender_hex + amount_hex

    staking_tier = "none"
    if amount >= TIER_ENTERPRISE_STAKE:
        staking_tier = "enterprise"
    elif amount >= TIER_PRIORITY_STAKE:
        staking_tier = "priority (20% credit discount)"
    elif amount >= TIER_LIST_STAKE:
        staking_tier = "standard (marketplace listing enabled)"

    return jsonify({
        "success": True,
        "instructions": {
            "step_1": {
                "description": f"Approve {amount} SINC for staking",
                "to": SINC_CONTRACT,
                "data": approve_calldata,
                "chain_id": 8453,
            },
            "step_2": {
                "description": f"Stake {amount} SINC for priority routing",
                "to": SINC_PLATFORM_ACCESS,
                "data": stake_calldata,
                "chain_id": 8453,
            },
            "amount": amount,
            "wallet": wallet,
            "unlocks": staking_tier,
            "cooldown_days": 7,
            "note": "Staked SINC cannot be immediately withdrawn. A 7-day cooldown applies.",
        },
    }), 201


@sinc_bp.post("/unstake")
def initiate_unstake():
    """Return calldata for requesting an unstake from SINCPlatformAccess.

    Body:
        wallet (str): staker's wallet
        amount (int): SINC to unstake
    """
    body = request.get_json(silent=True) or {}
    wallet = _validate_wallet(body.get("wallet", ""))
    try:
        amount = int(body.get("amount", 0))
    except (TypeError, ValueError):
        raise ApiError("invalid_amount", "amount must be an integer", status=400)
    if amount <= 0:
        raise ApiError("invalid_amount", "amount must be greater than zero", status=400)

    if not SINC_PLATFORM_ACCESS:
        raise ApiError("contract_not_deployed", "SINCPlatformAccess not configured", status=503)

    # requestUnstake(uint256) selector: keccak256("requestUnstake(uint256)") = 0x1831f37a
    unstake_selector = "0x1831f37a"
    amount_hex = hex(amount)[2:].zfill(64)
    unstake_calldata = unstake_selector + amount_hex

    return jsonify({
        "success": True,
        "instructions": {
            "step_1": {
                "description": f"Request unstake of {amount} SINC (starts 7-day cooldown)",
                "to": SINC_PLATFORM_ACCESS,
                "data": unstake_calldata,
                "chain_id": 8453,
            },
            "amount": amount,
            "wallet": wallet,
            "available_after": "7 days after on-chain confirmation",
            "note": "Call finaliseUnstake() after 7 days to receive your SINC.",
        },
    }), 201


# ---------------------------------------------------------------------------
# Access tiers reference
# ---------------------------------------------------------------------------

@sinc_bp.get("/tiers")
def get_tiers():
    """Return the platform access tier definitions."""
    return jsonify({
        "success": True,
        "tiers": [
            {
                "name": "Free",
                "sinc_required": 0,
                "model": "none",
                "features": ["Browse marketplace", "View Agent Cards", "Basic dashboard"],
            },
            {
                "name": "Standard Agent Call",
                "sinc_required": COST_PER_AGENT_CALL,
                "model": "pay-per-use (credits)",
                "features": ["1 SINC per agent call"],
            },
            {
                "name": "Advanced Features",
                "sinc_required": TIER_ADVANCED_HOLD,
                "model": "balance gate (hold)",
                "features": ["Advanced agent configurations", "Extended output modes"],
            },
            {
                "name": "Marketplace Listing",
                "sinc_required": TIER_LIST_STAKE,
                "model": "stake + 50 SINC listing fee",
                "features": ["List agents in marketplace", "Earn SINC from task revenue"],
            },
            {
                "name": "Priority Routing",
                "sinc_required": TIER_PRIORITY_STAKE,
                "model": "stake",
                "features": ["Priority task routing", "20% credit cost discount"],
            },
            {
                "name": "Agent Swarm",
                "sinc_required": COST_PER_SWARM_HOUR,
                "model": "metered credits (10 SINC/hour)",
                "features": ["Multi-agent orchestration", "Parallel task execution"],
            },
            {
                "name": "A2A External Task",
                "sinc_required": COST_PER_AGENT_CALL,
                "model": "pay-per-call (on-chain or credits)",
                "features": ["External A2A agent calls", "On-chain escrow available"],
            },
            {
                "name": "Enterprise",
                "sinc_required": TIER_ENTERPRISE_STAKE,
                "model": "stake",
                "features": [
                    "Custom agent deployment",
                    "Dedicated routing lanes",
                    "SLA support",
                ],
            },
        ],
        "staking_discount": {
            "threshold_sinc": TIER_PRIORITY_STAKE,
            "discount_percent": 20,
        },
        "credit_model": {
            "rate": "1 SINC = 1 credit",
            "minimum_purchase": 10,
            "expiry": "never",
        },
    })
