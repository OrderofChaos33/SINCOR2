"""W-136 follow-up: the genesis gate template must match the pending-
confirmation claim flow.

Since the W-136 fix, POST /api/genesis/claim no longer binds the wallet
or sets the sincor_genesis cookie — it returns
{"ok": true, "status": "pending_confirmation",
 "message": "Check your email to confirm your genesis claim."}
and the binding happens only when the user clicks the emailed
/api/genesis/confirm link. The gate template's submit handler must
therefore NOT redirect to "/" on a successful claim (that bounced the
user straight back to the still-gated page with no explanation); it
must show a check-your-email pending state instead.
"""
from __future__ import annotations

import os

import pytest

TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "templates", "genesis_gate.html"
)


def _template_source() -> str:
    with open(TEMPLATE_PATH, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def mvp_client():
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-32-char-minimum-ok")
    os.environ.setdefault("ADMIN_USERNAME", "admin")
    os.environ.setdefault("ADMIN_PASSWORD", "admin-password-32-char-minimum-ok")
    from sincor2.mvp_app import app

    app.config["TESTING"] = True
    app.config["SERVER_NAME"] = None
    return app.test_client()


# ---------------------------------------------------------------------------
# Source-level pins on the claim submit handler
# ---------------------------------------------------------------------------

def test_claim_success_does_not_redirect_to_home():
    src = _template_source()
    assert 'window.location.href = "/"' not in src
    assert "window.location.href = '/'" not in src


def test_template_handles_pending_confirmation_state():
    src = _template_source()
    # The JS must branch on / consume the pending_confirmation status the
    # claim API now returns.
    assert "pending_confirmation" in src
    # It must surface the server's check-your-email message (with the
    # canonical fallback text when data.message is absent).
    assert "Check your email to confirm your genesis claim." in src
    assert "data.message" in src


def test_template_has_dedicated_pending_notice_element():
    src = _template_source()
    # A visible status element distinct from the error line, so the
    # pending state reads as a neutral/success notice, not an error.
    assert 'id="notice"' in src
    assert 'role="status"' in src


def test_template_relabels_submit_button_after_send():
    src = _template_source()
    assert "Confirmation email sent" in src


def test_error_path_still_uses_err_element():
    src = _template_source()
    # Non-ok responses still surface data.error in #err and re-enable
    # the submit button.
    assert 'getElementById("err")' in src
    assert "data.error" in src


# ---------------------------------------------------------------------------
# Render-level: the gate still serves the claim form, with the new handler
# ---------------------------------------------------------------------------

def test_gate_renders_claim_form_and_pending_handler(mvp_client):
    r = mvp_client.get("/", follow_redirects=False)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="gate"' in html
    assert 'id="email"' in html
    assert 'id="wallet"' in html
    assert "pending_confirmation" in html
    assert 'window.location.href = "/"' not in html
