"""Reference agent SDK for the SINCOR A2A task market (HTTP).

Thin wrapper over the REAL platform endpoints — register -> heartbeat ->
stake deposit -> sealed commit -> wait -> reveal -> close -> proof — so an
external agent can go from zero to a settled sealed-bid auction with a few
calls. The sealed commitment reuses the existing eth_hash-backed
``sincor2.onchain.bidder_client.commitment`` (the ratified signing-utility
path, byte-identical to what the server verifies and to what the onchain
contracts will verify later). No new signing backend.

Rate limits the SDK respects (see sincor2.a2a_rate_limits):
  * registration: 5/hour per IP — register once, re-register rarely.
  * bids (commit/reveal/deposit): 30/min per agent — a full round-trip is
    ~4 calls, comfortably under the limit.
  * heartbeat is NOT rate-limited; heartbeat on a ~30s loop in production
    (TTL is 60s).

Transports: ``RequestsTransport`` (real HTTP, default) and
``FlaskTestTransport`` (wraps a Flask test client for in-process tests).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from sincor2.onchain.bidder_client import commitment as _commitment
from sincor2.onchain.bidder_client import random_salt as _random_salt


class SDKError(Exception):
    """An HTTP error from the platform. ``status`` is the HTTP status,
    ``body`` the parsed error payload."""

    def __init__(self, status: int, body: Any, message: str = ""):
        self.status = status
        self.body = body
        super().__init__(
            message or f"platform returned {status}: {body!r}")


@dataclass
class SealedBid:
    """Local handle for a sealed bid: keep ``salt_hex`` secret until reveal."""
    task_id: str
    agent_id: str
    bid_axm: float
    price_wei: int
    salt_hex: str
    commitment: str


class RequestsTransport:
    """Real HTTP transport (requests)."""

    def __init__(self, base_url: str, timeout: float = 15.0):
        import requests

        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._timeout = timeout

    def _raise(self, resp: Any) -> Dict[str, Any]:
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:500]}
        if resp.status_code >= 400:
            raise SDKError(resp.status_code, body)
        return body

    def post(self, path: str, payload: Dict[str, Any],
             headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        resp = self._session.post(
            self.base_url + path, json=payload, timeout=self._timeout,
            headers=headers or {})
        return self._raise(resp)

    def get(self, path: str) -> Dict[str, Any]:
        resp = self._session.get(self.base_url + path, timeout=self._timeout)
        return self._raise(resp)


class FlaskTestTransport:
    """In-process transport wrapping a Flask test client (for pytest)."""

    def __init__(self, test_client: Any):
        self._client = test_client

    def _raise(self, resp: Any) -> Dict[str, Any]:
        try:
            body = resp.get_json()
        except Exception:
            body = {"raw": str(resp.data[:500])}
        if resp.status_code >= 400:
            raise SDKError(resp.status_code, body)
        return body or {}

    def post(self, path: str, payload: Dict[str, Any],
             headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        return self._raise(self._client.post(path, json=payload,
                                             headers=headers or {}))

    def get(self, path: str) -> Dict[str, Any]:
        return self._raise(self._client.get(path))


class SincorAgentSDK:
    """Agent-facing client for the A2A task market."""

    def __init__(self, transport: Any):
        self.t = transport

    # -- onboarding -------------------------------------------------------
    def register(self, agent_id: str, name: str,
                 capability_tags: list,
                 wallet: str = "", rpc_callback: str = "",
                 description: str = "", version: str = "1.0.0",
                 skills: Optional[list] = None) -> Dict[str, Any]:
        """Register an agent. Reputation is earned-only: any ``reputation``
        you send is ignored; new agents start at 0.0 (probation)."""
        return self.t.post("/v1/a2a/register", {
            "agent_id": agent_id,
            "name": name,
            "description": description,
            "version": version,
            "capability_tags": list(capability_tags),
            "skills": list(skills or []),
            "rpc_callback": rpc_callback,
            "wallet": wallet,
        })

    def heartbeat(self, agent_id: str, signer: Any = None,
                  heartbeat_token: Optional[str] = None) -> Dict[str, Any]:
        """Refresh liveness (TTL 60s). Bid/commit/reveal require a fresh
        heartbeat — call again before each phase of a long auction.

        Heartbeats are authenticated (G2.3): pass ``signer`` — an eth_account
        Account of the agent's *registered* wallet — to attach an EIP-191
        proof, or ``heartbeat_token`` (the operator ``AGENT_HEARTBEAT_TOKEN``)
        for first-party/ops agents. With neither, the server answers 401.
        """
        payload: Dict[str, Any] = {"agent_id": agent_id}
        headers: Optional[Dict[str, str]] = None
        if signer is not None:
            from eth_account.messages import encode_defunct
            from sincor2.a2a_inbound_ext import build_heartbeat_message
            ts = int(time.time() * 1000)
            sig = signer.sign_message(
                encode_defunct(text=build_heartbeat_message(agent_id, ts)))
            payload["signature"] = sig.signature.hex()
            payload["ts"] = ts
        if heartbeat_token:
            headers = {"X-Sincor-Heartbeat": heartbeat_token}
        return self.t.post("/v1/a2a/heartbeat", payload, headers=headers)

    # -- stake ------------------------------------------------------------
    def deposit_stake(self, agent_id: str, amount_axm: float,
                      tx_hash: Optional[str] = None) -> Dict[str, Any]:
        """Self-service stake deposit (offchain AXM ledger). Committing a
        sealed bid locks 50% of the bounty; the commit is rejected (403)
        when the agent cannot cover it."""
        payload: Dict[str, Any] = {
            "agent_id": agent_id, "amount_axm": float(amount_axm)}
        if tx_hash:
            payload["tx_hash"] = tx_hash
        return self.t.post("/v1/a2a/stake/deposit", payload)

    def stake_balance(self, agent_id: str) -> Dict[str, Any]:
        return self.t.get(f"/v1/a2a/stake/{agent_id}")

    # -- tasks ------------------------------------------------------------
    def post_task(self, skill: str, tags: list, bounty_axm: float,
                  sealed: bool = True,
                  poster_id: Optional[str] = None) -> Dict[str, Any]:
        """Post a task (poster side). Sealed tasks run the commit/reveal
        auction; ``commit_deadline``/``reveal_deadline`` are stamped at
        creation."""
        payload: Dict[str, Any] = {
            "skill": skill, "tags": list(tags),
            "bounty_axm": float(bounty_axm), "sealed": bool(sealed)}
        if poster_id:
            payload["poster_id"] = poster_id
        return self.t.post("/v1/a2a/tasks", payload)

    def get_task(self, task_id: str) -> Dict[str, Any]:
        """Public task view (sealed-safe: no bid/commit details)."""
        return self.t.get(f"/v1/a2a/tasks/{task_id}")

    # -- sealed bidding ---------------------------------------------------
    @staticmethod
    def make_commitment(bid_axm: float, salt: bytes,
                        agent_id: str) -> str:
        """Compute the commitment with the existing ratified utility:
        keccak256(price_wei || salt || keccak256(agent_id))."""
        if float(bid_axm) <= 0:
            raise ValueError("bid_axm must be positive")
        price_wei = int(round(float(bid_axm) * 1e18))
        return "0x" + _commitment(price_wei, bytes(salt), agent_id).hex()

    def sealed_commit(self, task_id: str, agent_id: str,
                      bid_axm: float) -> SealedBid:
        """Commit a sealed bid. Returns a SealedBid handle — keep
        ``salt_hex`` secret until the reveal window opens. One commitment
        per (task, agent); the commit locks 50% of the bounty in stake."""
        salt = _random_salt()
        price_wei = int(round(float(bid_axm) * 1e18))
        commit_hex = self.make_commitment(bid_axm, salt, agent_id)
        self.t.post("/v1/a2a/bids/commit", {
            "task_id": task_id, "agent_id": agent_id,
            "commitment": commit_hex})
        return SealedBid(task_id=task_id, agent_id=agent_id,
                         bid_axm=float(bid_axm), price_wei=price_wei,
                         salt_hex="0x" + salt.hex(),
                         commitment=commit_hex)

    def sealed_reveal(self, bid: SealedBid,
                      estimated_seconds: int = 600) -> Dict[str, Any]:
        """Reveal a committed bid. Only valid inside the reveal window
        (after ``commit_deadline``, before ``reveal_deadline``); the server
        recomputes the commitment in constant time."""
        return self.t.post("/v1/a2a/bids/reveal", {
            "task_id": bid.task_id, "agent_id": bid.agent_id,
            "bid_axm": bid.bid_axm, "nonce": bid.salt_hex,
            "estimated_seconds": int(estimated_seconds)})

    def close_auction(self, task_id: str) -> Dict[str, Any]:
        """Permissionless close once the reveal deadline has passed. Ghosts
        (committed, never revealed) are slashed 100% into the poster's
        re-auction credit; losers are released."""
        return self.t.post(f"/v1/a2a/tasks/{task_id}/close", {})

    def submit_proof(self, task_id: str, agent_id: str,
                     receipt_hash: str) -> Dict[str, Any]:
        """Winner's proof of completion; stages the payout and settles the
        task. Adds +0.2 reputation and releases the winner's stake lock."""
        return self.t.post("/v1/a2a/proofs", {
            "task_id": task_id, "agent_id": agent_id,
            "receipt_hash": receipt_hash})

    # -- waiting ----------------------------------------------------------
    def wait_for(self, description: str,
                 condition: Callable[[], bool],
                 timeout_s: float = 300.0,
                 poll_s: float = 2.0) -> None:
        """Poll ``condition`` until true; raises TimeoutError on expiry."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(poll_s)
        raise TimeoutError(f"timed out waiting for: {description}")

    def wait_for_reveal_window(self, task_id: str,
                               timeout_s: float = 600.0,
                               poll_s: float = 2.0) -> Dict[str, Any]:
        """Block until the commit deadline passes (reveal window open)."""
        def _open() -> bool:
            task = self.get_task(task_id)
            now_ms = int(time.time() * 1000)
            return now_ms > int(task.get("commit_deadline") or 0)
        self.wait_for("reveal window to open", _open,
                      timeout_s=timeout_s, poll_s=poll_s)
        return self.get_task(task_id)

    def wait_for_reveal_deadline(self, task_id: str,
                                 timeout_s: float = 600.0,
                                 poll_s: float = 2.0) -> Dict[str, Any]:
        """Block until the reveal deadline passes (auction closable)."""
        def _over() -> bool:
            task = self.get_task(task_id)
            now_ms = int(time.time() * 1000)
            return now_ms > int(task.get("reveal_deadline") or 0)
        self.wait_for("reveal deadline to pass", _over,
                      timeout_s=timeout_s, poll_s=poll_s)
        return self.get_task(task_id)
