"""Dev-watch item 305 — CVE-2026-61546 (AnkiMCP DNS rebinding).

Grounding on base 4de2e165:
  * No global DNS-rebinding disable (the FastMCP transport-security
    flag set to False, or any TransportSecuritySettings / FastMCP
    transport) exists anywhere in the repo — the MCP SDK is not used;
    both servers are stdlib-only.
  * ``sincor2.mcp_server`` (discovery) is stdio-only: no HTTP listener,
    so there is no Host/Origin surface to rebind against.
  * ``sincor2.swarm_command_mcp`` is the ONLY HTTP MCP listener
    (WSGI, POST /mcp, loopback dev port 8899). It already had bearer
    auth fail-closed (503 unset / 401 wrong), but Origin was used only
    to reflect CORS headers and Host was never checked — a foreign
    Host/Origin with a valid token was served ``tools/list``.

These tests pin the fix: Host/Origin allowlisting is enforced on
POST /mcp BEFORE any JSON-RPC dispatch (before ``tools/list`` is
served), authentication is still required, a tunnel exception is
scoped to the exact tunneled host/origin only, and a config guard
fails if a global DNS-rebinding disable ever appears in our code.

The WSGI ``app`` is exercised directly — no sockets, no network.
"""

import io
import json
import re
from pathlib import Path

import pytest

from sincor2.swarm_command_mcp import app

TOKEN = "test-bearer-token-305"
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def board_path(tmp_path, monkeypatch):
    path = str(tmp_path / "board.json")
    monkeypatch.setenv("SWARM_BOARD_PATH", path)
    monkeypatch.setenv("SWARM_MCP_BEARER_TOKEN", TOKEN)
    monkeypatch.delenv("SWARM_MCP_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("SWARM_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("SWARM_MCP_ALLOWED_ORIGINS", raising=False)
    return path


def _rpc(method, params=None, msg_id=1):
    req = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        req["params"] = params
    return json.dumps(req)


def _post(body, token=TOKEN, host=None, origin=None):
    raw = body.encode("utf-8") if isinstance(body, str) else body
    env = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/mcp",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
    }
    if token is not None:
        env["HTTP_AUTHORIZATION"] = "Bearer %s" % token
    if host is not None:
        env["HTTP_HOST"] = host
    if origin is not None:
        env["HTTP_ORIGIN"] = origin
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    resp_body = b"".join(app(env, start_response))
    payload = None
    if resp_body:
        try:
            payload = json.loads(resp_body.decode("utf-8"))
        except ValueError:
            payload = None
    return captured["status"], captured["headers"], resp_body, payload


def _tools_list(**kwargs):
    return _post(_rpc("tools/list"), **kwargs)


def _assert_refused_before_tools_list(status, body, payload):
    assert status[:3] in {"401", "403", "503"}, status
    assert payload is not None
    assert "result" not in payload, payload
    assert b"board_lanes_read" not in body


# -- foreign Host / Origin: refused before tools/list -----------------------


def test_foreign_host_tools_list_refused_403(board_path):
    status, _h, body, payload = _tools_list(host="evil.example")
    assert status.startswith("403"), status
    assert payload["error"] == "forbidden host"
    _assert_refused_before_tools_list(status, body, payload)


def test_foreign_host_with_port_tools_list_refused(board_path):
    status, _h, body, payload = _tools_list(host="attacker.example:8899")
    assert status.startswith("403"), status
    _assert_refused_before_tools_list(status, body, payload)


def test_foreign_origin_tools_list_refused_403(board_path):
    status, _h, body, payload = _tools_list(
        host="127.0.0.1:8899", origin="https://evil.example"
    )
    assert status.startswith("403"), status
    assert payload["error"] == "forbidden origin"
    _assert_refused_before_tools_list(status, body, payload)


def test_null_origin_tools_list_refused(board_path):
    status, _h, body, payload = _tools_list(origin="null")
    assert status.startswith("403"), status
    _assert_refused_before_tools_list(status, body, payload)


def test_foreign_origin_tools_call_refused_and_not_executed(board_path, tmp_path):
    status, _h, body, payload = _post(
        _rpc(
            "tools/call",
            {
                "name": "discussion_post",
                "arguments": {"topic": "rebind", "author": "x", "text": "y"},
            },
        ),
        origin="https://evil.example",
    )
    assert status.startswith("403"), status
    _assert_refused_before_tools_list(status, body, payload)
    # No dispatch happened: the board file was never created/written.
    assert not Path(board_path).exists()


def test_foreign_host_refused_even_without_token(board_path):
    """Rebinding check precedes dispatch regardless of auth state."""
    status, _h, body, payload = _tools_list(token=None, host="evil.example")
    assert status.startswith("403"), status
    _assert_refused_before_tools_list(status, body, payload)


def test_cors_star_does_not_allowlist_foreign_origin(board_path, monkeypatch):
    """A "*" CORS setting is not a global rebinding exception."""
    monkeypatch.setenv("SWARM_MCP_CORS_ORIGINS", "*")
    status, _h, body, payload = _tools_list(origin="https://evil.example")
    assert status.startswith("403"), status
    _assert_refused_before_tools_list(status, body, payload)


# -- authentication still required before tools/list ------------------------


def test_unauthenticated_tools_list_refused(board_path):
    status, _h, body, payload = _tools_list(token=None, host="127.0.0.1:8899")
    assert status.startswith("401"), status
    _assert_refused_before_tools_list(status, body, payload)


def test_wrong_token_tools_list_refused(board_path):
    status, _h, body, payload = _tools_list(token="wrong", host="localhost:8899")
    assert status.startswith("401"), status
    _assert_refused_before_tools_list(status, body, payload)


def test_unconfigured_token_tools_list_refused_503(board_path, monkeypatch):
    monkeypatch.delenv("SWARM_MCP_BEARER_TOKEN", raising=False)
    status, _h, body, payload = _tools_list(token=None, host="127.0.0.1:8899")
    assert status.startswith("503"), status
    _assert_refused_before_tools_list(status, body, payload)


# -- allowed callers still served -------------------------------------------


def test_loopback_host_and_origin_tools_list_served(board_path):
    status, _h, _body, payload = _tools_list(
        host="127.0.0.1:8899", origin="http://127.0.0.1:8899"
    )
    assert status.startswith("200"), status
    names = [t["name"] for t in payload["result"]["tools"]]
    assert "board_lanes_read" in names


def test_localhost_host_tools_list_served(board_path):
    status, _h, _body, payload = _tools_list(host="localhost:8899")
    assert status.startswith("200"), status
    assert payload["result"]["tools"]


def test_no_host_no_origin_nonbrowser_client_served(board_path):
    """Server-to-server MCP clients send no Host/Origin; bearer gates them."""
    status, _h, _body, payload = _tools_list()
    assert status.startswith("200"), status
    assert payload["result"]["tools"]


def test_scoped_tunnel_exception_only_for_named_host(board_path, monkeypatch):
    """Tunnel/proxy exception is scoped to the exact tunneled host/origin."""
    monkeypatch.setenv("SWARM_MCP_ALLOWED_HOSTS", "tunnel.example")
    monkeypatch.setenv("SWARM_MCP_ALLOWED_ORIGINS", "https://tunnel.example")

    status, _h, _body, payload = _tools_list(
        host="tunnel.example", origin="https://tunnel.example"
    )
    assert status.startswith("200"), status
    assert payload["result"]["tools"]

    # Any other foreign host is still refused — the exception is not global.
    status, _h, body, payload = _tools_list(host="other.example")
    assert status.startswith("403"), status
    _assert_refused_before_tools_list(status, body, payload)

    status, _h, body, payload = _tools_list(
        host="tunnel.example", origin="https://other.example"
    )
    assert status.startswith("403"), status
    _assert_refused_before_tools_list(status, body, payload)


def test_origin_matching_configured_cors_origin_served(board_path, monkeypatch):
    monkeypatch.setenv("SWARM_MCP_CORS_ORIGINS", "https://manus.example")
    status, _h, _body, payload = _tools_list(
        host="manus.example", origin="https://manus.example"
    )
    assert status.startswith("200"), status
    assert payload["result"]["tools"]


# -- config guard: DNS-rebinding protection is never disabled globally ------

# Built by concatenation so this guard file can never match itself if the
# scan scope is ever widened to tests/.
_FORBIDDEN_SETTING = "enable_dns_rebinding_" + "protection"
_FORBIDDEN_RE = re.compile(_FORBIDDEN_SETTING + r"\s*=\s*False")


def _scanned_source_files():
    roots = [REPO_ROOT / "src"]
    scripts = REPO_ROOT / "scripts"
    if scripts.is_dir():
        roots.append(scripts)
    for root in roots:
        yield from root.rglob("*.py")


def test_no_global_dns_rebinding_disable_in_code():
    offenders = []
    for path in _scanned_source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if _FORBIDDEN_RE.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], (
        "DNS-rebinding protection disabled in: %s — never disable it "
        "globally; scope any tunnel exception to the tunneled host/origin "
        "via SWARM_MCP_ALLOWED_HOSTS / SWARM_MCP_ALLOWED_ORIGINS" % offenders
    )


def test_swarm_mcp_has_no_rebinding_kill_switch():
    """The HTTP MCP module must not grow a disable flag / env kill-switch."""
    source = (REPO_ROOT / "src" / "sincor2" / "swarm_command_mcp.py").read_text(
        encoding="utf-8"
    )
    assert _FORBIDDEN_SETTING not in source
    assert "SWARM_MCP_DISABLE" not in source
    assert "ALLOW_ALL_HOSTS" not in source
    assert "ALLOW_ALL_ORIGINS" not in source


def test_discovery_mcp_server_is_stdio_only():
    """mcp_server.py must stay stdio-only (no HTTP listener to rebind).

    If an HTTP transport is ever added there, this test fails on purpose:
    that transport must first get the same auth + Host/Origin
    allowlisting as swarm_command_mcp.
    """
    source = (REPO_ROOT / "src" / "sincor2" / "mcp_server.py").read_text(
        encoding="utf-8"
    )
    for token in ("make_server", "HTTPServer", "wsgiref", "WSGIServer"):
        assert token not in source, token
    assert "sys.stdin" in source and "sys.stdout" in source
