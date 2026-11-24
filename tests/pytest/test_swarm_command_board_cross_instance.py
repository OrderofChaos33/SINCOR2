"""W-200 — BoardStore cross-process lost updates / shared-tmp crash.

BoardStore reloads fresh on every ``_modify`` (best of the lost-update
family), but pre-fix it coordinated only with a per-instance
``threading.Lock`` — and the MCP server builds a new BoardStore per
request, so that lock coordinates nothing across instances/processes —
and wrote through a fixed tmp name (``path + ".tmp"``) shared across
processes. Two writers interleaved between load and write lose one
update; two writers sharing the tmp file can corrupt it or crash on
``os.replace``.

Fix (smallest change, per persistence-research-2026-11-23.md): an
exclusive flock on a sidecar ``.lock`` file around ``_modify`` (the
reload already happens inside it), plus a pid-unique tmp name.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import threading

from sincor2.swarm_command_board import BoardStore


def _titles_on_disk(path):
    raw = json.loads(open(path, encoding="utf-8").read())
    return sorted(c["title"] for c in raw["cards"])


def test_modify_excludes_other_instance_between_load_and_write(tmp_path):
    """Deterministic interleave: A loads state inside _modify and pauses
    in the mutator; B (a different instance, as the MCP server builds per
    request) attempts a write during that window.

    Post-fix B must block on the flock until A finishes, then reload
    fresh — both cards survive. Pre-fix B completes inside the window
    and A's stale-state write silently erases B's card.
    """
    path = str(tmp_path / "board.json")
    a = BoardStore(path)
    b = BoardStore(path)
    outcome = {}

    def _b_write():
        outcome["b_card"] = b.card_add("brainstorm", "from-b", "B", "bob")

    def _slow_fn(state):
        state["cards"].append(
            {
                "id": "aaaaaaaaaaaa",
                "lane": "brainstorm",
                "title": "from-a",
                "body": "A",
                "owner": "amy",
                "comments": [],
                "created_at": 0.0,
            }
        )
        t = threading.Thread(target=_b_write)
        t.start()
        t.join(timeout=3.0)
        outcome["b_blocked_during_a_modify"] = t.is_alive()
        outcome["thread"] = t
        return None

    a._modify(_slow_fn)
    outcome["thread"].join(timeout=10.0)

    assert outcome["b_blocked_during_a_modify"], (
        "other instance wrote while A held _modify — no cross-instance "
        "exclusion (flock) around the load-mutate-write cycle"
    )
    assert _titles_on_disk(path) == ["from-a", "from-b"], (
        f"lost update: disk titles={_titles_on_disk(path)}"
    )
    fresh = BoardStore(path)
    lanes = fresh.lanes_read()
    assert sorted(c["title"] for c in lanes["brainstorm"]) == ["from-a", "from-b"]


def test_write_uses_unique_tmp_not_shared_fixed_name(tmp_path, monkeypatch):
    """Two writes must not go through the same fixed ``path + '.tmp'``:
    that name is shared across processes and is a crash/corruption
    vector. Tmp names must be pid-unique (and unique per write)."""
    path = str(tmp_path / "board.json")
    store = BoardStore(path)
    seen = []
    real_replace = os.replace

    def _spy_replace(src, dst):
        seen.append(src)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _spy_replace)
    store.card_add("brainstorm", "one", "B", "amy")
    store.card_add("brainstorm", "two", "B", "amy")

    assert len(seen) == 2
    assert all(s != path + ".tmp" for s in seen), f"fixed shared tmp used: {seen}"
    assert str(os.getpid()) in seen[0], f"tmp name not pid-unique: {seen[0]}"
    assert seen[0] != seen[1], f"tmp name reused across writes: {seen}"
    assert not os.path.exists(path + ".tmp")


def _proc_worker(path, prefix, count, err_q):
    try:
        store = BoardStore(path)
        for i in range(count):
            store.card_add("brainstorm", f"{prefix}-{i}", "B", prefix)
    except Exception as exc:  # noqa: BLE001 - report to parent process
        err_q.put(f"{prefix}: {type(exc).__name__}: {exc}")


def test_two_processes_concurrent_adds_all_survive(tmp_path):
    """Two real processes hammering one board file: every card survives
    and neither process crashes on the shared tmp file."""
    path = str(tmp_path / "board.json")
    BoardStore(path)  # create the file up front
    # Seed a bulky board so load/dump widens the race window.
    seed = BoardStore(path)
    for i in range(60):
        seed.card_add("parked", f"seed-{i}", "x" * 200, "seed")

    ctx = mp.get_context("fork")
    err_q = ctx.Queue()
    per_proc = 30
    procs = [
        ctx.Process(target=_proc_worker, args=(path, name, per_proc, err_q))
        for name in ("proc-a", "proc-b")
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    errors = []
    while not err_q.empty():
        errors.append(err_q.get())
    assert not errors, f"worker process errors: {errors}"
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]

    titles = _titles_on_disk(path)
    expected = sorted(
        [f"seed-{i}" for i in range(60)]
        + [f"proc-a-{i}" for i in range(per_proc)]
        + [f"proc-b-{i}" for i in range(per_proc)]
    )
    assert titles == expected, (
        f"lost updates across processes: got {len(titles)} of "
        f"{len(expected)} cards; missing={sorted(set(expected) - set(titles))[:10]}"
    )


def test_two_instances_sequential_adds_both_survive(tmp_path):
    """Regression guard: the reload-on-modify behaviour that already
    worked sequentially must keep working."""
    path = str(tmp_path / "board.json")
    a = BoardStore(path)
    b = BoardStore(path)
    a.card_add("brainstorm", "first", "B", "amy")
    b.card_add("brainstorm", "second", "B", "bob")
    assert _titles_on_disk(path) == ["first", "second"]
