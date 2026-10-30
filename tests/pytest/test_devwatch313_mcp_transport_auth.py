"""Dev-watch item 313 hedge — MCP transport-mode auth (CVE-2026-62264 class).

The CVE pattern: an MCP server that is safe under stdio becomes an
*unauthenticated* server when the same code is run over HTTP, because the
HTTP transport never got the auth gate the operator assumed existed.

SINCOR2 ships exactly two MCP servers:

* ``sincor2.mcp_server`` — marketplace discovery, **stdio only**. It has no
  HTTP listener at all; its write tools are default-deny (two-phase
  confirmation tokens). The invariant pinned here: it must never grow an
  HTTP transport, and over its one real transport an unauthenticated
  (unconfirmed) write never executes.
* ``sincor2.swarm_command_mcp`` — Swarm Command board, **Streamable HTTP
  only** (WSGI app; ``__main__`` serves it via wsgiref). Every
  ``POST /mcp`` is bearer-gated, fail-closed 503 when
  ``SWARM_MCP_BEARER_TOKEN`` is unset. The invariant exercised here over a
  *real socket* (not just WSGI environ calls): unauthenticated callers are
  refused in the server's only transport mode.

Plus a config guard, in the style of ``test_mcp_tool_annotations_scan``:
no MCP deployment config in this repo (Smithery / Official Registry /
directory packages) and no MCP server module may enable an HTTP-class
transport without an auth gate configured for that transport. The guard
helpers are unit-tested against synthetic bad/good configs so the guard
provably has teeth even while the current repo passes it.
"""

import io
import json
import os
import re
import socket
import sys
import threading
import urllib.error
import urllib.request

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC_DIR = os.path.join(REPO_ROOT, "src", "sincor2")
DIST_DIR = os.path.join(REPO_ROOT, "docs", "distribution", "mcp-directories")

from sincor2 import mcp_server  # noqa: E402
from sincor2 import swarm_command_mcp as swarm  # noqa: E402


def _rpc(method, params=None, msg_id=1):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


# ---------------------------------------------------------------------------
# Swarm Command — HTTP transport over a real socket (its only mode)
# ---------------------------------------------------------------------------


@pytest.fixture()
def swarm_http(tmp_path, monkeypatch):
    """Serve swarm.app exactly like swarm_command_mcp._main() does:
    wsgiref make_server on 127.0.0.1, ephemeral port, real socket."""
    from wsgiref.simple_server import make_server

    monkeypatch.setenv("SWARM_BOARD_PATH", str(tmp_path / "board.json"))
    httpd = make_server("127.0.0.1", 0, swarm.app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _http_post(url, payload, headers=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"_raw": raw}


def test_swarm_http_fail_closed_503_when_token_unconfigured(swarm_http, monkeypatch):
    """No SWARM_MCP_BEARER_TOKEN -> every /mcp POST refused 503, even a
    well-formed tools/list. This is the CVE-313 failure mode (HTTP mode
    silently open) asserted impossible."""
    monkeypatch.delenv("SWARM_MCP_BEARER_TOKEN", raising=False)
    status, body = _http_post(swarm_http + "/mcp", _rpc("tools/list"))
    assert status == 503
    assert "result" not in body  # no tool data leaked


def test_swarm_http_missing_authorization_refused_401(swarm_http, monkeypatch):
    monkeypatch.setenv("SWARM_MCP_BEARER_TOKEN", "test-token-313")
    status, body = _http_post(swarm_http + "/mcp", _rpc("tools/list"))
    assert status == 401
    assert "result" not in body


def test_swarm_http_wrong_token_refused_401(swarm_http, monkeypatch):
    monkeypatch.setenv("SWARM_MCP_BEARER_TOKEN", "test-token-313")
    status, body = _http_post(
        swarm_http + "/mcp",
        _rpc("tools/call", {"name": "board_lanes_read", "arguments": {}}),
        headers={"Authorization": "Bearer not-the-token"},
    )
    assert status == 401
    assert "result" not in body


def test_swarm_http_unauthenticated_write_executes_nothing(swarm_http, monkeypatch):
    """A state-changing tools/call without auth must be refused AND leave
    no side effect on the board."""
    monkeypatch.setenv("SWARM_MCP_BEARER_TOKEN", "test-token-313")
    status, _ = _http_post(
        swarm_http + "/mcp",
        _rpc("tools/call", {
            "name": "board_card_add",
            "arguments": {"lane": "brainstorm",
                          "title": "unauthenticated write",
                          "body": "must never land", "owner": "probe"},
        }),
    )
    assert status == 401
    # Control read with the real token: the card must not exist.
    status, body = _http_post(
        swarm_http + "/mcp",
        _rpc("tools/call", {"name": "board_lanes_read", "arguments": {}}),
        headers={"Authorization": "Bearer test-token-313"},
    )
    assert status == 200
    assert "unauthenticated write" not in json.dumps(body)


def test_swarm_http_valid_token_control_200(swarm_http, monkeypatch):
    """Control: with the token, the same socket serves tools/list — so the
    refusals above are the auth gate, not a broken harness."""
    monkeypatch.setenv("SWARM_MCP_BEARER_TOKEN", "test-token-313")
    status, body = _http_post(
        swarm_http + "/mcp", _rpc("tools/list"),
        headers={"Authorization": "Bearer test-token-313"},
    )
    assert status == 200
    assert len(body["result"]["tools"]) == 10


def test_swarm_http_get_mcp_yields_no_tool_data(swarm_http, monkeypatch):
    """No secondary unauthenticated read mode: GET /mcp is not a transport
    this server offers (404, no tool catalogue)."""
    monkeypatch.setenv("SWARM_MCP_BEARER_TOKEN", "test-token-313")
    req = urllib.request.Request(swarm_http + "/mcp", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status, raw = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read().decode("utf-8", "replace")
    assert status == 404
    assert "tools" not in raw


# ---------------------------------------------------------------------------
# Discovery server — stdio transport (its only mode)
# ---------------------------------------------------------------------------


def test_discovery_stdio_write_without_confirmation_never_executes(monkeypatch):
    """Over stdio there is no network caller, but the write gate is the
    confirmation token: an unconfirmed register_agent call must return
    confirmation_required and perform ZERO outbound platform requests."""
    outbound = []

    def recording_request(method, path, body=None):
        outbound.append((method, path))
        return {"ok": True, "status": 200, "data": {}}

    monkeypatch.setattr(mcp_server, "_request", recording_request)
    resp = mcp_server.handle_message(_rpc("tools/call", {
        "name": "register_agent",
        "arguments": {"agent_id": "agent-313", "name": "Probe",
                      "capability_tags": ["lead-enrichment"]},
    }))
    assert resp is not None and "result" in resp
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["status"] == "confirmation_required"
    assert outbound == []  # nothing executed


def test_discovery_serve_opens_no_network_listener(monkeypatch):
    """Drive the real serve() loop over piped stdio with socket bind /
    listen / connect rigged to explode: the stdio transport must answer
    without ever touching the network stack."""

    class GuardedSocket(socket.socket):
        def bind(self, *a, **k):  # pragma: no cover - failure path
            raise AssertionError("stdio server attempted socket.bind()")

        def listen(self, *a, **k):  # pragma: no cover - failure path
            raise AssertionError("stdio server attempted socket.listen()")

    def no_connect(*a, **k):  # pragma: no cover - failure path
        raise AssertionError("stdio server attempted an outbound connection")

    monkeypatch.setattr(socket, "socket", GuardedSocket)
    monkeypatch.setattr(socket, "create_connection", no_connect)

    lines = "\n".join([
        json.dumps(_rpc("initialize", {}, msg_id=1)),
        json.dumps(_rpc("tools/list", msg_id=2)),
    ]) + "\n"
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(lines))
    monkeypatch.setattr(sys, "stdout", out)
    mcp_server.serve()
    responses = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
    assert [r["id"] for r in responses] == [1, 2]
    assert responses[0]["result"]["serverInfo"]["name"] == mcp_server.SERVER_NAME
    assert responses[1]["result"]["tools"]  # catalogue served over stdio only


def test_discovery_module_has_no_http_server_entrypoint():
    """Structural pin: the discovery module must not contain the machinery
    to start an HTTP listener. If a future change adds one, this fails and
    the config guard below additionally requires an auth gate for it."""
    source = _read(os.path.join(SRC_DIR, "mcp_server.py"))
    for marker in _LISTENER_MARKERS:
        assert marker not in source, (
            "mcp_server.py gained HTTP-listener machinery %r — an HTTP "
            "transport for the discovery server needs a founder-reviewed "
            "auth gate first (dev-watch 313)" % marker)
    assert "sys.stdin" in source  # its one transport is the stdio loop


# ---------------------------------------------------------------------------
# Config guard — no HTTP transport without an auth gate for that transport
# ---------------------------------------------------------------------------

_LISTENER_MARKERS = (
    "make_server", "HTTPServer", "BaseHTTPRequestHandler",
    "socketserver", "serve_forever",
)

_HTTP_TRANSPORT_PATTERNS = (
    re.compile(r'"type"\s*:\s*"(streamable-http|sse|http)"'),
    re.compile(r'"transport"\s*:\s*"?(streamable-http|sse)'),
    re.compile(r'"remotes"\s*:'),
    re.compile(r"(?m)^\s*type\s*:\s*(streamable-http|sse|http)\s*$"),
    re.compile(r"(?m)^\s*transport\s*:\s*(streamable-http|sse)\s*$"),
)

_AUTH_GATE_MARKERS = (
    "authorization", "bearer", "oauth", "api_key", "api-key", "apikey",
    "x-api-key",
)


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def scan_config_text(text, label):
    """Violations for one MCP deployment config's text: an HTTP-class
    transport declared with no auth gate declared for it in the same
    config. stdio configs are always clean."""
    lowered = text.lower()
    if not any(p.search(text) for p in _HTTP_TRANSPORT_PATTERNS):
        return []
    if any(marker in lowered for marker in _AUTH_GATE_MARKERS):
        return []
    return ["%s: enables an HTTP MCP transport but declares no auth gate "
            "(Authorization/bearer/oauth/api-key) for that transport" % label]


def scan_server_module_text(text, label):
    """Violations for one server module's source: constructing an HTTP
    listener obliges a fail-closed auth gate in the same module — an
    env-configured token, a refusal (503) when it is unset, and a
    credential comparison on the request path."""
    if not any(marker in text for marker in _LISTENER_MARKERS):
        return []
    problems = []
    if not re.search(r"environ\.get\([^)]*TOKEN", text):
        problems.append("no env-configured auth token")
    if "503" not in text:
        problems.append("no fail-closed 503 when the token is unconfigured")
    if "compare_digest" not in text and "401" not in text:
        problems.append("no credential check (compare_digest / 401)")
    return ["%s: HTTP listener without a complete auth gate (%s)"
            % (label, "; ".join(problems))] if problems else []


# -- guard teeth: synthetic configs ------------------------------------------


def test_guard_flags_http_transport_without_auth_gate():
    bad = json.dumps({"name": "x", "remotes": [
        {"type": "streamable-http", "url": "https://example.invalid/mcp"}]})
    violations = scan_config_text(bad, "synthetic-bad")
    assert violations and "synthetic-bad" in violations[0]


def test_guard_flags_yaml_http_transport_without_auth_gate():
    bad = "startCommand:\n  type: streamable-http\n  url: https://example.invalid/mcp\n"
    assert scan_config_text(bad, "synthetic-yaml")


def test_guard_passes_http_transport_with_auth_gate():
    good = json.dumps({"name": "x", "remotes": [{
        "type": "streamable-http", "url": "https://example.invalid/mcp",
        "headers": {"Authorization": "Bearer ${MCP_TOKEN}"}}]})
    assert scan_config_text(good, "synthetic-good") == []


def test_guard_passes_stdio_config():
    stdio = json.dumps({"packages": [{"registryType": "mcpb",
                                      "transport": {"type": "stdio"}}]})
    assert scan_config_text(stdio, "synthetic-stdio") == []


def test_guard_flags_listener_module_without_auth_gate():
    bare = "from wsgiref.simple_server import make_server\nmake_server('0.0.0.0', 80, app)\n"
    assert scan_server_module_text(bare, "synthetic-module")


def test_guard_passes_listener_module_with_fail_closed_gate():
    gated = (
        "import hmac, os\n"
        "from wsgiref.simple_server import make_server\n"
        "tok = os.environ.get('SOME_MCP_BEARER_TOKEN', '')\n"
        "if not tok: return '503 Service Unavailable'\n"
        "if not hmac.compare_digest(supplied, tok): return '401'\n"
    )
    assert scan_server_module_text(gated, "synthetic-gated") == []


# -- guard over the real repo -------------------------------------------------


def _deployment_config_files():
    """Machine-readable MCP deployment configs: the directory submission
    packages plus any root-level config a future submission might add.
    Markdown (SUBMIT.md / listing.md) documents options but enables
    nothing, so it is out of scope by design."""
    found = []
    if os.path.isdir(DIST_DIR):
        for dirpath, _dirs, files in os.walk(DIST_DIR):
            for name in files:
                if name.endswith((".json", ".yaml", ".yml")) or \
                        ".json." in name or ".yaml." in name:
                    found.append(os.path.join(dirpath, name))
    for name in ("smithery.yaml", "server.json", "glama.json", "mcp.json"):
        candidate = os.path.join(REPO_ROOT, name)
        if os.path.isfile(candidate):
            found.append(candidate)
    return sorted(found)


def test_repo_mcp_deployment_configs_gate_their_http_transports():
    configs = _deployment_config_files()
    assert configs, "expected at least the Smithery/registry draft configs"
    violations = []
    for path in configs:
        violations += scan_config_text(
            _read(path), os.path.relpath(path, REPO_ROOT))
    assert violations == []


def test_repo_mcp_server_modules_gate_their_http_transports():
    """Every src module that both concerns MCP and constructs an HTTP
    listener must carry the full fail-closed gate in-module."""
    violations = []
    examined = []
    for dirpath, _dirs, files in os.walk(SRC_DIR):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            text = _read(path)
            if "mcp" not in text.lower():
                continue
            if not any(m in text for m in _LISTENER_MARKERS):
                continue
            examined.append(os.path.relpath(path, REPO_ROOT))
            violations += scan_server_module_text(
                text, os.path.relpath(path, REPO_ROOT))
    # The one HTTP MCP server we ship must actually be examined, or the
    # guard is vacuous.
    assert any("swarm_command_mcp" in p for p in examined)
    assert violations == []
