"""MCP tool-schema guard (dev-watch item 316 — Skyfire MCP postmortem).

Skyfire's MCP payment tools broke across client frameworks because of
OPTIONAL / NULLABLE tool parameters: Anthropic callers passed the literal
string ``"null"`` for omitted fields, AutoGen mishandles
``type: ["string", "null"]`` unions, and servers silently coerced
whatever arrived (``float("5.5")``, a bare string where an array was
declared, a card literally titled ``"null"``).

This module enforces the SINCOR2 schema rule for every MCP server in the
repo (marketplace discovery ``sincor2.mcp_server``, Swarm Command
``sincor2.swarm_command_mcp``):

1. **No nullable unions** in a published tool input schema — no
   ``type: ["string", "null"]``, no bare ``"null"`` type, no
   ``anyOf``/``oneOf`` branch that admits null.
2. **Payment-adjacent parameters are always required** — a payment
   field (``bid_axm``, ``amount``, ``price``, ``pay_to``, ``tip``,
   ``cost``, anything ``*_axm`` / ``*_usdc`` / ``x402``/``payment``-
   named, at ANY nesting depth) may not appear in ``properties``
   without also appearing in ``required``.
3. **Strict server-side validation** of every ``tools/call``:
   :func:`validate_arguments` REJECTS — never coerces — a literal string
   ``"null"`` for a typed field (including whitespace-padded forms:
   consumers strip before use, so ``" null "`` IS ``"null"``), a JSON
   ``null`` value, a missing required field, a wrong-typed value
   (including ``bool`` where an integer/number is declared: in JSON,
   ``true`` is not a number), or a non-finite number (Python's
   ``json.loads`` accepts ``NaN``/``Infinity`` literals, so they DO
   arrive from the wire). Payment amounts must additionally survive
   the platform's 18-decimal fixed-point conversion — a finite float
   whose wei scaling overflows (``1e308 * 1e18``) is rejected here
   rather than crashing downstream. Object fields whose schema
   declares no ``properties`` still get leaf rules applied
   recursively: no ``"null"`` strings and no JSON nulls anywhere
   inside, though arbitrary structure/types are otherwise accepted.

Stdlib only, like the servers that consume it.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List


class SchemaValidationError(ValueError):
    """A tools/call argument set violates the tool's input schema.

    A ``ValueError`` so both servers map it onto their existing
    invalid-params path (JSON-RPC -32602) with the message intact — the
    message always names the tool and the offending field, loudly.
    """


#: Field names that mark a parameter as payment-adjacent (rule 2).
_PAYMENT_EXACT = {
    "bid_axm", "bid_amount", "amount", "amount_axm", "amount_usdc",
    "price", "pay_to", "payto", "x402_access_token", "tip", "cost",
}

#: Suffixes that mark a parameter as payment-adjacent (rule 2): amounts
#: denominated in the platform's tokens (``stake_axm``, ``fee_usdc``).
_PAYMENT_SUFFIXES = ("_axm", "_usdc")


def _is_payment_field(name: str) -> bool:
    n = name.lower()
    return (n in _PAYMENT_EXACT or "x402" in n or "payment" in n
            or n.endswith(_PAYMENT_SUFFIXES))


def _path_names(field: str) -> List[str]:
    """Split a composed lint/validation path into its field names.

    Paths are composed as ``cfg.bid_axm``, ``tags[]``, ``pair[0]`` or
    ``note.anyOf[1]`` — payment detection must see the LEAF name (and
    every segment), never just the whole dotted string.
    """
    names = []
    for seg in field.split("."):
        seg = seg.split("[")[0]
        if seg:
            names.append(seg)
    return names


def _lint_schema(tool: str, field: str, schema: Any,
                 required: bool, out: List[str]) -> None:
    if not isinstance(schema, dict):
        return
    t = schema.get("type")
    if isinstance(t, list) and "null" in t:
        out.append(
            "%s.%s: nullable union type %r — declare a single concrete "
            "type; optionality belongs in 'required', never in the type"
            % (tool, field, t))
    if t == "null":
        out.append(
            "%s.%s: bare 'null' type — a field must declare a concrete "
            "type or be omitted from the call" % (tool, field))
    for combiner in ("anyOf", "oneOf", "allOf"):
        branches = schema.get(combiner)
        if isinstance(branches, list):
            for i, branch in enumerate(branches):
                _lint_schema(tool, "%s.%s[%d]" % (field, combiner, i),
                             branch, required, out)
    enum = schema.get("enum")
    if isinstance(enum, list) and None in enum:
        out.append(
            "%s.%s: enum admits null %r — an enum must list only real "
            "values; null is never a selectable value" % (tool, field, enum))
    if not required and any(
            _is_payment_field(n) for n in _path_names(field)):
        out.append(
            "%s.%s: payment-adjacent field is optional — payment "
            "parameters must be listed in 'required'" % (tool, field))
    items = schema.get("items")
    if isinstance(items, dict):
        _lint_schema(tool, field + "[]", items, required, out)
    elif isinstance(items, list):
        # Tuple-form items: each positional schema is linted too.
        for i, item_schema in enumerate(items):
            _lint_schema(tool, "%s[%d]" % (field, i), item_schema,
                         required, out)
    props = schema.get("properties")
    if isinstance(props, dict):
        sub_required = set(schema.get("required") or [])
        for sub_name, sub_schema in props.items():
            _lint_schema(tool, "%s.%s" % (field, sub_name), sub_schema,
                         sub_name in sub_required, out)


def find_schema_violations(tools: List[Dict[str, Any]]) -> List[str]:
    """Lint published tool definitions against rules 1 and 2.

    ``tools`` is a list of ``{"name", "inputSchema"}`` dicts as served by
    ``tools/list``. Returns human-readable violation strings (empty list
    = clean).
    """
    out: List[str] = []
    for tool in tools:
        name = str(tool.get("name", "?"))
        schema = tool.get("inputSchema") or {}
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        for field, field_schema in props.items():
            _lint_schema(name, field, field_schema,
                         field in required, out)
    return out


def _fail(tool: str, field: str, problem: str) -> SchemaValidationError:
    return SchemaValidationError(
        "%s: argument '%s': %s" % (tool, field, problem))


def _reject_null_leaves(tool: str, field: str, value: Any) -> None:
    """Leaf rules for free-form (property-less) object values (B5).

    Recursively refuse the two null shapes anywhere inside: a JSON
    ``null`` leaf and a string whose stripped form is ``"null"``.
    Every other leaf type and every container shape passes untouched.
    """
    if value is None:
        raise _fail(tool, field,
                    "null is not a valid value — omit the argument or "
                    "pass a real value; null is never coerced")
    if isinstance(value, str) and value.strip() == "null":
        raise _fail(tool, field,
                    "literal string \"null\" rejected — omit the "
                    "argument or pass a real value; \"null\" is "
                    "never coerced to an absent field")
    if isinstance(value, dict):
        for sub_name, sub_value in value.items():
            _reject_null_leaves(tool, "%s.%s" % (field, sub_name),
                                sub_value)
    elif isinstance(value, list):
        for i, element in enumerate(value):
            _reject_null_leaves(tool, "%s[%d]" % (field, i), element)


def _validate_value(tool: str, field: str, schema: Dict[str, Any],
                    value: Any) -> None:
    if value is None:
        raise _fail(tool, field,
                    "null is not a valid value — omit the argument or "
                    "pass a real value; null is never coerced")
    t = schema.get("type")
    if isinstance(t, list):
        raise _fail(tool, field,
                    "schema declares a union type %r — nullable/union "
                    "types are not accepted; the schema must declare a "
                    "single concrete type" % (t,))
    if t == "string":
        if not isinstance(value, str):
            raise _fail(tool, field,
                        "expected string, got %s" % type(value).__name__)
        if value.strip() == "null":
            # Consumers strip before use, so " null " becomes "null"
            # downstream — judge the stripped value, not the raw one.
            raise _fail(tool, field,
                        "literal string \"null\" rejected — omit the "
                        "argument or pass a real value; \"null\" is "
                        "never coerced to an absent field")
    elif t == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, float) and not math.isfinite(value):
                raise _fail(tool, field,
                            "non-finite number %r rejected — NaN and "
                            "Infinity are not valid values" % (value,))
            raise _fail(tool, field,
                        "expected integer, got %s" % type(value).__name__)
    elif t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _fail(tool, field,
                        "expected number, got %s" % type(value).__name__)
        if isinstance(value, float):
            if not math.isfinite(value):
                raise _fail(tool, field,
                            "non-finite number %r rejected — NaN and "
                            "Infinity are not valid values" % (value,))
            leaf = _path_names(field)[-1] if _path_names(field) else field
            if _is_payment_field(leaf) and not math.isfinite(value * 1e18):
                # A payment amount is denominated in 18-decimal fixed
                # point across the platform; a magnitude whose wei
                # scaling overflows is unrepresentable, not a bid.
                raise _fail(tool, field,
                            "number %r rejected — its magnitude cannot "
                            "be represented in 18-decimal fixed point "
                            "(wei scaling overflows)" % (value,))
    elif t == "boolean":
        if not isinstance(value, bool):
            raise _fail(tool, field,
                        "expected boolean, got %s" % type(value).__name__)
    elif t == "array":
        if not isinstance(value, list):
            raise _fail(tool, field,
                        "expected array, got %s" % type(value).__name__)
        items = schema.get("items")
        if isinstance(items, dict):
            for i, element in enumerate(value):
                _validate_value(tool, "%s[%d]" % (field, i), items, element)
        elif isinstance(items, list):
            # Tuple-form items: validate each element positionally
            # against its declared item schema.
            for i, element in enumerate(value):
                if i < len(items) and isinstance(items[i], dict):
                    _validate_value(tool, "%s[%d]" % (field, i),
                                    items[i], element)
    elif t == "object":
        if not isinstance(value, dict):
            raise _fail(tool, field,
                        "expected object, got %s" % type(value).__name__)
        sub_props = schema.get("properties")
        if isinstance(sub_props, dict) and sub_props:
            for req in schema.get("required") or []:
                if req not in value:
                    raise _fail(tool, "%s.%s" % (field, req),
                                "missing required field")
            for sub_name, sub_value in value.items():
                sub_schema = sub_props.get(sub_name)
                if isinstance(sub_schema, dict):
                    _validate_value(tool, "%s.%s" % (field, sub_name),
                                    sub_schema, sub_value)
        else:
            # No declared properties: the structure stays free-form,
            # but the leaf rules still apply recursively — no "null"
            # strings and no JSON nulls anywhere inside.
            _reject_null_leaves(tool, field, value)
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise _fail(tool, field,
                    "value %r is not one of %r" % (value, enum))


def validate_arguments(tool: str, schema: Dict[str, Any],
                       arguments: Dict[str, Any]) -> None:
    """Strictly validate one ``tools/call`` argument set (rule 3).

    Raises :class:`SchemaValidationError` on the first violation.
    Arguments not declared in the schema are left alone (the MCP
    confirmation/payment flows attach their own argument names); every
    DECLARED field that is present is type-checked, and every required
    field must be present with a real value.
    """
    if not isinstance(arguments, dict):
        raise _fail(tool, "<arguments>", "arguments must be an object")
    props = schema.get("properties") or {}
    for req in schema.get("required") or []:
        if req not in arguments:
            raise _fail(tool, req, "missing required argument")
    for field, value in arguments.items():
        field_schema = props.get(field)
        if isinstance(field_schema, dict):
            _validate_value(tool, field, field_schema, value)
