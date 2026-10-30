"""Tests: dev-watch item 303 — CVE-2026-116684 (GHSA-w4fh-qvv9-3v23).

The MCP Python SDK's server-side bearer auth accepted a valid token
issued for a DIFFERENT service when one authorization server served
several resources and the token's audience (``aud``) was not checked.
Patched SDK: mcp 1.30.0 (1.x) / 2.2.0 (2.x), and only when
``validate_token_resource=True`` is set with ``resource_server_url``.

Grounding for THIS repo (verified on the base tree):
  * neither MCP server uses the SDK — ``src/sincor2/mcp_server.py``
    (stdio discovery server) and ``src/sincor2/swarm_command_mcp.py``
    (HTTP Swarm Command server) are both stdlib-only, and ``mcp`` /
    ``fastmcp`` appear in NO requirements file, pyproject, or import.
    There is therefore no SDK version to pin; the audience rule is
    implemented in the one HTTP bearer-auth layer we actually have:
    ``swarm_command_mcp``.
  * tokens here are minted INDEPENDENTLY of the server's own issuer
    helper (raw stdlib HMAC-SHA256 JWT construction), so the validator
    is exercised the way an external authorization server's token
    would arrive — including the headline negative case: a perfectly
    valid token minted for a SIBLING service must be refused.

The WSGI ``app`` is exercised directly with hand-built environ dicts —
no sockets, no network.
"""

import base64
import hashlib
import hmac
import io
import json
import time
from pathlib import Path

import pytest

from sincor2 import swarm_command_mcp as mod
from sincor2.swarm_command_board import BoardStore
from sincor2.swarm_command_mcp import app

SECRET = "test-bearer-token-123"  # static token AND default HMAC key
SIBLING_AUDIENCE = "sincor-marketplace-mcp"  # mcp_server.py SERVER_NAME
ISSUER = mod.DEFAULT_TOKEN_ISSUER


@pytest.fixture()
def board_path(tmp_path, monkeypatch):
    path = str(tmp_path / "board.json")
    monkeypatch.setenv("SWARM_BOARD_PATH", path)
    monkeypatch.setenv("SWARM_MCP_BEARER_TOKEN", SECRET)
    for var in (
        "SWARM_MCP_CORS_ORIGINS",
        "SWARM_MCP_AUDIENCE",
        "SWARM_MCP_TOKEN_ISSUER",
        "SWARM_MCP_TOKEN_SIGNING_SECRET",
        "SWARM_MCP_REQUIRE_AUDIENCE_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    return path


# -- independent token mint (does NOT use the module's issuer helper) -----


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint(payload, *, secret=SECRET, header=None, sign=True):
    hdr = {"alg": "HS256", "typ": "JWT"}
    if header:
        hdr.update(header)
    signing_input = "%s.%s" % (
        _b64(json.dumps(hdr, separators=(",", ":")).encode()),
        _b64(json.dumps(payload, separators=(",", ":")).encode()),
    )
    if not sign:
        return signing_input + "."
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return "%s.%s" % (signing_input, _b64(sig))


def good_payload(**overrides):
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": mod.SERVICE_AUDIENCE,
        "sub": "operator-1",
        "scope": mod.REQUIRED_TOKEN_SCOPE,
        "iat": now,
        "exp": now + 3600,
    }
    payload.update(overrides)
    return payload


# -- WSGI helpers (mirrors test_swarm_command_mcp.py) ----------------------


def _post(body, token):
    raw = body.encode("utf-8")
    env = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/mcp",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
    }
    if token is not None:
        env["HTTP_AUTHORIZATION"] = "Bearer %s" % token
    return env


def call_app(environ):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app(environ, start_response))
    payload = json.loads(body.decode("utf-8")) if body else None
    return captured["status"], payload


def ping(token):
    return call_app(_post(json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "ping"}), token))


# -- (c) config / CI assertions --------------------------------------------


def test_audience_validation_explicitly_enabled():
    """The patched-SDK opt-in, as a constant-on config fact here."""
    assert mod.AUDIENCE_VALIDATION_ENABLED is True
    assert mod.VALIDATE_TOKEN_RESOURCE is True
    assert mod.SERVICE_AUDIENCE == mod.SERVER_NAME == "swarm-command"
    assert mod.RESOURCE_SERVER_URL == mod.SERVICE_AUDIENCE
    assert mod.REQUIRED_TOKEN_SCOPE


def test_no_mcp_sdk_dependency_to_pin():
    """Repo fact the (a) deliverable rests on: no mcp SDK anywhere in the
    dependency declarations, so there is no vulnerable SDK version in
    the tree and no pin to apply."""
    root = Path(__file__).resolve().parents[2]
    for name in ("requirements.txt", "requirements.lock", "requirements-dev.txt"):
        for line in (root / name).read_text().splitlines():
            stripped = line.strip().lower()
            assert not stripped.startswith("mcp"), (name, line)
            assert not stripped.startswith("fastmcp"), (name, line)
    pyproject = (root / "pyproject.toml").read_text()
    assert '"mcp' not in pyproject and "'mcp" not in pyproject


def test_require_mode_rejects_plain_static_token(board_path, monkeypatch):
    monkeypatch.setenv("SWARM_MCP_REQUIRE_AUDIENCE_TOKEN", "1")
    status, _ = ping(SECRET)
    assert status.startswith("401"), status
    # ...while an audience-bound token for this service still passes.
    status, payload = ping(mint(good_payload()))
    assert status.startswith("200"), status
    assert payload["result"] == {"pong": True}


# -- positive: tokens actually minted for THIS service ---------------------


def test_audience_token_for_this_service_accepted(board_path):
    status, payload = ping(mint(good_payload()))
    assert status.startswith("200"), status
    assert payload["result"] == {"pong": True}


def test_module_issued_token_accepted_and_validates(board_path):
    token = mod.issue_audience_token()
    claims = mod.validate_audience_token(token)
    assert claims["aud"] == mod.SERVICE_AUDIENCE
    assert claims["iss"] == ISSUER
    status, _ = ping(token)
    assert status.startswith("200"), status


def test_single_element_aud_list_accepted(board_path):
    status, _ = ping(mint(good_payload(aud=[mod.SERVICE_AUDIENCE])))
    assert status.startswith("200"), status


def test_scope_as_list_accepted(board_path):
    status, _ = ping(mint(good_payload(scope=[mod.REQUIRED_TOKEN_SCOPE, "other"])))
    assert status.startswith("200"), status


def test_plain_static_token_still_accepted(board_path):
    """Backward compat: the deployed static credential keeps working
    (it is configured on this service alone — bound by deployment)."""
    status, payload = ping(SECRET)
    assert status.startswith("200"), status
    assert payload["result"] == {"pong": True}


# -- (d) NEGATIVE: valid token minted for a SIBLING service -----------------


def test_sibling_service_token_refused(board_path):
    """Headline CVE case: same issuer, same signing secret, valid expiry
    and scope — only the audience names the sibling marketplace MCP
    server instead of this one. Must be refused."""
    sibling = mint(good_payload(aud=SIBLING_AUDIENCE))
    # It IS a structurally valid, correctly signed token:
    with pytest.raises(mod.AudienceTokenError):
        mod.validate_audience_token(sibling)
    status, payload = ping(sibling)
    assert status.startswith("401"), status
    assert payload["error"] == "unauthorized"


def test_sibling_token_refused_on_write_no_side_effect(board_path):
    sibling = mint(good_payload(aud="swarm-command-board"))
    body = json.dumps({
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "discussion_post",
                   "arguments": {"topic": "sibling-should-not-post",
                                 "author": "x", "text": "y"}},
    })
    status, _ = call_app(_post(body, sibling))
    assert status.startswith("401"), status
    assert "sibling-should-not-post" not in BoardStore().discussion_read()


@pytest.mark.parametrize("bad_aud", [
    "swarm-command ",          # trailing space
    " Swarm-Command",          # case + space
    "SWARM-COMMAND",           # case differs — not an exact naming
    "https://swarm-command",   # URL form is a different audience string
    "swarm-command/mcp",
    "",
    None,
    ["swarm-command", SIBLING_AUDIENCE],  # multi-service aud: not exact
    [SIBLING_AUDIENCE],
    [],
])
def test_audience_must_name_service_exactly(board_path, bad_aud):
    status, _ = ping(mint(good_payload(aud=bad_aud)))
    assert status.startswith("401"), (bad_aud, status)


def test_missing_audience_refused(board_path):
    payload = good_payload()
    del payload["aud"]
    status, _ = ping(mint(payload))
    assert status.startswith("401"), status


# -- the other checks the scheme supports -----------------------------------


def test_expired_token_refused(board_path):
    now = int(time.time())
    status, _ = ping(mint(good_payload(iat=now - 7200, exp=now - 3600)))
    assert status.startswith("401"), status


def test_missing_expiry_refused(board_path):
    payload = good_payload()
    del payload["exp"]
    status, _ = ping(mint(payload))
    assert status.startswith("401"), status


def test_wrong_issuer_refused(board_path):
    status, _ = ping(mint(good_payload(iss="https://evil-issuer.example")))
    assert status.startswith("401"), status


def test_missing_scope_refused(board_path):
    payload = good_payload()
    del payload["scope"]
    status, _ = ping(mint(payload))
    assert status.startswith("401"), status


def test_wrong_scope_refused(board_path):
    status, _ = ping(mint(good_payload(scope="mcp:read-only")))
    assert status.startswith("401"), status


def test_wrong_signing_secret_refused(board_path):
    status, _ = ping(mint(good_payload(), secret="attacker-controlled-key"))
    assert status.startswith("401"), status


def test_tampered_payload_refused(board_path):
    """Re-encode a sibling token's payload with OUR audience, keep the
    old signature: signature validation must catch it."""
    head, _payload_seg, sig = mint(good_payload(aud=SIBLING_AUDIENCE)).split(".")
    forged = "%s.%s.%s" % (
        head,
        _b64(json.dumps(good_payload(), separators=(",", ":")).encode()),
        sig,
    )
    status, _ = ping(forged)
    assert status.startswith("401"), status


def test_alg_none_refused(board_path):
    token = mint(good_payload(), header={"alg": "none"}, sign=False)
    status, _ = ping(token)
    assert status.startswith("401"), status


def test_alg_confusion_refused(board_path):
    token = mint(good_payload(), header={"alg": "RS256"})
    status, _ = ping(token)
    assert status.startswith("401"), status


def test_not_yet_valid_token_refused(board_path):
    now = int(time.time())
    status, _ = ping(mint(good_payload(nbf=now + 3600)))
    assert status.startswith("401"), status


def test_garbage_structured_token_refused_no_static_fallback(board_path):
    status, _ = ping("aaa.bbb.ccc")
    assert status.startswith("401"), status


def test_validator_raises_for_sibling_directly(board_path):
    with pytest.raises(mod.AudienceTokenError):
        mod.validate_audience_token(mint(good_payload(aud=SIBLING_AUDIENCE)))
