"""Contract-Net task market: cosine invite, Vickrey sealed bids, ε-greedy juniors."""

from .engine import ContractNetEngine, make_auction_id, profiles_from_dicts, task_from_dict
from .compliance_filter import ComplianceAttestationFilter, ComplianceDecision
from .filter import filter_swarm, score_agents
from .keccak import keccak256, keccak256_hex
from .roster import demo_roster, demo_tasks
from .types import (
    AgentProfile,
    Award,
    ContractNetConfig,
    FilterResult,
    Invite,
    SealedBid,
    TaskSpec,
)
from .vickrey import PLATFORM_FEE_BPS, clear_vickrey, platform_fee_for, split_clearing_proceeds
from .volume_ledger import (
    BASE_FEE_BPS,
    STEP_UP_FEE_BPS,
    fee_bps_for_volumes,
    monthly_volumes_usd,
    record_clearing,
    resolve_fee_bps,
)
from .vectors import cosine_similarity, embed_tokens

try:
    from .hybrid_router import EpochBinding, RoutedAgent, TaskAuctionMemoryRouter
except Exception:  # pragma: no cover - optional when SINAX deps unavailable
    EpochBinding = None  # type: ignore[assignment]
    RoutedAgent = None  # type: ignore[assignment]
    TaskAuctionMemoryRouter = None  # type: ignore[assignment]

__all__ = [
    "AgentProfile",
    "Award",
    "ContractNetConfig",
    "ContractNetEngine",
    "ComplianceAttestationFilter",
    "ComplianceDecision",
    "FilterResult",
    "Invite",
    "SealedBid",
    "TaskSpec",
    "PLATFORM_FEE_BPS",
    "BASE_FEE_BPS",
    "STEP_UP_FEE_BPS",
    "clear_vickrey",
    "fee_bps_for_volumes",
    "monthly_volumes_usd",
    "platform_fee_for",
    "record_clearing",
    "resolve_fee_bps",
    "split_clearing_proceeds",
    "cosine_similarity",
    "demo_roster",
    "demo_tasks",
    "embed_tokens",
    "filter_swarm",
    "keccak256",
    "keccak256_hex",
    "make_auction_id",
    "profiles_from_dicts",
    "score_agents",
    "task_from_dict",
    "TaskAuctionMemoryRouter",
    "EpochBinding",
    "RoutedAgent",
]
