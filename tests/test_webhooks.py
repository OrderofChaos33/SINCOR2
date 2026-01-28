import os, json
from sincor_app import app


def setup_purchase(payment_id='DEMO-WEBHOOK-1'):
    os.makedirs('data', exist_ok=True)
    purchases_file = 'data/store_purchases.json'
    record = {
        'timestamp': '2026-01-01T00:00:00',
        'payment_id': payment_id,
        'product_id': 'book_auto_detailing',
        'status': 'pending'
    }
    with open(purchases_file, 'w', encoding='utf-8') as f:
        json.dump([record], f, indent=2)


def test_paypal_webhook_updates_purchase():
    client = app.test_client()
    setup_purchase()

    payload = {'payment_id': 'DEMO-WEBHOOK-1', 'status': 'COMPLETED'}
    r = client.post('/webhook/paypal', json=payload)
    assert r.status_code == 200

    # Verify persisted update
    with open('data/store_purchases.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    assert data[0]['status'] == 'completed'


