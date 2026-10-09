"""WP5 coverage-check tests.

(e) the coverage script finds unauthenticated routes (synthetic app),
    passes guarded routes, and respects the public allowlist.
"""

import functools
import sys
import textwrap

import pytest

sys.path.insert(0, "scripts")
from shadow_coverage_check import (
    _is_public_rule,
    check_app,
)


def _make_synthetic_app(tmp_path):
    """Build a real module file with guarded/unguarded/public routes."""
    module_path = tmp_path / "synth_routes.py"
    module_path.write_text(
        textwrap.dedent(
            """
            import functools
            from flask import Blueprint

            bp = Blueprint("synth", __name__)

            def jwt_required(fn):
                @functools.wraps(fn)
                def wrapper(*a, **k):
                    return fn(*a, **k)
                return wrapper

            @bp.post("/api/synth/protected")
            @jwt_required
            def protected():
                return "guarded"

            @bp.post("/api/synth/exposed")
            def exposed():
                return "no auth here"

            @bp.get("/api/synth/inbody")
            def inbody():
                from flask import request
                token = request.headers.get("Authorization", "")
                if not token:
                    return "unauthorized", 401
                return "ok"
            """
        )
    )
    import importlib.util

    spec = importlib.util.spec_from_file_location("synth_routes", str(module_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_coverage_check_flags_unguarded_post(tmp_path):
    from flask import Flask

    mod = _make_synthetic_app(tmp_path)
    app = Flask(__name__)
    app.register_blueprint(mod.bp)
    findings = check_app(app)
    by_rule = {f["rule"]: f for f in findings}
    assert "/api/synth/exposed" in by_rule
    assert by_rule["/api/synth/exposed"]["severity"] == "FAIL"


def test_coverage_check_passes_decorated_route(tmp_path):
    from flask import Flask

    mod = _make_synthetic_app(tmp_path)
    app = Flask(__name__)
    app.register_blueprint(mod.bp)
    findings = check_app(app)
    rules = [f["rule"] for f in findings]
    assert "/api/synth/protected" not in rules


def test_coverage_check_passes_inbody_auth(tmp_path):
    from flask import Flask

    mod = _make_synthetic_app(tmp_path)
    app = Flask(__name__)
    app.register_blueprint(mod.bp)
    findings = check_app(app)
    rules = [f["rule"] for f in findings]
    assert "/api/synth/inbody" not in rules


def test_public_allowlist_exact_match_only():
    # "/" must not prefix-match everything (regression test).
    assert _is_public_rule("/") is True
    assert _is_public_rule("/secret-admin") is False
    assert _is_public_rule("/api/auth/login") is True
    assert _is_public_rule("/static/app.js") is True
    assert _is_public_rule("/.well-known/x.json") is True
    assert _is_public_rule("/agents") is True
    assert _is_public_rule("/agents/123") is False  # exact-only, not prefix


def test_handler_chain_auth_detected(tmp_path):
    """Auth in a _handle_* function called by the view counts."""
    module_path = tmp_path / "synth_handler.py"
    module_path.write_text(
        textwrap.dedent(
            """
            from flask import Blueprint

            bp = Blueprint("synth2", __name__)

            def _verify_create_auth(params):
                # EIP-191 style verification
                return True

            def _handle_send(body):
                if not _verify_create_auth(body.get("params", {})):
                    return {"error": "unauthorized"}
                return {"ok": True}

            @bp.post("/api/synth2/send")
            def send():
                from flask import request
                return _handle_send(request.get_json() or {})
            """
        )
    )
    import importlib.util

    spec = importlib.util.spec_from_file_location("synth_handler", str(module_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(mod.bp)
    findings = check_app(app)
    rules = [f["rule"] for f in findings]
    assert "/api/synth2/send" not in rules
