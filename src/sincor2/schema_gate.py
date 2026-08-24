"""JSON Schema gate for A2A ``message/send``.

AgentSkill.input_schema is compiled once at module load. Incoming payloads
are validated before they reach the swarm. Failures return JSON-RPC ``-32602``
with per-field errors.

Prefers the ``jsonschema`` package (Draft-07) when installed; otherwise uses
a strict Draft-07 subset covering every keyword currently used in SINCOR_SKILLS.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("sincor.schema_gate")

_POLLUTION = frozenset({"__proto__", "constructor", "prototype"})

try:  # optional — requirements.txt lists jsonschema; tests still run without it
    from jsonschema import Draft7Validator  # type: ignore

    _JSONSCHEMA = True
except Exception:  # pragma: no cover
    Draft7Validator = None  # type: ignore
    _JSONSCHEMA = False


@dataclass
class FieldError:
    path: str
    message: str
    validator: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class GateResult:
    ok: bool
    skill_id: str
    source: str
    payload: Any = None
    errors: List[FieldError] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "skill_id": self.skill_id,
            "source": self.source,
            "payload": self.payload,
            "errors": [e.to_dict() for e in self.errors],
        }


class CompiledSchema:
    """Cached Draft-07 validator for one skill."""

    def __init__(self, schema: Dict[str, Any]) -> None:
        if not isinstance(schema, dict):
            raise TypeError("schema must be an object")
        self.schema = schema
        self.engine = "subset"
        self._js = None
        if _JSONSCHEMA and Draft7Validator is not None:
            try:
                Draft7Validator.check_schema(schema)
                self._js = Draft7Validator(schema)
                self.engine = "jsonschema"
            except Exception as exc:  # noqa: BLE001
                logger.warning("jsonschema rejected skill schema (%s); using subset", exc)

    def iter_errors(self, instance: Any) -> List[FieldError]:
        pollution = _pollution_errors(instance)
        if self._js is not None:
            out = [
                FieldError(
                    path=_json_pointer(e.absolute_path),
                    message=e.message,
                    validator=str(e.validator),
                )
                for e in self._js.iter_errors(instance)
            ]
            return pollution + out
        return pollution + list(_subset_validate(instance, self.schema, ""))


_CACHE: Dict[str, Optional[CompiledSchema]] = {}


def compile_schema(schema: Dict[str, Any]) -> CompiledSchema:
    return CompiledSchema(schema)


def compile_skill_schemas(skills: Iterable[Any]) -> Dict[str, Optional[CompiledSchema]]:
    """Compile every non-empty input_schema. Empty schema → None (freeform)."""
    cache: Dict[str, Optional[CompiledSchema]] = {}
    for skill in skills:
        skill_id = getattr(skill, "id", None) or skill.get("id")  # type: ignore[union-attr]
        schema = getattr(skill, "input_schema", None)
        if schema is None and isinstance(skill, dict):
            schema = skill.get("input_schema") or skill.get("inputSchema") or {}
        if not schema:
            cache[str(skill_id)] = None
            continue
        cache[str(skill_id)] = compile_schema(dict(schema))
    _CACHE.update(cache)
    return cache


def compiled_for(skill_id: str, schema: Optional[Dict[str, Any]] = None) -> Optional[CompiledSchema]:
    if skill_id in _CACHE:
        return _CACHE[skill_id]
    if schema:
        compiled = compile_schema(schema)
        _CACHE[skill_id] = compiled
        return compiled
    return None


def _json_pointer(path: Iterable[Any]) -> str:
    parts = [str(p) for p in path]
    return "/" + "/".join(parts) if parts else "/"


def _pollution_errors(instance: Any, prefix: str = "") -> List[FieldError]:
    errors: List[FieldError] = []
    if isinstance(instance, dict):
        for key, value in instance.items():
            path = f"{prefix}/{key}" if prefix else f"/{key}"
            if key in _POLLUTION:
                errors.append(
                    FieldError(path=path, message="prototype-pollution key rejected", validator="security")
                )
            errors.extend(_pollution_errors(value, path))
    elif isinstance(instance, list):
        for i, value in enumerate(instance):
            errors.extend(_pollution_errors(value, f"{prefix}/{i}"))
    return errors


def _is_type(value: Any, typ: str) -> bool:
    if typ == "object":
        return isinstance(value, dict)
    if typ == "array":
        return isinstance(value, list)
    if typ == "string":
        return isinstance(value, str)
    if typ == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if typ == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if typ == "boolean":
        return isinstance(value, bool)
    if typ == "null":
        return value is None
    return True


def _subset_validate(instance: Any, schema: Dict[str, Any], path: str) -> List[FieldError]:
    errors: List[FieldError] = []
    pointer = path or "/"

    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_is_type(instance, t) for t in types):
            errors.append(
                FieldError(
                    path=pointer,
                    message=f"expected type {expected}, got {type(instance).__name__}",
                    validator="type",
                )
            )
            return errors

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(
            FieldError(path=pointer, message=f"{instance!r} is not one of {schema['enum']}", validator="enum")
        )

    if "const" in schema and instance != schema["const"]:
        errors.append(
            FieldError(path=pointer, message=f"{instance!r} != {schema['const']!r}", validator="const")
        )

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < int(schema["minLength"]):
            errors.append(FieldError(path=pointer, message="shorter than minLength", validator="minLength"))
        if "maxLength" in schema and len(instance) > int(schema["maxLength"]):
            errors.append(FieldError(path=pointer, message="longer than maxLength", validator="maxLength"))
        pattern = schema.get("pattern")
        if pattern and re.search(pattern, instance) is None:
            errors.append(FieldError(path=pointer, message="does not match pattern", validator="pattern"))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(FieldError(path=pointer, message="below minimum", validator="minimum"))
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(FieldError(path=pointer, message="above maximum", validator="maximum"))
        if schema.get("exclusiveMinimum") is True and "minimum" in schema and instance <= schema["minimum"]:
            errors.append(FieldError(path=pointer, message="not above exclusiveMinimum", validator="exclusiveMinimum"))
        if isinstance(schema.get("exclusiveMinimum"), (int, float)) and instance <= schema["exclusiveMinimum"]:
            errors.append(FieldError(path=pointer, message="not above exclusiveMinimum", validator="exclusiveMinimum"))
        if schema.get("exclusiveMaximum") is True and "maximum" in schema and instance >= schema["maximum"]:
            errors.append(FieldError(path=pointer, message="not below exclusiveMaximum", validator="exclusiveMaximum"))
        if isinstance(schema.get("exclusiveMaximum"), (int, float)) and instance >= schema["exclusiveMaximum"]:
            errors.append(FieldError(path=pointer, message="not below exclusiveMaximum", validator="exclusiveMaximum"))

    if isinstance(instance, list) and (schema.get("type") == "array" or "items" in schema):
        if "minItems" in schema and len(instance) < int(schema["minItems"]):
            errors.append(FieldError(path=pointer, message="fewer items than minItems", validator="minItems"))
        if "maxItems" in schema and len(instance) > int(schema["maxItems"]):
            errors.append(FieldError(path=pointer, message="more items than maxItems", validator="maxItems"))
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(instance):
                errors.extend(_subset_validate(item, items, f"{path}/{i}"))
        elif isinstance(items, list):
            for i, (item, sub) in enumerate(zip(instance, items)):
                if isinstance(sub, dict):
                    errors.extend(_subset_validate(item, sub, f"{path}/{i}"))

    if isinstance(instance, dict) and (schema.get("type") == "object" or "properties" in schema or "required" in schema):
        required = schema.get("required") or []
        for key in required:
            if key not in instance:
                errors.append(
                    FieldError(
                        path=f"{path}/{key}" if path else f"/{key}",
                        message=f"missing required property '{key}'",
                        validator="required",
                    )
                )
        properties = schema.get("properties") or {}
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child = f"{path}/{key}" if path else f"/{key}"
            if key in properties and isinstance(properties[key], dict):
                errors.extend(_subset_validate(value, properties[key], child))
            elif additional is False:
                errors.append(FieldError(path=child, message="additional property not allowed", validator="additionalProperties"))
            elif isinstance(additional, dict):
                errors.extend(_subset_validate(value, additional, child))

    return errors


def _first_data_part(msg_obj: Any) -> Any:
    if not isinstance(msg_obj, dict):
        return None
    for part in msg_obj.get("parts") or []:
        if not isinstance(part, dict):
            continue
        if part.get("kind") == "data" and "data" in part:
            return part.get("data")
        if "data" in part and not part.get("text"):
            return part.get("data")
    return None


def _try_json(text: str) -> Tuple[Any, bool]:
    stripped = (text or "").strip()
    if not stripped or stripped[0] not in "{[":
        return None, False
    try:
        return json.loads(stripped), True
    except json.JSONDecodeError:
        return None, False


def _promote_freeform(text: str, schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a lone text string onto a single required string field when unambiguous."""
    if not text or not isinstance(schema, dict):
        return None
    required = list(schema.get("required") or [])
    properties = schema.get("properties") or {}
    string_required = [
        key
        for key in required
        if isinstance(properties.get(key), dict) and properties[key].get("type") == "string"
    ]
    if len(required) == 1 and string_required == required:
        return {required[0]: text}
    for key in ("query", "text", "prompt", "input", "company", "target", "subject"):
        if key in required and key in string_required and set(required) == {key}:
            return {key: text}
    return None


def extract_payload(
    *,
    params: Dict[str, Any],
    msg_obj: Any,
    input_text: str,
    schema: Optional[Dict[str, Any]],
) -> Tuple[Any, str]:
    """Resolve the object that will be schema-checked.

    Precedence: params.input / params.data → DataPart → JSON text → freeform promotion.
    """
    for key in ("input", "data", "payload"):
        if key in params and params[key] not in (None, ""):
            return params[key], f"params.{key}"
    data_part = _first_data_part(msg_obj)
    if data_part is not None:
        return data_part, "message.parts.data"
    parsed, ok = _try_json(input_text)
    if ok:
        return parsed, "message.parts.text.json"
    if schema:
        promoted = _promote_freeform(input_text, schema)
        if promoted is not None:
            return promoted, "message.parts.text.promoted"
    if input_text:
        return {"text": input_text}, "message.parts.text"
    return None, "empty"


def validate_skill_input(
    *,
    skill_id: str,
    schema: Optional[Dict[str, Any]],
    params: Dict[str, Any],
    msg_obj: Any,
    input_text: str,
) -> GateResult:
    if not schema:
        payload, source = extract_payload(params=params, msg_obj=msg_obj, input_text=input_text, schema=None)
        return GateResult(ok=True, skill_id=skill_id, source=source or "freeform", payload=payload)

    compiled = compiled_for(skill_id, schema)
    payload, source = extract_payload(params=params, msg_obj=msg_obj, input_text=input_text, schema=schema)
    if payload is None:
        return GateResult(
            ok=False,
            skill_id=skill_id,
            source=source,
            errors=[FieldError(path="/", message="no input payload", validator="required")],
        )
    assert compiled is not None
    errors = compiled.iter_errors(payload)
    return GateResult(ok=not errors, skill_id=skill_id, source=source, payload=payload, errors=errors)


def engine_name() -> str:
    return "jsonschema" if _JSONSCHEMA else "subset"
