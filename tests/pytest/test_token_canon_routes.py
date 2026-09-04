import json
from pathlib import Path


def test_token_canon_html_page(client):
    resp = client.get("/token")
    assert resp.status_code == 200
    assert b"SINC Token Canon" in resp.data


def test_token_canon_json_default(client):
    resp = client.get("/token.json")
    assert resp.status_code == 200
    assert resp.mimetype == "application/json"
    payload = json.loads(resp.data.decode("utf-8"))
    assert payload["token"]["symbol"] == "SINC"


def test_token_canon_json_rejects_out_of_repo_override(client, monkeypatch, tmp_path):
    outside = tmp_path / "TOKEN_CANON.json"
    outside.write_text('{"token":{"symbol":"SINC"}}', encoding="utf-8")
    monkeypatch.setenv("TOKEN_CANON_JSON_PATH", str(outside))

    resp = client.get("/token.json")

    assert resp.status_code == 500
    assert resp.get_json()["error"] == "invalid token canon path"


def test_token_canon_json_rejects_invalid_payload(client, monkeypatch):
    monkeypatch.setenv("TOKEN_CANON_JSON_PATH", "TOKEN_CANON.json")
    real_read_bytes = Path.read_bytes

    def bad_read(self):
        if self.name == "TOKEN_CANON.json":
            return b"{bad json"
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", bad_read)
    resp = client.get("/token.json")

    assert resp.status_code == 500
    assert resp.get_json()["error"] == "invalid token canon payload"


def test_token_canon_json_not_found(client, monkeypatch):
    monkeypatch.setenv("TOKEN_CANON_JSON_PATH", "TOKEN_CANON.json")
    real_read_bytes = Path.read_bytes

    def missing_read(self):
        if self.name == "TOKEN_CANON.json":
            raise FileNotFoundError
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", missing_read)
    resp = client.get("/token.json")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not found"


def test_token_canon_json_rejects_wrong_token_shape(client, monkeypatch):
    monkeypatch.setenv("TOKEN_CANON_JSON_PATH", "TOKEN_CANON.json")
    real_read_bytes = Path.read_bytes

    def wrong_shape_read(self):
        if self.name == "TOKEN_CANON.json":
            return b'{"token":[]}'
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", wrong_shape_read)
    resp = client.get("/token.json")

    assert resp.status_code == 500
    assert resp.get_json()["error"] == "invalid token canon payload"


def test_token_canon_json_rejects_missing_symbol(client, monkeypatch):
    monkeypatch.setenv("TOKEN_CANON_JSON_PATH", "TOKEN_CANON.json")
    real_read_bytes = Path.read_bytes

    def missing_symbol_read(self):
        if self.name == "TOKEN_CANON.json":
            return b'{"token":{}}'
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", missing_symbol_read)
    resp = client.get("/token.json")

    assert resp.status_code == 500
    assert resp.get_json()["error"] == "invalid token canon payload"
