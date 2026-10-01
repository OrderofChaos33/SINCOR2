"""Item 26 acceptance: blueprint registration is idempotent and no
hardcoded signing secret ships in ``sincor2.a2a_inbound``.

- Double ``register(app)`` / ``mount(app)`` on the same Flask app is a
  safe no-op (second call returns False) instead of raising Flask's
  duplicate-blueprint error.
- ``sign_payload`` requires an explicit ``secret`` argument; the old
  module-level ``DEMO_SECRET`` default (``"sincor-a2a-demo"``) is gone.
"""
from __future__ import annotations

import pytest
from flask import Flask

import sincor2.a2a_inbound as inbound
from sincor2.a2a_inbound import register as register_inbound


def _fresh_app() -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


def test_double_register_same_app_is_noop():
    inbound.reset_fabric()
    app = _fresh_app()
    assert register_inbound(app) is True
    assert register_inbound(app) is False  # no exception, no duplicate
    assert list(app.blueprints).count("a2a_inbound") == 1
    client = app.test_client()
    assert client.get("/v1/a2a/chain").status_code == 200


def test_register_two_apps_both_mount():
    inbound.reset_fabric()
    app_a, app_b = _fresh_app(), _fresh_app()
    assert register_inbound(app_a) is True
    assert register_inbound(app_b) is True  # per-app guard, not process-wide
    assert "a2a_inbound" in app_a.blueprints
    assert "a2a_inbound" in app_b.blueprints


def test_no_hardcoded_demo_secret():
    assert not hasattr(inbound, "DEMO_SECRET")
    import sincor2.a2a_inbound_ext as ext

    assert not hasattr(ext, "DEMO_SECRET")


def test_sign_payload_requires_explicit_secret():
    with pytest.raises(TypeError):
        inbound.sign_payload({"a": 1})  # type: ignore[call-arg]  # no default allowed
    sig_a = inbound.sign_payload({"a": 1}, "s3cret")
    sig_b = inbound.sign_payload({"a": 1}, "s3cret")
    assert sig_a == sig_b and len(sig_a) == 64
    assert inbound.sign_payload({"a": 1}, "other") != sig_a
