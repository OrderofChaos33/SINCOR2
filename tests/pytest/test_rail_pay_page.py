"""Production pay-page wiring: P0-2 (transfer recipient) + P0-3 (mounting,
402 handling, checkout payload mapping).

The human payment pages (GET /rail/pay/<resource_id>, GET /rail/onramp)
were registered only in the legacy sincor2.app module, so the production
app — sincor2.mvp_app, the module railway_start.py loads — never served
them. The page JS also (a) threw on /rail/checkout's intentional HTTP
402 challenge response, (b) sent the raw x402 resource id as pack_sku
although the server keys packs by bare SKU (PACK_SKUS.get(raw)), so every
pack checkout 400'd, and (c) encoded the ERC-20 transfer with
payment.asset — the USDC contract itself — as the recipient, which
would have locked buyer funds in the token contract.

These tests drive the REAL production app object (sincor2.mvp_app.app)
and statically pin the rail_pay.html JS contract. No network, no keys,
nothing broadcast: checkout only mints challenges against hermetic
stores, exactly like the rail test suites.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import sincor2.x402_payments as x402
from sincor2.governance.money_gate import reset_money_boundary

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAIL_PAY_HTML = REPO_ROOT / "templates" / "rail_pay.html"

PAYER = "0x" + "11" * 20


@pytest.fixture(autouse=True)
def _fresh_boundary():
    reset_money_boundary()
    yield
    reset_money_boundary()


@pytest.fixture(scope="module")
def mvp_app_obj(tmp_path_factory):
    """The production Flask app, imported with hermetic stores."""
    tmp = tmp_path_factory.mktemp("mvp-rail-pay")
    os.environ["ORDERS_DB_PATH"] = str(tmp / "orders.db")
    os.environ["SINCOR_REVENUE_DB"] = str(tmp / "revenue.db")
    os.environ["SINCOR_DATA_DIR"] = str(tmp / "data")
    from sincor2.mvp_app import app as mvp_app
    mvp_app.config["TESTING"] = True
    mvp_app.config["SERVER_NAME"] = None
    return mvp_app


@pytest.fixture()
def mvp_client(mvp_app_obj, tmp_path, monkeypatch):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("SINCOR_REVENUE_DB", str(tmp_path / "revenue.db"))
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    x402.init_x402_db()
    return mvp_app_obj.test_client()


# ---------------------------------------------------------------------------
# P0-3: the pay pages are mounted on the production app
# ---------------------------------------------------------------------------

def test_rail_pay_routes_mounted_on_mvp_app(mvp_app_obj):
    rules = {str(r) for r in mvp_app_obj.url_map.iter_rules()}
    assert "/rail/pay/<resource_id>" in rules
    assert "/rail/onramp" in rules
    # The rest of the rail came with it.
    assert "/rail/checkout" in rules
    assert "/rail/settle" in rules


@pytest.mark.parametrize("resource_id", [
    "rail-pack-credentialing_launch",
    "rail-pack-webbuilder_site",
    "rail-pack-digital_bi_template_pack",  # a DIGITAL_SKUS pack
    "rail-sub-starter",
])
def test_pay_pages_return_200_on_mvp_app(mvp_client, resource_id):
    resp = mvp_client.get(f"/rail/pay/{resource_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert f'"resource_id": "{resource_id}"' in html


def test_health_exposes_rail_mount_status(mvp_client):
    resp = mvp_client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["rail"]["rail_routes"] is True
    assert body["rail"]["rail_delivery"] is True
    assert body["rail"]["rail_pay"] is True
    assert body["rail"]["mounted"] is True


# ---------------------------------------------------------------------------
# P0-3: the mapped checkout payloads mint valid 402 x402 challenges
# (these are exactly the bodies the page JS builds via checkoutBody())
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sku", [
    "credentialing_launch",        # rail-pack-credentialing_launch
    "webbuilder_site",             # rail-pack-webbuilder_site
    "digital_bi_template_pack",    # rail-pack-digital_bi_template_pack
])
def test_mapped_pack_checkout_payloads_mint_402(mvp_client, sku):
    res = mvp_client.post("/rail/checkout", json={"pack_sku": sku})
    assert res.status_code == 402, res.get_json()
    body = res.get_json()
    assert body["challenge_id"]
    assert body["pack_sku"] == sku
    payment = body["payment"]
    assert payment["payTo"]
    assert payment["asset"]
    assert payment["maxAmountRequired"]


def test_mapped_subscription_checkout_payload_mints_402(mvp_client):
    res = mvp_client.post("/rail/checkout", json={
        "subscription_plan": "starter", "payer_wallet": PAYER})
    assert res.status_code == 402, res.get_json()
    body = res.get_json()
    assert body["challenge_id"]
    assert body["plan_id"] == "starter"
    assert body["payment"]["payTo"]


def test_raw_resource_id_as_pack_sku_is_rejected(mvp_client):
    # Why the JS mapping exists: the server keys PACK_SKUS by bare SKU,
    # so the pre-fix page's verbatim {pack_sku: resourceId} always 400'd.
    res = mvp_client.post(
        "/rail/checkout", json={"pack_sku": "rail-pack-credentialing_launch"})
    assert res.status_code == 400
    assert res.get_json()["error"].startswith("Unknown pack_sku")


# ---------------------------------------------------------------------------
# P0-2 + P0-3: the rail_pay.html JS contract (static)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pay_html():
    return RAIL_PAY_HTML.read_text(encoding="utf-8")


def test_transfer_recipient_is_payto_never_the_token_contract(pay_html):
    # The ERC-20 transfer's recipient argument is the treasury (payTo).
    assert "erc20TransferData(payment.payTo, payment.maxAmountRequired)" \
        in pay_html
    # ...and never the token contract itself (funds sent there are locked).
    assert "erc20TransferData(payment.asset" not in pay_html
    # The transaction is still ADDRESSED to the token contract — that part
    # was always correct and must stay.
    assert "to: payment.asset" in pay_html


def test_payto_asset_guard_present(pay_html):
    assert "payment.payTo.toLowerCase() !== payment.asset.toLowerCase()" \
        in pay_html
    assert "isValidAddress(payment.payTo)" in pay_html
    assert "isValidAddress(payment.asset)" in pay_html


def test_checkout_402_is_parsed_not_thrown(pay_html):
    # A dedicated checkout caller treats HTTP 402 + a payment payload as
    # the challenge; loadCheckout uses it.
    assert "postCheckoutJSON" in pay_html
    assert 'postCheckoutJSON("/rail/checkout", checkoutBody())' in pay_html
    assert "r.status === 402" in pay_html
    # The generic postJSON (settle/bind) still throws on !r.ok.
    assert "if (!r.ok)" in pay_html


def test_checkout_body_maps_resource_ids(pay_html):
    assert 'resourceId.indexOf("rail-pack-") === 0' in pay_html
    assert 'resourceId.slice("rail-pack-".length)' in pay_html
    assert 'resourceId.indexOf("rail-sub-") === 0' in pay_html
    assert 'resourceId.slice("rail-sub-".length)' in pay_html
    assert "subscription_plan" in pay_html
    assert "payer_wallet" in pay_html
    # The pre-fix verbatim send is gone.
    assert 'postJSON("/rail/checkout", { pack_sku: resourceId })' \
        not in pay_html
