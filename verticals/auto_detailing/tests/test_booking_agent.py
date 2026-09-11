"""Quote matrix edge cases and Calendly URL construction."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from verticals.auto_detailing.booking_agent import (
    DetailingBookingAgent,
    build_calendly_url,
    quote_matrix,
)
from verticals.auto_detailing.protocols import PACKAGES, VEHICLE_SIZE_MULTIPLIER, deposit_for
from verticals.auto_detailing.schemas import Vehicle


def test_zero_deposit_on_express_wash():
    assert deposit_for("express_wash") == 0.0
    agent = DetailingBookingAgent()
    out = agent.quote(
        {"package_id": "express_wash", "vehicle": {"body_style": "sedan"}}
    )
    assert out["deposit"] == 0
    assert out["deposit_rate"] == 0.0
    assert out["total"] == out["balance_due_at_bay"]


def test_unknown_size_prices_as_sedan():
    agent = DetailingBookingAgent()
    out = agent.quote(
        {
            "package_id": "full_detail",
            "vehicle": {"size": "spaceship", "make": "Honda"},
        }
    )
    assert out["vehicle_size"] == "sedan"
    assert out["size_assumed"] is True
    assert out["size_requested"] == "spaceship"
    assert out["size_multiplier"] == VEHICLE_SIZE_MULTIPLIER["sedan"]
    sedan = agent.quote({"package_id": "full_detail", "vehicle": {"size": "sedan"}})
    assert out["total"] == sedan["total"]


def test_quote_matrix_coating_deposit():
    rows = {row["package_id"]: row for row in quote_matrix()}
    ceramic_suv = rows["ceramic"]["cells"]["suv"]
    assert ceramic_suv["deposit"] == round(ceramic_suv["total"] * 0.5, 2)
    wash = rows["express_wash"]["cells"]["truck"]
    assert wash["deposit"] == 0
    assert set(rows["ppf"]["cells"]) == set(VEHICLE_SIZE_MULTIPLIER)


def test_calendly_handoff_url_construction():
    agent = DetailingBookingAgent()
    out = agent.calendly_handoff(
        {
            "package_id": "interior",
            "name": "Marcus Hale",
            "email": "marcus@example.com",
            "vehicle": {
                "year": 2019,
                "make": "Toyota",
                "model": "Tundra",
                "body_style": "truck",
            },
            "calendly_handle": "northline-detail",
        }
    )
    url = out["calendly_url"]
    parsed = urlparse(url)
    assert parsed.netloc == "calendly.com"
    assert parsed.path == "/northline-detail/interior-revival"
    qs = parse_qs(parsed.query)
    assert qs["name"] == ["Marcus Hale"]
    assert qs["email"] == ["marcus@example.com"]
    assert "2019 Toyota Tundra" in qs["a1"][0]
    assert qs["utm_source"] == ["chroma"]
    assert out["quote"]["vehicle_size"] == "truck"
    assert [r["offset_hours"] for r in out["reminders"]] == [24, 2]


def test_build_calendly_url_helper():
    url = build_calendly_url(
        handle="shop-x",
        event="ceramic-coating",
        name="Sam",
        email="sam@x.test",
        vehicle=Vehicle(year=2022, make="BMW", model="X5"),
        package_label="Ceramic Coating",
        package_id="ceramic",
    )
    assert url.startswith("https://calendly.com/shop-x/ceramic-coating?")
    assert "name=Sam" in url
    assert "utm_campaign=ceramic" in url


def test_unknown_package_falls_back_to_full_detail():
    agent = DetailingBookingAgent()
    out = agent.quote({"package_id": "not-a-pkg", "vehicle": {"size": "sedan"}})
    assert out["package_id"] == "full_detail"
    assert out["base_price"] == PACKAGES["full_detail"]["price"]
