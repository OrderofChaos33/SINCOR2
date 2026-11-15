"""Tests for CNP-8 streaming dynamic utility bidding.

Covers: stream lifecycle, anti-spam rejections, reveal-chain validation
(increment voiding, tamper, cross-auction replay), settlement handoff,
authentication, thread-safety, and — the hard constraint — three privacy
proofs showing streamed commitments leak nothing about plaintext
valuations.

Run: PYTHONPATH=src:. ~/.venvs/sincor2/bin/python -m pytest
       tests/pytest/test_streaming_bids.py -q
"""

import random
import threading

import pytest

from sincor2.onchain.bidder_client import commitment, random_salt
from sincor2.streaming_bids import (
    StreamConfig,
    StreamingBidManager,
)

CHAIN_ID = 84532
AID_A = bytes.fromhex("0a" * 32)
AID_B = bytes.fromhex("0b" * 32)


class FakeClock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def seeded_salts(n: int, seed: int = 1234):
    rng = random.Random(seed)
    return [rng.randbytes(32) for _ in range(n)]


def mgr(**kw):
    kw.setdefault("clock", FakeClock())
    kw.setdefault("config", StreamConfig(min_update_interval_seconds=0))
    return StreamingBidManager(**kw), kw["clock"]


def open_ok(m, aid=AID_A, agent="agent-1", deadline_offset=3600.0,
            min_inc=0):
    now = m._clock()
    r = m.open_stream(aid, agent, commit_deadline_ts=now + deadline_offset,
                      min_increment_wei=min_inc)
    assert r.ok, r
    return r


def stream_chain(m, aid, agent, prices, salts):
    """Submit commitments for a price chain; returns (prices, salts)."""
    for p, s in zip(prices, salts):
        c = commitment(p, s, agent, aid, CHAIN_ID)
        r = m.submit_update(aid, agent, c)
        assert r.ok, r
    return prices, salts


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def test_open_stream_ok():
    m, _ = mgr()
    r = open_ok(m)
    assert r.ok and r.reason == "opened"
    summary = m.stream_summary(AID_A)
    assert summary["agent-1"]["updates"] == 0
    assert summary["agent-1"]["open"] is True


def test_open_stream_rejects_past_deadline():
    m, clock = mgr()
    r = m.open_stream(AID_A, "agent-1", commit_deadline_ts=clock() - 1,
                      min_increment_wei=0)
    assert not r.ok and r.reason == "window_closed"


def test_open_stream_rejects_bad_auction_id():
    m, clock = mgr()
    r = m.open_stream(b"short", "agent-1", commit_deadline_ts=clock() + 60)
    assert not r.ok and r.reason == "bad_auction_id"
    r = m.open_stream(b"", "agent-1", commit_deadline_ts=clock() + 60)
    assert not r.ok and r.reason == "bad_auction_id"


def test_open_stream_duplicate_rejected():
    m, _ = mgr()
    open_ok(m)
    r = m.open_stream(AID_A, "agent-1",
                      commit_deadline_ts=m._clock() + 60)
    assert not r.ok and r.reason == "stream_exists"


def test_submit_update_accepted_and_sequenced():
    m, _ = mgr()
    open_ok(m)
    prices = [100, 150, 120]
    salts = seeded_salts(3)
    seqs = []
    for p, s in zip(prices, salts):
        r = m.submit_update(AID_A, "agent-1", commitment(p, s, "agent-1",
                                                         AID_A, CHAIN_ID))
        assert r.ok and r.reason == "accepted"
        seqs.append(r.seq)
    assert seqs == [0, 1, 2]
    pkg = m.reveal_package(AID_A, "agent-1")
    assert pkg == [commitment(p, s, "agent-1", AID_A, CHAIN_ID)
                   for p, s in zip(prices, salts)]
    assert m.latest_commitment(AID_A, "agent-1") == pkg[-1]


def test_unknown_stream_rejected():
    m, _ = mgr()
    r = m.submit_update(AID_A, "ghost", b"\x01" * 32)
    assert not r.ok and r.reason == "unknown_stream"
    assert m.reveal_package(AID_A, "ghost") == []
    assert m.latest_commitment(AID_A, "ghost") is None


# ---------------------------------------------------------------------------
# Anti-spam
# ---------------------------------------------------------------------------

def test_malformed_commitment_rejected():
    m, _ = mgr()
    open_ok(m)
    for bad in (b"", b"\x01" * 31, b"\x01" * 33, b"not-bytes-but-long-enough-!"):
        r = m.submit_update(AID_A, "agent-1", bad)
        assert not r.ok and r.reason == "malformed_commitment", bad


def test_duplicate_commitment_rejected():
    m, _ = mgr()
    open_ok(m)
    c = commitment(100, seeded_salts(1)[0], "agent-1", AID_A, CHAIN_ID)
    assert m.submit_update(AID_A, "agent-1", c).ok
    r = m.submit_update(AID_A, "agent-1", c)  # replay -> spam
    assert not r.ok and r.reason == "duplicate_commitment"


def test_submit_after_deadline_rejected():
    m, clock = mgr()
    open_ok(m, deadline_offset=100.0)
    clock.advance(101.0)
    r = m.submit_update(AID_A, "agent-1", b"\x02" * 32)
    assert not r.ok and r.reason == "window_closed"


def test_rate_limit_rejects_rapid_updates():
    cfg = StreamConfig(min_update_interval_seconds=20.0)
    clock = FakeClock()
    m = StreamingBidManager(config=cfg, clock=clock)
    open_ok(m)
    salts = seeded_salts(3, seed=99)
    c1 = commitment(100, salts[0], "agent-1", AID_A, CHAIN_ID)
    c2 = commitment(200, salts[1], "agent-1", AID_A, CHAIN_ID)
    c3 = commitment(300, salts[2], "agent-1", AID_A, CHAIN_ID)
    assert m.submit_update(AID_A, "agent-1", c1).ok
    r = m.submit_update(AID_A, "agent-1", c2)
    assert not r.ok and r.reason == "rate_limited"
    clock.advance(19.9)
    r = m.submit_update(AID_A, "agent-1", c2)
    assert not r.ok and r.reason == "rate_limited"
    clock.advance(0.2)  # 20.1 s total -> allowed
    r = m.submit_update(AID_A, "agent-1", c2)
    assert r.ok and r.seq == 1
    # A rejected rapid update must not consume the rate-limit budget twice:
    # immediate retry after the accepted one is still limited.
    r = m.submit_update(AID_A, "agent-1", c3)
    assert not r.ok and r.reason == "rate_limited"


def test_update_cap_reached():
    cfg = StreamConfig(min_update_interval_seconds=0, max_updates_per_stream=3)
    m = StreamingBidManager(config=cfg, clock=FakeClock())
    open_ok(m)
    salts = seeded_salts(4, seed=7)
    for i in range(3):
        c = commitment(100 + i, salts[i], "agent-1", AID_A, CHAIN_ID)
        assert m.submit_update(AID_A, "agent-1", c).ok
    r = m.submit_update(AID_A, "agent-1",
                        commitment(999, salts[3], "agent-1", AID_A, CHAIN_ID))
    assert not r.ok and r.reason == "update_cap_reached"


def test_close_stream_blocks_updates():
    m, _ = mgr()
    open_ok(m)
    assert m.close_stream(AID_A, "agent-1").ok
    r = m.submit_update(AID_A, "agent-1", b"\x03" * 32)
    assert not r.ok and r.reason == "stream_closed"
    # Idempotent close
    assert m.close_stream(AID_A, "agent-1").ok


def test_auction_stream_cap():
    cfg = StreamConfig(min_update_interval_seconds=0, max_streams_per_auction=2)
    m = StreamingBidManager(config=cfg, clock=FakeClock())
    now = m._clock()
    assert m.open_stream(AID_A, "a1", commit_deadline_ts=now + 60).ok
    assert m.open_stream(AID_A, "a2", commit_deadline_ts=now + 60).ok
    r = m.open_stream(AID_A, "a3", commit_deadline_ts=now + 60)
    assert not r.ok and r.reason == "auction_stream_cap"


# ---------------------------------------------------------------------------
# Reveal-chain validation
# ---------------------------------------------------------------------------

def test_reveal_chain_valid_full():
    m, _ = mgr()
    open_ok(m, min_inc=50)
    prices = [1000, 1100, 900]  # moves of 100 and 200 >= 50
    salts = seeded_salts(3)
    stream_chain(m, AID_A, "agent-1", prices, salts)
    v = m.validate_reveal_chain(AID_A, "agent-1", prices, salts, CHAIN_ID)
    assert v.ok and v.effective_seq == 2 and v.effective_price == 900
    assert v.failures == []


def test_reveal_chain_increment_violation_voids_suffix():
    m, _ = mgr()
    open_ok(m, min_inc=100)
    prices = [1000, 1100, 1150]  # last move is 50 < 100 -> voided
    salts = seeded_salts(3)
    stream_chain(m, AID_A, "agent-1", prices, salts)
    v = m.validate_reveal_chain(AID_A, "agent-1", prices, salts, CHAIN_ID)
    assert not v.ok
    assert v.effective_seq == 1 and v.effective_price == 1100
    assert any("increment_too_small" in f for f in v.failures)


def test_reveal_chain_wrong_salt_fails():
    m, _ = mgr()
    open_ok(m)
    prices = [100, 200]
    salts = seeded_salts(2)
    stream_chain(m, AID_A, "agent-1", prices, salts)
    bad_salts = [salts[0], seeded_salts(1, seed=555)[0]]
    v = m.validate_reveal_chain(AID_A, "agent-1", prices, bad_salts, CHAIN_ID)
    assert not v.ok
    assert v.effective_seq == 0 and v.effective_price == 100
    assert any("commitment_mismatch" in f for f in v.failures)


def test_reveal_chain_tampered_price_fails():
    m, _ = mgr()
    open_ok(m)
    prices = [100, 200]
    salts = seeded_salts(2)
    stream_chain(m, AID_A, "agent-1", prices, salts)
    v = m.validate_reveal_chain(AID_A, "agent-1", [100, 201], salts, CHAIN_ID)
    assert not v.ok and v.effective_seq == 0
    assert any("commitment_mismatch" in f for f in v.failures)


def test_reveal_chain_cross_auction_replay_fails():
    # Commitments computed for auction A are streamed into auction B's
    # stream.  The auction_id binding inside the hash must make validation
    # for B fail — the replay buys nothing.
    m, _ = mgr()
    open_ok(m, aid=AID_B)
    prices = [100, 200]
    salts = seeded_salts(2)
    for p, s in zip(prices, salts):
        c = commitment(p, s, "agent-1", AID_A, CHAIN_ID)  # bound to A
        assert m.submit_update(AID_B, "agent-1", c).ok  # opaque: accepted
    v = m.validate_reveal_chain(AID_B, "agent-1", prices, salts, CHAIN_ID)
    assert not v.ok and v.effective_seq == -1
    assert any("commitment_mismatch" in f for f in v.failures)


def test_reveal_length_mismatch():
    m, _ = mgr()
    open_ok(m)
    salts = seeded_salts(2)
    stream_chain(m, AID_A, "agent-1", [100, 200], salts)
    v = m.validate_reveal_chain(AID_A, "agent-1", [100], salts[:1], CHAIN_ID)
    assert not v.ok and v.effective_seq == -1
    assert any("reveal_length_mismatch" in f for f in v.failures)


def test_reveal_chain_unknown_stream():
    m, _ = mgr()
    v = m.validate_reveal_chain(AID_A, "nobody", [1], [b"\x00" * 32],
                                CHAIN_ID)
    assert not v.ok and v.failures == ["unknown_stream"]


# ---------------------------------------------------------------------------
# Settlement handoff — final committed bid is what settles
# ---------------------------------------------------------------------------

def test_effective_final_bid_feeds_settlement():
    # Two agents stream; both reveal; first-price settlement on the
    # validated FINAL bids picks the higher valuation.  The winner's
    # settlement commitment must equal their latest streamed commitment.
    m, _ = mgr()
    for agent, final in (("agent-1", 500), ("agent-2", 700)):
        open_ok(m, agent=agent)
        prices = [final - 200, final - 50, final]
        salts = seeded_salts(3, seed=hash(agent) % 10000)
        stream_chain(m, AID_A, agent, prices, salts)
        fb = m.effective_final_bid(AID_A, agent, prices, salts, CHAIN_ID)
        assert fb.ok and fb.seq == 2 and fb.price_wei == final
        assert fb.commitment == m.latest_commitment(AID_A, agent)
        assert fb.salt == salts[2]

    # First-price over the validated finals (the close_auction rule).
    finals = {}
    for agent in ("agent-1", "agent-2"):
        summary_prices = {"agent-1": 500, "agent-2": 700}[agent]
        salts = seeded_salts(3, seed=hash(agent) % 10000)
        prices = [summary_prices - 200, summary_prices - 50, summary_prices]
        fb = m.effective_final_bid(AID_A, agent, prices, salts, CHAIN_ID)
        finals[agent] = fb.price_wei
    winner = max(finals, key=finals.get)
    assert winner == "agent-2" and finals[winner] == 700


def test_effective_final_bid_voids_bad_suffix():
    m, _ = mgr()
    open_ok(m, min_inc=100)
    prices = [1000, 1100, 1105]  # grind at the end
    salts = seeded_salts(3)
    stream_chain(m, AID_A, "agent-1", prices, salts)
    fb = m.effective_final_bid(AID_A, "agent-1", prices_wei=prices,
                              salts=salts, chain_id=CHAIN_ID)
    assert fb.ok  # longest valid prefix still yields a bid
    assert fb.seq == 1 and fb.price_wei == 1100
    assert "suffix_voided" in fb.reason
    # The voided grind commitment is NOT what would settle.
    assert fb.commitment != commitment(1105, salts[2], "agent-1", AID_A,
                                       CHAIN_ID)


# ---------------------------------------------------------------------------
# Privacy proofs — streamed updates leak nothing about valuations
# ---------------------------------------------------------------------------

def _spearman(xs, ys):
    n = len(xs)
    rx = [sorted(xs).index(x) + 1 for x in xs]  # no ties in practice
    ry = [sorted(ys).index(y) + 1 for y in ys]
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def test_privacy_hash_order_uncorrelated_with_price_order():
    # If commitment ordering leaked price ordering, a sniffer could
    # front-run.  Spearman rho between price rank and hash-int rank must be
    # at the null level for a keccak PRF.
    rng = random.Random(20261002)
    n = 300
    prices = [rng.randint(1, 2**62) for _ in range(n)]
    salts = [rng.randbytes(32) for _ in range(n)]
    hashes = [int.from_bytes(commitment(p, s, "agent-1", AID_A, CHAIN_ID),
                             "big")
              for p, s in zip(prices, salts)]
    rho = _spearman(prices, hashes)
    assert abs(rho) < 0.15, f"hash order correlates with price order: {rho}"


def test_privacy_adversary_cannot_pick_higher_bidder():
    # Adversary sees only the two latest commitments and guesses the higher
    # bidder by hash magnitude.  Success must be ~coin-flip.
    rng = random.Random(777)
    wins = 0
    trials = 200
    for _ in range(trials):
        p1, p2 = rng.randint(1, 2**62), rng.randint(1, 2**62)
        if p1 == p2:
            continue
        h1 = int.from_bytes(commitment(p1, rng.randbytes(32), "a1", AID_A,
                                       CHAIN_ID), "big")
        h2 = int.from_bytes(commitment(p2, rng.randbytes(32), "a2", AID_A,
                                       CHAIN_ID), "big")
        guess_higher_is_1 = h1 > h2
        actual_higher_is_1 = p1 > p2
        wins += guess_higher_is_1 == actual_higher_is_1
    rate = wins / trials
    assert 0.40 <= rate <= 0.60, f"adversary success rate {rate} != ~0.5"


def _walk_values(obj, out):
    if isinstance(obj, (bytes, bytearray)):
        out.append(bytes(obj))
    elif isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, int):
        out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _walk_values(k, out)
            _walk_values(v, out)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for v in obj:
            _walk_values(v, out)
    elif hasattr(obj, "__dict__"):
        _walk_values(vars(obj), out)


def test_privacy_store_holds_no_plaintext():
    # The stream store must never persist a valuation in any form:
    # decimal string, 32-byte big-endian encoding, or raw int.
    rng = random.Random(31337)
    m, _ = mgr()
    open_ok(m)
    prices = [rng.randint(2**40, 2**62) for _ in range(5)]
    salts = [rng.randbytes(32) for _ in range(5)]
    stream_chain(m, AID_A, "agent-1", prices, salts)

    vals = []
    _walk_values(m._streams, vals)
    for p in prices:
        assert str(p) not in [v for v in vals if isinstance(v, str)], p
        assert p.to_bytes(32, "big") not in [
            v for v in vals if isinstance(v, bytes)], p
        assert p not in [v for v in vals if isinstance(v, int)], p
    # And the public read paths expose only commitments/metadata.
    summary = m.stream_summary(AID_A)
    blob = repr(summary) + repr(m.reveal_package(AID_A, "agent-1"))
    for p in prices:
        assert str(p) not in blob


# ---------------------------------------------------------------------------
# Auth + thread-safety
# ---------------------------------------------------------------------------

def test_auth_verifier_enforced():
    seen = []

    def verifier(agent_id, auction_hex, comm):
        seen.append((agent_id, auction_hex))
        return agent_id == "agent-1"

    m = StreamingBidManager(clock=FakeClock(),
                            config=StreamConfig(
                                min_update_interval_seconds=0),
                            auth_verifier=verifier)
    open_ok(m)  # agent-1 stream
    open_ok(m, agent="agent-2")
    c = commitment(100, seeded_salts(1)[0], "agent-1", AID_A, CHAIN_ID)
    assert m.submit_update(AID_A, "agent-1", c).ok
    c2 = commitment(100, seeded_salts(1, seed=5)[0], "agent-2", AID_A,
                    CHAIN_ID)
    r = m.submit_update(AID_A, "agent-2", c2)
    assert not r.ok and r.reason == "auth_failed"
    assert seen and seen[0][0] == "agent-1"


def test_auth_verifier_crash_rejects():
    def boom(agent_id, auction_hex, comm):
        raise RuntimeError("kaboom")

    m = StreamingBidManager(clock=FakeClock(),
                            config=StreamConfig(
                                min_update_interval_seconds=0),
                            auth_verifier=boom)
    open_ok(m)
    r = m.submit_update(AID_A, "agent-1", b"\x04" * 32)
    assert not r.ok and r.reason == "auth_failed"


def test_production_requires_auth_verifier(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FLASK_ENV", "production")
    with pytest.raises(ValueError):
        StreamingBidManager(clock=FakeClock())
    # Explicit verifier is fine in production.
    StreamingBidManager(clock=FakeClock(),
                        auth_verifier=lambda a, h, c: True)


def test_concurrent_submits_threadsafe():
    cfg = StreamConfig(min_update_interval_seconds=0,
                       max_updates_per_stream=200)
    m = StreamingBidManager(config=cfg, clock=FakeClock())
    open_ok(m)
    salts = seeded_salts(80, seed=4242)
    results, lock = [], threading.Lock()

    def worker(base):
        for i in range(base, base + 10):
            c = commitment(1000 + i, salts[i], "agent-1", AID_A, CHAIN_ID)
            r = m.submit_update(AID_A, "agent-1", c)
            with lock:
                results.append(r)

    threads = [threading.Thread(target=worker, args=(k * 10,))
               for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 80
    assert all(r.ok for r in results)
    seqs = sorted(r.seq for r in results)
    assert seqs == list(range(80))
    assert len(m.reveal_package(AID_A, "agent-1")) == 80


def test_stream_config_validation():
    with pytest.raises(ValueError):
        StreamConfig(max_updates_per_stream=0).validate()
    with pytest.raises(ValueError):
        StreamConfig(min_update_interval_seconds=-1).validate()
    with pytest.raises(ValueError):
        StreamConfig(max_streams_per_auction=0).validate()
