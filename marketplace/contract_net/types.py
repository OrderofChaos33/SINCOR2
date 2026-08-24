"""Dataclasses for the Contract-Net task market.

Integer prices are micro-AXM (1_000_000 = 1 AXM) to avoid float rounding in
the Vickrey clearing step.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple


VECTOR_DIM = 64
MICRO_AXM = 1_000_000
BASE_CHAIN_ID = 8453
# Domain verifying contract: platform treasury (canonical, Base).
# The market does not move funds itself; this binds typed-data to the chain.
DEFAULT_VERIFYING_CONTRACT = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
DOMAIN_NAME = "SINCOR Contract-Net"
DOMAIN_VERSION = "1"

EMPTY_KECCAK = "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"

_STOP = {
    "a", "an", "and", "the", "of", "for", "to", "in", "on", "at", "by", "or",
    "with", "from", "into", "over", "under", "than", "then", "that", "this",
    "its", "it", "is", "are", "be", "as", "vs", "via",
}


class AuctionPhase(str, Enum):
    FILTER = "filter"
    INVITE = "invite"
    SEALED = "sealed"
    CLEARED = "cleared"
    FAILED = "failed"


class SigType(str, Enum):
    SECP256K1 = "eip712-secp256k1"
    HMAC = "eip712-hmac-sha256"


@dataclass(frozen=True)
class ContractNetConfig:
    """Tunable market parameters. Defaults match the product spec."""

    invite_k: int = 4  # top 3–5 skill matches are invited
    invite_k_min: int = 3
    invite_k_max: int = 5
    cosine_floor: float = 0.02
    epsilon: float = 0.12  # 12% of auctions reserved for juniors (10–15% band)
    junior_task_threshold: int = 3
    junior_subsidy_multiplier: float = 2.0
    eval_tokens_per_bid: int = 800  # assumed LLM tokens per unfiltered bid draft
    chain_id: int = BASE_CHAIN_ID
    verifying_contract: str = DEFAULT_VERIFYING_CONTRACT
    domain_name: str = DOMAIN_NAME
    domain_version: str = DOMAIN_VERSION
    vector_dim: int = VECTOR_DIM
    bid_ttl_seconds: int = 120

    def __post_init__(self) -> None:
        if not (self.invite_k_min <= self.invite_k <= self.invite_k_max):
            raise ValueError(
                f"invite_k must be in [{self.invite_k_min}, {self.invite_k_max}], got {self.invite_k}"
            )
        if not (0.10 <= self.epsilon <= 0.15):
            raise ValueError(f"epsilon must be in [0.10, 0.15], got {self.epsilon}")
        if self.vector_dim <= 0:
            raise ValueError("vector_dim must be positive")


@dataclass
class AgentProfile:
    """Market participant used by the filter and auction."""

    agent_id: str
    name: str
    skills: Tuple[str, ...]
    wallet: str
    tasks_completed: int = 0
    success_rate: float = 0.5
    # True minimum margin the agent would bid under Vickrey (micro-AXM).
    true_min_price: int = 1_000_000
    estimated_tokens: int = 400
    is_junior: bool = False
    spawned_at: str = ""
    signing_secret: str = ""  # hex; HMAC path / deterministic demo key
    private_key: str = ""  # hex secp256k1; optional

    def skill_tokens(self) -> Tuple[str, ...]:
        return tuple(token.lower() for token in self.skills if token)


@dataclass
class TaskSpec:
    task_id: str
    goal: str
    requirements: Tuple[str, ...]
    budget_tokens: int
    max_price: int
    created_at: int = 0
    payer: str = DEFAULT_VERIFYING_CONTRACT

    def requirement_tokens(self) -> Tuple[str, ...]:
        tokens = [token.lower() for token in self.requirements if token]
        return tuple(token for token in tokens if token and token not in _STOP and len(token) > 1)


@dataclass
class ScoredAgent:
    agent: AgentProfile
    cosine: float
    junior: bool


@dataclass
class Invite:
    agent_id: str
    name: str
    cosine: float
    junior: bool
    llm_invited: bool
    reason: str
    subsidy_tokens: int = 0


@dataclass
class FilterResult:
    ranked: List[ScoredAgent]
    invited: List[Invite]
    junior_reserved: bool
    pool_size: int
    llm_calls_avoided: int
    tokens_saved: int


@dataclass
class SealedBid:
    auction_id: str
    task_id: str
    agent_id: str
    agent_wallet: str
    price: int
    estimated_tokens: int
    nonce: int
    deadline: int
    digest: str
    signature: str
    sig_type: str
    typed_data: Dict[str, Any] = field(default_factory=dict)
    submitted_at: int = 0
    valid: bool = True
    reject_reason: str = ""


@dataclass
class Award:
    auction_id: str
    task_id: str
    winner_id: str
    winner_wallet: str
    winner_bid_price: int
    clearing_price: int  # second price; what the winner is paid
    savings_vs_first_price: int
    mechanism: str
    junior_reserved: bool
    junior_winner: bool
    invites: List[Invite]
    bids: List[SealedBid]
    llm_calls_avoided: int
    tokens_saved: int
    valid_bid_count: int
    rejected_bid_count: int
    phase: str = AuctionPhase.CLEARED.value
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["invites"] = [asdict(invite) for invite in self.invites]
        payload["bids"] = [asdict(bid) for bid in self.bids]
        return payload


def clamp_invite_k(value: int) -> int:
    return max(3, min(5, int(value)))


def is_junior_agent(agent: AgentProfile, threshold: int) -> bool:
    if agent.is_junior:
        return True
    return agent.tasks_completed < threshold
