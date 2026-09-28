"""Liveness Phase 1 tests.

(a) commitment byte-identity vs the server's sealed_commitment
(b) runner state machine over a fake HTTP layer
(c) crash recovery: committed state survives a "restart"
(d) cohort.json validity
(e) fountain task schema
(f) disclosure endpoint wiring
(g) three-lane fountain: lane routing, scope-drift refusal, Lane C
    blocklist, Lane B cursor idempotency
"""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIVENESS = os.path.join(REPO, "liveness")
sys.path.insert(0, LIVENESS)
sys.path.insert(0, os.path.join(REPO, "src"))

import fountain  # noqa: E402
import runner as liv_runner  # noqa: E402


# ---------------------------------------------------------------------------
# (a) commitment round-trip
# ---------------------------------------------------------------------------

def test_commitment_byte_identical_to_server():
    from sincor2.a2a_inbound_market import sealed_commitment as server_commit
    import secrets
    for _ in range(5):
        price_wei = 750_000_000_000_000_000
        salt = secrets.token_bytes(32)
        aid = "sincor-liveness-01"
        assert liv_runner.sealed_commitment(price_wei, salt, aid) == \
            server_commit(price_wei, salt, aid)
    # wrong salt must not match (negative control)
    salt2 = secrets.token_bytes(32)
    assert liv_runner.sealed_commitment(price_wei, salt, aid) != \
        server_commit(price_wei, salt2, aid)


# ---------------------------------------------------------------------------
# fake HTTP layer for (b) and (c)
# ---------------------------------------------------------------------------

class FakeServer:
    """Minimal in-memory stand-in for the A2A HTTP surface."""

    def __init__(self):
        self.now_ms = int(time.time() * 1000)
        self.tasks = {}
        self.commits = {}   # (task_id, agent_id) -> {"commitment": hex, "revealed": bool}
        self.proofs = []
        self.closed = []
        self.heartbeats = []

    # -- HTTP surface ------------------------------------------------------
    def get_json(self, base_url, path, **kw):
        if path.startswith("/v1/a2a/tasks?"):
            return {"tasks": list(self.tasks.values())}
        if path.startswith("/v1/a2a/tasks/"):
            tid = path.split("/")[4]
            return dict(self.tasks[tid])
        if path.startswith("/v1/a2a/agents"):
            return {"agents": []}
        if path == "/v1/a2a/pool":
            return {"funded_axm": 20000.0, "allocated_axm": 91.0,
                    "available_axm": 19909.0}
        raise AssertionError(f"unexpected GET {path}")

    def post_json(self, base_url, path, body, **kw):
        if path == "/v1/a2a/heartbeat":
            self.heartbeats.append(body["agent_id"])
            return {"ok": True}
        if path == "/v1/a2a/bids/commit":
            key = (body["task_id"], body["agent_id"])
            if key in self.commits:
                from net import HttpError
                raise HttpError(409, "already committed")
            self.commits[key] = {"commitment": body["commitment"],
                                 "revealed": False}
            return {"committed": True}
        if path == "/v1/a2a/bids/reveal":
            key = (body["task_id"], body["agent_id"])
            rec = self.commits.get(key)
            assert rec, "reveal without commit"
            # verify the commitment matches what the runner claims
            from sincor2.a2a_inbound_market import sealed_commitment as sc
            price_wei = int(round(body["bid_axm"] * 1e18))
            salt = bytes.fromhex(body["nonce"])
            expect = "0x" + sc(price_wei, salt, body["agent_id"]).hex()
            assert rec["commitment"] == expect, "commitment mismatch on reveal"
            rec["revealed"] = True
            return {"revealed": True, "bid_axm": body["bid_axm"]}
        if path.endswith("/close"):
            tid = path.split("/")[4]
            self.closed.append(tid)
            self.tasks[tid]["state"] = "assigned"
            return {"task_id": tid, "state": "assigned"}
        if path == "/v1/a2a/proofs":
            self.proofs.append(body)
            return {"proof_id": "prf_test", "status": "paid"}
        raise AssertionError(f"unexpected POST {path}")

    # -- fixture builders ----------------------------------------------------
    def add_task(self, task_id="t1", bounty=1.0, tags=("defi", "data"),
                 seed_key="fountain-defi-metrics-sweep-2026-09-28"):
        now = self.now_ms
        self.tasks[task_id] = {
            "task_id": task_id,
            "state": "open",
            "sealed": True,
            "tags": list(tags),
            "bounty_axm": bounty,
            "seed_key": seed_key,
            "title": "T",
            "commit_deadline": now + 5 * 60_000,
            "reveal_deadline": now + 10 * 60_000,
        }
        return self.tasks[task_id]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    srv = FakeServer()
    monkeypatch.setattr(liv_runner, "get_json", srv.get_json)
    monkeypatch.setattr(liv_runner, "post_json", srv.post_json)
    # fountain executors call net's functions via fountain's own namespace,
    # so they need the fake too (otherwise they hit the real network)
    monkeypatch.setattr(fountain, "get_json", srv.get_json)
    monkeypatch.setattr(fountain, "post_json", srv.post_json)
    monkeypatch.setattr(liv_runner, "COMMITS_PATH",
                        str(tmp_path / "commits.json"))
    monkeypatch.setattr(liv_runner, "QUEUE_PATH",
                        str(tmp_path / "queue.json"))
    monkeypatch.setattr(liv_runner.fountain, "ensure_supply",
                        lambda *a, **k: {"lane_a": [], "lane_b": [],
                                         "lane_c": []})
    return srv


def _cohort():
    return liv_runner.load_cohort()


# ---------------------------------------------------------------------------
# (b) state machine: commit -> persist -> reveal -> close -> proof
# ---------------------------------------------------------------------------

def test_runner_full_lifecycle(fake, monkeypatch):
    task = fake.add_task()
    r = liv_runner.Runner("http://x", _cohort())

    # heartbeat + commit sweep
    r.heartbeat_all()
    assert sorted(fake.heartbeats) == sorted(a["agent_id"] for a in _cohort())
    r.sweep_commits(r._task_index())
    committed_agents = {aid for (tid, aid) in fake.commits}
    assert 3 <= len(committed_agents) <= 4, committed_agents

    # salt persisted BEFORE the POST (crash safety): state file has entries
    # for every committed agent, each with a 64-hex-char salt
    on_disk = json.load(open(liv_runner.COMMITS_PATH))
    assert set(on_disk["t1"]) == committed_agents
    for entry in on_disk["t1"].values():
        assert len(entry["salt_hex"]) == 64
        assert entry["status"] == "committed"

    # move into the reveal window and reveal
    task["commit_deadline"] = fake.now_ms - 1_000
    task["reveal_deadline"] = fake.now_ms + 5 * 60_000
    r.now_ms = fake.now_ms
    r.reveal_due(r._task_index())
    assert all(v["revealed"] for v in fake.commits.values())

    # close after reveal deadline
    task["reveal_deadline"] = fake.now_ms - 1_000
    r.close_due(r._task_index())
    assert fake.closed == ["t1"]

    # assigned fountain task -> performed + proven (all checks pass)
    task["state"] = "assigned"
    task["assigned_to"] = sorted(committed_agents)[0]
    r.perform_assigned(r._task_index())
    assert len(fake.proofs) == 1
    assert fake.proofs[0]["task_id"] == "t1"
    assert fake.proofs[0]["receipt_hash"].startswith("0x")
    assert len(fake.proofs[0]["receipt_hash"]) == 66


def test_runner_never_fakes_catalog_proofs(fake):
    fake.add_task(task_id="t9", seed_key="p01-vault-architecture-spec")
    r = liv_runner.Runner("http://x", _cohort())
    t = fake.tasks["t9"]
    t["state"] = "assigned"
    t["assigned_to"] = "sincor-liveness-01"
    r.perform_assigned(r._task_index())
    assert fake.proofs == []
    queue = json.load(open(liv_runner.QUEUE_PATH))
    assert len(queue) == 1 and queue[0]["task_id"] == "t9"


# ---------------------------------------------------------------------------
# (c) crash recovery
# ---------------------------------------------------------------------------

def test_crash_recovery_reveals_after_restart(fake):
    fake.add_task()
    r1 = liv_runner.Runner("http://x", _cohort())
    r1.sweep_commits(r1._task_index())
    committed = {aid for (tid, aid) in fake.commits}
    assert committed  # preconditions: commits happened pre-crash

    # "restart": brand-new Runner, same state dir, reveal window now open
    fake.tasks["t1"]["commit_deadline"] = fake.now_ms - 1_000
    fake.tasks["t1"]["reveal_deadline"] = fake.now_ms + 5 * 60_000
    r2 = liv_runner.Runner("http://x", _cohort())
    r2.now_ms = fake.now_ms
    r2.recover_and_reveal(r2._task_index())
    assert all(v["revealed"] for v in fake.commits.values()), \
        "every committed bid must be revealed after restart — never ghost"


def test_salt_persisted_before_post_survives_crash(fake, monkeypatch):
    """If the commit POST itself raises a transport error AFTER the server
    recorded it, the entry must survive so the retry is idempotent."""
    fake.add_task()
    real_post = fake.post_json
    calls = {"n": 0}

    def flaky(base_url, path, body, **kw):
        if path == "/v1/a2a/bids/commit":
            calls["n"] += 1
            if calls["n"] == 1:
                # server recorded it, response lost
                real_post(base_url, path, body, **kw)
                raise ConnectionError("response lost")
        return real_post(base_url, path, body, **kw)

    monkeypatch.setattr(liv_runner, "post_json", flaky)
    r = liv_runner.Runner("http://x", _cohort()[:1])
    # force single agent eligibility by shrinking cohort
    r.cohort = [a for a in _cohort() if a["agent_id"] == "sincor-liveness-01"]
    r.sweep_commits(r._task_index())
    on_disk = json.load(open(liv_runner.COMMITS_PATH))
    assert "t1" in on_disk and "sincor-liveness-01" in on_disk["t1"]
    # retry hits "already committed" -> treated as success, no duplicate
    r2 = liv_runner.Runner("http://x", _cohort()[:1])
    r2.cohort = r.cohort
    r2.sweep_commits(r2._task_index())
    assert len([k for k in fake.commits]) == 1


# ---------------------------------------------------------------------------
# (d) cohort validity
# ---------------------------------------------------------------------------

def test_cohort_valid():
    cohort = _cohort()
    assert len(cohort) == 6
    ids = [a["agent_id"] for a in cohort]
    assert ids == [f"sincor-liveness-0{i}" for i in range(1, 7)]
    for a in cohort:
        w = a["wallet"]
        assert w.startswith("0x") and len(w) == 42, w
        int(w[2:], 16)  # valid hex
        assert a["capability_tags"], "tags must be non-empty"
        assert "SINCOR-operated" in a["description"]
    assert len({a["wallet"] for a in cohort}) == 6, "wallets must be unique"


# ---------------------------------------------------------------------------
# (e) fountain schema
# ---------------------------------------------------------------------------

def test_fountain_schema():
    assert len(fountain.FOUNTAIN_TYPES) == 6
    for ttype in fountain.FOUNTAIN_TYPES:
        fountain.validate_type(ttype)
        assert ttype in fountain.EXECUTORS
        spec = fountain.FOUNTAIN_TYPES[ttype]
        assert "Acceptance criteria" in spec["description"]
    import datetime
    sk = fountain.seed_key_for("defi-metrics-sweep",
                               datetime.date(2026, 9, 28))
    assert sk == "fountain-defi-metrics-sweep-2026-09-28"
    assert liv_runner.fountain_type_of(sk) == "defi-metrics-sweep"


def test_fountain_executors_return_checks():
    for ttype, fn in fountain.EXECUTORS.items():
        # executors hit HTTP; just verify the contract shape via signature
        import inspect
        assert list(inspect.signature(fn).parameters) == ["base_url"], ttype


# ---------------------------------------------------------------------------
# (f) disclosure endpoint
# ---------------------------------------------------------------------------

def test_disclosure_payload_and_route():
    from sincor2.liveness import disclosure, is_liveness_agent, attach_liveness
    assert is_liveness_agent("sincor-liveness-01")
    assert not is_liveness_agent("someone-else")
    d = disclosure()
    assert len(d["agents"]) == 6
    for a in d["agents"]:
        assert a["wallet"].startswith("0x")
        assert "SINCOR-operated" in a["note"]

    from flask import Flask
    app = Flask(__name__)
    attach_liveness(app)
    client = app.test_client()
    r = client.get("/.well-known/liveness-agents.json")
    assert r.status_code == 200
    assert len(r.get_json()["agents"]) == 6


# ---------------------------------------------------------------------------
# (g) three-lane fountain: routing, scope-drift refusal, Lane C blocklist,
#     Lane B cursor idempotency
# ---------------------------------------------------------------------------

def test_unknown_fountain_type_refused():
    with pytest.raises(fountain.FountainRefused):
        fountain.validate_type("freeform-evil-task")
    with pytest.raises(fountain.FountainRefused):
        fountain.post_lane_a("http://x", "not-a-real-type")


def test_lane_c_blocklist_rejected():
    base = fountain.allowlist_entries()[0]
    for bad in ["src/sincor2/stake_ledger.py",
                "src/sincor2/sponsored_stake.py",
                "src/sincor2/a2a_bounty_pool.py",
                "src/sincor2/a2a_task_store.py",
                "src/sincor2/auth_system.py",
                "src/sincor2/kya_registry.py",
                "src/sincor2/adjudication.py",
                "contracts/ExecutionEscrowManager.sol",
                "src/sincor2/treasury.py",
                "config/credentials.json",
                "keys/deployer.pem",
                ".env"]:
        evil = dict(base)
        evil["id"] = "evil-test"
        evil["file_paths"] = [bad]
        evil["description"] = base["description"] + f"\nTouch {bad}\n"
        with pytest.raises(fountain.FountainRefused):
            fountain.validate_lane_c(evil)


def test_lane_c_allowlist_all_valid_and_gated():
    entries = fountain.allowlist_entries()
    assert len(entries) >= 4
    for e in entries:
        fountain.validate_lane_c(e)
        low = e["description"].lower()
        assert "branch + pr" in low, e["id"]
        assert "never direct to main" in low, e["id"]
        assert "human merge gate" in low, e["id"]
        for fp in e["file_paths"]:
            assert fp in e["description"], (e["id"], fp)


def test_lane_c_missing_delivery_phrase_refused():
    base = dict(fountain.allowlist_entries()[0])
    base["id"] = "no-gate"
    base["description"] = "Polish docs/A2A_EXTERNAL_WIRE.md copy."
    with pytest.raises(fountain.FountainRefused):
        fountain.validate_lane_c(base)


def test_lane_routing():
    assert liv_runner.fountain_lane_of(
        "fountain-defi-metrics-sweep-2026-09-28") == "a"
    assert liv_runner.fountain_lane_of(
        "selfimprove-docs-wire-clarity-2026-09-28") == "c"
    assert liv_runner.fountain_lane_of("p01-vault-architecture-spec") == "b"
    assert liv_runner.fountain_lane_of("p99-not-a-real-task") is None
    assert liv_runner.fountain_lane_c_id(
        "selfimprove-docs-wire-clarity-2026-09-28") == "docs-wire-clarity"
    assert liv_runner.fountain_type_of(
        "fountain-defi-metrics-sweep-2026-09-28") == "defi-metrics-sweep"


def _wave_one_spec_keys():
    import glob as _glob
    spec = set()
    for p in sorted(_glob.glob(
            os.path.join(fountain.DEFI_CATALOG_DIR, "p*.json"))):
        for t in json.load(open(p)):
            if t["phase"] == "spec":
                spec.add(t["task_key"])
    return spec


def test_lane_b_due_respects_dependencies():
    st = {"defi_posted": [], "lane_c_posted": {}, "daily": {}}
    assert fountain.due_defi_tasks(set(), st, 1000) == []
    spec = _wave_one_spec_keys()
    assert len(spec) == 78
    due = fountain.due_defi_tasks(spec, st, 1000)
    assert due, "scaffold wave should unlock once wave-one spec is live"
    assert all(t["phase"] == "scaffold" for t in due)
    for t in due:
        assert all(d in spec for d in (t.get("depends_on") or []))
    # already-posted keys are never due again
    st["defi_posted"] = [t["task_key"] for t in due]
    due2 = fountain.due_defi_tasks(spec, st, 1000)
    assert not {t["task_key"] for t in due2} & set(st["defi_posted"])


def test_lane_b_post_marks_cursor_and_is_idempotent(fake, monkeypatch,
                                                    tmp_path):
    monkeypatch.setattr(fountain, "STATE_PATH",
                        str(tmp_path / "fstate.json"))
    for sk in _wave_one_spec_keys():
        fake.tasks[f"live-{sk}"] = {"task_id": f"live-{sk}",
                                   "seed_key": sk, "state": "open"}
    posted_calls = []

    def counting(base_url, path, body, **kw):
        if path == "/v1/a2a/tasks":
            posted_calls.append(body["seed_key"])
            assert body["poster_id"] == "sincor-fountain"
            assert body["sealed"] is True
            tid = f"t-{body['seed_key']}"
            fake.tasks[tid] = {"task_id": tid, "seed_key": body["seed_key"],
                               "state": "open", "title": body["title"]}
            return {"task_id": tid}
        return fake.get_json(base_url, path, **kw)

    monkeypatch.setattr(fountain, "post_json", counting)
    first = fountain.post_lane_b("http://x", max_n=5)
    assert len(first) == 5 and len(set(posted_calls)) == 5
    st = fountain.load_state()
    assert len(st["defi_posted"]) == 5
    # second call: cursor + live board both say "already there" — no reposts
    second = fountain.post_lane_b("http://x", max_n=5)
    assert second == [] and len(posted_calls) == 5


def test_runner_queues_lane_b_and_c_wins_never_fakes(fake):
    fake.add_task(task_id="tb", seed_key="p02-clmm-repo-scaffold")
    fake.add_task(task_id="tc",
                 seed_key="selfimprove-docs-wire-clarity-2026-09-28")
    r = liv_runner.Runner("http://x", _cohort())
    for tid in ("tb", "tc"):
        fake.tasks[tid]["state"] = "assigned"
        fake.tasks[tid]["assigned_to"] = "sincor-liveness-01"
    r.perform_assigned(r._task_index())
    assert fake.proofs == [], "Lane B/C wins must never be faked"
    queue = json.load(open(liv_runner.QUEUE_PATH))
    by_id = {q["task_id"]: q for q in queue}
    assert by_id["tb"]["lane"] == "b"
    assert by_id["tc"]["lane"] == "c"


def test_estimate_seconds_lane_aware(fake):
    r = liv_runner.Runner("http://x", _cohort())
    assert r._estimate_seconds(
        {"seed_key": "fountain-fee-ledger-watch-2026-09-28"}) == 180
    assert r._estimate_seconds(
        {"seed_key": "p01-vault-architecture-spec"}) == 7200  # effort M
    assert r._estimate_seconds(
        {"seed_key": "selfimprove-docs-wire-clarity-2026-09-28"}) == 3600
