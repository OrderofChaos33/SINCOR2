import json
from sincor_app import app


def test_store_demo_flow():
    client = app.test_client()

    # List products
    r = client.get('/store/products')
    assert r.status_code == 200
    data = r.get_json()
    assert data['success'] is True
    products = data['products']
    assert any(p['id'] == 'book_auto_detailing' for p in products)

    # Create payment for book
    r = client.post('/store/create', json={'product_id': 'book_auto_detailing', 'email': 'court@example.com'})
    assert r.status_code == 200
    data = r.get_json()
    assert data['success'] is True
    payment_id = data['payment_id']
    assert data['approval_url'] is not None

    # Execute payment (demo)
    r = client.post('/store/execute', json={'payment_id': payment_id, 'payer_id': 'DEMO-PAYER', 'product_id': 'book_auto_detailing', 'email': 'court@example.com'})
    assert r.status_code == 200
    data = r.get_json()
    assert data['success'] is True
    assert 'download_link' in data
    assert data['download_link'].endswith('book_auto_detailing.zip')
