"""MCP adapter: decorator wrapping with no MCP SDK anywhere in the loop."""
import json

from sincor_x402 import verified_tool

from x402pkg_helpers import AMOUNT_ATOMIC, make_payment_payload, payment_header


def _make_tool(requirement, platform, facilitator):
    @verified_tool(requirement=requirement,
                   spec={"required_keys": ["answer"]},
                   platform=platform, facilitator=facilitator,
                   name="summarize",
                   description="Summarize text",
                   input_schema={"type": "object",
                                 "properties": {"q": {"type": "string"}}})
    def summarize(q: str = ""):
        return {"answer": 42, "q": q}
    return summarize


def test_tool_definition_is_mcp_shaped(requirement, platform, facilitator):
    tool = _make_tool(requirement, platform, facilitator)
    defn = tool.tool_definition()
    assert defn["name"] == "summarize"
    assert defn["inputSchema"]["type"] == "object"
    assert defn["x-sincor-accepts"][0]["maxAmountRequired"] == AMOUNT_ATOMIC
    assert defn["x-sincor-escrow"]["specHash"]


def test_tool_call_paid_full_loop(requirement, platform, facilitator):
    tool = _make_tool(requirement, platform, facilitator)
    challenge = tool.call_paid(q="hello")
    assert challenge.status == 402
    header = payment_header(make_payment_payload(requirement))
    resp = tool.call_paid(payment_header=header, q="hello")
    assert resp.status == 200
    assert resp.body["result"]["answer"] == 42
    assert platform.released_total == int(AMOUNT_ATOMIC)


def test_tool_garbage_result_refunds(requirement, platform, facilitator):
    @verified_tool(requirement=requirement,
                   spec={"required_keys": ["answer"]},
                   platform=platform, facilitator=facilitator)
    def bad_tool(q: str = ""):
        return {"junk": True}
    header = payment_header(make_payment_payload(requirement))
    resp = bad_tool.call_paid(payment_header=header, q="x")
    assert resp.status == 422
    assert platform.refunded_total == int(AMOUNT_ATOMIC)
    assert platform.released_total == 0


class _FakeFastMCP:
    def __init__(self):
        self.registered = {}

    def tool(self, name=None, description=None):
        def _deco(fn):
            self.registered[name] = fn
            return fn
        return _deco


def test_register_on_fastmcp_style_server(requirement, platform, facilitator):
    tool = _make_tool(requirement, platform, facilitator)
    server = _FakeFastMCP()
    assert tool.register(server) is True
    assert "summarize" in server.registered
    out = server.registered["summarize"](
        payment_header=payment_header(make_payment_payload(requirement)),
        q="hi")
    assert out["status"] == 200


def test_register_on_foreign_server_is_noop(requirement, platform, facilitator):
    tool = _make_tool(requirement, platform, facilitator)
    assert tool.register(object()) is False  # no .tool surface → host wires it
