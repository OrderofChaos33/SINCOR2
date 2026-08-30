"""sincor-a2a — Python client for SINCOR inbound A2A."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any, Awaitable, Callable, Optional

import urllib.request

OnTask = Callable[[dict[str, Any]], Awaitable[None] | None]


def sign(payload: dict[str, Any], secret: str = "sincor-a2a-demo") -> str:
    body = json.dumps(
        {k: payload[k] for k in sorted(payload) if k != "signature"},
        separators=(",", ":"),
    )
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


class SincorA2A:
    def __init__(self, base_url: str, agent_id: str, secret: str = "sincor-a2a-demo"):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.secret = secret
        self._handlers: list[OnTask] = []

    def on_task(self, fn: OnTask) -> OnTask:
        self._handlers.append(fn)
        return fn

    def register(
        self,
        tags: list[str],
        wallet: str,
        rpc_callback: str,
        name: str | None = None,
    ) -> dict:
        payload = {
            "agent_id": self.agent_id,
            "name": name or self.agent_id,
            "capability_tags": tags,
            "rpc_callback": rpc_callback,
            "wallet": wallet,
            "chain_id": 8453,
        }
        payload["signature"] = sign(payload, self.secret)
        return self._post("/v1/a2a/register", payload)

    def heartbeat(self) -> dict:
        return self._post("/v1/a2a/heartbeat", {"agent_id": self.agent_id})

    def bid(self, task_id: str, bid_axm: float, time_est_ms: int) -> dict:
        payload = {
            "task_id": task_id,
            "agent_id": self.agent_id,
            "bid_axm": bid_axm,
            "time_est_ms": time_est_ms,
        }
        return self._post("/v1/a2a/bids", payload)

    def submit_proof(self, task_id: str, receipt_hash: str) -> dict:
        payload = {
            "task_id": task_id,
            "agent_id": self.agent_id,
            "receipt_hash": receipt_hash,
        }
        return self._post("/v1/a2a/proofs", payload)

    async def listen(self, tags: Optional[list[str]] = None) -> None:
        q = "&".join([f"tags={t}" for t in (tags or [])])
        url = f"{self.base_url}/v1/a2a/stream?{q}"
        while True:
            try:
                with urllib.request.urlopen(url, timeout=90) as resp:
                    for raw in resp:
                        line = raw.decode().strip()
                        if not line.startswith("data:"):
                            continue
                        event = json.loads(line[5:].strip())
                        if event.get("type") == "task.created":
                            for handler in self._handlers:
                                result = handler(event["payload"])
                                if asyncio.iscoroutine(result):
                                    await result
            except Exception:
                await asyncio.sleep(1.5)

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
