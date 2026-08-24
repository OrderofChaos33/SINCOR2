"""Contract-Net task market: cosine invite, Vickrey sealed bids, ε-greedy juniors."""

from .engine import ContractNetEngine, make_auction_id, profiles_from_dicts, task_from_dict
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
from .vickrey import clear_vickrey
from .vectors import cosine_similarity, embed_tokens

__all__ = [
    "AgentProfile",
    "Award",
    "ContractNetConfig",
    "ContractNetEngine",
    "FilterResult",
    "Invite",
    "SealedBid",
    "TaskSpec",
    "clear_vickrey",
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
]
