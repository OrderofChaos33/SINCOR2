"""Dev-watch item 316 — MCP tool-schema strictness (Skyfire postmortem).

Skyfire's MCP payment tools broke across frameworks because of OPTIONAL /
NULLABLE tool parameters: Anthropic callers passed the literal string
"null" for omitted fields, AutoGen mishandles ``type: ["string", "null"]``
unions, and servers silently coerced whatever arrived.

The SINCOR2 rule enforced here, on BOTH MCP servers (the marketplace
discovery server ``sincor2.mcp_server`` and Swarm Command
``sincor2.swarm_command_mcp``):

1. No nullable union types (``["string", "null"]``, ``anyOf``-with-null)
   in any published tool input schema.
2. Payment-adjacent parameters are always ``required`` — no optional
   payment fields in a published schema.
3. Server-side strict validation on every ``tools/call``: a literal
   string ``"null"``, a JSON ``null``, a missing required field, or a
   wrong-typed value is REJECTED with a loud typed error (-32602 naming
   the field) — never coerced, never silently mis-shaped.

Call-shape tests drive Anthropic-style (string "null"), AutoGen-style
(union-shaped values), and OpenAI-style (correctly typed) arguments:
each must either succeed identically or fail loudly.
"""

from __future__ import annotations

import io
import json

import pytest

import sincor2.mcp_server as ms
from sincor2 import mcp_schema_guard as guard
from sincor2 import swarm_command_mcp as swarm


# ---------------------------------------------------------------------------
# Schema lint — both servers' published schemas
# ---------------------------------------------------------------------------

def _all_tools():
    return list(ms.TOOLS) + list(swarm._tool_defs())


def test_lint_clean_on_both_servers():
    violations = guard.find_schema_violations(_all_tools())
    assert violations == [], violations


def _walk_schemas(node, path="$"):
    """Yield (path, subschema-dict) for every dict in a schema tree."""
    if isinstance(node, dict):
        yield path, node
        for key, val in node.items():
            yield from _walk_schemas(val, f"{path}.{key}")
    elif isinstance(node, list):
        for i, val in enumerate(node):
            yield from _walk_schemas(val, f"{path}[{i}]")


def test_no_nullable_unions_raw_walk():
    """Independent of the guard: no published schema may declare nullability."""
    for tool in _all_tools():
        for path, sub in _walk_schemas(tool["inputSchema"]):
            t = sub.get("type")
            assert not (isinstance(t, list) and "null" in t), (
                f"{tool['name']} {path}: nullable union type {t}")
            assert t != "null", f"{tool['name']} {path}: bare null type"


def test_lint_flags_nullable_union_type():
    bad = [{"name": "t", "inputSchema": {
        "type": "object",
        "properties": {"note": {"type": ["string", "null"]}},
    }}]
    violations = guard.find_schema_violations(bad)
    assert any("note" in v and "null" in v for v in violations), violations


def test_lint_flags_anyof_null_branch():
    bad = [{"name": "t", "inputSchema": {
        "type": "object",
        "properties": {"note": {"anyOf": [{"type": "string"},
                                          {"type": "null"}]}},
    }}]
    violations = guard.find_schema_violations(bad)
    assert any("note" in v for v in violations), violations


def test_lint_flags_optional_payment_field():
    bad = [{"name": "t", "inputSchema": {
        "type": "object",
        "properties": {"bid_axm": {"type": "number"}},
    }}]
    violations = guard.find_schema_violations(bad)
    assert any("bid_axm" in v for v in violations), violations

    good = [{"name": "t", "inputSchema": {
        "type": "object", "required": ["bid_axm"],
        "properties": {"bid_axm": {"type": "number"},
                       "memo": {"type": "string"}},
    }}]
    assert guard.find_schema_violations(good) == []


# ---------------------------------------------------------------------------
# Validator unit tests
# ---------------------------------------------------------------------------

_SCHEMA = {
    "type": "object",
    "required": ["task_id", "bid_axm"],
    "properties": {
        "task_id": {"type": "string"},
        "bid_axm": {"type": "number"},
        "estimated_seconds": {"type": "integer"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "flag": {"type": "boolean"},
    },
}


def test_validator_accepts_valid_and_omitted_optional():
    guard.validate_arguments("t", _SCHEMA, {"task_id": "a", "bid_axm": 1.5})
    guard.validate_arguments("t", _SCHEMA, {
        "task_id": "a", "bid_axm": 2, "estimated_seconds": 30,
        "tags": ["x"], "flag": True,
    })


@pytest.mark.parametrize("args,fragment", [
    ({"task_id": "null", "bid_axm": 1.0}, "task_id"),          # Anthropic "null"
    ({"bid_axm": 1.0}, "task_id"),                             # missing required
    ({"task_id": None, "bid_axm": 1.0}, "task_id"),            # JSON null
    ({"task_id": "a", "bid_axm": "null"}, "bid_axm"),          # "null" for number
    ({"task_id": "a", "bid_axm": "5.5"}, "bid_axm"),           # string, no coercion
    ({"task_id": "a", "bid_axm": 1.0, "estimated_seconds": True},
     "estimated_seconds"),                                     # bool is not int
    ({"task_id": "a", "bid_axm": 1.0, "estimated_seconds": "300"},
     "estimated_seconds"),                                     # string, no coercion
    ({"task_id": "a", "bid_axm": 1.0, "tags": "lead"}, "tags"),  # str not array
    ({"task_id": "a", "bid_axm": 1.0, "tags": ["ok", 5]}, "tags"),  # bad item
    ({"task_id": "a", "bid_axm": 1.0, "tags": ["null"]}, "tags"),   # "null" item
    ({"task_id": ["a"], "bid_axm": 1.0}, "task_id"),           # union-shaped list
])
def test_validator_rejects_loudly(args, fragment):
    with pytest.raises(guard.SchemaValidationError) as excinfo:
        guard.validate_arguments("t", _SCHEMA, args)
    assert fragment in str(excinfo.value)


# ---------------------------------------------------------------------------
# Call shapes — marketplace discovery server (sincor2.mcp_server)
# ---------------------------------------------------------------------------

@pytest.fixture
def stub_upstream(monkeypatch):
    calls = []

    def fake_request(method, path, body=None):
        calls.append((method, path, body))
        return {"ok": True, "status": 200,
                "data": {"price_axm": "1.0", "auctions": [], "cards": []}}

    monkeypatch.setattr(ms, "_request", fake_request)
    return calls


def _call_rpc(name, arguments):
    return ms.handle_message({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })


def _assert_loud_rejection(resp, field):
    assert resp is not None and "error" in resp, resp
    assert resp["error"]["code"] == -32602, resp
    assert field in resp["error"]["message"], resp


def test_discovery_anthropic_string_null_bid_rejected(stub_upstream):
    resp = _call_rpc("submit_bid", {
        "task_id": "tsk_1", "agent_id": "a1", "bid_axm": "null"})
    _assert_loud_rejection(resp, "bid_axm")
    assert stub_upstream == []  # nothing reached the platform


def test_discovery_anthropic_string_null_task_id_rejected(stub_upstream):
    resp = _call_rpc("get_task", {"task_id": "null"})
    _assert_loud_rejection(resp, "task_id")
    assert stub_upstream == []


def test_discovery_autogen_union_shaped_values_rejected(stub_upstream):
    # AutoGen-style: a value shaped by a ["string", "null"] union arrives
    # as a list (or a stringified number) — must fail loudly, not coerce.
    resp = _call_rpc("get_quote", {"skill_id": ["lead-enrichment"]})
    _assert_loud_rejection(resp, "skill_id")

    resp = _call_rpc("list_tasks", {"limit": "50"})
    _assert_loud_rejection(resp, "limit")
    assert stub_upstream == []


def test_discovery_string_bid_never_coerced(stub_upstream):
    resp = _call_rpc("submit_bid", {
        "task_id": "tsk_1", "agent_id": "a1", "bid_axm": "5.5"})
    _assert_loud_rejection(resp, "bid_axm")
    assert stub_upstream == []


def test_discovery_missing_required_rejected(stub_upstream):
    resp = _call_rpc("submit_bid", {"task_id": "tsk_1", "agent_id": "a1"})
    _assert_loud_rejection(resp, "bid_axm")
    assert stub_upstream == []


def test_discovery_json_null_rejected(stub_upstream):
    resp = _call_rpc("get_task", {"task_id": None})
    _assert_loud_rejection(resp, "task_id")
    assert stub_upstream == []


def test_discovery_register_agent_wrong_typed_tags_rejected(stub_upstream):
    resp = _call_rpc("register_agent", {"capability_tags": "lead-enrichment"})
    _assert_loud_rejection(resp, "capability_tags")
    resp = _call_rpc("register_agent", {"capability_tags": ["ok", 7]})
    _assert_loud_rejection(resp, "capability_tags")
    assert stub_upstream == []


def test_discovery_openai_style_valid_call_succeeds(stub_upstream):
    resp = _call_rpc("get_quote", {
        "skill_id": "lead-enrichment", "caller_id": "openai-caller"})
    assert resp is not None and "result" in resp, resp
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["price_axm"] == "1.0"
    assert len(stub_upstream) == 1


def test_discovery_payment_gate_and_validation_layering(stub_upstream):
    """Item 80 layering preserved: the payment gate is outermost for
    unpaid calls (the challenge is the loud response; nothing executes).
    A call carrying a token passes the gate and THEN faces strict
    validation — an Anthropic "null" is rejected before dispatch."""
    ms.register_paid_tool("get_quote", price="0.01", pay_to="0x" + "ab" * 20)
    try:
        # No token -> payment challenge result (not an error, no execute).
        resp = _call_rpc("get_quote", {"skill_id": "lead-enrichment"})
        assert resp is not None and "result" in resp, resp
        payload = json.loads(resp["result"]["content"][0]["text"])
        assert payload["status"] == "payment_required"
        assert stub_upstream == []
        # Token attached + invalid shape -> validation rejects loudly.
        resp = _call_rpc("get_quote", {
            "skill_id": "null", "x402_access_token": "tok"})
        _assert_loud_rejection(resp, "skill_id")
        assert stub_upstream == []
        # Token attached + valid shape -> executes identically.
        resp = _call_rpc("get_quote", {
            "skill_id": "lead-enrichment", "x402_access_token": "tok"})
        assert resp is not None and "result" in resp, resp
        assert len(stub_upstream) == 1
    finally:
        ms.PAID_TOOLS.discard("get_quote")
        ms.PAID_TOOL_PRICING.pop("get_quote", None)


# ---------------------------------------------------------------------------
# Call shapes — Swarm Command server (WSGI)
# ---------------------------------------------------------------------------

TOKEN = "dw316-test-token"


@pytest.fixture
def swarm_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_BOARD_PATH", str(tmp_path / "board.json"))
    monkeypatch.setenv("SWARM_MCP_BEARER_TOKEN", TOKEN)
    monkeypatch.delenv("SWARM_MCP_CORS_ORIGINS", raising=False)


def _swarm_call(name, arguments):
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": name, "arguments": arguments}}
    raw = json.dumps(req).encode("utf-8")
    environ = {
        "REQUEST_METHOD": "POST", "PATH_INFO": "/mcp",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(raw)),
        "HTTP_AUTHORIZATION": "Bearer %s" % TOKEN,
        "wsgi.input": io.BytesIO(raw),
    }
    captured = {}

    def start_response(status, headers):
        captured["status"] = status

    body = b"".join(swarm.app(environ, start_response))
    return json.loads(body.decode("utf-8"))


def _swarm_add_card(**overrides):
    args = {"lane": "brainstorm", "title": "Hello", "body": "world",
            "owner": "Muse"}
    args.update(overrides)
    return _swarm_call("board_card_add", args)


def test_swarm_anthropic_string_null_rejected_no_side_effect(swarm_env):
    resp = _swarm_add_card(title="null")
    assert "error" in resp and resp["error"]["code"] == -32602, resp
    assert "title" in resp["error"]["message"], resp
    # Nothing was written: the board is still empty.
    read = _swarm_call("board_lanes_read", {})
    lanes = json.loads(read["result"]["content"][0]["text"])
    assert all(not cards for cards in lanes.values()), lanes


def test_swarm_missing_required_rejected(swarm_env):
    args = {"lane": "brainstorm", "title": "Hello", "body": "world"}
    resp = _swarm_call("board_card_add", args)
    assert "error" in resp and resp["error"]["code"] == -32602, resp
    assert "owner" in resp["error"]["message"], resp


def test_swarm_wrong_type_rejected(swarm_env):
    resp = _swarm_add_card(lane=123)
    assert "error" in resp and resp["error"]["code"] == -32602, resp
    assert "lane" in resp["error"]["message"], resp


def test_swarm_optional_field_string_null_rejected(swarm_env):
    # discussion_read.topic is optional, but an explicit "null" string is
    # the Anthropic failure shape — reject, never look up a "null" thread.
    resp = _swarm_call("discussion_read", {"topic": "null"})
    assert "error" in resp and resp["error"]["code"] == -32602, resp
    assert "topic" in resp["error"]["message"], resp


def test_swarm_openai_style_valid_call_succeeds_identically(swarm_env):
    resp = _swarm_add_card()
    assert "result" in resp, resp
    card = json.loads(resp["result"]["content"][0]["text"])
    assert card["title"] == "Hello"
    # Omitted optional argument still works.
    read = _swarm_call("discussion_read", {})
    assert "result" in read, read


# ---------------------------------------------------------------------------
# Repair wave — Builder B adversarial probes (B1–B5 + lint gaps)
#
# Builder B returned NOT CONFIRMED on the first 316 branch: the guard
# worked for the exact shapes in the tests above but broke on variants —
# non-finite floats, whitespace-padded "null", an un-schematized array
# item object, fields the impl consumes but the schema never declared,
# and property-less objects with zero recursion. Each test below mirrors
# one of those probes.
# ---------------------------------------------------------------------------

# -- B1: non-finite / unrepresentable numbers -------------------------------

def test_validator_rejects_nonfinite_bid_amounts():
    # Python's json.loads accepts NaN/Infinity literals, so these values
    # DO arrive from the wire; the guard must refuse them by name.
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(guard.SchemaValidationError) as excinfo:
            guard.validate_arguments("t", _SCHEMA,
                                     {"task_id": "a", "bid_axm": bad})
        assert "bid_axm" in str(excinfo.value)


def test_validator_rejects_unrepresentable_payment_magnitude():
    # 1e308 is finite as a float, but a payment amount must survive the
    # platform's 18-decimal fixed-point conversion (bid_axm * 1e18 in
    # the bid planner) — 1e308 overflows to infinity there and crashed
    # with a raw OverflowError. The guard rejects it by name instead.
    with pytest.raises(guard.SchemaValidationError) as excinfo:
        guard.validate_arguments("t", _SCHEMA,
                                 {"task_id": "a", "bid_axm": 1e308})
    assert "bid_axm" in str(excinfo.value)


def test_validator_nonpayment_number_only_requires_finite():
    # The magnitude rule is a payment-representability rule, not a
    # general range cap: a non-payment number field accepts any finite
    # float and still rejects non-finite ones.
    schema = {"type": "object",
              "properties": {"reading": {"type": "number"}}}
    guard.validate_arguments("t", schema, {"reading": 1e308})
    with pytest.raises(guard.SchemaValidationError) as excinfo:
        guard.validate_arguments("t", schema, {"reading": float("nan")})
    assert "reading" in str(excinfo.value)


def test_validator_integer_fields_reject_float_shapes_cleanly():
    # Integer fields refuse non-finite and non-integral floats through
    # the guard's named-field error — never a raw downstream exception.
    for bad in (float("nan"), float("inf"), 1.5):
        with pytest.raises(guard.SchemaValidationError) as excinfo:
            guard.validate_arguments("t", _SCHEMA, {
                "task_id": "a", "bid_axm": 1.0, "estimated_seconds": bad})
        assert "estimated_seconds" in str(excinfo.value)


def test_discovery_nonfinite_bid_rejected_at_guard(stub_upstream):
    for bad in (float("nan"), float("inf"), 1e308):
        resp = _call_rpc("submit_bid", {
            "task_id": "tsk_1", "agent_id": "a1", "bid_axm": bad})
        _assert_loud_rejection(resp, "bid_axm")
    assert stub_upstream == []  # no confirmation, no execution


# -- B2: whitespace-padded "null" -------------------------------------------

def test_validator_rejects_whitespace_padded_null_strings():
    # Consumers strip before use (the board store strips titles, the
    # task lookup strips ids), so " null " becomes "null" downstream —
    # the guard must judge the stripped value, not the raw one.
    for bad in (" null ", "\tnull\n", "null ", " null"):
        with pytest.raises(guard.SchemaValidationError) as excinfo:
            guard.validate_arguments("t", _SCHEMA,
                                     {"task_id": bad, "bid_axm": 1.0})
        assert "task_id" in str(excinfo.value)
    # A string merely CONTAINING "null" is a real value, not the hole.
    guard.validate_arguments("t", _SCHEMA,
                             {"task_id": "nullable-task", "bid_axm": 1.0})


def test_discovery_padded_null_task_id_rejected(stub_upstream):
    resp = _call_rpc("get_task", {"task_id": "\tnull\n"})
    _assert_loud_rejection(resp, "task_id")
    assert stub_upstream == []


def test_swarm_padded_null_title_rejected_no_side_effect(swarm_env):
    resp = _swarm_add_card(title=" null ")
    assert "error" in resp and resp["error"]["code"] == -32602, resp
    assert "title" in resp["error"]["message"], resp
    read = _swarm_call("board_lanes_read", {})
    lanes = json.loads(read["result"]["content"][0]["text"])
    assert all(not cards for cards in lanes.values()), lanes


# -- B3: stack_propose items carry a real item schema ------------------------

def test_swarm_stack_propose_null_item_fields_rejected(swarm_env):
    resp = _swarm_call("stack_propose", {
        "items": [{"title": "null", "detail": "null", "owner": "null"}],
        "proposed_by": "Muse"})
    assert "error" in resp and resp["error"]["code"] == -32602, resp
    assert "items" in resp["error"]["message"], resp


def test_swarm_stack_propose_missing_item_field_rejected(swarm_env):
    resp = _swarm_call("stack_propose", {
        "items": [{"title": "T", "detail": "D"}],
        "proposed_by": "Muse"})
    assert "error" in resp and resp["error"]["code"] == -32602, resp
    assert "owner" in resp["error"]["message"], resp


def test_swarm_stack_propose_valid_items_succeed(swarm_env):
    resp = _swarm_call("stack_propose", {
        "items": [{"title": "Build", "detail": "the thing",
                   "owner": "Muse"}],
        "proposed_by": "Muse"})
    assert "result" in resp, resp
    stack = json.loads(resp["result"]["content"][0]["text"])
    assert stack["items"][0]["title"] == "Build"


# -- B4: register_agent declares every field its impl consumes ---------------

@pytest.mark.parametrize("args,field", [
    ({"chain_id": "null"}, "chain_id"),
    ({"chain_id": ["evil"]}, "chain_id"),
    ({"chain_id": 8453.5}, "chain_id"),
    ({"agent_url": 12345}, "agent_url"),
    ({"agent_url": " null "}, "agent_url"),
])
def test_discovery_register_agent_forwarded_fields_validated(
        stub_upstream, args, field):
    # _tool_register_agent forwards agent_url and chain_id upstream;
    # both are declared in the published schema, so the guard sees them.
    resp = _call_rpc("register_agent", args)
    _assert_loud_rejection(resp, field)
    assert stub_upstream == []


def test_discovery_register_agent_declared_fields_accepted(stub_upstream):
    resp = _call_rpc("register_agent", {
        "name": "Agent A", "chain_id": 8453,
        "agent_url": "https://agent.example.com/rpc"})
    assert resp is not None and "result" in resp, resp
    assert stub_upstream == []  # confirmation issued; nothing executed yet


# -- B5: property-less objects still get leaf rules ---------------------------

@pytest.mark.parametrize("card", [
    {"name": "A", "skills": [{"id": "s1", "bid_axm": "null"}]},   # nested "null"
    {"name": "A", "contact": None},                               # JSON null leaf
    {"name": "A", "aliases": ["ok", " null "]},                   # padded "null" in a list
    {"name": "A", "meta": {"deep": {"deeper": ["null"]}}},        # deep nesting
])
def test_discovery_register_agent_card_nested_nulls_rejected(
        stub_upstream, card):
    resp = _call_rpc("register_agent", {"agent_card": card})
    _assert_loud_rejection(resp, "agent_card")
    assert stub_upstream == []


def test_discovery_register_agent_card_legit_structure_accepted(
        stub_upstream):
    # Arbitrary structure/types stay welcome — only null-shaped leaves
    # are refused inside a property-less object.
    card = {"name": "Agent A", "version": "1.0", "chain_id": 8453,
            "active": True, "rating": 4.5,
            "skills": [{"id": "s1", "name": "Skill", "price": 0.5}],
            "tags": ["x", "y"], "limits": {"daily": 10}}
    resp = _call_rpc("register_agent", {"agent_card": card})
    assert resp is not None and "result" in resp, resp
    assert stub_upstream == []


# -- Lint: dotted names, tuple items, payment-name coverage, enum -----------

def test_lint_flags_nested_payment_field_by_leaf_name():
    # The payment test must see the LEAF name: "cfg.bid_axm" is a
    # payment field even though the dotted string is not.
    bad = [{"name": "t", "inputSchema": {
        "type": "object",
        "properties": {"cfg": {"type": "object", "properties": {
            "bid_axm": {"type": "number"}}}},
    }}]
    violations = guard.find_schema_violations(bad)
    assert any("bid_axm" in v for v in violations), violations

    good = [{"name": "t", "inputSchema": {
        "type": "object",
        "properties": {"cfg": {"type": "object", "required": ["bid_axm"],
                               "properties": {
                                   "bid_axm": {"type": "number"}}}},
    }}]
    assert guard.find_schema_violations(good) == []


def test_lint_flags_payment_field_inside_items_object():
    bad = [{"name": "t", "inputSchema": {
        "type": "object",
        "properties": {"batch": {"type": "array", "items": {
            "type": "object",
            "properties": {"amount": {"type": "number"}}}}},
    }}]
    violations = guard.find_schema_violations(bad)
    assert any("amount" in v for v in violations), violations


def test_lint_flags_tuple_form_items_nullable():
    bad = [{"name": "t", "inputSchema": {
        "type": "object",
        "properties": {"pair": {"type": "array", "items": [
            {"type": ["string", "null"]}]}},
    }}]
    violations = guard.find_schema_violations(bad)
    assert any("pair" in v and "null" in v for v in violations), violations


@pytest.mark.parametrize("field", ["stake_axm", "fee_usdc", "tip", "cost"])
def test_lint_flags_extended_payment_names(field):
    bad = [{"name": "t", "inputSchema": {
        "type": "object",
        "properties": {field: {"type": "number"}},
    }}]
    violations = guard.find_schema_violations(bad)
    assert any(field in v for v in violations), violations


def test_lint_extended_names_do_not_flag_nonpayment_fields():
    good = [{"name": "t", "inputSchema": {
        "type": "object",
        "properties": {"hostname": {"type": "string"},
                       "cost_center": {"type": "string"},
                       "tip_amount": {"type": "number"}},
    }}]
    assert guard.find_schema_violations(good) == []


def test_lint_flags_enum_containing_null():
    bad = [{"name": "t", "inputSchema": {
        "type": "object",
        "properties": {"mode": {"type": "string",
                                "enum": ["auto", None]}},
    }}]
    violations = guard.find_schema_violations(bad)
    assert any("mode" in v and "enum" in v for v in violations), violations


# -- Validator: tuple-form items are validated positionally ------------------

_TUPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "pair": {"type": "array",
                 "items": [{"type": "string"}, {"type": "integer"}]},
    },
}


def test_validator_tuple_items_positional():
    guard.validate_arguments("t", _TUPLE_SCHEMA, {"pair": ["ok", 5]})
    for bad in (["ok", "5"], ["null", 5], ["ok", True]):
        with pytest.raises(guard.SchemaValidationError) as excinfo:
            guard.validate_arguments("t", _TUPLE_SCHEMA, {"pair": bad})
        assert "pair" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Repair round 2 — Builder B re-check: the payment representability rule
# must cover JSON integers, not only floats.
#
# Round 1 nested BOTH the non-finite check and the wei-scaling check
# under ``isinstance(value, float)``, so an integer skipped both — and
# downstream ``_plan_bid`` converts through float and scales by 1e18:
# ``bid_axm: 10**400`` crashed the float() conversion itself and
# ``bid_axm: 10**308`` crashed the wei scaling, both surfacing as a raw
# -32603 OverflowError instead of the guard's named-field -32602.
# ---------------------------------------------------------------------------

# Ground truth, independent of the guard — RETARGETED in repair
# round 3 (below): through round 2 the boundary was the float/wei
# step (exact scaled magnitude strictly below 2**1024 - 2**970).
# Round 3 tightened it to the commit path's uint256 packing bound, so
# these pins now assert the WEI amounts fit in 32 bytes instead.
_WEI_LIMIT = 2**1024 - 2**970
_LARGEST_SCALABLE_INT = (_WEI_LIMIT - 1) // 10**18


@pytest.mark.parametrize("bad", [
    10**400,                    # float() conversion itself overflows
    10**308,                    # converts (1e308); wei scaling overflows
    10**291,                    # just past the round-2 boundary
    -10**291,                   # magnitude rule is symmetric
    _LARGEST_SCALABLE_INT + 1,  # first unrepresentable integer (r2)
])
def test_validator_rejects_unrepresentable_payment_ints(bad):
    with pytest.raises(guard.SchemaValidationError) as excinfo:
        guard.validate_arguments("t", _SCHEMA,
                                 {"task_id": "a", "bid_axm": bad})
    assert "bid_axm" in str(excinfo.value)


def test_validator_accepts_largest_representable_payment_int():
    # Round-2 razor pin, retargeted to the round-3 bound: the round-2
    # largest scalable integer's wei scaling stays below the float-
    # overflow threshold (ground truth above), but nothing that large
    # fits the uint256 packing — the guard's largest accepted integer
    # is _LARGEST_FITTING_INT (round-3 block below), and its wei amount
    # fits in 32 bytes while the next integer's does not survive the
    # downstream float pipeline.
    assert _LARGEST_SCALABLE_INT * 10**18 < _WEI_LIMIT
    assert (_LARGEST_SCALABLE_INT + 1) * 10**18 >= _WEI_LIMIT
    assert _downstream_price_wei(_LARGEST_FITTING_INT) < 2**256
    guard.validate_arguments("t", _SCHEMA, {
        "task_id": "a", "bid_axm": _LARGEST_FITTING_INT})
    guard.validate_arguments("t", _SCHEMA,
                             {"task_id": "a", "bid_axm": 10**18})


@pytest.mark.parametrize("as_int,as_float,accepted", [
    (10**290, 1e290, False),   # r3: both rejected — uint256 packing
    (10**291, 1e291, False),   # same magnitude, both types: rejected
])
def test_validator_payment_int_float_agree_at_boundary(
        as_int, as_float, accepted):
    # The int and float spellings of each magnitude must get the SAME
    # verdict. Through round 2, 10**290/1e290 were accepted (their wei
    # scalings are 1e308, finite); round 3 rejects both spellings —
    # the sealed commitment cannot pack a 1e308 wei amount into
    # 32 bytes, in any mode, so the guard refuses them up front.
    assert float(as_int) == as_float  # same double, same magnitude
    if accepted:
        guard.validate_arguments("t", _SCHEMA,
                                 {"task_id": "a", "bid_axm": as_int})
        guard.validate_arguments("t", _SCHEMA,
                                 {"task_id": "a", "bid_axm": as_float})
    else:
        for value in (as_int, as_float):
            with pytest.raises(guard.SchemaValidationError) as excinfo:
                guard.validate_arguments("t", _SCHEMA,
                                         {"task_id": "a", "bid_axm": value})
            assert "bid_axm" in str(excinfo.value)


def test_validator_nonpayment_number_accepts_huge_int():
    # The representability rule is payment-only: arbitrary-precision
    # JSON integers are legitimate non-payment data and stay accepted.
    schema = {"type": "object",
              "properties": {"reading": {"type": "number"}}}
    guard.validate_arguments("t", schema, {"reading": 10**400})
    guard.validate_arguments("t", schema, {"reading": -(10**400)})


def test_discovery_huge_int_bid_rejected_at_guard(stub_upstream):
    # 10**400 in the default (auto) mode crashed float() conversion in
    # _plan_bid with -32603; the guard must refuse it first, by name.
    resp = _call_rpc("submit_bid", {
        "task_id": "tsk_1", "agent_id": "a1", "bid_axm": 10**400})
    _assert_loud_rejection(resp, "bid_axm")
    assert stub_upstream == []


def test_discovery_huge_int_bid_rejected_in_legacy_mode(stub_upstream):
    resp = _call_rpc("submit_bid", {
        "task_id": "tsk_1", "agent_id": "a1", "bid_axm": 10**400,
        "mode": "legacy"})
    _assert_loud_rejection(resp, "bid_axm")
    assert stub_upstream == []


def test_discovery_unscalable_int_bid_rejected_at_guard(stub_upstream):
    # 10**308 converts to a finite float (1e308) but its wei scaling
    # overflows in auto mode's commit path — same -32603 crash class,
    # now refused by the guard before dispatch.
    resp = _call_rpc("submit_bid", {
        "task_id": "tsk_1", "agent_id": "a1", "bid_axm": 10**308})
    _assert_loud_rejection(resp, "bid_axm")
    assert stub_upstream == []


# ---------------------------------------------------------------------------
# Repair round 3 — Builder B re-check 2: the representability bound
# must ALSO cover the commit path's uint256 packing.
#
# Rounds 1–2 bounded only the float/wei-scaling step (~2**1024). One
# call deeper, submit_bid's commit planning computes
# ``price_wei = int(round(bid_axm * 1e18))`` and
# ``a2a_inbound_market.sealed_commitment`` packs it via
# ``int(price_wei).to_bytes(32, "big")`` — the wei amount must fit
# uint256, capping the bid near 2**256 / 1e18 ≈ 1.158e59 AXM. B's
# probes on the round-2 branch: ``bid_axm: 10**290`` (int), ``1.3e59``
# (float) and ``10**60`` (int) in commit mode (explicit, or auto
# resolving to commit on a sealed-auction task) crashed with a raw
# -32603 OverflowError; ``10**59`` / ``1e59`` issued confirmations.
# The razor integer ``(2**256 - 1) // 10**18`` crashes too: the
# planner float-converts first and the double rounds UP across
# 2**256, so the guard's bound carries headroom for the conversion.
# The guard runs before mode dispatch, so the tightened bound is
# global — legacy/reveal spellings of the same magnitudes are refused
# up front as well, rather than crashing if the mode ever resolves
# to commit.
# ---------------------------------------------------------------------------

# The guard's own safety margin, restated independently: the exact
# scaled magnitude must stay below 2**256 - 2**210 (the double
# product near 2**256 rounds by up to 2**202; the margin covers that
# plus the int->double conversion once integers are judged by the
# double they become).
_GUARD_LIMIT = 2**256 - 2**210
# Largest integer the guard accepts, derived from the guard's rule in
# exact arithmetic. Doubles first: find the largest double whose exact
# scaled value stays below the limit (a nextafter walk of a few steps
# from the cap's own double — the predicate is monotone in the
# double). The largest accepted integer is then the largest n whose
# float() conversion IS that double; near 1.16e59 an integer is ~2**144
# times finer than a double, so n must be located from the double,
# never by stepping integers one at a time.
import math as _math
from fractions import Fraction as _Fraction


def _largest_fitting_int():
    d = float(_GUARD_LIMIT // 10**18)
    while _Fraction(_math.nextafter(d, _math.inf)) * 10**18 < _GUARD_LIMIT:
        d = _math.nextafter(d, _math.inf)
    while _Fraction(d) * 10**18 >= _GUARD_LIMIT:
        d = _math.nextafter(d, -_math.inf)
    # d is the boundary double: accepted, one step up is not.
    assert _Fraction(d) * 10**18 < _GUARD_LIMIT
    assert (_Fraction(_math.nextafter(d, _math.inf)) * 10**18
            >= _GUARD_LIMIT)
    # Every integer within half a spacing of d rounds to d — at this
    # magnitude that is ~2**143 integers, so the top one is located
    # arithmetically (the midpoint itself is a ties-to-even case:
    # step back one if it rounded up to the next double).
    n = int(d) + int(_math.ulp(d)) // 2
    if float(n) != d:
        n -= 1
    assert float(n) == d and float(n + 1) != d
    return n


_LARGEST_FITTING_INT = _largest_fitting_int()

# The downstream pipeline being modelled, restated from the sources:
# mcp_server._plan_bid float-converts, scales, rounds; then
# a2a_inbound_market.sealed_commitment packs the wei integer.
def _downstream_price_wei(value):
    return int(round(float(value) * 1e18))


@pytest.mark.parametrize("bad", [
    10**290,                    # B probe: guard-accepted in r2, crashed
    10**60,                     # B probe: just past the uint256 cap
    -10**60,                    # magnitude rule is symmetric
    (2**256 - 1) // 10**18,     # the razor: float() rounds it UP over
])
def test_validator_rejects_commit_overflowing_payment_ints(bad):
    # Ground truth, independent of the guard: each of these crashes
    # the commit pipeline — the wei amount does not fit 32 bytes.
    with pytest.raises(OverflowError):
        _downstream_price_wei(bad).to_bytes(32, "big")
    with pytest.raises(guard.SchemaValidationError) as excinfo:
        guard.validate_arguments("t", _SCHEMA,
                                 {"task_id": "a", "bid_axm": bad})
    assert "bid_axm" in str(excinfo.value)


def test_validator_rejects_first_int_past_the_boundary():
    # The first integer past the guard's boundary is refused even
    # though the pipeline would (just) survive it: it sits inside the
    # headroom margin the bound deliberately carries for the float
    # pipeline's roundings, and the guard's verdict is the contract.
    with pytest.raises(guard.SchemaValidationError) as excinfo:
        guard.validate_arguments("t", _SCHEMA, {
            "task_id": "a", "bid_axm": _LARGEST_FITTING_INT + 1})
    assert "bid_axm" in str(excinfo.value)


@pytest.mark.parametrize("bad", [1.3e59, 1e60, -1.3e59])
def test_validator_rejects_commit_overflowing_payment_floats(bad):
    with pytest.raises(OverflowError):
        _downstream_price_wei(bad).to_bytes(32, "big")
    with pytest.raises(guard.SchemaValidationError) as excinfo:
        guard.validate_arguments("t", _SCHEMA,
                                 {"task_id": "a", "bid_axm": bad})
    assert "bid_axm" in str(excinfo.value)


def test_validator_boundary_sweep_int_float_agree():
    # Sweep the new boundary: the largest fitting integer is accepted,
    # one more is rejected, and the float spelling of each magnitude
    # (the very double the planner will see) gets the same verdict.
    assert _downstream_price_wei(_LARGEST_FITTING_INT) < 2**256
    guard.validate_arguments("t", _SCHEMA, {
        "task_id": "a", "bid_axm": _LARGEST_FITTING_INT})
    guard.validate_arguments("t", _SCHEMA, {
        "task_id": "a", "bid_axm": float(_LARGEST_FITTING_INT)})
    for value in (_LARGEST_FITTING_INT + 1,
                  float(_LARGEST_FITTING_INT + 1)):
        with pytest.raises(guard.SchemaValidationError) as excinfo:
            guard.validate_arguments("t", _SCHEMA,
                                     {"task_id": "a", "bid_axm": value})
        assert "bid_axm" in str(excinfo.value)
    # Practical magnitudes straddling the cap agree too.
    for as_int, as_float, accepted in ((10**59, 1e59, True),
                                       (10**60, 1e60, False)):
        assert float(as_int) == as_float  # same double
        if accepted:
            guard.validate_arguments("t", _SCHEMA,
                                     {"task_id": "a", "bid_axm": as_int})
            guard.validate_arguments("t", _SCHEMA, {
                "task_id": "a", "bid_axm": as_float})
        else:
            for value in (as_int, as_float):
                with pytest.raises(guard.SchemaValidationError):
                    guard.validate_arguments("t", _SCHEMA, {
                        "task_id": "a", "bid_axm": value})


def test_guard_never_accepts_a_commit_crashing_bid():
    # Property pin over the boundary neighbourhood: every value the
    # guard accepts — int or float — must survive the commit pipeline
    # (scale + 32-byte pack) without OverflowError.
    import math as _math
    candidates = [10**59, 1e59, _LARGEST_FITTING_INT]
    x = float(_LARGEST_FITTING_INT)
    for _ in range(2000):
        x = _math.nextafter(x, _math.inf)
        candidates.append(x)
    x = float(_LARGEST_FITTING_INT)
    for _ in range(2000):
        x = _math.nextafter(x, -_math.inf)
        candidates.append(x)
    for n in range(_LARGEST_FITTING_INT - 2000,
                   _LARGEST_FITTING_INT + 2000):
        candidates.append(n)
    accepted = 0
    for value in candidates:
        try:
            guard.validate_arguments("t", _SCHEMA,
                                     {"task_id": "a", "bid_axm": value})
        except guard.SchemaValidationError:
            continue
        accepted += 1
        _downstream_price_wei(value).to_bytes(32, "big")  # must not raise
    assert accepted > 0


# -- RPC shapes: B's exact probes, every mode spelling -----------------------

@pytest.mark.parametrize("mode", ["commit", "auto", "legacy", "reveal"])
@pytest.mark.parametrize("bad", [10**290, 10**60, 1.3e59])
def test_discovery_commit_overflowing_bids_rejected_at_guard(
        stub_upstream, mode, bad):
    # With the stub upstream answering ok, "auto" resolves to commit
    # (bidder-kit found) — the exact resolution B probed. The guard
    # refuses every spelling before dispatch: -32602 naming bid_axm,
    # zero upstream calls, never the raw -32603 OverflowError.
    resp = _call_rpc("submit_bid", {
        "task_id": "tsk_1", "agent_id": "a1", "bid_axm": bad,
        "mode": mode})
    _assert_loud_rejection(resp, "bid_axm")
    assert stub_upstream == []


@pytest.mark.parametrize("good", [10**59, 1e59])
def test_discovery_commit_mode_controls_still_confirm(stub_upstream,
                                                      good):
    # B's controls: at the cap's near side, commit-mode bids still
    # plan end-to-end (commitment computed, confirmation issued).
    resp = _call_rpc("submit_bid", {
        "task_id": "tsk_1", "agent_id": "a1", "bid_axm": good,
        "mode": "commit"})
    assert resp is not None and "result" in resp, resp
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload.get("confirmation_token") or payload.get(
        "action_id"), payload
    assert stub_upstream == []  # confirmation only; nothing executed
