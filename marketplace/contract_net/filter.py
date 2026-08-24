"""Two-phase heuristic filter: cosine pre-rank, invite only top-k.

Uninvited agents never draft an LLM bid. Tokens saved are counted as
``(swarm_size - invite_k) * eval_tokens_per_bid``.
"""

from __future__ import annotations

import random
from typing import Optional, Sequence

from .types import (
    AgentProfile,
    ContractNetConfig,
    FilterResult,
    Invite,
    ScoredAgent,
    TaskSpec,
    clamp_invite_k,
    is_junior_agent,
)
from .vectors import cosine_similarity, embed_tokens


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    a = {token.lower() for token in left if token}
    b = {token.lower() for token in right if token}
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def score_agents(
    task: TaskSpec,
    agents: Sequence[AgentProfile],
    config: ContractNetConfig,
) -> list[ScoredAgent]:
    task_tokens = task.requirement_tokens()
    task_vec = embed_tokens(task_tokens, dim=config.vector_dim)
    scored: list[ScoredAgent] = []
    for agent in agents:
        skills = agent.skill_tokens()
        cosine = cosine_similarity(task_vec, embed_tokens(skills, dim=config.vector_dim))
        # Exact skill overlap is the product signal; hashed cosine breaks ties
        # and still ranks near-synonyms that do not share a token.
        match = 0.55 * cosine + 0.45 * _jaccard(task_tokens, skills)
        scored.append(
            ScoredAgent(
                agent=agent,
                cosine=match,
                junior=is_junior_agent(agent, config.junior_task_threshold),
            )
        )
    scored.sort(key=lambda row: (-row.cosine, row.agent.agent_id))
    return scored


def invite_agents(
    scored: Sequence[ScoredAgent],
    config: ContractNetConfig,
    *,
    rng: random.Random,
    force_junior: Optional[bool] = None,
) -> tuple[list[Invite], bool]:
    invite_k = clamp_invite_k(config.invite_k)
    junior_reserved = rng.random() < config.epsilon if force_junior is None else force_junior

    above_floor = [row for row in scored if row.cosine >= config.cosine_floor]
    universe = above_floor or list(scored)

    if junior_reserved:
        junior_pool = [row for row in universe if row.junior]
        if junior_pool:
            pool = junior_pool
        else:
            junior_reserved = False
            pool = universe
    else:
        pool = universe

    selected = list(pool[: min(invite_k, len(pool))])
    invites: list[Invite] = []
    for row in selected:
        subsidy = 0
        if junior_reserved and row.junior:
            subsidy = int(
                round(config.eval_tokens_per_bid * (config.junior_subsidy_multiplier - 1.0))
            )
        reason = (
            "junior-reserved cosine match"
            if junior_reserved and row.junior
            else "top-k cosine match"
        )
        invites.append(
            Invite(
                agent_id=row.agent.agent_id,
                name=row.agent.name,
                cosine=row.cosine,
                junior=row.junior,
                llm_invited=True,
                reason=reason,
                subsidy_tokens=subsidy,
            )
        )
    return invites, junior_reserved


def filter_swarm(
    task: TaskSpec,
    agents: Sequence[AgentProfile],
    config: ContractNetConfig,
    *,
    rng: Optional[random.Random] = None,
    force_junior: Optional[bool] = None,
) -> FilterResult:
    rng = rng or random.Random()
    scored = score_agents(task, agents, config)
    invites, junior_reserved = invite_agents(
        scored, config, rng=rng, force_junior=force_junior
    )
    invited_n = len(invites)
    swarm_n = len(agents)
    avoided = max(0, swarm_n - invited_n)
    return FilterResult(
        ranked=list(scored),
        invited=invites,
        junior_reserved=junior_reserved,
        pool_size=swarm_n,
        llm_calls_avoided=avoided,
        tokens_saved=avoided * config.eval_tokens_per_bid,
    )
