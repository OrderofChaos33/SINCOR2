"""Swarm Command MCP server (Streamable HTTP, stdlib only).

Speaks JSON-RPC 2.0 over HTTP POST at ``/mcp`` — the transport Manus 2.0
custom MCP servers and Gemini Spark custom apps consume. Bearer-token
auth on every ``POST /mcp``; fail-closed 503 when no token is configured.

Never touches money paths, auth systems, ledgers, or production.

Configuration:
    SWARM_MCP_BEARER_TOKEN   required; if unset/empty the server refuses
                             every /mcp POST with 503 (never open)
    SWARM_BOARD_PATH         board JSON file (default data/swarm_board.json)
    SWARM_MCP_PORT           local dev port for __main__ (default 8899)
    SWARM_MCP_CORS_ORIGINS   comma-separated; exact-match or "*"
    SWARM_MCP_ALLOWED_HOSTS  comma-separated extra Host values allowed on
                             POST /mcp (host or host:port)
    SWARM_MCP_ALLOWED_ORIGINS comma-separated extra Origin values allowed
                             on POST /mcp (exact origin, e.g.
                             https://manus.example)

DNS-rebinding protection (dev-watch item 305, CVE-2026-61546):
    This is the only HTTP MCP listener in the repo (the discovery server,
    ``sincor2.mcp_server``, is stdio-only). Every ``POST /mcp`` is checked
    for Host and Origin allowlisting BEFORE authentication is evaluated
    for dispatch and before any JSON-RPC method — including
    ``tools/list`` — is served. By default only loopback Host/Origin
    values (127.0.0.1, localhost, ::1, any port) are allowed; a request
    carrying no Host/Origin (a non-browser, server-to-server MCP client)
    is not a browser rebinding vector and is left to bearer auth.
    This protection is NEVER disabled globally. A tunnel/proxy exception
    is scoped to the tunneled host/origin only, by naming that exact
    host in SWARM_MCP_ALLOWED_HOSTS and/or that exact origin in
    SWARM_MCP_ALLOWED_ORIGINS (or SWARM_MCP_CORS_ORIGINS) — a "*" CORS
    value reflects CORS headers only and does NOT allowlist a foreign
    Origin for tool dispatch.

Run locally:  python -m sincor2.swarm_command_mcp
"""

from __future__ import annotations

import hmac
import json
import os
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlparse

from sincor2.swarm_command_board import BoardStore, LANES

SERVER_NAME = "swarm-command"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-06-18"

_JSON = "application/json"

# -- request/tool-input limits (dev-watch item 269: MCP SDK Oct-20 advisories)
#
# Defaults left permissive are a finding; these protections are
# explicitly ENABLED here (not default-off):
#  * bodies larger than MAX_REQUEST_BODY_BYTES are rejected with 413
#    BEFORE they are buffered  (advisory b: unbounded request bodies,
#    High 7.5, fixed in SDK 1.30.1)
#  * tool arguments are bounded by MAX_TOOL_INPUT_ELEMENTS elements and
#    MAX_TOOL_INPUT_DEPTH nesting (advisory a: validation memory
#    exhaustion, High 7.5, fixed by default-off maxToolInputElements)
#  * JSON-RPC batches are rejected outright, never partially executed
MAX_REQUEST_BODY_BYTES = 1_048_576  # 1 MiB
MAX_TOOL_INPUT_ELEMENTS = 1_000
MAX_TOOL_INPUT_DEPTH = 32

# -- tool catalogue -------------------------------------------------------
# Each entry: name -> (required args, schema, handler-name on the dispatcher)


def _tool_defs() -> List[Dict[str, Any]]:
    def obj(**props):
        return {"type": "object", "properties": props}

    required = lambda *names: list(names)

    lane_desc = "lane name: one of " + ", ".join(LANES)
    return [
        {
            "name": "board_lanes_read",
            "annotations": {"title": "Board lanes read", "read_only_hint": True},
            "description": "Read the Swarm Command board: cards grouped by lane.",
            "inputSchema": obj(),
        },
        {
            "name": "board_card_add",
            "annotations": {
                "title": "Board card add",
                "read_only_hint": False,
                "destructive_hint": False,   # appends a card; deletes nothing
                "idempotent_hint": False,    # each call mints a new card id
                "open_world_hint": False,    # closed world: the local swarm board file only
            },
            "description": "Add a card to a lane.",
            "inputSchema": {
                **obj(
                    lane={"type": "string", "description": lane_desc},
                    title={"type": "string"},
                    body={"type": "string"},
                    owner={"type": "string", "description": "Founder, Muse, Manus, Gemini, or any free name"},
                ),
                "required": required("lane", "title", "body", "owner"),
            },
        },
        {
            "name": "board_card_move",
            "annotations": {
                "title": "Board card move",
                "read_only_hint": False,
                "destructive_hint": False,   # relocates; deletes nothing
                "idempotent_hint": True,     # repeat with identical args leaves the card in the same lane
                "open_world_hint": False,
            },
            "description": "Move a card to another lane.",
            "inputSchema": {
                **obj(
                    card_id={"type": "string"},
                    to_lane={"type": "string", "description": lane_desc},
                ),
                "required": required("card_id", "to_lane"),
            },
        },
        {
            "name": "board_card_comment",
            "annotations": {
                "title": "Board card comment",
                "read_only_hint": False,
                "destructive_hint": False,   # appends a comment; deletes nothing
                "idempotent_hint": False,    # each call appends another comment
                "open_world_hint": False,
            },
            "description": "Append a comment to a card.",
            "inputSchema": {
                **obj(
                    card_id={"type": "string"},
                    author={"type": "string"},
                    text={"type": "string"},
                ),
                "required": required("card_id", "author", "text"),
            },
        },
        {
            "name": "stack_propose",
            "annotations": {
                "title": "Stack propose",
                "read_only_hint": False,
                "destructive_hint": False,   # appends a proposal; deletes nothing
                "idempotent_hint": False,    # each call mints a new stack id
                "open_world_hint": False,
            },
            "description": "Propose a daily work stack of items.",
            "inputSchema": {
                **obj(
                    items={
                        "type": "array",
                        "description": "non-empty list of {title, detail, owner}",
                    },
                    proposed_by={"type": "string"},
                ),
                "required": required("items", "proposed_by"),
            },
        },
        {
            "name": "stack_approve",
            "annotations": {
                "title": "Stack approve",
                "read_only_hint": False,
                "destructive_hint": False,   # flips status; deletes nothing
                "idempotent_hint": True,     # one-shot: a repeat is rejected, no additional effect
                "open_world_hint": False,
            },
            "description": "Approve a proposed stack (one-shot).",
            "inputSchema": {
                **obj(
                    stack_id={"type": "string"},
                    approver={"type": "string"},
                ),
                "required": required("stack_id", "approver"),
            },
        },
        {
            "name": "dispatch_assign",
            "annotations": {
                "title": "Dispatch assign",
                "read_only_hint": False,
                "destructive_hint": False,   # creates assignments; deletes nothing
                "idempotent_hint": False,    # each call mints fresh assignment ids
                "open_world_hint": False,
            },
            "description": "Dispatch an approved stack: one assignment per item.",
            "inputSchema": {
                **obj(stack_id={"type": "string"}),
                "required": required("stack_id"),
            },
        },
        {
            "name": "results_loopback",
            "annotations": {
                "title": "Results loopback",
                "read_only_hint": False,
                "destructive_hint": False,   # marks done; deletes nothing
                "idempotent_hint": True,     # repeat with identical args leaves identical state
                "open_world_hint": False,
            },
            "description": "Record a result on an assignment and mark it done.",
            "inputSchema": {
                **obj(
                    assignment_id={"type": "string"},
                    result={"type": "string"},
                ),
                "required": required("assignment_id", "result"),
            },
        },
        {
            "name": "discussion_post",
            "annotations": {
                "title": "Discussion post",
                "read_only_hint": False,
                "destructive_hint": False,   # appends a post; deletes nothing
                "idempotent_hint": False,    # each call appends another post
                "open_world_hint": False,
            },
            "description": "Post to a discussion thread.",
            "inputSchema": {
                **obj(
                    topic={"type": "string"},
                    author={"type": "string"},
                    text={"type": "string"},
                ),
                "required": required("topic", "author", "text"),
            },
        },
        {
            "name": "discussion_read",
            "annotations": {"title": "Discussion read", "read_only_hint": True},
            "description": "Read a discussion thread (all threads when topic omitted).",
            "inputSchema": obj(topic={"type": "string"}),
        },
    ]


def _store() -> BoardStore:
    return BoardStore()


def _dispatch_tool(name: str, args: Dict[str, Any], store: BoardStore) -> Any:
    if name == "board_lanes_read":
        return store.lanes_read()
    if name == "board_card_add":
        return store.card_add(args["lane"], args["title"], args["body"], args["owner"])
    if name == "board_card_move":
        return store.card_move(args["card_id"], args["to_lane"])
    if name == "board_card_comment":
        return store.card_comment(args["card_id"], args["author"], args["text"])
    if name == "stack_propose":
        return store.stack_propose(args["items"], args["proposed_by"])
    if name == "stack_approve":
        return store.stack_approve(args["stack_id"], args["approver"])
    if name == "dispatch_assign":
        return store.dispatch_assign(args["stack_id"])
    if name == "results_loopback":
        return store.results_loopback(args["assignment_id"], args["result"])
    if name == "discussion_post":
        return store.discussion_post(args["topic"], args["author"], args["text"])
    if name == "discussion_read":
        return store.discussion_read(args.get("topic"))
    raise ValueError("unknown tool %r" % (name,))


# -- HTTP plumbing --------------------------------------------------------


def _configured_token() -> str:
    return os.environ.get("SWARM_MCP_BEARER_TOKEN", "")


def _cors_origins() -> List[str]:
    raw = os.environ.get("SWARM_MCP_CORS_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


def _cors_origin_for(request_origin: str) -> str | None:
    origins = _cors_origins()
    if not origins:
        return None
    if "*" in origins:
        return "*"
    if request_origin and request_origin in origins:
        return request_origin
    return None


# -- DNS-rebinding protection: Host / Origin allowlists (item 305) ---------
#
# MCP TransportSecuritySettings equivalent for this stdlib server:
# DNS-rebinding protection is conceptually always ON here — there is
# deliberately no setting that turns these checks off.

LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})


def _allowed_hosts() -> List[str]:
    raw = os.environ.get("SWARM_MCP_ALLOWED_HOSTS", "")
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def _allowed_origins() -> List[str]:
    """Exact origins allowlisted for dispatch (never "*").

    Sources: SWARM_MCP_ALLOWED_ORIGINS plus the exact-match entries of
    SWARM_MCP_CORS_ORIGINS. A "*" CORS entry is a CORS-reflection setting
    only; it must not become a global rebinding exception.
    """
    raw = os.environ.get("SWARM_MCP_ALLOWED_ORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    origins.extend(o for o in _cors_origins() if o != "*")
    return origins


def _hostname_of(value: str) -> str:
    """Hostname (lowercase, no port/brackets) from a Host or origin-ish value."""
    v = (value or "").strip().lower()
    if not v:
        return ""
    if "://" in v:
        return (urlparse(v).hostname or "").lower()
    if v.startswith("["):  # [::1]:8899
        end = v.find("]")
        return v[1:end] if end > 0 else v.strip("[]")
    return v.split(":", 1)[0].rstrip(".")


def _normalised_host_header(host_header: str) -> str:
    v = (host_header or "").strip().lower()
    if "://" in v:
        v = v.split("://", 1)[1]
    return v.rstrip(".")


def _host_allowed(host_header: str) -> bool:
    """True when a POST /mcp Host header may be served.

    Absent Host: allowed — real HTTP/1.1 clients (and every rebinding
    browser request) always send Host; absence only occurs in direct
    WSGI/non-browser invocation, which bearer auth still gates.
    Present Host: loopback, an exact SWARM_MCP_ALLOWED_HOSTS entry
    (host or host:port), or the hostname of an allowlisted origin.
    """
    if not (host_header or "").strip():
        return True
    hostname = _hostname_of(host_header)
    if hostname in LOOPBACK_HOSTNAMES:
        return True
    normalised = _normalised_host_header(host_header)
    for entry in _allowed_hosts():
        if normalised == _normalised_host_header(entry) or hostname == _hostname_of(entry):
            return True
    for origin in _allowed_origins():
        if hostname and hostname == _hostname_of(origin):
            return True
    return False


def _origin_allowed(origin: str) -> bool:
    """True when a POST /mcp Origin header may be served.

    Absent Origin: allowed (non-browser MCP clients send none).
    Present Origin: exact allowlisted origin, a loopback origin, or an
    origin whose hostname is an allowlisted (tunneled) host. Everything
    else — including the opaque ``null`` origin — is foreign.
    """
    if not (origin or "").strip():
        return True
    normalised = origin.strip().rstrip("/").lower()
    if normalised == "null":
        return False
    for allowed in _allowed_origins():
        if normalised == allowed.strip().rstrip("/").lower():
            return True
    hostname = _hostname_of(origin)
    if hostname in LOOPBACK_HOSTNAMES:
        return True
    for entry in _allowed_hosts():
        if hostname and hostname == _hostname_of(entry):
            return True
    return False


def _json_response(status: str, payload: Any, extra: List[Tuple[str, str]] = ()):
    body = json.dumps(payload).encode("utf-8")
    headers = [("Content-Type", _JSON), ("Content-Length", str(len(body)))]
    headers.extend(extra)
    return status, headers, [body]


def _error(id_: Any, code: int, message: str):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _validate_tool_input_size(arguments: Any) -> None:
    """Bound tool arguments by element count and nesting depth.

    Raises _JsonRpcParamsError (maps to JSON-RPC -32602) when the arguments
    exceed MAX_TOOL_INPUT_ELEMENTS elements or MAX_TOOL_INPUT_DEPTH nesting.
    Every dict, list, scalar, and dict key counts as one element; the walk
    is iterative so a hostile payload cannot blow the Python call stack.
    """
    count = 0
    stack = [(arguments, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > MAX_TOOL_INPUT_DEPTH:
            raise _JsonRpcParamsError(
                "arguments exceed maximum nesting depth (%d)"
                % MAX_TOOL_INPUT_DEPTH
            )
        count += 1  # this node
        if isinstance(value, dict):
            count += len(value)  # keys
            if count > MAX_TOOL_INPUT_ELEMENTS:
                break
            stack.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend((v, depth + 1) for v in value)
        if count > MAX_TOOL_INPUT_ELEMENTS:
            break
    if count > MAX_TOOL_INPUT_ELEMENTS:
        raise _JsonRpcParamsError(
            "arguments exceed maximum input size (%d elements)"
            % MAX_TOOL_INPUT_ELEMENTS
        )


# -- the WSGI app ----------------------------------------------------------


def app(environ, start_response):
    """WSGI callable. Routes: POST /mcp, GET /healthz, OPTIONS /mcp."""
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/") or "/"
    request_origin = environ.get("HTTP_ORIGIN", "")

    cors_headers: List[Tuple[str, str]] = []
    matched = _cors_origin_for(request_origin)
    if matched:
        cors_headers.append(("Access-Control-Allow-Origin", matched))
        cors_headers.append(("Vary", "Origin"))

    if method == "OPTIONS" and path == "/mcp":
        if matched:
            cors_headers += [
                ("Access-Control-Allow-Methods", "POST, OPTIONS"),
                ("Access-Control-Allow-Headers", "Authorization, Content-Type"),
                ("Access-Control-Max-Age", "86400"),
            ]
        status, headers, body = _json_response("204 No Content", {}, cors_headers)
        start_response(status, headers)
        return body

    if method == "GET" and path == "/healthz":
        status, headers, body = _json_response("200 OK", {"ok": True}, cors_headers)
        start_response(status, headers)
        return body

    if not (method == "POST" and path == "/mcp"):
        status, headers, body = _json_response("404 Not Found", {"error": "not found"}, cors_headers)
        start_response(status, headers)
        return body

    # -- /mcp POST: DNS-rebinding Host/Origin allowlist (item 305) -----------
    # Enforced before content-type, body, auth and ANY JSON-RPC dispatch,
    # so a foreign Host/Origin is refused before tools/list (or any other
    # method) is served — even to a caller holding a valid bearer token.
    # No CORS headers are reflected on a refusal.
    if not _host_allowed(environ.get("HTTP_HOST", "")):
        status, headers, body = _json_response(
            "403 Forbidden", {"error": "forbidden host"}, []
        )
        start_response(status, headers)
        return body
    if not _origin_allowed(request_origin):
        status, headers, body = _json_response(
            "403 Forbidden", {"error": "forbidden origin"}, []
        )
        start_response(status, headers)
        return body

    # -- /mcp POST: content type --------------------------------------------
    ctype = environ.get("CONTENT_TYPE", "")
    if "application/json" not in ctype:
        status, headers, body = _json_response(
            "415 Unsupported Media Type",
            {"error": "content type must be application/json"},
            cors_headers,
        )
        start_response(status, headers)
        return body

    # -- /mcp POST: request body size cap (dev-watch 269) --------------------
    # Reject BEFORE buffering: declared or actual size over the cap gets
    # 413 without ever reading the full body into memory. The read is
    # capped at cap+1 bytes so a spoofed/missing Content-Length cannot
    # bypass the limit either.
    try:
        length = int(environ.get("CONTENT_LENGTH", "") or 0)
    except (TypeError, ValueError):
        length = 0
    if length > MAX_REQUEST_BODY_BYTES:
        status, headers, body = _json_response(
            "413 Content Too Large",
            {"error": "request body too large (max %d bytes)"
                      % MAX_REQUEST_BODY_BYTES},
            cors_headers,
        )
        start_response(status, headers)
        return body

    # -- /mcp POST: auth -----------------------------------------------------
    expected = _configured_token()
    if not expected:
        status, headers, body = _json_response(
            "503 Service Unavailable", {"error": "not configured"}, cors_headers
        )
        start_response(status, headers)
        return body
    auth = environ.get("HTTP_AUTHORIZATION", "")
    scheme, _, supplied = auth.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied, expected):
        status, headers, body = _json_response(
            "401 Unauthorized", {"error": "unauthorized"}, cors_headers
        )
        start_response(status, headers)
        return body

    # -- /mcp POST: JSON-RPC -------------------------------------------------
    # (length was parsed and capped against MAX_REQUEST_BODY_BYTES above)
    to_read = MAX_REQUEST_BODY_BYTES + 1 if length <= 0 else min(
        length, MAX_REQUEST_BODY_BYTES + 1)
    raw = environ.get("wsgi.input").read(to_read)
    if len(raw) > MAX_REQUEST_BODY_BYTES:
        status, headers, body = _json_response(
            "413 Content Too Large",
            {"error": "request body too large (max %d bytes)"
                      % MAX_REQUEST_BODY_BYTES},
            cors_headers,
        )
        start_response(status, headers)
        return body
    try:
        request = json.loads(raw.decode("utf-8") if raw else "")
    except (ValueError, UnicodeDecodeError):
        status, headers, body = _json_response(
            "200 OK", _error(None, -32700, "Parse error"), cors_headers
        )
        start_response(status, headers)
        return body

    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        status, headers, body = _json_response(
            "200 OK", _error(None, -32600, "Invalid Request"), cors_headers
        )
        start_response(status, headers)
        return body

    method_name = request.get("method")
    msg_id = request.get("id")
    params = request.get("params") or {}
    if not isinstance(params, dict):
        status, headers, body = _json_response(
            "200 OK", _error(msg_id, -32602, "params must be an object"), cors_headers
        )
        start_response(status, headers)
        return body

    if method_name == "notifications/initialized":
        start_response("202 Accepted", [("Content-Length", "0")] + cors_headers)
        return [b""]

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    store = None

    try:
        if method_name == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
            status, headers, body = _json_response("200 OK", ok(result), cors_headers)
            start_response(status, headers)
            return body

        if method_name == "ping":
            status, headers, body = _json_response("200 OK", ok({"pong": True}), cors_headers)
            start_response(status, headers)
            return body

        if method_name == "tools/list":
            status, headers, body = _json_response(
                "200 OK", ok({"tools": _tool_defs()}), cors_headers
            )
            start_response(status, headers)
            return body

        if method_name == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments", {})
            if not isinstance(args, dict):
                raise _JsonRpcParamsError("arguments must be an object")
            _validate_tool_input_size(args)
            store = _store()
            known = {t["name"] for t in _tool_defs()}
            if tool_name not in known:
                raise _JsonRpcParamsError("unknown tool %r" % (tool_name,))
            result = _dispatch_tool(tool_name, args, store)
            content = [{"type": "text", "text": json.dumps(result)}]
            status, headers, body = _json_response(
                "200 OK", ok({"content": content}), cors_headers
            )
            start_response(status, headers)
            return body

        # unknown method
        status, headers, body = _json_response(
            "200 OK", _error(msg_id, -32601, "Method not found: %r" % (method_name,)), cors_headers
        )
        start_response(status, headers)
        return body

    except _JsonRpcParamsError as e:
        status, headers, body = _json_response(
            "200 OK", _error(msg_id, -32602, str(e)), cors_headers
        )
        start_response(status, headers)
        return body
    except ValueError as e:
        # BoardStore validation / missing required args
        status, headers, body = _json_response(
            "200 OK", _error(msg_id, -32602, str(e)), cors_headers
        )
        start_response(status, headers)
        return body
    except KeyError as e:
        status, headers, body = _json_response(
            "200 OK", _error(msg_id, -32602, "missing required argument %s" % (e,)), cors_headers
        )
        start_response(status, headers)
        return body
    except Exception as e:  # never leak tracebacks; code error, not client error
        status, headers, body = _json_response(
            "200 OK", _error(msg_id, -32603, "Internal error"), cors_headers
        )
        start_response(status, headers)
        return body


class _JsonRpcParamsError(ValueError):
    """Invalid params / unknown tool: maps to -32602."""


def _main() -> None:
    from wsgiref.simple_server import make_server

    port = int(os.environ.get("SWARM_MCP_PORT", "8899"))
    httpd = make_server("127.0.0.1", port, app)
    print("swarm-command MCP on http://127.0.0.1:%d" % port, flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    _main()
