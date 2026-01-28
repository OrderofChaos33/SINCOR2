from app import app

def test_health():
    with app.test_client() as client:
        r = client.get("/health")
        assert r.status_code == 200
        data = r.get_json()
        # Accept either 'ok' or 'healthy' as valid status values
        assert data.get("status") in ("ok", "healthy")
