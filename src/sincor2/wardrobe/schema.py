"""Minimal wardrobe document checks. Fail closed on missing identity."""

from __future__ import annotations

from typing import Any

WARDROBE_SCHEMA = {
    "required": ["identity"],
    "identity_required": ["id", "name", "status"],
}


def validate_wardrobe(doc: Any) -> tuple[bool, str]:
    if not isinstance(doc, dict):
        return False, "not_object"
    ident = doc.get("identity")
    if not isinstance(ident, dict):
        # Legacy agent YAML uses top-level id/name/status.
        if doc.get("id") and doc.get("name"):
            return True, "legacy"
        return False, "identity_missing"
    for key in WARDROBE_SCHEMA["identity_required"]:
        if not ident.get(key):
            return False, f"identity.{key}_missing"
    return True, "ok"
