import json, os
from marketplace import SINCMarketplace


def test_settlement_demo_flow(monkeypatch):
    # Force demo mode
    monkeypatch.setenv('MARKETPLACE_FORCE_DEMO', '1')

    m = SINCMarketplace()

    # Create payment
    res = m.generate_payment_request('content-writer', '0xDEMOUSER', 1)
    assert res['success'] is True
    payment_id = res['payment_id']

    # Simulate reception (demo)
    chk = m.check_payment_received(payment_id)
    assert chk['success'] is True

    # Settle to agent
    settle = m.settle_payment(payment_id, '0xAGENTADDRESS', 0.75)
    assert settle['success'] is True
    assert 'tx' in settle

    # Ensure payment status updated to settled
    p = next((p for p in m.pending_payments if p['payment_id'] == payment_id), None)
    assert p is not None and p['status'] == 'settled'