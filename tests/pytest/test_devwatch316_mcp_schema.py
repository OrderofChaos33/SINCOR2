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

# Ground truth, independent of the guard: the largest finite double is
# 2**1024 - 2**971 (sys.float_info.max) and a product rounds to
# infinity from half an ulp above it, so an exact scaled magnitude is
# representable iff it stays strictly below 2**1024 - 2**970.
_WEI_LIMIT = 2**1024 - 2**970
_LARGEST_SCALABLE_INT = (_WEI_LIMIT - 1) // 10**18


@pytest.mark.parametrize("bad", [
    10**400,                    # float() conversion itself overflows
    10**308,                    # converts (1e308); wei scaling overflows
    10**291,                    # just past the representable boundary
    -10**291,                   # magnitude rule is symmetric
    _LARGEST_SCALABLE_INT + 1,  # first unrepresentable integer
])
def test_validator_rejects_unrepresentable_payment_ints(bad):
    with pytest.raises(guard.SchemaValidationError) as excinfo:
        guard.validate_arguments("t", _SCHEMA,
                                 {"task_id": "a", "bid_axm": bad})
    assert "bid_axm" in str(excinfo.value)


def test_validator_accepts_largest_representable_payment_int():
    # The razor pin, in exact arithmetic: this integer's wei scaling
    # stays below the float-overflow threshold; one more does not.
    assert _LARGEST_SCALABLE_INT * 10**18 < _WEI_LIMIT
    assert (_LARGEST_SCALABLE_INT + 1) * 10**18 >= _WEI_LIMIT
    guard.validate_arguments("t", _SCHEMA, {
        "task_id": "a", "bid_axm": _LARGEST_SCALABLE_INT})
    guard.validate_arguments("t", _SCHEMA,
                             {"task_id": "a", "bid_axm": 10**290})
    guard.validate_arguments("t", _SCHEMA,
                             {"task_id": "a", "bid_axm": 10**18})


@pytest.mark.parametrize("as_int,as_float,accepted", [
    (10**290, 1e290, True),    # same magnitude, both types: accepted
    (10**291, 1e291, False),   # same magnitude, both types: rejected
])
def test_validator_payment_int_float_agree_at_boundary(
        as_int, as_float, accepted):
    # 10**290 / 10**291 straddle the practical boundary (their wei
    # scalings are 1e308, finite, and 1e309, infinity); the int and
    # float spellings of each magnitude must get the SAME verdict.
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
