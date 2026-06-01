

def test_login_success(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin-password"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert "access_token" in payload


def test_login_missing_fields(client):
    response = client.post("/api/auth/login", json={})
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "error"
    assert payload["code"] == "invalid_request"


def test_profile_requires_auth(client):
    response = client.get("/api/auth/profile")
    assert response.status_code == 401


def test_profile_with_auth(client, auth_headers):
    response = client.get("/api/auth/profile", headers=auth_headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
