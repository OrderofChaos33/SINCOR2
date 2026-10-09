#!/usr/bin/env python3
"""WP5 static coverage check: every non-public HTTP route must have auth.

The nginx-ui lesson (dev-watch item 85): auth on ``/mcp`` but NOT on
``/mcp_message`` -- twin endpoints where one ships without the auth check.
This script enumerates every route in the Flask app's url_map and verifies
each non-public route carries an authentication/authorization guard.

Detection (runtime introspection, ground truth from the loaded app):
1. Walk the view function's ``__wrapped__`` chain for known auth decorator
   names (jwt_required, admin_required, login_required, _require_admin, ...).
2. Inspect the view function's source for in-body auth calls
   (e.g. ``_require_admin(request)``, ``get_jwt_identity()``).
3. Check blueprint-level ``before_request`` handlers for auth.
4. Match against the PUBLIC_ALLOWLIST (login, health, static assets, ...).

Exit 0: all non-public routes have auth.
Exit 1: one or more routes lack auth (lists them). CI must fail on this.

Usage:
    PYTHONPATH=src:. python scripts/shadow_coverage_check.py
"""

from __future__ import annotations

import inspect
import os
import sys

# ---------------------------------------------------------------------------
# Known auth markers
# ---------------------------------------------------------------------------

#: Decorator/function names that indicate an auth guard is present.
AUTH_DECORATOR_NAMES = frozenset(
    {
        "jwt_required",
        "jwt_required_optional",
        "admin_required",
        "_require_admin",
        "login_required",
        "require_auth",
        "require_admin",
        "require_api_key",
        "verify_jwt",
        "check_admin",
        "require_operator",
    }
)

#: Source-code markers: if the view function's source contains one of these,
#: it performs an auth check in-body (defense in depth, not decorator-only).
AUTH_SOURCE_MARKERS = (
    "jwt_required",
    "get_jwt_identity",
    "verify_jwt_in_request",
    "_require_admin(",
    "_check_admin_token(",
    "_check_admin_key(",
    "_is_admin_session(",
    "_check_heartbeat_auth(",
    "verify_signature",
    "verify_eip191",
    "_verify_create_auth(",
    "_verify_auth(",
    "recover_signer",
    "eth_account",
    "check_signature(",
    "authenticate(",
    "verify_auth(",
    "require_signature",
    "login_required",
    "decode_token(",
    "request.headers.get(\"Authorization\"",
    "request.headers.get('Authorization'",
    "X-API-Key",
    "api_key",
)

#: Routes that are intentionally public (no auth required).
#: Each entry is (rule_string_prefix_or_exact, methods_note).
PUBLIC_ALLOWLIST = frozenset(
    {
        # Auth entry points
        "/api/auth/login",
        # Health / readiness
        "/health",
        "/healthz",
        "/ready",
        "/__boot",
        # Public marketing / landing
        "/",
        "/signup",
        "/pricing",
        # Static assets
        "/static/",
        # Agent discovery (public by design: marketplace cards, well-known)
        "/.well-known/",
        "/agents",
        "/api/a2a/agents",
        # Launch marketing pages
        "/launch/",
        # Public token metadata (well-known token list pattern)
        "/api/token/metadata",
    }
)

#: HTTP methods that change state. A route accepting one of these without
#: auth is a FAIL (CI must block). GET/HEAD without auth is REVIEW
#: (often intentionally public; needs human classification).
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})


def _is_public_rule(rule_str: str) -> bool:
    for entry in PUBLIC_ALLOWLIST:
        if entry.endswith("/") and len(entry) > 1:
            # Path prefix (e.g. "/static/"): matches the prefix and below.
            if rule_str.startswith(entry):
                return True
        else:
            # Exact match only ("/" must not prefix-match everything).
            if rule_str == entry:
                return True
    return False


def _wrapped_names(view_func) -> set:
    """Collect __name__/__qualname__ through the decorator chain."""
    names = set()
    seen = set()
    func = view_func
    while func is not None and id(func) not in seen:
        seen.add(id(func))
        names.add(getattr(func, "__name__", ""))
        names.add(getattr(func, "__qualname__", ""))
        func = getattr(func, "__wrapped__", None)
    return {n for n in names if n}


def _has_auth_decorator(view_func) -> bool:
    return bool(_wrapped_names(view_func) & AUTH_DECORATOR_NAMES)


def _has_auth_in_source(view_func) -> bool:
    try:
        source = inspect.getsource(view_func)
    except (OSError, TypeError):
        return False
    # Unwrap to the innermost function for the most accurate source.
    func = view_func
    seen = set()
    while id(func) not in seen:
        seen.add(id(func))
        inner = getattr(func, "__wrapped__", None)
        if inner is None:
            break
        func = inner
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError):
        pass
    if any(marker in source for marker in AUTH_SOURCE_MARKERS):
        return True
    # One level down: view delegates to a _handle_* function that performs
    # auth (e.g. EIP-191 verification in _verify_create_auth). Resolve the
    # handler in the view's module and check its source too.
    return _handler_has_auth(func, source)


def _handler_has_auth(view_func, view_source: str) -> bool:
    """Check auth markers in handler functions called by the view.

    Finds ``_handle_*`` / ``_verify_*`` / ``handle_*`` references in the view
    source, resolves them in the view function's module globals, and checks
    their source for auth markers. One level only (keeps the check fast and
    predictable).
    """
    import re as _re
    import sys as _sys

    module = inspect.getmodule(view_func)
    if module is None:
        # Fallback for dynamically loaded modules: resolve via __module__.
        module = _sys.modules.get(getattr(view_func, "__module__", ""))
    # Last resort: the view function's own globals dict (works even when
    # the module isn't registered in sys.modules).
    view_globals = getattr(view_func, "__globals__", {})
    handler_names = set(
        _re.findall(r"\b(_handle_\w+|_verify_\w+|handle_\w+)\b", view_source)
    )
    for name in handler_names:
        handler = None
        if module is not None:
            handler = getattr(module, name, None)
        if handler is None:
            handler = view_globals.get(name)
        if handler is None or not callable(handler):
            continue
        try:
            handler_source = inspect.getsource(handler)
        except (OSError, TypeError):
            continue
        if any(marker in handler_source for marker in AUTH_SOURCE_MARKERS):
            return True
    return False


def _blueprint_has_auth_before_request(app, blueprint_name: str) -> bool:
    """True if the blueprint registers a before_request that looks like auth."""
    bp = app.blueprints.get(blueprint_name)
    if bp is None:
        return False
    for func in bp.before_request_funcs.get(None, []):
        names = _wrapped_names(func)
        if names & AUTH_DECORATOR_NAMES:
            return True
        try:
            source = inspect.getsource(func)
        except (OSError, TypeError):
            continue
        if any(m in source for m in AUTH_SOURCE_MARKERS):
            return True
    return False


def check_app(app) -> list:
    """Return a list of unauthenticated non-public routes.

    Each entry: {"rule": str, "methods": [...], "endpoint": str,
                 "reason": str, "severity": "FAIL" | "REVIEW"}.

    Severity:
    - FAIL: state-changing method (POST/PUT/DELETE/PATCH) without auth.
      CI must block on these.
    - REVIEW: GET/HEAD without auth and not allowlisted. Often intentionally
      public (marketing, discovery); needs human classification, does not
      block CI alone.
    """
    findings = []
    for rule in app.url_map.iter_rules():
        rule_str = str(rule)
        methods = sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS"))
        if _is_public_rule(rule_str):
            continue
        view_func = app.view_functions.get(rule.endpoint)
        if view_func is None:
            findings.append(
                {
                    "rule": rule_str,
                    "methods": methods,
                    "endpoint": rule.endpoint,
                    "reason": "no view function found",
                    "severity": "FAIL",
                }
            )
            continue
        if _has_auth_decorator(view_func):
            continue
        if _has_auth_in_source(view_func):
            continue
        bp_name = rule.endpoint.split(".")[0] if "." in rule.endpoint else ""
        if bp_name and _blueprint_has_auth_before_request(app, bp_name):
            continue
        severity = (
            "FAIL"
            if (set(methods) & STATE_CHANGING_METHODS)
            else "REVIEW"
        )
        findings.append(
            {
                "rule": rule_str,
                "methods": methods,
                "endpoint": rule.endpoint,
                "reason": "no auth decorator, no in-body auth check, "
                "no blueprint before_request guard",
                "severity": severity,
            }
        )
    return findings


def main() -> int:
    # Import the app with test-safe env (never production credentials).
    os.environ.setdefault("FLASK_ENV", "test")
    try:
        from sincor2.mvp_app import app
    except Exception as exc:
        print(f"ERROR: could not import sincor2.mvp_app: {exc}", file=sys.stderr)
        return 2

    findings = check_app(app)
    total = sum(1 for _ in app.url_map.iter_rules())
    fails = [f for f in findings if f["severity"] == "FAIL"]
    reviews = [f for f in findings if f["severity"] == "REVIEW"]
    print(
        f"checked {total} routes; {len(fails)} FAIL (state-changing, no auth), "
        f"{len(reviews)} REVIEW (GET, no auth, needs classification)"
    )

    if reviews and not fails:
        print("\nREVIEW (GET routes without auth -- classify as public or guard):")
        for f in reviews[:20]:
            print(f"  {','.join(f['methods']):8} {f['rule']}")
        if len(reviews) > 20:
            print(f"  ... and {len(reviews) - 20} more")

    if fails:
        print("\nFAIL -- state-changing routes without auth (CI must block):")
        for f in fails:
            print(f"  {','.join(f['methods']):8} {f['rule']}")
            print(f"           endpoint={f['endpoint']} reason={f['reason']}")
        return 1

    print("OK: no state-changing route lacks an auth guard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
