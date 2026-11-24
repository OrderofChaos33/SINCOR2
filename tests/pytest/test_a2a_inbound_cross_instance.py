"""W-201: fabric board cross-instance lost-update regression tests.

The fabric singleton in ``sincor2.a2a_inbound`` loads tasks/agents once
at boot; ``_save_tasks`` / ``_save_agents`` used to rewrite the whole
on-disk state from the (possibly stale) in-memory snapshot with no file
lock and no reload before save, so two instances/processes silently
dropped each other's tasks, bids, commits, proofs, and agent
registrations. Agents save was worse: a plain ``path.write_text`` —
non-atomic, so a concurrent reader (or a crash) could observe a torn
file.

These tests simulate two processes with two independent ``Fabric``
instances sharing one store path: instance A saves, then stale
instance B saves; A's records must survive.
"""

from __future__ import annotations

import json

import pytest

import sincor2.a2a_inbound as inbound
from sincor2.a2a_inbound import Fabric, _load_agents, _load_tasks, _save_agents, _save_tasks


@pytest.fixture
def agents_env(monkeypatch, tmp_path):
    """Isolated agent-registry data dir shared by every Fabric here."""
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    return tmp_path


@pytest.fixture
def board_env(monkeypatch, tmp_path):
    """Isolated board store: tasks JSON + checkpoint DB beside it."""
    tasks_path = tmp_path / "board" / "a2a_inbound_tasks.json"
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(tasks_path))
    monkeypatch.delenv("SINCOR_MARKETPLACE_CHECKPOINT_PATH", raising=False)
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    return tasks_path


def _agent(agent_id: str) -> dict:
    return {"agent_id": agent_id, "name": agent_id, "reputation": 0.5}


def _task(task_id: str) -> dict:
    return {"task_id": task_id, "state": "open", "skill": "lead-enrichment"}


# ── agents ───────────────────────────────────────────────────────────


def test_agents_cross_instance_lost_update(agents_env):
    """Stale instance B's save must not erase agent A registered."""
    fabric_a = Fabric()
    fabric_b = Fabric()
    _load_agents(fabric_a)
    _load_agents(fabric_b)  # both boot against an empty registry

    fabric_a.agents["agent-a"] = _agent("agent-a")
    _save_agents(fabric_a)

    # B never saw agent-a; its save used to rewrite the file with only
    # its own snapshot, silently dropping A's registration.
    fabric_b.agents["agent-b"] = _agent("agent-b")
    _save_agents(fabric_b)

    path = inbound._persist_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    saved_ids = {a["agent_id"] for a in raw["agents"]}
    assert saved_ids == {"agent-a", "agent-b"}
    assert raw["expected_agent_count"] == 2

    fresh = Fabric()
    restored, _expected = _load_agents(fresh)
    assert restored == 2
    assert set(fresh.agents) == {"agent-a", "agent-b"}


def test_agents_stale_save_preserves_peer_update_and_own_update(agents_env):
    """On key conflict the saving instance's record wins; peer-only
    records survive untouched."""
    fabric_a = Fabric()
    fabric_b = Fabric()
    _load_agents(fabric_a)
    _load_agents(fabric_b)

    fabric_a.agents["shared"] = {**_agent("shared"), "name": "from-a"}
    fabric_a.agents["only-a"] = _agent("only-a")
    _save_agents(fabric_a)

    fabric_b.agents["shared"] = {**_agent("shared"), "name": "from-b"}
    _save_agents(fabric_b)

    fresh = Fabric()
    _load_agents(fresh)
    assert set(fresh.agents) == {"shared", "only-a"}
    assert fresh.agents["shared"]["name"] == "from-b"


def test_agents_save_is_atomic_via_tmp_replace(agents_env, monkeypatch):
    """The agents write must go through tmp + os.replace (a torn
    write_text of the registry file itself is the W-201 defect)."""
    replace_calls = []
    real_replace = inbound.os.replace

    def spy_replace(src, dst, *args, **kwargs):
        replace_calls.append((str(src), str(dst)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(inbound.os, "replace", spy_replace)
    fabric = Fabric()
    fabric.agents["agent-a"] = _agent("agent-a")
    _save_agents(fabric)

    path = inbound._persist_path()
    assert any(dst == str(path) and src != str(path) for src, dst in replace_calls), (
        "agents save did not persist via an atomic tmp + os.replace"
    )
    # And the file a concurrent reader sees is always complete JSON.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["expected_agent_count"] == 1


# ── tasks / board ────────────────────────────────────────────────────


def test_tasks_cross_instance_lost_update(board_env):
    """Stale instance B's save must not erase A's task/bid/proof."""
    fabric_a = Fabric()
    fabric_b = Fabric()
    _load_tasks(fabric_a)
    _load_tasks(fabric_b)  # both boot against an empty board

    fabric_a.tasks["task-a"] = _task("task-a")
    fabric_a.bids["bid-a"] = {"bid_id": "bid-a", "task_id": "task-a"}
    fabric_a.commits["task-a\x00agent-a"] = {"task_id": "task-a", "agent_id": "agent-a"}
    fabric_a.proofs["proof-a"] = {"proof_id": "proof-a", "task_id": "task-a"}
    _save_tasks(fabric_a)

    fabric_b.tasks["task-b"] = _task("task-b")
    _save_tasks(fabric_b)

    fresh = Fabric()
    _load_tasks(fresh)
    assert set(fresh.tasks) == {"task-a", "task-b"}
    assert set(fresh.bids) == {"bid-a"}
    assert set(fresh.proofs) == {"proof-a"}
    assert "task-a\x00agent-a" in fresh.commits

    raw = json.loads(board_env.read_text(encoding="utf-8"))
    assert {t["task_id"] for t in raw["tasks"]} == {"task-a", "task-b"}
    # Checkpoint pairing semantics preserved: the JSON records the
    # monotonic version of the checkpoint written by the same save.
    assert isinstance(raw["checkpoint_version"], int)


def test_tasks_delete_by_owner_is_not_resurrected(board_env):
    """A task its owner instance deleted (and saved) stays deleted —
    the reload-merge must honor same-instance deletions, not union
    the stale on-disk copy back in."""
    fabric = Fabric()
    _load_tasks(fabric)
    fabric.tasks["task-x"] = _task("task-x")
    _save_tasks(fabric)

    fabric.tasks.pop("task-x", None)
    _save_tasks(fabric)

    fresh = Fabric()
    _load_tasks(fresh)
    assert "task-x" not in fresh.tasks
