"""Tests for the Swarm Command MCP server (WSGI, stdlib only).

The WSGI ``app`` is exercised DIRECTLY: environ dicts are built by hand
and the response is captured with a start_response closure — no sockets,
no network.
"""

import io
import json
import os

import pytest

from sincor2.swarm_command_board import BoardStore
from sincor2.swarm_command_mcp import app

TOKEN = "test-bearer-token-123"


@pytest.fixture()
def board_path(tmp_path, monkeypatch):
    path = str(tmp_path / "board.json")
    monkeypatch.setenv("SWARM_BOARD_PATH", path)
    monkeypatch.setenv("SWARM_MCP_BEARER_TOKEN", TOKEN)
    monkeypatch.delenv("SWARM_MCP_CORS_ORIGINS", raising=False)
    return path


def _post_environ(body, token=TOKEN, content_type="application/json", origin=None):
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    env = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/mcp",
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
    }
    if token is not None:
        env["HTTP_AUTHORIZATION"] = "Bearer %s" % token
    if origin:
        env["HTTP_ORIGIN"] = origin
    return env


def _rpc(method, params=None, msg_id=1):
    req = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        req["params"] = params
    return json.dumps(req)


def call_app(environ):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app(environ, start_response))
    payload = None
    if body:
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            payload = None
    return captured["status"], captured["headers"], body, payload


def _mcp(board_path_unused, method, params=None, msg_id=1, token=TOKEN, body=None):
    raw = body if body is not None else _rpc(method, params, msg_id)
    return call_app(_post_environ(raw, token=token))


def _tool(board_path_fixture_value, name, arguments):
    return _mcp(board_path_fixture_value, "tools/call", {"name": name, "arguments": arguments})


def _tool_result_text(payload):
    return json.loads(payload["result"]["content"][0]["text"])


# -- auth ------------------------------------------------------------------


def test_missing_token_401(board_path):
    status, headers, body, payload = _mcp(board_path, "ping", token=None)
    assert status.startswith("401")
    assert payload["error"] == "unauthorized"


def test_wrong_token_401(board_path):
    status, headers, body, payload = _mcp(board_path, "ping", token="wrong")
    assert status.startswith("401")


def test_empty_token_config_503(board_path, monkeypatch):
    monkeypatch.setenv("SWARM_MCP_BEARER_TOKEN", "")
    status, headers, body, payload = _mcp(board_path, "ping")
    assert status.startswith("503")
    assert payload["error"] == "not configured"


def test_unset_token_config_503(board_path, monkeypatch):
    monkeypatch.delenv("SWARM_MCP_BEARER_TOKEN", raising=False)
    status, headers, body, payload = _mcp(board_path, "ping", token=None)
    assert status.startswith("503")
    assert payload["error"] == "not configured"


def test_scheme_not_bearer_401(board_path):
    env = _post_environ(_rpc("ping"))
    env["HTTP_AUTHORIZATION"] = "Basic abcdef"
    status, headers, body, payload = call_app(env)
    assert status.startswith("401")


# -- healthz -----------------------------------------------------------------


def test_healthz_no_auth(board_path):
    status, headers, body, payload = call_app(
        {"REQUEST_METHOD": "GET", "PATH_INFO": "/healthz"}
    )
    assert status.startswith("200")
    assert payload == {"ok": True}


def test_healthz_with_cors_unset_has_no_cors_headers(board_path):
    status, headers, body, payload = call_app(
        {"REQUEST_METHOD": "GET", "PATH_INFO": "/healthz", "HTTP_ORIGIN": "https://x.example"}
    )
    assert "Access-Control-Allow-Origin" not in headers


# -- JSON-RPC plumbing --------------------------------------------------------


def test_initialize_shape(board_path):
    status, headers, body, payload = _mcp(board_path, "initialize")
    assert status.startswith("200")
    result = payload["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"] == {"name": "swarm-command", "version": "0.1.0"}


def test_notifications_initialized_202_empty(board_path):
    env = _post_environ(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    status, headers, body, payload = call_app(env)
    assert status.startswith("202")
    assert body == b""


def test_ping(board_path):
    status, headers, body, payload = _mcp(board_path, "ping")
    assert payload["result"] == {"pong": True}


def test_tools_list_has_all_ten(board_path):
    status, headers, body, payload = _mcp(board_path, "tools/list")
    names = [t["name"] for t in payload["result"]["tools"]]
    assert names == [
        "board_lanes_read",
        "board_card_add",
        "board_card_move",
        "board_card_comment",
        "stack_propose",
        "stack_approve",
        "dispatch_assign",
        "results_loopback",
        "discussion_post",
        "discussion_read",
    ]
    for tool in payload["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object"


def test_malformed_json_parse_error(board_path):
    status, headers, body, payload = _mcp(board_path, None, body=b"{not json")
    assert payload["error"]["code"] == -32700


def test_unknown_method(board_path):
    status, headers, body, payload = _mcp(board_path, "frobnicate")
    assert payload["error"]["code"] == -32601


def test_bad_params_not_object(board_path):
    status, headers, body, payload = _mcp(
        board_path, None, body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": [1]})
    )
    assert payload["error"]["code"] == -32602


def test_unknown_tool(board_path):
    status, headers, body, payload = _tool(board_path, "nope", {})
    assert payload["error"]["code"] == -32602


def test_wrong_content_type_rejected(board_path):
    status, headers, body, payload = call_app(
        _post_environ(_rpc("ping"), content_type="text/plain")
    )
    assert status.startswith("415")


# -- board tools ---------------------------------------------------------------


def test_card_add_move_comment_roundtrip(board_path):
    status, h, b, payload = _tool(
        board_path, "board_card_add",
        {"lane": "brainstorm", "title": "T1", "body": "B1", "owner": "Muse"},
    )
    card = _tool_result_text(payload)
    assert len(card["id"]) == 12
    assert card["lane"] == "brainstorm"

    status, h, b, payload = _tool(
        board_path, "board_card_move", {"card_id": card["id"], "to_lane": "in_design"}
    )
    moved = _tool_result_text(payload)
    assert moved["lane"] == "in_design"

    status, h, b, payload = _tool(
        board_path, "board_card_comment",
        {"card_id": card["id"], "author": "Manus", "text": "looks good"},
    )
    commented = _tool_result_text(payload)
    assert commented["comments"][-1]["author"] == "Manus"
    assert commented["comments"][-1]["text"] == "looks good"

    status, h, b, payload = _tool(board_path, "board_lanes_read", {})
    lanes = _tool_result_text(payload)
    assert set(lanes.keys()) == {"brainstorm", "in_design", "approved", "parked"}
    assert [c["id"] for c in lanes["in_design"]] == [card["id"]]


def test_invalid_lane_rejected(board_path):
    status, h, b, payload = _tool(
        board_path, "board_card_add",
        {"lane": "limbo", "title": "T", "body": "B", "owner": "Muse"},
    )
    assert payload["error"]["code"] == -32602
    assert "unknown lane" in payload["error"]["message"]


def test_invalid_move_lane_rejected(board_path):
    status, h, b, payload = _tool(
        board_path, "board_card_add",
        {"lane": "brainstorm", "title": "T", "body": "B", "owner": "Muse"},
    )
    card = _tool_result_text(payload)
    status, h, b, payload = _tool(
        board_path, "board_card_move", {"card_id": card["id"], "to_lane": "nowhere"}
    )
    assert payload["error"]["code"] == -32602


def test_missing_required_arg_32602(board_path):
    status, h, b, payload = _tool(
        board_path, "board_card_add", {"lane": "brainstorm", "title": "T"}
    )
    assert payload["error"]["code"] == -32602


def test_content_block_shape(board_path):
    status, h, b, payload = _tool(board_path, "board_lanes_read", {})
    content = payload["result"]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"
    json.loads(content[0]["text"])  # must be valid JSON


# -- stack / dispatch loop ------------------------------------------------------


def _items():
    return [
        {"title": "Build MCP", "detail": "streamable http", "owner": "Muse"},
        {"title": "Review", "detail": "adversarial", "owner": "Manus"},
    ]


def test_full_loop_propose_approve_dispatch_loopback(board_path):
    status, h, b, payload = _tool(
        board_path, "stack_propose", {"items": _items(), "proposed_by": "Muse"}
    )
    stack = _tool_result_text(payload)
    assert stack["status"] == "proposed"

    status, h, b, payload = _tool(
        board_path, "stack_approve", {"stack_id": stack["id"], "approver": "Founder"}
    )
    approved = _tool_result_text(payload)
    assert approved["status"] == "approved"
    assert approved["approved_by"] == "Founder"

    # re-approve must fail (one-shot)
    status, h, b, payload = _tool(
        board_path, "stack_approve", {"stack_id": stack["id"], "approver": "Founder"}
    )
    assert payload["error"]["code"] == -32602
    assert "one-shot" in payload["error"]["message"]

    status, h, b, payload = _tool(
        board_path, "dispatch_assign", {"stack_id": stack["id"]}
    )
    assignments = _tool_result_text(payload)
    assert len(assignments) == 2
    assert all(a["status"] == "assigned" for a in assignments)
    assert all(a["stack_id"] == stack["id"] for a in assignments)

    first = assignments[0]
    status, h, b, payload = _tool(
        board_path, "results_loopback", {"assignment_id": first["id"], "result": "done ok"}
    )
    done = _tool_result_text(payload)
    assert done["status"] == "done"
    assert done["result"] == "done ok"


def test_dispatch_requires_approved(board_path):
    status, h, b, payload = _tool(
        board_path, "stack_propose", {"items": _items(), "proposed_by": "Muse"}
    )
    stack = _tool_result_text(payload)
    status, h, b, payload = _tool(
        board_path, "dispatch_assign", {"stack_id": stack["id"]}
    )
    assert payload["error"]["code"] == -32602
    assert "approved" in payload["error"]["message"]


# -- discussions -----------------------------------------------------------------


def test_discussion_post_and_read(board_path):
    status, h, b, payload = _tool(
        board_path, "discussion_post",
        {"topic": "launch", "author": "Gemini", "text": "hello swarm"},
    )
    thread = _tool_result_text(payload)
    assert thread["topic"] == "launch"
    assert thread["posts"][0]["text"] == "hello swarm"

    status, h, b, payload = _tool(
        board_path, "discussion_read", {"topic": "launch"}
    )
    thread = _tool_result_text(payload)
    assert len(thread["posts"]) == 1

    status, h, b, payload = _tool(board_path, "discussion_read", {})
    all_threads = _tool_result_text(payload)
    assert "launch" in all_threads


# -- persistence --------------------------------------------------------------------


def test_two_store_instances_share_data(board_path):
    a = BoardStore(board_path)
    b = BoardStore(board_path)
    card = a.card_add("brainstorm", "T", "B", "Muse")
    lanes = b.lanes_read()
    assert [c["id"] for c in lanes["brainstorm"]] == [card["id"]]


# -- CORS -------------------------------------------------------------------------------


def test_cors_preflight_when_configured(board_path, monkeypatch):
    monkeypatch.setenv("SWARM_MCP_CORS_ORIGINS", "https://manus.example,https://gemini.example")
    env = {
        "REQUEST_METHOD": "OPTIONS",
        "PATH_INFO": "/mcp",
        "HTTP_ORIGIN": "https://manus.example",
    }
    status, headers, body, payload = call_app(env)
    assert status.startswith("204")
    assert headers["Access-Control-Allow-Origin"] == "https://manus.example"


def test_cors_star_wildcard(board_path, monkeypatch):
    monkeypatch.setenv("SWARM_MCP_CORS_ORIGINS", "*")
    env = {
        "REQUEST_METHOD": "OPTIONS",
        "PATH_INFO": "/mcp",
        "HTTP_ORIGIN": "https://anything.example",
    }
    status, headers, body, payload = call_app(env)
    assert headers["Access-Control-Allow-Origin"] == "*"


def test_cors_unmatched_origin_gets_no_header(board_path, monkeypatch):
    monkeypatch.setenv("SWARM_MCP_CORS_ORIGINS", "https://manus.example")
    env = {
        "REQUEST_METHOD": "OPTIONS",
        "PATH_INFO": "/mcp",
        "HTTP_ORIGIN": "https://evil.example",
    }
    status, headers, body, payload = call_app(env)
    assert "Access-Control-Allow-Origin" not in headers
