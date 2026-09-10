from __future__ import annotations

from verticals.auto_detailing.seed import ensure_demo, seed


def test_seed_from_empty(store):
    result = seed(store=store, reset=True)
    assert result["leads"] == 10
    assert result["quotes"] >= 3
    assert result["bookings"] >= 2
    assert result["pending_sends"] >= 3
    again = ensure_demo(store)
    assert again["leads"] == 10
