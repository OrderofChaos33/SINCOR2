#!/usr/bin/env python3
"""
Vault Event Listener — Base logs → TOA feedback loop.

Polls eth_getLogs on the SharedLiquidityVault (Base mainnet), decodes the
treasury-relevant events, and feeds them into
``TOAOrchestrator.ingest_vault_event`` so vault outcomes continuously refine
the forecaster's priors.  Restart-safe: last processed block is persisted to
a state file.

Zero new dependencies — plain JSON-RPC over ``requests`` (already in
requirements.txt).  Read-only: never signs, never sends transactions.

Run standalone::

    python -m verticals.trading.polyclaw.vault_event_listener            # loop
    python -m verticals.trading.polyclaw.vault_event_listener --once     # one poll

Environment:
    WEB3_PROVIDER or BASE_RPC_URL   Base RPC endpoint (default mainnet.base.org)
    VAULT_ADDRESS                   vault contract (default: canonical deployment)
    SINC_ADDRESS                    SINC token (18 decimals; USDC assumed 6)
    VAULT_POLL_INTERVAL_SEC         poll interval (default 30)
    VAULT_CONFIRMATIONS             blocks to wait before ingesting (default 3)
    VAULT_LISTENER_STATE_PATH       state file (default /data/vault_listener_state.json)
    VAULT_START_BLOCK               first block to scan when no state exists
                                    (default: 2000 blocks back from head)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger("vault_event_listener")

VAULT_ADDRESS = os.environ.get(
    "VAULT_ADDRESS", "0xeA90a257e5Dae20a0472C4812775F28614459bb6"
)
DEFAULT_RPC = os.environ.get(
    "BASE_RPC_URL", os.environ.get("WEB3_PROVIDER", "https://mainnet.base.org")
)
SINC_ADDRESS = os.environ.get(
    "SINC_ADDRESS", "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
)
BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

# keccak256 event topics (verified against SharedLiquidityVault.sol)
TOPICS: Dict[str, str] = {
    "Deposited":    "0x8752a472e571a816aea92eec8dae9baf628e840f4929fbcc2d155e6233ff68a7",
    "Withdrawn":    "0xd1c19fbcd4551a5edfb66d43d2e337c04837afda3482b42bdf569a8fccdae5fb",
    "DrawDown":     "0x8a96d16b9427470a5195b15942acb97c675e3d883639356211523fe727b12e42",
    "Settled":      "0x873cb6fdafcd53897e0b704a9aea1c3281ee9faac5a37763d5f37fc0d245a4fd",
    "FeesHarvested":"0xf5b58036d39911dd0308d450a6fa6d6556cc93c1c82f300be5c01108f68bb8a3",
}
TOPIC_TO_NAME = {v: k for k, v in TOPICS.items()}


def _addr(word: str) -> str:
    return "0x" + word[-40:]


def _uint(word: str) -> int:
    return int(word, 16)


def _token_decimals(token: str, sinc_address: str = SINC_ADDRESS) -> int:
    return 18 if token.lower() == sinc_address.lower() else 6


def _human(raw: int, token: str) -> float:
    return raw / 10 ** _token_decimals(token)


def decode_log(log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Decode one vault log into the dict shape ingest_vault_event expects."""
    topics: List[str] = log.get("topics", [])
    if not topics:
        return None
    name = TOPIC_TO_NAME.get(topics[0].lower())
    if name is None:
        return None

    data = log.get("data", "0x")[2:]
    words = [data[i:i + 64] for i in range(0, len(data), 64) if data[i:i + 64]]

    base = {
        "event": name,
        "txHash": log.get("transactionHash"),
        "blockNumber": _uint(log.get("blockNumber", "0x0")),
        "logIndex": _uint(log.get("logIndex", "0x0")),
        "timestamp": "",  # orchestrator fills if empty
    }

    if name in ("Deposited", "Withdrawn"):
        token = _addr(topics[2])
        return {**base, "lp": _addr(topics[1]), "token": token,
                "amount": _human(_uint(words[0]), token)}

    if name == "FeesHarvested":
        token = _addr(topics[2])
        return {**base, "lp": _addr(topics[1]), "token": token,
                "amount": _human(_uint(words[0]), token),
                "to": _addr(words[1])}

    if name == "DrawDown":
        token = _addr(topics[3])
        return {**base, "lp": _addr(topics[1]),
                "strategyId": _uint(topics[2]), "token": token,
                "amount": _human(_uint(words[0]), token)}

    if name == "Settled":
        token = _addr(topics[3])
        return {**base, "lp": _addr(topics[1]),
                "strategyId": _uint(topics[2]), "token": token,
                "principal": _human(_uint(words[0]), token),
                "fee": _human(_uint(words[1]), token)}

    return None


class VaultEventListener:
    """Restart-safe eth_getLogs poller feeding TOA.ingest_vault_event."""

    def __init__(
        self,
        ingest: Callable[[Dict[str, Any]], None],
        vault_address: str = VAULT_ADDRESS,
        rpc_url: str = DEFAULT_RPC,
        state_path: str = os.environ.get(
            "VAULT_LISTENER_STATE_PATH", "/data/vault_listener_state.json"
        ),
        confirmations: int = int(os.environ.get("VAULT_CONFIRMATIONS", "3")),
        start_block: Optional[int] = None,
    ):
        self.ingest = ingest
        self.vault_address = vault_address
        self.rpc_url = rpc_url
        self.state_path = Path(state_path)
        self.confirmations = confirmations
        self._start_block = start_block
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # JSON-RPC
    # ------------------------------------------------------------------
    def _rpc(self, method: str, params: List[Any]) -> Any:
        resp = self._session.post(
            self.rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"RPC error: {body['error']}")
        return body["result"]

    def _head_block(self) -> int:
        return _uint(self._rpc("eth_blockNumber", []))

    def _get_logs(self, from_block: int, to_block: int) -> List[Dict[str, Any]]:
        return self._rpc("eth_getLogs", [{
            "address": self.vault_address,
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "topics": [list(TOPICS.values())],
        }])

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def _load_last_block(self, head: int) -> int:
        try:
            return int(json.loads(self.state_path.read_text())["last_block"])
        except Exception:
            if self._start_block is not None:
                return self._start_block
            env_start = os.environ.get("VAULT_START_BLOCK")
            return int(env_start) if env_start else max(head - 2000, 0)

    def _save_last_block(self, block: int) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"last_block": block}))
        except OSError as exc:
            logger.warning("state save failed: %s", exc)

    # ------------------------------------------------------------------
    # Poll
    # ------------------------------------------------------------------
    def poll_once(self) -> int:
        """One poll cycle. Returns number of events ingested."""
        head = self._head_block()
        safe_head = head - self.confirmations
        last = self._load_last_block(head)
        if safe_head <= last:
            return 0

        logs = self._get_logs(last + 1, safe_head)
        ingested = 0
        for log in logs:
            event = decode_log(log)
            if event is None:
                continue
            try:
                self.ingest(event)
                ingested += 1
                logger.info(
                    "ingested %s block=%s tx=%s",
                    event["event"], event["blockNumber"], event["txHash"],
                )
            except Exception as exc:
                logger.error("ingest failed for %s: %s", event.get("txHash"), exc)

        self._save_last_block(safe_head)
        return ingested

    def run_forever(self, interval: Optional[float] = None) -> None:
        interval = interval if interval is not None else float(
            os.environ.get("VAULT_POLL_INTERVAL_SEC", "30")
        )
        logger.info(
            "vault listener live | vault=%s rpc=%s interval=%ss confirmations=%s",
            self.vault_address, self.rpc_url, interval, self.confirmations,
        )
        while True:
            try:
                n = self.poll_once()
                if n:
                    logger.info("poll: %d event(s) ingested", n)
            except Exception as exc:
                logger.error("poll failed (will retry): %s", exc)
            time.sleep(interval)


def build_default_ingest() -> Callable[[Dict[str, Any]], None]:
    """Wire the listener to a TOA orchestrator feedback loop."""
    from agents.toa import TOAOrchestrator

    orchestrator = TOAOrchestrator()
    return orchestrator.ingest_vault_event


def main() -> None:
    parser = argparse.ArgumentParser(description="SharedLiquidityVault → TOA listener")
    parser.add_argument("--once", action="store_true", help="single poll then exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    listener = VaultEventListener(ingest=build_default_ingest())
    if args.once:
        print(f"ingested {listener.poll_once()} event(s)")
    else:
        listener.run_forever()


if __name__ == "__main__":
    main()
