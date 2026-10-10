"""Multi-signature approval gate for the offensive playbook.

Access model (per founder directive 2026-10-09):
  - The playbook framework lives in the repo (docs/strategy/offensive/).
  - READING the framework is open to repo readers.
  - DEPLOYING any play (executing an offensive action) requires approval:
      * EITHER one human founder/manager signature,
      * OR M-of-N signatures from designated approver agents (multi-sig).

Designated approver agents (must hold status: Active):
  - E-toa-44            (Temporal Optimization Agent)
  - E-critic-46         (Critic)
  - E-strategist-45     (Strategist)
  - E-treasury-exec-47  (Treasury Executive)

Default quorum: 3-of-4 agent signatures, or 1 founder signature.
Each approver agent maps to an Ethereum signing address in the manifest.
Signatures are EIP-191 personal_sign over the play hash, low-s enforced
(sig_canonical.require_low_s), and approval records are hash-chained so the
audit trail is tamper-evident.

Nothing here executes a play — this module only records and verifies
approvals. Execution remains founder-gated separately.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field

from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import sig_canonical

# ---------------------------------------------------------------------------
# Manifest: approver set + quorum
# ---------------------------------------------------------------------------

# Approver agent IDs in priority order. Addresses are placeholders until the
# founder assigns real signing keys (founder-controlled, never in chat).
APPROVER_AGENTS: tuple[str, ...] = (
    "E-toa-44",
    "E-critic-46",
    "E-strategist-45",
    "E-treasury-exec-47",
)

AGENT_QUORUM = 3  # M-of-N agent signatures required

# Founder/manager bypass: a single signature from the founder address
# approves unconditionally. Set via env at deploy time; empty = disabled.
FOUNDER_ADDRESS_ENV = "OFFPLAY_FOUNDER_ADDRESS"

# Registry maps agent id -> signing address. Populated by operator config.
# Kept in-memory; production loads from a founder-controlled config file.
_registry: dict[str, str] = {}


def register_approver(agent_id: str, address: str) -> None:
    """Register (or rotate) an approver agent's signing address."""
    if agent_id not in APPROVER_AGENTS:
        raise ValueError(f"not a designated approver: {agent_id}")
    if not address or not address.startswith("0x") or len(address) != 42:
        raise ValueError("malformed ethereum address")
    _registry[agent_id.lower()] = address.lower()


def approver_address(agent_id: str) -> str | None:
    return _registry.get(agent_id.lower())


# ---------------------------------------------------------------------------
# Play hashing
# ---------------------------------------------------------------------------

def play_hash(play_id: str, action: str, target_scope: str, created_at: int) -> bytes:
    """Canonical digest identifying exactly what is being approved."""
    payload = json.dumps(
        {
            "play_id": play_id,
            "action": action,
            "target_scope": target_scope,
            "created_at": created_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(b"offplay/v1:" + payload).digest()


def approval_message(play_digest: bytes, agent_id: str) -> bytes:
    """Domain-separated message each approver signs."""
    return b"offplay-approval/v1:" + play_digest + b":" + agent_id.encode()


# ---------------------------------------------------------------------------
# Approval records (hash-chained)
# ---------------------------------------------------------------------------

@dataclass
class Approval:
    play_id: str
    agent_id: str  # or "founder"
    signature: str  # 0x hex, 65 bytes, low-s
    signed_at: int
    prev_hash: str = ""  # hex chain link
    record_hash: str = field(default="", init=False)

    def __post_init__(self) -> None:
        body = json.dumps(
            {
                "play_id": self.play_id,
                "agent_id": self.agent_id,
                "signature": self.signature,
                "signed_at": self.signed_at,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self.record_hash = hashlib.sha256(body).hexdigest()


class ApprovalLedger:
    """In-memory hash-chained ledger of play approvals."""

    def __init__(self) -> None:
        self._records: list[Approval] = []

    def append(self, approval: Approval) -> Approval:
        approval.prev_hash = self._records[-1].record_hash if self._records else "GENESIS"
        # recompute chain link now that prev_hash is set
        approval.__post_init__()
        self._records.append(approval)
        return approval

    def for_play(self, play_id: str) -> list[Approval]:
        return [r for r in self._records if r.play_id == play_id]

    def verify_chain(self) -> bool:
        prev = "GENESIS"
        for rec in self._records:
            if rec.prev_hash != prev:
                return False
            check = Approval(
                play_id=rec.play_id,
                agent_id=rec.agent_id,
                signature=rec.signature,
                signed_at=rec.signed_at,
                prev_hash=rec.prev_hash,
            )
            if check.record_hash != rec.record_hash:
                return False
            prev = rec.record_hash
        return True


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

class PlaybookGate:
    """Verifies multi-sig / founder approval for offensive plays."""

    def __init__(self, ledger: ApprovalLedger | None = None) -> None:
        self.ledger = ledger or ApprovalLedger()

    def submit_approval(
        self,
        *,
        play_id: str,
        action: str,
        target_scope: str,
        created_at: int,
        agent_id: str,
        signature: str,
        founder_address: str | None = None,
    ) -> Approval:
        """Verify one signature and record it. Raises ValueError if invalid."""
        digest = play_hash(play_id, action, target_scope, created_at)
        raw_sig = sig_canonical.require_low_s(signature)  # rejects high-s
        message = encode_defunct(approval_message(digest, agent_id))
        recovered = Account.recover_message(message, signature=raw_sig).lower()

        if agent_id == "founder":
            if not founder_address or recovered != founder_address.lower():
                raise ValueError("founder signature does not match founder address")
        else:
            expected = approver_address(agent_id)
            if not expected:
                raise ValueError(f"approver not registered: {agent_id}")
            if recovered != expected.lower():
                raise ValueError(
                    f"signature recovered to {recovered}, expected {expected}"
                )

        # One approval per agent per play (replay within a play rejected).
        for existing in self.ledger.for_play(play_id):
            if existing.agent_id == agent_id:
                raise ValueError(f"duplicate approval from {agent_id} for {play_id}")

        return self.ledger.append(
            Approval(
                play_id=play_id,
                agent_id=agent_id,
                signature="0x" + raw_sig.hex(),
                signed_at=int(time.time()),
            )
        )

    def is_approved(
        self,
        *,
        play_id: str,
        action: str,
        target_scope: str,
        created_at: int,
        founder_address: str | None = None,
    ) -> tuple[bool, str]:
        """Check whether a play currently meets the approval threshold."""
        digest = play_hash(play_id, action, target_scope, created_at)
        approvals = self.ledger.for_play(play_id)

        valid_agents: set[str] = set()
        for ap in approvals:
            try:
                raw_sig = sig_canonical.require_low_s(ap.signature)
            except ValueError:
                continue  # tampered/non-canonical record never counts
            message = encode_defunct(approval_message(digest, ap.agent_id))
            try:
                recovered = Account.recover_message(message, signature=raw_sig).lower()
            except Exception:
                continue
            if ap.agent_id == "founder":
                if founder_address and recovered == founder_address.lower():
                    return True, "founder"
            else:
                expected = approver_address(ap.agent_id)
                if expected and recovered == expected.lower():
                    valid_agents.add(ap.agent_id.lower())

        if len(valid_agents) >= AGENT_QUORUM:
            return True, f"quorum {len(valid_agents)}/{len(APPROVER_AGENTS)}"
        return False, f"{len(valid_agents)}/{AGENT_QUORUM} agent approvals"
