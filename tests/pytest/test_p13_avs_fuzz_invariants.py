"""Audit-prep invariant + adversarial fuzz tests for P13 (avs_tranching).

Property tests an auditor would demand of the AVS tranching waterfall
reference model:

- oracle_fuzz: slash probability and severity outside 0..10000 bps are
  refused at publish; reads for unknown AVSs raise; reads older than the
  staleness limit raise — new allocations are fail-closed on stale data.
- allocate_fuzz: allocations below the $300 floor, junior_pct outside
  (0, 30%], and under-covered tranches are all rejected; every accepted
  allocation has senior + junior == total exactly, junior == int(total *
  junior_pct), and modeled coverage >= the 1.10x gate.
- slash_waterfall_fuzz: junior absorbs losses first; senior is impaired
  only after junior is fully wiped; loss is clamped to [0, total];
  survivors + losses reconcile exactly to the allocation total.
- yield_waterfall_fuzz: senior is paid its pro-rated target first (capped
  at available yield); junior receives exactly the residual;
  senior_paid + junior_paid == total_yield; the 18 bps treasury fee is
  integer-exact and routes to the canonical Treasury; negative yield is
  refused.
- live_fuzz: live restaking always raises, fail-closed.

Money is integer cents. Deterministic: seeded RNG. N/N must pass.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi import avs_tranching as avs  # noqa: E402
from src.sincor2.defi.catalog import TREASURY  # noqa: E402
from src.sincor2.defi.avs_tranching import (  # noqa: E402
    AVSRisk,
    LiveRestakeBlocker,
    SlashOracle,
    Trancher,
    apply_slash,
    distribute_yield,
)

RNG = random.Random(0xA01313)


def fuzz_oracle(now: int, n: int = 3, fresh: bool = True) -> SlashOracle:
    oracle = SlashOracle()
    for i in range(n):
        age = RNG.randint(0, avs.ORACLE_STALENESS_LIMIT_S) if fresh \
            else RNG.randint(avs.ORACLE_STALENESS_LIMIT_S + 1, 10**6)
        oracle.publish(AVSRisk(
            avs_id=f"avs{i}",
            slash_prob_bps=RNG.randint(0, 10_000),
            expected_slash_severity_bps=RNG.randint(0, 10_000),
            as_of=now - age,
        ))
    return oracle


def test_oracle_publish_and_freshness_gates():
    oracle = SlashOracle()
    for bad in (-1, 10_001):
        for kwargs in ({"slash_prob_bps": bad, "expected_slash_severity_bps": 0},
                       {"slash_prob_bps": 0, "expected_slash_severity_bps": bad}):
            try:
                oracle.publish(AVSRisk("a", as_of=1000, **kwargs))
                raise AssertionError("out-of-range risk accepted")
            except ValueError:
                pass
    now = 10**6
    try:
        oracle.read("ghost", now)
        raise AssertionError("unknown AVS read accepted")
    except avs.StaleOracleError:
        pass
    oracle.publish(AVSRisk("a", 100, 1000, now))
    assert oracle.read("a", now).slash_prob_bps == 100
    # exactly at the limit is fresh; one second past is stale
    assert oracle.read("a", now + avs.ORACLE_STALENESS_LIMIT_S).slash_prob_bps == 100
    try:
        oracle.read("a", now + avs.ORACLE_STALENESS_LIMIT_S + 1)
        raise AssertionError("stale oracle read accepted")
    except avs.StaleOracleError:
        pass


def test_allocate_gates_and_conservation():
    now = 10**6
    for _ in range(300):
        oracle = fuzz_oracle(now, fresh=RNG.choice([True, True, False]))
        trancher = Trancher(oracle)
        gid = RNG.choice(["avs0", "avs1", "avs2", "ghost"])
        total = RNG.choice([RNG.randint(1, 10**9),
                            avs.MIN_CAPITAL_CENTS,
                            avs.MIN_CAPITAL_CENTS - 1, 0])
        junior_pct = RNG.choice([RNG.uniform(0.01, 0.30), 0.0, -0.1,
                                 0.31, 1.0])
        try:
            alloc = trancher.allocate(gid, total, junior_pct, now)
        except (ValueError, avs.JuniorCapError, avs.CoverBreachError,
                avs.StaleOracleError):
            continue
        # every accepted allocation: exact conservation + cap + cover gate
        assert alloc.senior_cents + alloc.junior_cents == alloc.total_cents
        assert alloc.junior_cents == int(total * junior_pct)
        assert 0 < junior_pct <= avs.JUNIOR_CAP_PCT
        assert total >= avs.MIN_CAPITAL_CENTS
        assert alloc.coverage_x100 * 100 >= avs.REQUIRED_COVER_BPS - 100
    # explicit rejections
    oracle = fuzz_oracle(now)
    trancher = Trancher(oracle)
    for bad_total in (0, avs.MIN_CAPITAL_CENTS - 1):
        try:
            trancher.allocate("avs0", bad_total, 0.2, now)
            raise AssertionError("sub-floor allocation accepted")
        except ValueError:
            pass
    for bad_junior in (0.0, -0.05, 0.3000001, 0.9):
        try:
            trancher.allocate("avs0", avs.MIN_CAPITAL_CENTS, bad_junior, now)
            raise AssertionError(f"junior {bad_junior} accepted")
        except avs.JuniorCapError:
            pass
    # stale oracle blocks everything
    stale = fuzz_oracle(now, fresh=False)
    try:
        Trancher(stale).allocate("avs0", avs.MIN_CAPITAL_CENTS, 0.2, now)
        raise AssertionError("stale-oracle allocation accepted")
    except avs.StaleOracleError:
        pass
    # very risky AVS fails the cover gate even at max junior
    risky = SlashOracle()
    risky.publish(AVSRisk("risky", 10_000, 10_000, now))  # 100% * 100% loss
    try:
        Trancher(risky).allocate("risky", avs.MIN_CAPITAL_CENTS, 0.30, now)
        raise AssertionError("under-covered allocation accepted")
    except avs.CoverBreachError:
        pass


def test_slash_waterfall_strict():
    now = 10**6
    for _ in range(300):
        oracle = fuzz_oracle(now)
        trancher = Trancher(oracle)
        try:
            alloc = trancher.allocate(
                RNG.choice(["avs0", "avs1", "avs2"]),
                RNG.randint(avs.MIN_CAPITAL_CENTS, 10**8),
                RNG.uniform(0.05, 0.30), now)
        except (avs.CoverBreachError, avs.StaleOracleError):
            continue
        loss = RNG.choice([RNG.randint(0, 2 * alloc.total_cents), 0,
                           alloc.total_cents])
        out = apply_slash(alloc, loss)
        clamped = min(max(loss, 0), alloc.total_cents)
        assert out.loss_cents == clamped
        # junior absorbs first, senior only after junior is wiped
        assert out.junior_loss_cents == min(clamped, alloc.junior_cents)
        assert out.senior_loss_cents == clamped - out.junior_loss_cents
        if clamped <= alloc.junior_cents:
            assert out.senior_loss_cents == 0
        # exact reconciliation
        assert (out.junior_surviving_cents + out.senior_surviving_cents
                == alloc.total_cents - clamped)
        assert out.junior_surviving_cents >= 0
        assert out.senior_surviving_cents >= 0


def test_yield_waterfall_ordering():
    now = 10**6
    for _ in range(300):
        oracle = fuzz_oracle(now)
        trancher = Trancher(oracle)
        try:
            alloc = trancher.allocate(
                RNG.choice(["avs0", "avs1", "avs2"]),
                RNG.randint(avs.MIN_CAPITAL_CENTS, 10**8),
                RNG.uniform(0.05, 0.30), now)
        except (avs.CoverBreachError, avs.StaleOracleError):
            continue
        total_yield = RNG.choice([RNG.randint(0, 10**8), 0, 1])
        elapsed = RNG.randint(0, 2 * avs.SECONDS_PER_YEAR)
        dist = distribute_yield(alloc, total_yield, elapsed)
        target = (alloc.senior_cents * avs.SENIOR_TARGET_APR * elapsed
                  // avs.SECONDS_PER_YEAR)
        # senior paid first, capped at target and at available yield
        assert dist.senior_paid_cents == min(target, total_yield)
        # junior receives exactly the residual
        assert dist.junior_paid_cents == total_yield - dist.senior_paid_cents
        assert dist.senior_paid_cents + dist.junior_paid_cents == total_yield
        # 18 bps treasury fee, integer-exact, to the canonical treasury
        assert dist.fee_cents == total_yield * avs.FEE_BPS // 10_000
        assert dist.fee_to == TREASURY
    oracle = SlashOracle()
    oracle.publish(AVSRisk("safe", 0, 0, now))
    alloc = Trancher(oracle).allocate("safe", avs.MIN_CAPITAL_CENTS, 0.2, now)
    try:
        distribute_yield(alloc, -1, 100)
        raise AssertionError("negative yield accepted")
    except ValueError:
        pass


def test_live_restake_blocked():
    now = 10**6
    # zero-risk oracle so the allocation itself is guaranteed to pass gates
    oracle = SlashOracle()
    oracle.publish(AVSRisk("safe", 0, 0, now))
    alloc = Trancher(oracle).allocate("safe", avs.MIN_CAPITAL_CENTS, 0.2, now)
    blocker = LiveRestakeBlocker()
    for _ in range(20):
        try:
            blocker.restake_live(alloc)
            raise AssertionError("live restake escaped")
        except avs.LiveBlockedError:
            pass
