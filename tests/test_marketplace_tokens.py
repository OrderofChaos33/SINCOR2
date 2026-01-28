import json, os
from marketplace import SINCMarketplace


def test_generate_payment_request_demo_mode(tmp_path, monkeypatch):
    # Force demo mode by ensuring PRIVATE_KEY not set
    # Force demo mode via explicit environment variable to avoid test flakiness
    monkeypatch.setenv('MARKETPLACE_FORCE_DEMO', '1')

    m = SINCMarketplace()
    # Ensure blockchain not enabled
    assert not getattr(m, 'blockchain_enabled', False)

    res = m.generate_payment_request('content-writer', '0xDEMOUSER', 2)
    assert res['success'] is True
    payment_id = res['payment_id']

    # Check pending payments recorded
    found = next((p for p in m.pending_payments if p['payment_id'] == payment_id), None)
    assert found is not None
    assert found['status'] == 'pending'

    # Simulate checking payment in demo mode -> should immediately finalize
    chk = m.check_payment_received(payment_id)
    assert chk['success'] is True
    assert chk['rental']['agent_id'] == 'content-writer'


def test_check_payment_not_found(monkeypatch):
    monkeypatch.delenv('PRIVATE_KEY', raising=False)
    m = SINCMarketplace()
    res = m.check_payment_received('nonexistent')
    assert res['success'] is False
    assert 'not found' in res['error']
