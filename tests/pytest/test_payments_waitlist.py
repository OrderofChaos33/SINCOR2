

def test_stripe_checkout(client):
    response = client.post(
        "/api/payment/stripe/create-checkout",
        json={"amount": 10, "description": "Starter checkout", "currency": "USD"},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["session_id"] == "cs_test_123"


def test_paypal_invalid_amount(client):
    response = client.post("/api/payment/paypal/create-order", json={"amount": "oops"})
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["code"] == "invalid_amount"


def test_waitlist_join_validation(client):
    response = client.post("/api/waitlist/join", json={"email": "not-an-email"})
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["code"] == "invalid_waitlist_payload"


def test_waitlist_join_success(client):
    response = client.post(
        "/api/waitlist/join",
        json={"email": "person@example.com", "name": "Jane"},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["success"] is True
