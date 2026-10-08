"""Reference agent SDK for the SINCOR A2A task market (HTTP).

Thin wrapper over the REAL platform endpoints — register -> heartbeat ->
stake deposit -> sealed commit -> wait -> reveal -> close -> proof — so an
external agent can go from zero to a settled sealed-bid auction with a few
calls. The sealed commitment reuses ``sincor2.a2a_inbound_market.
sealed_commitment`` — the offchain shim the server verifies against. (The
onchain scheme additionally binds auctionId and chainId; see
``sincor2.onchain.bidder_client``.) No new signing backend.

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
                 skills: Optional[list] = None,
                 signer: Any = None) -> Dict[str, Any]:
        """Register an agent. Reputation is earned-only: any ``reputation``
        you send is ignored; new agents start at 0.0 (probation).

        Pass ``signer`` (an eth_account Account holding the claimed
        wallet's key) to attach the EIP-191 proof the server requires:

        * first-time registration: signature over
          ``SINCOR-REGISTER|<agent_id>|<ts>`` + ``registration_wallet``
          (fail-closed C3: an unsigned wallet claim is rejected with 403);
        * re-registration of an existing record: signature by the
          *registered* wallet over the exact new record contents
          (G2.2 proof-gated).

        The SDK probes ``GET /v1/a2a/agents`` to pick the right proof
        format. ``wallet`` must equal ``signer.address`` when both are
        given, otherwise registration is refused client-side.
        """
        body: Dict[str, Any] = {
            "agent_id": agent_id,
            "name": name,
            "description": description,
            "version": version,
            "capability_tags": list(capability_tags),
            "skills": list(skills or []),
            "rpc_callback": rpc_callback,
            "wallet": wallet,
        }
        if signer is not None:
            from eth_account.messages import encode_defunct
            signer_address = getattr(signer, "address", "")
            if wallet and signer_address.lower() != wallet.lower():
                raise ValueError(
                    "signer.address must equal the claimed wallet "
                    "(server rejects mismatched claims)")
            wallet = wallet or signer_address
            body["wallet"] = wallet
            if self._agent_exists(agent_id):
                from sincor2.a2a_inbound import _now_ms, build_reregistration_message
                ts = _now_ms()
                message = build_reregistration_message(body, ts)
            else:
                from sincor2.a2a_identity import register_message
                import time as _time
                ts = int(_time.time() * 1000)
                message = register_message(agent_id, ts)
                body["registration_wallet"] = wallet
            sig = signer.sign_message(encode_defunct(text=message)).signature
            body["registration_ts"] = ts
            body["registration_signature"] = "0x" + bytes(sig).hex()
        return self.t.post("/v1/a2a/register", body)

    def _agent_exists(self, agent_id: str) -> bool:
        """Best-effort existence probe for proof-format selection.

        Fail-closed: any probe error is treated as "exists", so the SDK
        falls back to the re-registration proof rather than minting a
        duplicate first-registration proof.
        """
        try:
            listing = self.t.get("/v1/a2a/agents")
            agents = listing.get("agents", []) if isinstance(listing, dict) else []
            return any(a.get("agent_id") == agent_id for a in agents
                       if isinstance(a, dict))
        except Exception:
            return True

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

    @staticmethod
    def quota_signature_payload(skill_id: str, input_text: str = "",
                                timestamp_ms: Optional[int] = None,
                                for_quote: bool = False) -> Dict[str, str]:
        """Build the canonical EIP-191 quota message for the caller to sign.

        Free quota is keyed on the wallet recovered from this signature — the
        self-declared ``caller_id`` no longer grants free calls. Sign the
        returned ``message`` with the caller's wallet (EIP-191
        ``personal_sign``), then send ``signature`` + ``quota_ts`` + ``wallet``
        with the tasks/send (or quote) request. The ``wallet`` claim is
        required: the server checks the recovered signer equals it (ECDSA
        recovery returns *some* address for any message/signature pair, so
        the check is what binds the signature to the message). The SDK never
        signs: the caller's own wallet tooling (e.g. eth_account) does.
        """
        from sincor2.a2a_integration import (
            quota_message_for_quote,
            quota_message_for_send,
        )
        ts = int(timestamp_ms) if timestamp_ms is not None else int(time.time() * 1000)
        if for_quote:
            message = quota_message_for_quote(skill_id, ts)
        else:
            message = quota_message_for_send(skill_id, input_text, ts)
        return {"message": message, "quota_ts": str(ts)}

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

    # -- socialfi ---------------------------------------------------------
    def issue_creator_token(self, agent_id: str, name: str, symbol: str,
                            creator_id: Optional[str] = None,
                            description: str = "", bio: str = "",
                            dry_run: bool = True) -> Dict[str, Any]:
        """Request P24 creator-token issuance for a registered agent.

        Metadata is screened against the content-policy deny-list
        server-side (violation -> 400 with the ruleset version). creator_id
        is bound to the caller's agent_id: omit it to issue under your own
        agent_id, or pass creator_id == agent_id; any other value is
        rejected with 403. P24 is live-blocked, so only dry-run issuance is
        served until the founder releases the live block; pass
        dry_run=False only when live issuance is authorized (currently
        refused with 403)."""
        payload: Dict[str, Any] = {
            "agent_id": agent_id, "name": name, "symbol": symbol,
            "description": description, "bio": bio,
            "dry_run": bool(dry_run)}
        if creator_id:
            payload["creator_id"] = creator_id
        return self.t.post("/v1/a2a/socialfi/issue", payload)

    # -- tasks ------------------------------------------------------------
    def post_task(self, skill: str, tags: list, bounty_axm: float,
                  sealed: bool = True,
                  poster_id: Optional[str] = None,
                  agent_id: Optional[str] = None,
                  auth_signature: Optional[str] = None,
                  auth_timestamp: Optional[int] = None,
                  auth_nonce: Optional[str] = None) -> Dict[str, Any]:
        """Post a task (poster side). Sealed tasks run the commit/reveal
        auction; ``commit_deadline``/``reveal_deadline`` are stamped at
        creation.

        Poster attribution is SERVER-BOUND (P3 item 14): the server ignores
        any bare ``poster_id`` for attribution. To have the task attributed
        to your wallet (and receive ghost-slash re-auction credit), sign
        the market task-create message — see ``make_poster_auth`` — and pass
        the resulting ``auth_signature``/``auth_timestamp``/``auth_nonce``
        (plus your ``agent_id`` label). Unsigned posts are anonymous.
        """
        payload: Dict[str, Any] = {
            "skill": skill, "tags": list(tags),
            "bounty_axm": float(bounty_axm), "sealed": bool(sealed)}
        if poster_id:
            payload["poster_id"] = poster_id
        if agent_id:
            payload["agent_id"] = agent_id
        if auth_signature:
            payload["auth_signature"] = auth_signature
        if auth_timestamp is not None:
            payload["auth_timestamp"] = int(auth_timestamp)
        if auth_nonce:
            payload["auth_nonce"] = auth_nonce
        return self.t.post("/v1/a2a/tasks", payload)

    @staticmethod
    def make_poster_auth(agent_id: str, skill: str, timestamp: int,
                         sign_text, nonce: Optional[str] = None) -> Dict[str, Any]:
        """Build the signed poster-auth fields for :meth:`post_task`.

        ``sign_text`` is a caller-supplied callable taking the canonical
        message text and returning the EIP-191 ``personal_sign`` signature
        as hex (``0x`` prefix optional) — e.g.
        ``lambda m: Account.sign_message(encode_defunct(text=m)).signature.hex()``.
        The SDK never sees private keys. A fresh ``nonce`` is generated per
        call unless given; it is returned in the dict (the server requires
        it to rebuild the signed message, and signatures are single-use).
        """
        import secrets

        from sincor2.a2a_integration import _auth_market_create_message
        nonce = nonce or secrets.token_hex(8)
        message = _auth_market_create_message(str(skill).strip().lower(),
                                              int(timestamp), nonce)
        return {
            "agent_id": str(agent_id),
            "auth_signature": sign_text(message),
            "auth_timestamp": int(timestamp),
            "auth_nonce": nonce,
        }

    def get_task(self, task_id: str) -> Dict[str, Any]:
        """Public task view (sealed-safe: no bid/commit details)."""
        return self.t.get(f"/v1/a2a/tasks/{task_id}")

    # -- sealed bidding ---------------------------------------------------
    @staticmethod
    def make_commitment(bid_axm: float, salt: bytes,
                        agent_id: str) -> str:
        """Compute the commitment for the OFFCHAIN sealed-bid API
        (``POST /v1/a2a/bids/commit``): the server-side shim scheme
        ``keccak256(price_wei || salt || keccak256(agent_id))``, exactly what
        ``sincor2.a2a_inbound_market.sealed_commitment`` computes. This is
        NOT an onchain commitment — the onchain scheme additionally binds
        auctionId and chainId (see ``sincor2.onchain.bidder_client``)."""
        from sincor2.a2a_inbound_market import sealed_commitment as _shim
        if float(bid_axm) <= 0:
            raise ValueError("bid_axm must be positive")
        price_wei = int(round(float(bid_axm) * 1e18))
        return "0x" + _shim(price_wei, bytes(salt), agent_id).hex()

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
                     receipt_hash: str,
                     *,
                     deliverable: Any = None,
                     evidence: Dict[str, Any] | None = None,
                     deliverable_uri: str = "",
                     content_binding_sig: str = "") -> Dict[str, Any]:
        """Winner's proof of completion; stages the payout and settles the
        task. Adds +0.2 reputation and releases the winner's stake lock.

        Optional receipt-content binding (item 77): pass ``deliverable``
        (raw content; the server hashes it) or a pre-built ``evidence``
        mapping.  Receipt-only evidence is rejected fail-closed.
        """
        body: Dict[str, Any] = {
            "task_id": task_id, "agent_id": agent_id,
            "receipt_hash": receipt_hash}
        if deliverable is not None:
            body["deliverable"] = deliverable
            body["deliverable_uri"] = deliverable_uri
            body["content_binding_sig"] = content_binding_sig
        if evidence is not None:
            body["evidence"] = evidence
        return self.t.post("/v1/a2a/proofs", body)

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
