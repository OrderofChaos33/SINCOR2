

def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "healthy"
    assert "timestamp" in payload


def test_home_route(client):
    response = client.get("/")
    assert response.status_code == 200


def test_request_id_header_set(client):
    response = client.get("/health")
    assert response.headers.get("X-Request-ID")
