from __future__ import annotations

import os


def test_get_resource_supports_usdc_priced_skill(monkeypatch, tmp_path):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    from sincor2.x402_payments import get_resource

    resource = get_resource("healthcare-credential-check")
    assert resource is not None
    assert resource["token"] == "USDC"
    assert resource["skill_id"] == "healthcare-credential-check"
    assert float(resource["amount_display"]) == 5.0


def test_finalize_challenge_payment_records_burn_log_and_inflow(monkeypatch, tmp_path):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    from sincor2 import x402_payments

    recorded = {}

    def _record_platform_payment(**kwargs):
        recorded["payment"] = kwargs
        return {"ok": True, **kwargs}

    def _record_inflow(amount, **kwargs):
        recorded["inflow"] = {"amount": amount, **kwargs}

        class _Event:
            def to_dict(self):
                return {"amount": amount, **kwargs}

        return _Event()

    monkeypatch.setattr(x402_payments, "record_platform_payment", _record_platform_payment)
    monkeypatch.setattr(x402_payments, "record_inflow", _record_inflow)

    result = x402_payments.finalize_challenge_payment(
        {
            "ok": True,
            "status": "fulfilled",
            "challenge_id": "x402-demo",
            "resource_id": "healthcare-credential-check",
            "tx_hash": "0x" + "ab" * 32,
            "payer_wallet": "0x" + "12" * 20,
            "amount_atomic": "5000000",
            "amount_display": 5.0,
        }
    )

    assert recorded["payment"]["token"] == "USDC"
    assert recorded["payment"]["product_name"] == "x402:healthcare-credential-check"
    assert recorded["inflow"]["asset"] == "USDC"
    assert recorded["inflow"]["source"] == "x402_payment"
    assert recorded["inflow"]["projected"] is False
    assert result["treasury_inflow"]["amount"] == 5.0


def test_execute_paid_resource_dispatches_healthcare_skill(tmp_path):
    os.environ["ORDERS_DB_PATH"] = str(tmp_path / "orders.db")
    from sincor2.x402_payments import execute_paid_resource

    status, body = execute_paid_resource(
        "healthcare-credential-check",
        {
            "provider_id": "PROV-123",
            "provider_npi": "1234567890",
            "provider_name": "Demo Provider",
            "specialty": "family_medicine",
            "state": "TX",
            "payer_ids": ["BCBS01"],
        },
    )

    assert status == 200
    assert body["ok"] is True
    assert body["skill_id"] == "healthcare-credential-check"
    assert body["execution"]["status"] == "success"
    assert body["execution"]["result"]["provider_id"] == "PROV-123"
