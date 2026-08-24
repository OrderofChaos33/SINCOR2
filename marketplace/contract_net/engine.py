"""Contract-Net orchestrator: cosine invite → sealed Vickrey → ε-greedy juniors.

This module is additive. It does not replace ``sincor2.bidding_engine.BiddingEngine.run_auction``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from .eip712 import (
    demo_signing_secret,
    sign_digest,
    to_hex,
    typed_data_digest,
    typed_data_payload,
    verify_digest,
)
from .filter import filter_swarm
from .keccak import keccak256
from .types import (
    AgentProfile,
    Award,
    AuctionPhase,
    ContractNetConfig,
    FilterResult,
    SealedBid,
    TaskSpec,
)
from .vickrey import clear_vickrey


def make_auction_id(task_id: str, salt: str = "") -> str:
    material = f"{task_id}|{salt or uuid.uuid4().hex}".encode("utf-8")
    return to_hex(keccak256(material))


@dataclass
class AuctionRecord:
    auction_id: str
    task: TaskSpec
    filter: FilterResult
    award: Award
    created_at: int


class ContractNetEngine:
    """In-process sealed-bid market. Thread-hostile on purpose — one process loop."""

    def __init__(self, config: Optional[ContractNetConfig] = None) -> None:
        self.config = config or ContractNetConfig()
        self._history: List[AuctionRecord] = []
        self._nonces: Dict[str, int] = {}

    def next_nonce(self, agent_id: str) -> int:
        value = self._nonces.get(agent_id, 0) + 1
        self._nonces[agent_id] = value
        return value

    def history(self) -> List[AuctionRecord]:
        return list(self._history)

    def stats(self) -> Dict[str, float | int]:
        records = self._history
        n = len(records)
        if n == 0:
            return {
                "auctions": 0,
                "junior_reserved": 0,
                "junior_wins": 0,
                "junior_win_rate": 0.0,
                "tokens_saved": 0,
                "llm_calls_avoided": 0,
                "mean_invite_k": 0.0,
            }
        junior_reserved = sum(1 for row in records if row.award.junior_reserved)
        junior_wins = sum(1 for row in records if row.award.junior_winner)
        return {
            "auctions": n,
            "junior_reserved": junior_reserved,
            "junior_wins": junior_wins,
            "junior_win_rate": junior_wins / n,
            "tokens_saved": sum(row.award.tokens_saved for row in records),
            "llm_calls_avoided": sum(row.award.llm_calls_avoided for row in records),
            "mean_invite_k": sum(len(row.filter.invited) for row in records) / n,
        }

    def draft_bid(
        self,
        *,
        auction_id: str,
        task: TaskSpec,
        agent: AgentProfile,
        now: Optional[int] = None,
        price: Optional[int] = None,
    ) -> SealedBid:
        now = int(now if now is not None else time.time())
        nonce = self.next_nonce(agent.agent_id)
        deadline = now + self.config.bid_ttl_seconds
        bid_price = int(price if price is not None else agent.true_min_price)
        digest = typed_data_digest(
            self.config,
            auction_id=auction_id,
            task_id=task.task_id,
            agent=agent.wallet,
            price=bid_price,
            estimated_tokens=agent.estimated_tokens,
            nonce=nonce,
            deadline=deadline,
        )
        secret = agent.signing_secret or demo_signing_secret(agent.agent_id)
        signature, sig_type = sign_digest(
            digest, private_key=agent.private_key, signing_secret=secret
        )
        bid = SealedBid(
            auction_id=auction_id,
            task_id=task.task_id,
            agent_id=agent.agent_id,
            agent_wallet=agent.wallet,
            price=bid_price,
            estimated_tokens=agent.estimated_tokens,
            nonce=nonce,
            deadline=deadline,
            digest=to_hex(digest),
            signature=signature,
            sig_type=sig_type,
            typed_data=typed_data_payload(
                self.config,
                auction_id=auction_id,
                task_id=task.task_id,
                agent=agent.wallet,
                price=bid_price,
                estimated_tokens=agent.estimated_tokens,
                nonce=nonce,
                deadline=deadline,
            ),
            submitted_at=now,
        )
        self._validate_bid(bid, task, agent, now=now, signing_secret=secret)
        return bid

    def _validate_bid(
        self,
        bid: SealedBid,
        task: TaskSpec,
        agent: Optional[AgentProfile] = None,
        *,
        now: int,
        signing_secret: str = "",
    ) -> None:
        if bid.price <= 0:
            bid.valid = False
            bid.reject_reason = "non_positive_price"
            return
        if bid.price > task.max_price:
            bid.valid = False
            bid.reject_reason = "over_max_price"
            return
        if bid.deadline < now:
            bid.valid = False
            bid.reject_reason = "expired"
            return
        secret = signing_secret
        if not secret and agent is not None:
            secret = agent.signing_secret or demo_signing_secret(agent.agent_id)
        digest = bytes.fromhex(bid.digest[2:] if bid.digest.startswith("0x") else bid.digest)
        expected = typed_data_digest(
            self.config,
            auction_id=bid.auction_id,
            task_id=bid.task_id,
            agent=bid.agent_wallet,
            price=bid.price,
            estimated_tokens=bid.estimated_tokens,
            nonce=bid.nonce,
            deadline=bid.deadline,
        )
        if digest != expected:
            bid.valid = False
            bid.reject_reason = "digest_mismatch"
            return
        if not verify_digest(
            digest,
            bid.signature,
            sig_type=bid.sig_type,
            expected_address=bid.agent_wallet,
            signing_secret=secret,
        ):
            bid.valid = False
            bid.reject_reason = "bad_signature"

    def run(
        self,
        task: TaskSpec,
        agents: Sequence[AgentProfile],
        *,
        seed: Optional[int] = None,
        force_junior: Optional[bool] = None,
        now: Optional[int] = None,
        shade: Optional[Dict[str, float]] = None,
    ) -> Award:
        """Run one sealed round.

        ``shade`` maps agent_id → multiplier on true_min_price so tests can
        prove that overbidding cannot improve payoff under Vickrey.
        """
        import random

        rng = random.Random(seed)
        now = int(now if now is not None else time.time())
        auction_id = make_auction_id(task.task_id, salt=f"{seed}-{now}")
        filtered = filter_swarm(
            task, agents, self.config, rng=rng, force_junior=force_junior
        )
        by_id = {agent.agent_id: agent for agent in agents}
        bids: List[SealedBid] = []
        shade = shade or {}
        for invite in filtered.invited:
            agent = by_id[invite.agent_id]
            multiplier = float(shade.get(agent.agent_id, 1.0))
            price = int(round(agent.true_min_price * multiplier))
            bids.append(
                self.draft_bid(
                    auction_id=auction_id,
                    task=task,
                    agent=agent,
                    now=now,
                    price=price,
                )
            )
        award = clear_vickrey(
            auction_id=auction_id,
            task_id=task.task_id,
            bids=bids,
            invites=filtered.invited,
            junior_reserved=filtered.junior_reserved,
            llm_calls_avoided=filtered.llm_calls_avoided,
            tokens_saved=filtered.tokens_saved,
        )
        if award.phase == AuctionPhase.FAILED.value:
            award.phase = AuctionPhase.FAILED.value
        self._history.append(
            AuctionRecord(
                auction_id=auction_id,
                task=task,
                filter=filtered,
                award=award,
                created_at=now,
            )
        )
        return award

    def run_many(
        self,
        tasks: Sequence[TaskSpec],
        agents: Sequence[AgentProfile],
        *,
        rounds: int = 40,
        seed: int = 7,
    ) -> List[Award]:
        awards: List[Award] = []
        n_tasks = len(tasks)
        if n_tasks == 0:
            return awards
        for i in range(rounds):
            task = tasks[i % n_tasks]
            awards.append(self.run(task, agents, seed=seed + i))
        return awards


def profiles_from_dicts(rows: Iterable[Dict]) -> List[AgentProfile]:
    agents: List[AgentProfile] = []
    for row in rows:
        agent_id = str(row.get("agent_id") or row.get("id") or "")
        skills_raw = row.get("skills") or row.get("skills_required") or ()
        if isinstance(skills_raw, str):
            skills = tuple(part.strip() for part in skills_raw.split(",") if part.strip())
        else:
            skills = tuple(str(item) for item in skills_raw)
        wallet = str(row.get("wallet") or "")
        if not wallet and agent_id:
            from .eip712 import demo_wallet

            wallet = demo_wallet(agent_id)
        agents.append(
            AgentProfile(
                agent_id=agent_id,
                name=str(row.get("name") or agent_id),
                skills=skills,
                wallet=wallet,
                tasks_completed=int(row.get("tasks_completed") or 0),
                success_rate=float(row.get("success_rate") or 0.5),
                true_min_price=int(row.get("true_min_price") or row.get("price") or 1_000_000),
                estimated_tokens=int(row.get("estimated_tokens") or row.get("estimated_cost_tokens") or 400),
                is_junior=bool(row.get("is_junior", False)),
                signing_secret=str(row.get("signing_secret") or demo_signing_secret(agent_id)),
                private_key=str(row.get("private_key") or ""),
            )
        )
    return agents


def task_from_dict(row: Dict) -> TaskSpec:
    req = row.get("requirements") or row.get("skills_required") or ()
    if isinstance(req, str):
        requirements = tuple(part.strip() for part in req.split(",") if part.strip())
    else:
        requirements = tuple(str(item) for item in req)
    return TaskSpec(
        task_id=str(row.get("task_id") or row.get("id") or "task"),
        goal=str(row.get("goal") or row.get("description") or ""),
        requirements=requirements,
        budget_tokens=int(row.get("budget_tokens") or 1000),
        max_price=int(row.get("max_price") or 2_000_000),
        created_at=int(row.get("created_at") or 0),
        payer=str(row.get("payer") or TaskSpec.__dataclass_fields__["payer"].default),
    )
