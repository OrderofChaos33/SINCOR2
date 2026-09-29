"""Audit-prep invariant + adversarial fuzz tests for P03 (intent_dark_pool).

Property tests an auditor would demand:

- commit_binding: commitment is deterministic; tampering with ANY single
  field changes the commitment (reveal must match commitment).
- record_secrecy: the on-chain record carries only the commitment —
  amount_in/min_out never appear as record values (fuzzed).
- nonce_sequence: per-user nonces advance exactly; reuse/gap/skip rejected.
- match_soundness: every match from match_pair/find_matches is price-
  feasible for both sides; verify_batch accepts honest batches and
  rejects any tampered amount (fuzzed).
- settlement_conservation: per-asset gross == net paid + fees, exactly;
  fee is exactly 8 bps per leg (fuzzed multi-match batches).
- replay_safety: duplicate intent_id rejected; duplicate batch is a safe
  no-op; settling an already-settled intent reverts; double claim pays 0.
- asset_gate: any non-AXM/USDC leg reverts with ZERO state change.
- split_solver_invariants: every leg meets its min_out; leg amounts sum
  to the intent; below-threshold never splits; split only when it beats
  the best single venue.
- adversarial: zero/dust/max-uint256 amounts, empty intents, expired
  intents, unknown ids — never crash the matcher/settler.

Deterministic: seeded RNG, no hypothesis dependency. N/N must pass.
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi.intent_dark_pool import (
    ADMIN_ROLE,
    GUARDIAN_ROLE,
    MATCHER_ROLE,
    TREASURY,
    BatchVerificationError,
    DarkFill,
    DarkPool,
    DarkPoolError,
    Intent,
    IntentReplayError,
    Match,
    Matcher,
    MinOutViolationError,
    SettlementAssetError,
    SettlementEngine,
    SplitSolver,
    UnauthorizedError,
    VenueQuote,
    _feasible,
    match_pair,
)

RNG = random.Random(0xDAA903)
USERS = ["alice", "bob", "carol", "dave", "erin"]
ASSETS = ["AXM", "USDC"]
WEIRD_ASSETS = ["WETH", "axm", "", "AXM2", "USDC "]


def fuzz_intent(iid: str, user: str, nonce: int, assets=(("AXM", "USDC"),)) -> Intent:
    ain, aout = RNG.choice(assets)
    amt = RNG.choice([0, 1, 100, 10**6, 10**12, 2**256 - 1,
                      RNG.randint(1, 10**18)])
    # min_out: sometimes satisfiable, sometimes not, sometimes degenerate
    min_out = RNG.choice([0, 1, amt, amt * 99 // 100, amt * 2, 2**256 - 1])
    return Intent(iid, user, ain, aout, amt, min_out,
                  time.time() + 3600, nonce, salt=f"salt-{RNG.randint(0, 999)}")


def good_pair(i: int, amt: int | None = None):
    """A deliberately price-crossing AXM<->USDC pair."""
    a = amt if amt is not None else RNG.randint(10**3, 10**12)
    return (
        Intent(f"pa{i}", "alice", "AXM", "USDC", a, a * 99 // 100,
               time.time() + 3600, 2 * i),
        Intent(f"pb{i}", "bob", "USDC", "AXM", a, a * 99 // 100,
               time.time() + 3600, 2 * i),
    )


# -- commit / reveal binding ---------------------------------------------------
def test_commitment_deterministic_and_binding():
    """PROPERTY: commitment is a pure function of all fields; flipping any
    single field changes it (150 fuzzed intents x 9 fields)."""
    fields = ["intent_id", "user", "asset_in", "asset_out", "amount_in",
              "min_out", "expiry_ts", "nonce", "salt"]
    for _ in range(150):
        it = fuzz_intent(f"id{RNG.randint(0, 10**9)}",
                         RNG.choice(USERS), RNG.randint(0, 100))
        c0 = it.commitment
        assert it.commitment == c0  # deterministic
        d = dict(it.__dict__)
        for f in fields:
            d2 = dict(d)
            d2[f] = "TAMPERED" if isinstance(d2[f], str) else d2[f] + 1
            it2 = Intent(**d2)
            assert it2.commitment != c0, f"field {f} not bound by commitment"


def test_record_carries_commitment_only_fuzz():
    """PROPERTY: the on-chain record exposes exactly the commitment —
    amount_in/min_out never appear as record values (100 fuzzed)."""
    pool = DarkPool()
    for i in range(100):
        it = fuzz_intent(f"r{i}", "alice", i)
        rec = pool.submit(it)
        d = rec.to_dict()
        # The key set IS the secrecy property: no amount_in/min_out keys.
        # (A value-membership check would be unsound: small int nonces can
        # collide with dust amounts; the commitment is a hash, not plaintext.)
        assert set(d) == {"intent_id", "user", "commitment", "expiry_ts",
                          "nonce", "status"}
        assert rec.commitment == it.commitment


# -- nonces --------------------------------------------------------------------
def test_nonce_sequence_fuzz():
    """PROPERTY: nonces advance exactly 0..N-1 per user; any reuse, gap, or
    skip is rejected; users are independent."""
    pool = DarkPool()
    nonces = {u: 0 for u in USERS}
    for i in range(120):
        u = RNG.choice(USERS)
        op = RNG.random()
        if op < 0.7:
            it = fuzz_intent(f"n{i}", u, nonces[u])
            pool.submit(it)
            nonces[u] += 1
            assert pool.nonce_of(u) == nonces[u]
        elif nonces[u] > 0:
            with pytest.raises(IntentReplayError):  # nonce reuse
                pool.submit(fuzz_intent(f"n{i}x", u, nonces[u] - 1))
        else:
            with pytest.raises(IntentReplayError):  # nonce gap
                pool.submit(fuzz_intent(f"n{i}y", u, nonces[u] + 2))


# -- matching soundness ----------------------------------------------------------
def test_match_soundness_fuzz():
    """PROPERTY: every match produced is price-feasible for BOTH sides,
    and verify_batch accepts exactly the honest output (200 fuzzed sets)."""
    matcher = Matcher()
    for _ in range(200):
        intents = [fuzz_intent(f"m{i}", RNG.choice(USERS), i)
                   for i in range(RNG.randint(0, 6))]
        matches = matcher.find_matches(intents)
        by_id = {it.intent_id: it for it in intents}
        seen = set()
        for m in matches:
            assert m.buy_intent_id not in seen and m.sell_intent_id not in seen
            seen.add(m.buy_intent_id)
            seen.add(m.sell_intent_id)
            a, b = by_id[m.buy_intent_id], by_id[m.sell_intent_id]
            assert _feasible(a, b, m.amount_in_buy, m.amount_in_sell)
            assert m.amount_in_buy > 0 and m.amount_in_sell > 0
        batch = matcher.build_batch(f"b{RNG.randint(0, 10**9)}", matches)
        assert matcher.verify_batch(batch, by_id) is True
        # tamper with one amount -> rejected
        if matches:
            bad = [Match(m.buy_intent_id, m.sell_intent_id,
                         m.amount_in_buy + 1, m.amount_in_sell)
                   for m in matches]
            bad_batch = matcher.build_batch("bad", bad)
            assert matcher.verify_batch(bad_batch, by_id) is False


def test_match_pair_degenerate_no_crash():
    """Adversarial: zero amounts / zero min_out / max-uint never crash
    match_pair and never produce a zero-quantity match."""
    cases = [
        ("a", "alice", "AXM", "USDC", 0, 0, "b", "bob", "USDC", "AXM", 0, 0),
        ("a", "alice", "AXM", "USDC", 2**256 - 1, 1, "b", "bob", "USDC", "AXM",
         2**256 - 1, 1),
        ("a", "alice", "AXM", "USDC", 100, 0, "b", "bob", "USDC", "AXM", 100, 0),
    ]
    for (i1, u1, ai1, ao1, am1, mo1, i2, u2, ai2, ao2, am2, mo2) in cases:
        a = Intent(i1, u1, ai1, ao1, am1, mo1, time.time() + 3600, 0)
        b = Intent(i2, u2, ai2, ao2, am2, mo2, time.time() + 3600, 0)
        m = match_pair(a, b)
        if m is not None:
            assert m.amount_in_buy > 0 and m.amount_in_sell > 0
            assert _feasible(a, b, m.amount_in_buy, m.amount_in_sell)


# -- settlement ------------------------------------------------------------------
def _settle_n_pairs(n: int, amt: int | None = None):
    pool, matcher, engine = DarkPool(), Matcher(), SettlementEngine()
    intents = {}
    nonces = {"alice": 0, "bob": 0}
    for i in range(n):
        a_amt = amt if amt is not None else RNG.randint(10**3, 10**12)
        a = Intent(f"pa{i}", "alice", "AXM", "USDC", a_amt, a_amt * 99 // 100,
                   time.time() + 3600, nonces["alice"])
        b = Intent(f"pb{i}", "bob", "USDC", "AXM", a_amt, a_amt * 99 // 100,
                   time.time() + 3600, nonces["bob"])
        nonces["alice"] += 1
        nonces["bob"] += 1
        pool.submit(a)
        pool.submit(b)
        intents[a.intent_id] = a
        intents[b.intent_id] = b
    matches = matcher.find_matches(list(intents.values()))
    batch = matcher.build_batch("batch-1", matches)
    matcher.submit_batch(batch, intents)
    return pool, matcher, engine, engine.settle(batch, intents, pool), intents, batch


def test_settlement_conservation_and_fee_fuzz():
    """PROPERTY: per asset, gross in == net out + fees EXACTLY, and every
    leg's fee is exactly floor(gross * 8 / 10000) — 1..5 pair batches."""
    for n in (1, 2, 3, 5):
        pool, matcher, engine, st, intents, batch = _settle_n_pairs(n)
        gross, paid, fees = {}, {}, {}
        for m in batch.matches:
            buy, sell = intents[m.buy_intent_id], intents[m.sell_intent_id]
            for asset, g in ((buy.asset_in, m.amount_in_buy),
                             (sell.asset_in, m.amount_in_sell)):
                gross[asset] = gross.get(asset, 0) + g
                fees[asset] = fees.get(asset, 0) + g * 8 // 10_000
        for (user, asset), v in st.net.items():
            paid[asset] = paid.get(asset, 0) + v
        for asset in gross:
            assert paid.get(asset, 0) + st.fees.get(asset, 0) == gross[asset]
            assert st.fees.get(asset, 0) == fees[asset]
        assert st.treasury == TREASURY


def test_settlement_max_uint_conserves():
    """Adversarial: 2**256-1 amounts settle with exact conservation."""
    pool, matcher, engine, st, intents, batch = _settle_n_pairs(1, 2**256 - 1)
    assert st.net[("bob", "AXM")] == (2**256 - 1) - (2**256 - 1) * 8 // 10_000
    assert engine.claim("bob", "AXM") == st.net[("bob", "AXM")]
    assert engine.claim("bob", "AXM") == 0


def test_asset_gate_no_state_change_fuzz():
    """PROPERTY: any non-AXM/USDC leg reverts settlement with ZERO state
    change — records stay open, no balances credited (fuzzed assets)."""
    for weird in WEIRD_ASSETS:
        pool, matcher, engine = DarkPool(), Matcher(), SettlementEngine()
        a = Intent("wa", "alice", weird, "USDC", 1_000, 900,
                   time.time() + 3600, 0)
        b = Intent("wb", "bob", "USDC", weird, 1_000, 900,
                   time.time() + 3600, 0)
        pool.submit(a)
        pool.submit(b)
        intents = {"wa": a, "wb": b}
        batch = matcher.build_batch("wb1", matcher.find_matches([a, b]))
        matcher.submit_batch(batch, intents)
        with pytest.raises(SettlementAssetError):
            engine.settle(batch, intents, pool)
        assert pool.record_of("wa").status == "open"
        assert pool.record_of("wb").status == "open"
        assert engine.balance_of("alice", "USDC") == 0
        assert engine.balance_of("bob", weird) == 0


def test_replay_and_double_ops():
    """Replay safety: duplicate intent_id rejected; duplicate batch is a
    no-op; re-settling settled intents reverts; double settle of a batch
    reverts; unknown claim pays 0."""
    pool, matcher, engine = DarkPool(), Matcher(), SettlementEngine()
    a, b = good_pair(0)
    pool.submit(a)
    with pytest.raises(DarkPoolError):
        pool.submit(a)  # duplicate intent_id
    pool.submit(b)
    intents = {a.intent_id: a, b.intent_id: b}
    matches = matcher.find_matches([a, b])
    batch = matcher.build_batch("b1", matches)
    assert matcher.submit_batch(batch, intents)["status"] == "accepted"
    assert matcher.submit_batch(batch, intents)["status"] == "duplicate-noop"
    engine.settle(batch, intents, pool)
    with pytest.raises(DarkPoolError):
        engine.settle(batch, intents, pool)  # double settle
    # new batch over settled intents -> replay reverts at mark_settling
    batch2 = matcher.build_batch("b2", matches)
    matcher.submit_batch(batch2, intents)
    with pytest.raises(IntentReplayError):
        engine.settle(batch2, intents, pool)
    assert engine.claim("nobody", "AXM") == 0


def test_lifecycle_status_machine_fuzz():
    """PROPERTY: random submit/cancel/expire/settle sequences keep the
    status machine consistent — terminal states never regress."""
    pool = DarkPool()
    ids = []
    for i in range(80):
        u = RNG.choice(USERS)
        it = fuzz_intent(f"s{i}", u, pool.nonce_of(u))
        try:
            pool.submit(it)
        except DarkPoolError:
            continue  # already expired at submit time (ttl edge)
        ids.append(it.intent_id)
        op = RNG.random()
        if op < 0.3:
            pool.cancel(it.intent_id, u)
            assert pool.record_of(it.intent_id).status == "cancelled"
            with pytest.raises(DarkPoolError):
                pool.cancel(it.intent_id, u)  # terminal: no re-cancel
        elif op < 0.4:
            pool.sweep_expiry(now=time.time() + 7200)
    for iid in ids:
        st = pool.record_of(iid).status
        assert st in ("open", "cancelled", "expired", "settling", "settled")


# -- split solver ------------------------------------------------------------------
def _venues(n: int = 3):
    return [VenueQuote(f"v{i}", RNG.randint(50, 110), 100) for i in range(n)]


def test_split_solver_invariants_fuzz():
    """PROPERTY: every leg meets its min_out; leg inputs sum to the intent;
    total == sum of legs; below-threshold never splits (150 fuzzed)."""
    solver = SplitSolver(split_threshold=5_000)
    for _ in range(150):
        amt = RNG.choice([1, 100, 4_999, 5_000, 10_000, RNG.randint(1, 10**7)])
        min_out = RNG.choice([0, 1, amt // 2, amt * 99 // 100])
        intent = Intent("sp", "alice", "AXM", "USDC", amt, min_out,
                        time.time() + 3600, 0)
        venues = _venues()
        dark = DarkFill(RNG.randint(0, amt), RNG.randint(50, 110), 100)
        best_single = max(amt * v.rate_num // v.rate_den for v in venues)
        if best_single < min_out:
            with pytest.raises(MinOutViolationError):
                solver.route(intent, dark, venues)
            continue
        plan = solver.route(intent, dark, venues)
        assert plan.total_expected_out == sum(l.expected_out for l in plan.legs)
        assert sum(l.amount_in for l in plan.legs) == amt
        for leg in plan.legs:
            assert leg.expected_out >= leg.min_out_leg
            assert leg.amount_in > 0
        if amt < 5_000:
            assert plan.split is False and len(plan.legs) == 1
        if plan.split:
            assert plan.total_expected_out > best_single


def test_split_solver_no_venues_reverts():
    """No allowlisted venues -> DarkPoolError (fail-closed)."""
    solver = SplitSolver()
    intent = Intent("sp", "alice", "AXM", "USDC", 10_000, 9_000,
                    time.time() + 3600, 0)
    with pytest.raises(DarkPoolError):
        solver.route(intent, DarkFill(1_000, 99, 100), [])


# -- access control --------------------------------------------------------------
def test_pause_guard_interleaved_fuzz():
    """Adversarial: pause/unpause interleaved with submits and hostile
    cancels — the guard holds on every operation."""
    pool = DarkPool()
    roles = {MATCHER_ROLE: {"m"}, GUARDIAN_ROLE: {"g"}, ADMIN_ROLE: {"a"}}
    pool.submit(Intent("pz", "alice", "AXM", "USDC", 100, 90,
                       time.time() + 3600, 0))
    for _ in range(50):
        op = RNG.choice(["pause", "unpause", "submit", "cancel"])
        if op == "pause":
            pool.pause(roles, "g")
            assert pool.paused
        elif op == "unpause":
            pool.unpause(roles, "g")
            assert not pool.paused
        elif op == "submit":
            it = Intent(f"px{RNG.randint(0, 10**9)}", "bob", "AXM", "USDC",
                        100, 90, time.time() + 3600, pool.nonce_of("bob"))
            if pool.paused:
                with pytest.raises(DarkPoolError):
                    pool.submit(it)
            else:
                pool.submit(it)
        else:
            with pytest.raises((DarkPoolError, KeyError, UnauthorizedError)):
                pool.cancel("pz", "mallory")  # non-owner cancel always fails
    with pytest.raises(UnauthorizedError):
        pool.pause(roles, "stranger")
