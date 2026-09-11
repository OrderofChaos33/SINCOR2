from __future__ import annotations

from verticals.auto_detailing.config import DEFAULT_SHOP
from verticals.auto_detailing.seed import ensure_demo, seed


def test_seed_from_empty(store):
    result = seed(store=store, reset=True)
    assert result["leads"] == 10
    assert result["quotes"] >= 3
    assert result["bookings"] >= 2
    assert result["pending_sends"] >= 3
    assert result["books"] >= 3
    money = store.books_totals()
    assert money["money_in"] > money["money_out"]
    settings = store.get_settings()
    assert settings["shop_name"] == "Clinton Auto Detailing"
    assert settings["city"] == "Clinton"
    assert settings["phone"] == DEFAULT_SHOP["phone"]
    assert "clintondetail.com" in settings["url"]
    assert "squareup.com" in (settings.get("booking_url") or "")
    again = ensure_demo(store)
    assert again["leads"] == 10
