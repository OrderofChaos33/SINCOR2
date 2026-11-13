"""Regression tests: dev-watch item 269 — MCP SDK Oct-20 advisory batch.

Covers the Oct-20 MCP advisories' themes as they apply to our stdlib-only
Swarm Command MCP server (src/sincor2/swarm_command_mcp.py):

  (a) validation memory exhaustion (High 7.5)  -> default-off
      ``maxToolInputElements`` must be explicitly enabled here: per-tool
      arguments are now bounded by MAX_TOOL_INPUT_ELEMENTS (elements) and
      MAX_TOOL_INPUT_DEPTH (nesting depth).
  (b) unbounded request bodies/batches (High 7.5) -> fixed in SDK 1.30.1:
      POST /mcp bodies are now capped at MAX_REQUEST_BODY_BYTES (413 when
      exceeded); JSON-RPC batches are rejected outright (never partially
      executed).

The WSGI ``app`` is exercised directly with hand-built environ dicts —
no sockets, no network.
"""

import io
import json
import os

import pytest

from sincor2 import swarm_command_mcp
from sincor2.swarm_command_board import BoardStore
from sincor2.swarm_command_mcp import app

TOKEN="test-bearer-token"

MAX_BODY = swarm_command_mcp.MAX_REQUEST_BODY_BYTES
MAX_ELEMENTS = swarm_command_mcp.MAX_TOOL_INPUT_ELEMENTS
MAX_DEPTH = swarm_command_mcp.MAX_TOOL_INPUT_DEPTH


@pytest.fixture()
def board_path(tmp_path, monkeypatch):
    path = str(tmp_path / "board.json")
    monkeypatch.setenv("SWARM_BOARD_PATH", path)
    monkeypatch.setenv("SWARM_MCP_BEARER_TOKEN", TOKEN)
    monkeypatch.delenv("SWARM_MCP_CORS_ORIGINS", raising=False)
    return path


def _post_environ(body_bytes, token=TOKEN, content_type="application/json",
                  omit_content_length=False):
    env = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/mcp",
        "CONTENT_TYPE": content_type,
        "wsgi.input": io.BytesIO(body_bytes),
    }
    if not omit_content_length:
        env["CONTENT_LENGTH"] = str(len(body_bytes))
    if token is not None:
        env["HTTP_AUTHORIZATION"] = "Bearer %s" % token
    return env


def _rpc(method, params=None, msg_id=1):
    req = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        req["params"] = params
    return json.dumps(req).encode("utf-8")


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


def _mcp(board_path_unused, method, params=None, msg_id=1, token=TOKEN,
          raw_body=None, omit_content_length=False):
    raw = raw_body if raw_body is not None else _rpc(method, params, msg_id)
    return call_app(_post_environ(raw, token=TOKEN,
                                  omit_content_length=omit_content_length))


# -- request body size ------------------------------------------------------


def test_oversized_request_body_rejected_413(board_path):
    """A body over MAX_REQUEST_BODY_BYTES is rejected with 413 before parsing."""
    big = _rpc("ping") + b" " * (MAX_BODY + 1)
    status, _headers, _body, _payload = _mcp(
        board_path, "ping", raw_body=big)
    assert status.startswith("413"), status


def test_body_one_byte_over_limit_rejected(board_path):
    pad = b" " * (MAX_BODY + 1 - len(_rpc("ping")))
    status, _h, _b, _p = _mcp(board_path, "ping", raw_body=_rpc("ping") + pad)
    assert status.startswith("413"), status


def test_body_exactly_at_limit_accepted(board_path):
    """The boundary itself is accepted: exactly MAX bytes -> 200, not 413."""
    pad = b" " * (MAX_BODY - len(_rpc("ping")))
    raw = _rpc("ping") + pad
    assert len(raw) == MAX_BODY
    status, _h, _b, payload = _mcp(board_path, "ping", raw_body=raw)
    assert status.startswith("200"), status
    assert payload["result"] == {"pong": True}


def test_missing_content_length_large_body_rejected(board_path):
    """Spoofed/missing Content-Length must not bypass the cap."""
    big = _rpc("ping") + b" " * (MAX_BODY + 100)
    status, _h, _b, _p = _mcp(board_path, "ping", raw_body=big,
                              omit_content_length=True)
    assert status.startswith("413"), status


# -- tool input element limits ----------------------------------------------


def _tool_call(board_path, name, arguments, msg_id=1):
    return _mcp(board_path, "tools/call",
                {"name": name, "arguments": arguments}, msg_id=msg_id)


def test_oversized_tool_input_rejected(board_path):
    """arguments with more than MAX_TOOL_INPUT_ELEMENTS elements -> -32602.

    Stays well under the body cap so only the element limit fires.
    """
    args = {"topic": "t", "author": "a",
            "text": "x", "pad": ["x"] * MAX_ELEMENTS}
    status, _h, _b, payload = _tool_call(board_path, "discussion_post", args)
    assert status.startswith("200"), status
    assert payload["error"]["code"] == -32602, payload
    assert "maximum input size" in payload["error"]["message"].lower()


def test_tool_input_at_element_boundary_accepted(board_path):
    """Exactly MAX_TOOL_INPUT_ELEMENTS elements is accepted (boundary)."""
    # Element counting: 1 (root dict) + 4 (keys) + 3 (scalar values) +
    # 1 (the list) + K items  == MAX_ELEMENTS  =>  K = MAX - 9.
    k = MAX_ELEMENTS - 9
    args = {"topic": "t", "author": "a", "text": "x", "pad": ["x"] * k}
    status, _h, _b, payload = _tool_call(board_path, "discussion_post", args)
    assert status.startswith("200"), status
    assert "error" not in payload, payload
    assert payload["result"]["content"][0]["type"] == "text"


def test_tool_input_one_over_element_boundary_rejected(board_path):
    k = MAX_ELEMENTS - 8  # one element over the boundary
    args = {"topic": "t", "author": "a", "text": "x", "pad": ["x"] * k}
    status, _h, _b, payload = _tool_call(board_path, "discussion_post", args)
    assert status.startswith("200"), status
    assert payload["error"]["code"] == -32602, payload


def test_deeply_nested_tool_input_rejected(board_path):
    """Nesting deeper than MAX_TOOL_INPUT_DEPTH is rejected -> -32602."""
    nested = cur = {}
    for _ in range(MAX_DEPTH + 10):
        nxt = {}
        cur["a"] = nxt
        cur = nxt
    args = {"topic": "t", "author": "a", "text": "x", "pad": nested}
    status, _h, _b, payload = _tool_call(board_path, "discussion_post", args)
    assert status.startswith("200"), status
    assert payload["error"]["code"] == -32602, payload


# -- JSON-RPC batches -------------------------------------------------------


def test_batch_request_rejected_not_executed(board_path):
    """A JSON-RPC batch is rejected wholesale (-32600), never partially run."""
    batch = json.dumps([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "discussion_post",
                    "arguments": {"topic": "batch-should-not-run",
                                  "author": "x", "text": "y"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
    ]).encode("utf-8")
    status, _h, _b, payload = _mcp(board_path, "ping", raw_body=batch)
    assert status.startswith("200"), status
    assert payload["error"]["code"] == -32600, payload
    # Prove no partial execution: the batched tools/call had no effect.
    store = BoardStore()
    assert "batch-should-not-run" not in store.discussion_read()
