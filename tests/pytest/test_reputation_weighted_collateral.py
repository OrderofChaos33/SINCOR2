"""Reputation-weighted collateral: B_stake,i = B_base x (1 - 0.8 x R_i).

A newborn (R_i = 0) locks 100 % of the base requirement; a proven agent
(R_i = 1) locks 20 %.  The discount is bid-independent, so Vickrey
selection is untouched — reputation buys bidding CAPACITY (5x concurrent
bids on the same collateral), never wins.  Leaving SINCOR resets R_i to 0:
the switching cost is a 5x capital call.

R_i = min(1, D/50) x min(1, P/3) x min(1, tenure/90d) x 0.5^U over the
trailing 180 days.  Ghosting resets the record; upheld disputes halve its
weight; single-poster wash-trading cannot inflate P; the tenure gate
blunts farm-then-burn.

Covers: newborn full lock, veteran 20 % lock, linear partial scaling,
tenure gate, wash-trade resistance, window expiry, ghost regime-change,
upheld-dispute halving, cleared-dispute completion, idempotent recording,
alpha fixed (no runtime override), bid-independence of the discount,
discounted top-up at reveal, composition with the sponsored clawback,
balance reporting, and lock-event audit fields.
"""
from __future__ import annotations

import time

import pytest

from sincor2.onchain.stake_ledger import (
    ADJUDICATOR_ENV,
    COLLATERAL_ALPHA,
    REP_COMPLETIONS_FOR_FULL,
    REP_POSTERS_FOR_FULL,
    REP_TENURE_DAYS_FOR_FULL,
    StakeLedger,
    required_stake_wei,
    reset_stake_ledger,
    stake_ledger,
)
from sincor2.sponsored_stake import (
    ENABLE_ENV,
    front_sponsored_stake,
    reset_sponsored_ledger,
)

AXM = 10**18
AGENT = "rep-agent-1"
ADJ = "test-adjudicator"
DAY = 86400


def _expect_discounted(base_wei: int, r: float) -> int:
    """Mirror of the ledger's integer discount: pins the 8000/10000
    constants independently of the implementation."""
    r_bps = round(r * 10_000)
    keep_bps = 10_000 - 8000 * r_bps // 10_000
    return base_wei * keep_bps // 10_000


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv(ADJUDICATOR_ENV, ADJ)
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    return stake_ledger()


def _veteranize(ledger: StakeLedger, agent: str = AGENT, n: int = 50,
                posters=("p1", "p2", "p3"), tenure_days: int = 100) -> None:
    """Record n dispute-free completions spread over tenure_days."""
    now = int(time.time())
    first = now - tenure_days * DAY
    for i in range(n):
        ledger.record_completion(agent, f"task-{i}",
                                 poster_id=posters[i % len(posters)],
                                 at=first + i)


def test_newborn_locks_full_base(ledger):
    bounty = 20 * AXM
    base = required_stake_wei(bounty)
    assert ledger.reputation_score(AGENT) == 0.0
    assert ledger.stake_required_wei(bounty, AGENT) == base
    ledger.deposit(AGENT, base)
    assert ledger.lock_for_commit(AGENT, "t1", bounty) == base


def test_veteran_locks_twenty_percent(ledger):
    _veteranize(ledger)
    assert ledger.reputation_score(AGENT) == pytest.approx(1.0)
    bounty = 20 * AXM
    base = required_stake_wei(bounty)
    assert ledger.stake_required_wei(bounty, AGENT) == base * 2000 // 10_000
    ledger.deposit(AGENT, base * 2000 // 10_000)
    assert ledger.lock_for_commit(AGENT, "t1", bounty) == base * 2000 // 10_000


def test_partial_reputation_scales_linearly(ledger):
    _veteranize(ledger, n=25)  # half the completions -> R = 0.5
    assert ledger.reputation_score(AGENT) == pytest.approx(0.5)
    bounty = 20 * AXM
    base = required_stake_wei(bounty)
    assert ledger.stake_required_wei(bounty, AGENT) == base * 6000 // 10_000


def test_tenure_gate_blunts_farm_then_burn(ledger):
    # 50 completions in 10 days: D and P max out, tenure caps R at 10/90.
    _veteranize(ledger, tenure_days=10)
    expected_r = 10 / REP_TENURE_DAYS_FOR_FULL
    assert ledger.reputation_score(AGENT) == pytest.approx(expected_r)
    bounty = 20 * AXM
    base = required_stake_wei(bounty)
    assert ledger.stake_required_wei(bounty, AGENT) == \
        _expect_discounted(base, expected_r)


def test_single_poster_cannot_inflate(ledger):
    # Wash-trading 50 completions against one sock-puppet poster: P = 1/3.
    _veteranize(ledger, posters=("only-poster",))
    expected_r = 1 / REP_POSTERS_FOR_FULL
    assert ledger.reputation_score(AGENT) == pytest.approx(expected_r)
    bounty = 20 * AXM
    base = required_stake_wei(bounty)
    assert ledger.stake_required_wei(bounty, AGENT) == \
        _expect_discounted(base, expected_r)


def test_window_expiry_prunes_history(ledger):
    now = int(time.time())
    old = now - 200 * DAY  # outside the 180-day window
    for i in range(50):
        ledger.record_completion(AGENT, f"old-{i}", poster_id="p1", at=old + i)
    assert ledger.reputation_score(AGENT) == 0.0


def test_ghost_resets_reputation_to_zero(ledger):
    _veteranize(ledger)
    assert ledger.reputation_score(AGENT) == pytest.approx(1.0)
    bounty = 20 * AXM
    base = required_stake_wei(bounty)
    ledger.deposit(AGENT, base)
    ledger.lock_for_commit(AGENT, "t-ghost", bounty)  # veteran: 20 % of base
    ledger.slash_ghost(AGENT, "t-ghost")
    # Collateral regime change: the record is cleared, not just slashed.
    assert ledger.reputation_score(AGENT) == 0.0
    assert ledger.stake_required_wei(bounty, AGENT) == base
    kinds = [e["kind"] for e in ledger._data["events"]]
    assert "rep_reset" in kinds


def test_upheld_dispute_halves_weight(ledger):
    _veteranize(ledger)
    bounty = 20 * AXM
    ledger.deposit(AGENT, required_stake_wei(bounty))
    ledger.lock_for_commit(AGENT, "t-q", bounty)
    ledger.adjudicate(AGENT, "t-q", True, adjudicator=ADJ)
    # 0.5^1: the slash took the stake, the R_i hit raises future locks.
    assert ledger.reputation_score(AGENT) == pytest.approx(0.5)
    base = required_stake_wei(bounty)
    assert ledger.stake_required_wei(bounty, AGENT) == base * 6000 // 10_000


def test_cleared_dispute_counts_as_completion(ledger):
    bounty = 20 * AXM
    ledger.deposit(AGENT, required_stake_wei(bounty))
    ledger.lock_for_commit(AGENT, "t-c", bounty)
    result = ledger.adjudicate(AGENT, "t-c", False, adjudicator=ADJ,
                               poster_id="p1")
    assert result["upheld"] is False
    rep = ledger._agent(AGENT)["rep"]
    assert len(rep["completions"]) == 1


def test_completion_idempotent_on_task(ledger):
    ledger.record_completion(AGENT, "t-dup", poster_id="p1")
    ledger.record_completion(AGENT, "t-dup", poster_id="p1")
    rep = ledger._agent(AGENT)["rep"]
    assert len(rep["completions"]) == 1


def test_alpha_fixed_no_runtime_override(ledger):
    assert COLLATERAL_ALPHA == 0.8
    assert not hasattr(ledger, "set_collateral_alpha")
    # The discount can only ever REDUCE the requirement, never raise it.
    _veteranize(ledger)
    for bid_axm in (1, 7, 100, 10_000):
        bid = bid_axm * AXM
        assert ledger.stake_required_wei(bid, AGENT) <= required_stake_wei(bid)
        assert ledger.stake_required_wei(bid, "nobody") == \
            required_stake_wei(bid)


def test_discount_is_bid_independent_factor(ledger):
    # For a fixed agent the discount factor (in bps) is identical across
    # bid sizes: the factor never depends on the bid, so the Vickrey payment
    # rule and its truthfulness argument are untouched by the discount.
    _veteranize(ledger, n=25)
    factors = set()
    for bid_axm in (2, 13, 500):
        bid = bid_axm * AXM
        base = required_stake_wei(bid)
        factors.add(ledger.stake_required_wei(bid, AGENT) * 10_000 // base)
    assert factors == {6000}


def test_top_up_uses_discount(ledger):
    _veteranize(ledger)
    bounty = 10 * AXM
    bid = 20 * AXM
    discounted_bounty = required_stake_wei(bounty) * 2000 // 10_000
    discounted_bid = required_stake_wei(bid) * 2000 // 10_000
    ledger.deposit(AGENT, discounted_bid)
    ledger.lock_for_commit(AGENT, "t-r", bounty)
    assert ledger.top_up_for_reveal(AGENT, "t-r", bid) == \
        discounted_bid - discounted_bounty


def test_composes_with_sponsored_clawback(tmp_path, monkeypatch):
    # Veteran discount shrinks the lock; the senior clawback still applies
    # to the ACTUAL slashed amount.  Fronted capital can never leak into
    # poster re-auction credit, discounted or not.
    monkeypatch.setenv(ADJUDICATOR_ENV, ADJ)
    monkeypatch.setenv(ENABLE_ENV, "1")
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    reset_sponsored_ledger(path=str(tmp_path / "sponsored.json"))
    ledger = stake_ledger()
    _veteranize(ledger)
    front_sponsored_stake(AGENT, 25 * AXM, approved_by="admin")
    bounty = 100 * AXM  # base requirement 50 AXM; veteran locks 10 AXM
    locked = ledger.lock_for_commit(AGENT, "t-s", bounty)
    assert locked == 10 * AXM
    result = ledger.slash_ghost(AGENT, "t-s", poster_id="poster-x")
    assert result["slashed_wei"] == str(10 * AXM)
    # Platform front (25 AXM outstanding) is senior to the 10 AXM slash.
    assert result["clawback_wei"] == str(10 * AXM)
    assert result["poster_credit_wei"] == "0"
    assert ledger.reauction_credit("__platform__") == 10 * AXM
    assert ledger.reauction_credit("poster-x") == 0


def test_balance_reports_score_and_discount(ledger):
    _veteranize(ledger, n=25)
    bal = ledger.balance_of(AGENT)
    assert bal["reputation_score"] == "0.5000"
    assert bal["stake_discount_bps"] == str(int(0.8 * 0.5 * 10_000))
    newborn = ledger.balance_of("nobody")
    assert newborn["reputation_score"] == "0.0000"
    assert newborn["stake_discount_bps"] == "0"


def test_lock_event_carries_audit_fields(ledger):
    _veteranize(ledger, n=25)
    bounty = 20 * AXM
    base = required_stake_wei(bounty)
    ledger.deposit(AGENT, base)
    ledger.lock_for_commit(AGENT, "t-audit", bounty)
    lock_events = [e for e in ledger._data["events"] if e["kind"] == "lock"]
    assert lock_events
    evt = lock_events[-1]
    assert evt["base_stake_wei"] == str(base)
    assert evt["reputation_score"] == "0.5000"
    assert evt["amount_wei"] == str(base * 6000 // 10_000)


def test_rep_constants_match_ratified_design():
    assert REP_COMPLETIONS_FOR_FULL == 50
    assert REP_POSTERS_FOR_FULL == 3
    assert REP_TENURE_DAYS_FOR_FULL == 90
