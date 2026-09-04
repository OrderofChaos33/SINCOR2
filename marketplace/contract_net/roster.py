"""Canonical demo swarm used by tests, the Flask sandbox, and the operator console.

Prices are true minimum margins in micro-AXM. Under Vickrey the dominant
strategy is to bid this number in a single sealed pass.
"""

from __future__ import annotations

from typing import List

from .eip712 import demo_signing_secret, demo_wallet
from .types import AgentProfile, MICRO_AXM, TaskSpec


def _agent(
    agent_id: str,
    name: str,
    skills: tuple[str, ...],
    *,
    tasks_completed: int,
    success_rate: float,
    axm: float,
    estimated_tokens: int,
    is_junior: bool = False,
    supported_schemas: tuple[str, ...] = ("default", "a2a-v1"),
) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        name=name,
        skills=skills,
        wallet=demo_wallet(agent_id),
        tasks_completed=tasks_completed,
        success_rate=success_rate,
        true_min_price=int(round(axm * MICRO_AXM)),
        estimated_tokens=estimated_tokens,
        execution_budget=estimated_tokens * 2,
        supported_schemas=supported_schemas,
        is_junior=is_junior or tasks_completed < 3,
        signing_secret=demo_signing_secret(agent_id),
    )


def demo_roster() -> List[AgentProfile]:
    return [
        _agent(
            "atlas-scout",
            "Atlas Scout",
            ("prospect", "scrape", "research", "monitor", "validate"),
            tasks_completed=48,
            success_rate=0.91,
            axm=1.20,
            estimated_tokens=520,
        ),
        _agent(
            "helix-synth",
            "Helix Synthesizer",
            ("summarize", "analyze", "curate", "dedup", "deconflict", "research"),
            tasks_completed=36,
            success_rate=0.88,
            axm=1.10,
            estimated_tokens=480,
        ),
        _agent(
            "forge-builder",
            "Forge Builder",
            ("develop", "automate", "deploy", "test", "debug"),
            tasks_completed=52,
            success_rate=0.86,
            axm=1.40,
            estimated_tokens=640,
        ),
        _agent(
            "pact-negotiator",
            "Pact Negotiator",
            ("outreach", "negotiate", "present", "close", "persuade"),
            tasks_completed=29,
            success_rate=0.84,
            axm=1.35,
            estimated_tokens=410,
        ),
        _agent(
            "vault-auditor",
            "Vault Auditor",
            ("evaluate", "verify", "investigate", "report", "certify"),
            tasks_completed=41,
            success_rate=0.93,
            axm=1.25,
            estimated_tokens=390,
        ),
        _agent(
            "nova-director",
            "Nova Director",
            ("prioritize", "coordinate", "allocate", "plan", "decide"),
            tasks_completed=60,
            success_rate=0.90,
            axm=1.60,
            estimated_tokens=360,
        ),
        _agent(
            "ledger-ops",
            "Ledger Ops",
            ("settlement", "treasury", "monitor", "report"),
            tasks_completed=22,
            success_rate=0.81,
            axm=1.05,
            estimated_tokens=430,
        ),
        _agent(
            "spark-scout",
            "Spark Scout",
            ("prospect", "scrape", "research"),
            tasks_completed=1,
            success_rate=0.62,
            axm=0.85,
            estimated_tokens=540,
            is_junior=True,
        ),
        _agent(
            "ember-builder",
            "Ember Builder",
            ("develop", "test", "debug"),
            tasks_completed=0,
            success_rate=0.50,
            axm=0.90,
            estimated_tokens=610,
            is_junior=True,
        ),
        _agent(
            "moss-caretaker",
            "Moss Caretaker",
            ("clean", "label", "organize", "maintain", "backup"),
            tasks_completed=2,
            success_rate=0.71,
            axm=0.70,
            estimated_tokens=280,
            is_junior=True,
        ),
        _agent(
            "wick-analyst",
            "Wick Analyst",
            ("analyze", "research", "report", "summarize"),
            tasks_completed=1,
            success_rate=0.58,
            axm=0.80,
            estimated_tokens=450,
            is_junior=True,
        ),
        _agent(
            "hatch-ops",
            "Hatch Ops",
            ("automate", "deploy", "monitor", "backup"),
            tasks_completed=0,
            success_rate=0.50,
            axm=0.95,
            estimated_tokens=500,
            is_junior=True,
        ),
        _agent(
            "sapling-legal",
            "Sapling Legal",
            ("evaluate", "verify", "certify", "policy"),
            tasks_completed=2,
            success_rate=0.66,
            axm=0.88,
            estimated_tokens=370,
            is_junior=True,
        ),
    ]


def demo_tasks() -> List[TaskSpec]:
    return [
        TaskSpec(
            task_id="cn-map-competitors",
            goal="Map competitor pricing for enterprise A2A listings",
            requirements=("research", "scrape", "analyze"),
            budget_tokens=1800,
            max_price=2_000_000,
            required_schema="a2a-v1",
            runtime_capabilities=("research", "scrape"),
            execution_budget=1200,
        ),
        TaskSpec(
            task_id="cn-ship-webhook",
            goal="Ship the settlement webhook and tests",
            requirements=("develop", "test", "deploy"),
            budget_tokens=2400,
            max_price=2_200_000,
            required_schema="a2a-v1",
            runtime_capabilities=("develop", "test"),
            execution_budget=1800,
        ),
        TaskSpec(
            task_id="cn-outreach-sequence",
            goal="Draft outreach sequence for staked agents",
            requirements=("outreach", "persuade", "present"),
            budget_tokens=1200,
            max_price=1_800_000,
            required_schema="default",
            runtime_capabilities=("outreach", "present"),
            execution_budget=900,
        ),
        TaskSpec(
            task_id="cn-memory-backup",
            goal="Nightly backup and label of memory shards",
            requirements=("backup", "label", "maintain"),
            budget_tokens=900,
            max_price=1_200_000,
            required_schema="default",
            runtime_capabilities=("backup", "maintain"),
            execution_budget=500,
        ),
        TaskSpec(
            task_id="cn-eip712-certify",
            goal="Certify the EIP-712 bid domain against the treasury",
            requirements=("evaluate", "verify", "certify"),
            budget_tokens=1100,
            max_price=1_600_000,
            required_schema="a2a-v1",
            runtime_capabilities=("verify", "certify"),
            execution_budget=700,
        ),
        TaskSpec(
            task_id="cn-route-plan",
            goal="Prioritize the week’s routing queue and allocate juniors",
            requirements=("prioritize", "allocate", "coordinate"),
            budget_tokens=800,
            max_price=2_000_000,
            required_schema="default",
            runtime_capabilities=("allocate", "coordinate"),
            execution_budget=450,
        ),
    ]


def task_by_id(task_id: str) -> TaskSpec:
    for task in demo_tasks():
        if task.task_id == task_id:
            return task
    raise KeyError(task_id)
