"""Bazaar discovery extension (x402 ``extensions.bazaar``) for challenges.

Coinbase's x402 Bazaar indexes a paid endpoint only when (a) its 402
response declares ``extensions.bazaar`` metadata and (b) a paid call
settles through the CDP Facilitator (the settle side is founder-gated
and lives outside this package). This module is the package's ONE
generator + local validator for the declaration, mirroring the shape
the platform module (``sincor2.x402_payments``) already serves so the
two surfaces cannot drift apart:

    extensions.bazaar = {
        "info": {
            "input":  {"type": "http", "method": "POST",
                       "bodyType": "json", "body": <example body>},
            "output": {"type": "json", "example": <example output>},
        },
        "schema": { ... JSON Schema (draft 2020-12) validating `info`,
                    with the resource's real input schema embedded at
                    properties.input.properties.body ... },
    }

Shape follows the x402 extension spec (specs/extensions/bazaar.md):
for body methods the schema requires type/method/bodyType/body, and
``$ref``/``$id`` values must be same-document fragments — this builder
never emits any. Descriptive text lives on the requirement itself
(``PaymentRequirement.description``); the extension carries schemas and
examples only, so nothing here can read as an instruction to an agent
(the Bazaar metadata content policy screens for exactly that).
"""
from __future__ import annotations

import copy
from typing import Any

#: JSON Schema primitive/composite types accepted in an input schema.
_JSON_SCHEMA_TYPES = frozenset(
    {"string", "integer", "number", "boolean", "array", "object", "null"}
)

_BODY_METHODS = ("POST", "PUT", "PATCH")
_QUERY_METHODS = ("GET", "HEAD", "DELETE")


def build_bazaar_extension(
    *,
    method: str = "POST",
    body_schema: dict[str, Any] | None = None,
    example_body: dict[str, Any] | None = None,
    output_example: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the complete ``extensions`` block for a 402 challenge.

    ``body_schema`` is the endpoint's real request-body JSON Schema and
    ``example_body`` a type-valid example of it; both are embedded so a
    buyer agent can construct the paid call before paying. Endpoints
    taking no business input omit both and declare an empty body.
    """
    method = str(method or "POST").upper()
    if method not in _BODY_METHODS + _QUERY_METHODS:
        raise ValueError(f"unsupported bazaar discovery method: {method!r}")
    schema_body = copy.deepcopy(body_schema) if body_schema else {
        "type": "object", "properties": {}}
    if schema_body.get("type") != "object":
        raise ValueError("bazaar body schema must be an object schema")

    if method in _BODY_METHODS:
        info_input: dict[str, Any] = {
            "type": "http",
            "method": method,
            "bodyType": "json",
            "body": copy.deepcopy(example_body) if example_body else {},
        }
        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "type": {"type": "string", "const": "http"},
                "method": {"type": "string", "enum": list(_BODY_METHODS)},
                "bodyType": {
                    "type": "string",
                    "enum": ["json", "form-data", "text"],
                },
                "body": schema_body,
                "queryParams": {"type": "object"},
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["type", "method", "bodyType", "body"],
            "additionalProperties": False,
        }
    else:
        info_input = {
            "type": "http",
            "method": method,
            "queryParams": copy.deepcopy(example_body) if example_body else {},
        }
        input_schema = {
            "type": "object",
            "properties": {
                "type": {"type": "string", "const": "http"},
                "method": {"type": "string", "enum": list(_QUERY_METHODS)},
                "queryParams": schema_body,
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["type", "method"],
            "additionalProperties": False,
        }

    return {
        "bazaar": {
            "info": {
                "input": info_input,
                "output": {
                    "type": "json",
                    "example": copy.deepcopy(output_example)
                    if output_example else {"ok": True},
                },
            },
            "schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {
                    "input": input_schema,
                    "output": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "example": {"type": "object"},
                        },
                        "required": ["type"],
                    },
                },
                "required": ["input"],
            },
        }
    }


def validate_bazaar_extension(extension: Any) -> list[str]:
    """Local check of the documented Bazaar validator requirements.

    Returns a list of problems (empty = valid). Structural and
    dependency-free: the extension must carry ``info.input`` (type
    http, a real method; bodyType + body for body methods) AND a full
    ``schema`` (object schema requiring ``input``, embedding an object
    input schema whose every declared property has a sane JSON Schema
    type). This is the check that catches the known failure shape —
    ``info.input`` present, ``schema`` absent — before any listing
    attempt.
    """
    if not isinstance(extension, dict) or not isinstance(
        extension.get("bazaar"), dict
    ):
        return ["extensions.bazaar missing or not an object"]
    bazaar = extension["bazaar"]
    errors: list[str] = []
    info = bazaar.get("info")
    if not isinstance(info, dict):
        errors.append("bazaar.info missing or not an object")
        info = {}
    info_input = info.get("input")
    method = None
    if not isinstance(info_input, dict):
        errors.append("bazaar.info.input missing or not an object")
    else:
        if info_input.get("type") != "http":
            errors.append("bazaar.info.input.type must be 'http'")
        method = info_input.get("method")
        if method not in _BODY_METHODS + _QUERY_METHODS:
            errors.append("bazaar.info.input.method missing or unsupported")
        elif method in _BODY_METHODS:
            if info_input.get("bodyType") != "json":
                errors.append(
                    "bazaar.info.input.bodyType must be 'json' for body "
                    "methods")
            if not isinstance(info_input.get("body"), dict):
                errors.append(
                    "bazaar.info.input.body missing or not an object "
                    "example")
    schema = bazaar.get("schema")
    if not isinstance(schema, dict):
        errors.append("bazaar.schema missing or not an object")
        return errors
    if schema.get("type") != "object":
        errors.append("bazaar.schema.type must be 'object'")
    if "input" not in (schema.get("required") or []):
        errors.append("bazaar.schema.required must include 'input'")
    props = schema.get("properties")
    input_schema = props.get("input") if isinstance(props, dict) else None
    if not isinstance(input_schema, dict):
        errors.append("bazaar.schema.properties.input missing")
        return errors
    inner_props = input_schema.get("properties") or {}
    if method in _QUERY_METHODS:
        params_schema = inner_props.get("queryParams")
        label = "queryParams"
    else:
        params_schema = inner_props.get("body")
        label = "body"
    if not isinstance(params_schema, dict) \
            or params_schema.get("type") != "object":
        errors.append(
            f"bazaar.schema input {label} schema missing or not an "
            "object schema")
        return errors
    for name, prop in (params_schema.get("properties") or {}).items():
        if not isinstance(prop, dict) \
                or prop.get("type") not in _JSON_SCHEMA_TYPES:
            errors.append(
                f"bazaar input schema property {name!r} has insane type "
                f"{(prop or {}).get('type')!r}")
    return errors
