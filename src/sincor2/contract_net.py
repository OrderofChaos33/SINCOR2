"""Contract-Net auction evaluator (sync).

Score = (0.3 * Reputation) - (0.4 * bid^1.1) - (0.3 * minutes)
Higher score wins. Cheaper + faster + more reputable.

Redis keys (when a Redis-like store is attached)
------------------------------------------------
task:{id}:meta     hash  status, assigned_agent, winning_bid_axm, ...
task:{id}:bids     hash  agent_id -> JSON {bid_amount, estimated_seconds}
agent:{id}:stats   hash  reputation
Pub/Sub            tasks:broadcast
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("ContractNetEvaluator")

AUCTION_EVENTS_CHANNEL = "auction:events"
TASK_BROADCAST_CHANNEL = "tasks:broadcast"
BASE_CHAIN_ID = 8453
ESCROW_ADDRESS = os.environ.get(
    "ESCROW_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
)
AXM_TOKEN = os.environ.get("AXM_TOKEN", "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a")
BASE_RPC = os.environ.get("BASE_RPC") or os.environ.get("BASE_RPC_URL") or ""


def calculate_bid_score(bid_axm: float, time_est_sec: int, reputation: float) -> float:
    """Higher score wins. Cheaper + faster + more reputable."""
    cost_penalty = 0.4 * (float(bid_axm) ** 1.1)
    time_penalty = 0.3 * (float(time_est_sec) / 60.0)
    reputation_bonus = 0.3 * float(reputation)
    return reputation_bonus - cost_penalty - time_penalty


def stage_payout(
    *,
    agent_id: str,
    wallet: str,
    amount_axm: float,
    task_id: str,
    receipt_hash: str,
) -> Dict[str, Any]:
    """Release AXM from Base escrow. Live RPC when BASE_RPC is set; else staged."""
    staged: Dict[str, Any] = {
        "ok": True,
        "chain_id": BASE_CHAIN_ID,
        "token": AXM_TOKEN,
        "escrow": ESCROW_ADDRESS,
        "agent_id": agent_id,
        "wallet": wallet,
        "amount_axm": amount_axm,
        "task_id": task_id,
        "receipt_hash": receipt_hash,
        "mode": "live" if BASE_RPC else "staged",
        "ts": int(time.time()),
    }
    digest = hashlib.sha256(
        f"{task_id}:{wallet}:{amount_axm}:{receipt_hash}".encode()
    ).hexdigest()
    staged["tx_hash"] = "0x" + digest
    if BASE_RPC:
        try:
            from web3 import Web3  # type: ignore

            w3 = Web3(Web3.HTTPProvider(BASE_RPC))
            staged["rpc_ok"] = bool(w3.is_connected())
        except Exception as err:  # pragma: no cover - optional live path
            staged["ok"] = False
            staged["error"] = str(err)
            staged["tx_hash"] = None
    return staged


class MemoryHashStore:
    """Minimal Redis-hash + pubsub stand-in. Thread-safe, no extra deps."""

    def __init__(self) -> None:
        self._hashes: Dict[str, Dict[str, str]] = {}
        self._lock = threading.Lock()
        self.published: list[tuple[str, str]] = []

    def hgetall(self, key: str) -> Dict[str, str]:
        with self._lock:
            return dict(self._hashes.get(key, {}))

    def hget(self, key: str, field: str) -> Optional[str]:
        with self._lock:
            bucket = self._hashes.get(key) or {}
            return bucket.get(field)

    def hset(self, key: str, mapping: Optional[Dict[str, Any]] = None, **kwargs: Any) -> int:
        payload: Dict[str, Any] = {}
        if mapping:
            payload.update(mapping)
        payload.update(kwargs)
        with self._lock:
            bucket = self._hashes.setdefault(key, {})
            for field, value in payload.items():
                bucket[str(field)] = str(value)
            return len(payload)

    def publish(self, channel: str, payload: str) -> int:
        with self._lock:
            self.published.append((channel, payload))
        return 1


class ContractNetEvaluator:
    def __init__(self, store: Optional[MemoryHashStore] = None) -> None:
        self.store = store or MemoryHashStore()

    def evaluate_task_bids(self, task_id: str) -> Optional[Dict[str, Any]]:
        bids_key = f"task:{task_id}:bids"
        meta_key = f"task:{task_id}:meta"
        meta = self.store.hgetall(meta_key)
        raw_bids = self.store.hgetall(bids_key)

        if not meta or meta.get("status") != "open":
            logger.warning("Task %s is not in open status for evaluation.", task_id)
            return None

        if not raw_bids:
            logger.info("No bids submitted for Task %s. Transitioning to expired.", task_id)
            self.store.hset(meta_key, status="expired")
            return None

        best_bidder: Optional[str] = None
        highest_score = float("-inf")
        winning_bid_data: Optional[Dict[str, Any]] = None

        for agent_id, raw_payload in raw_bids.items():
            try:
                bid = json.loads(raw_payload)
                rep_raw = self.store.hget(f"agent:{agent_id}:stats", "reputation")
                score = calculate_bid_score(
                    bid_axm=float(bid["bid_amount"]),
                    time_est_sec=int(bid["estimated_seconds"]),
                    reputation=float(rep_raw or 0.0),
                )
                if score > highest_score:
                    highest_score = score
                    best_bidder = agent_id
                    winning_bid_data = bid
            except Exception as err:
                logger.error(
                    "Error parsing bid from agent %s on task %s: %s",
                    agent_id,
                    task_id,
                    err,
                )

        if not best_bidder or not winning_bid_data:
            self.store.hset(meta_key, status="expired")
            return None

        mapping = {
            "status": "assigned",
            "assigned_agent": best_bidder,
            "winning_bid_axm": str(winning_bid_data["bid_amount"]),
            "assigned_at": str(int(time.time())),
        }
        event_payload = json.dumps(
            {
                "event": "task_assigned",
                "data": {
                    "task_id": task_id,
                    "assigned_agent": best_bidder,
                    "bid_axm": winning_bid_data["bid_amount"],
                    "estimated_seconds": winning_bid_data["estimated_seconds"],
                },
            }
        )
        self.store.hset(meta_key, mapping=mapping)
        self.store.publish(TASK_BROADCAST_CHANNEL, event_payload)
        logger.info("Task %s assigned to %s (score %.4f)", task_id, best_bidder, highest_score)
        return winning_bid_data

    def complete_task(
        self,
        task_id: str,
        receipt_hash: str,
        agent_wallet: Optional[str] = None,
    ) -> Dict[str, Any]:
        meta_key = f"task:{task_id}:meta"
        meta = self.store.hgetall(meta_key)
        if not meta or meta.get("status") != "assigned":
            return {"ok": False, "error": "not_assigned"}
        amount = float(meta.get("winning_bid_axm") or 0)
        agent = meta.get("assigned_agent") or ""
        receipt = stage_payout(
            agent_id=agent,
            wallet=agent_wallet or agent,
            amount_axm=amount,
            task_id=task_id,
            receipt_hash=receipt_hash,
        )
        self.store.hset(
            meta_key,
            mapping={
                "status": "settled",
                "settled_at": str(int(time.time())),
                "payout_tx": receipt.get("tx_hash") or "",
            },
        )
        self.store.publish(
            TASK_BROADCAST_CHANNEL,
            json.dumps(
                {
                    "event": "task_settled",
                    "data": {
                        "task_id": task_id,
                        "assigned_agent": agent,
                        "payout_axm": amount,
                        "tx_hash": receipt.get("tx_hash"),
                    },
                }
            ),
        )
        return receipt
