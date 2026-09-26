"""Public HTML pages.\n\nRailway serves mvp_app, not sincor2.app.\n"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from flask import Blueprint

bp = Blueprint("mvp_pages", __name__)


def _bind_mvp():
    import sincor2.mvp_app as mvp
    g = globals()
    for key, value in vars(mvp).items():
        if key.startswith("__") or key in {"bp", "_bind_mvp"}:
            continue
        g[key] = value


_bind_mvp()

GENESIS_COOKIE = "sincor_genesis"
GENESIS_LAUNCH_AT = "2026-11-09T00:00:00.000Z"  # legacy constant; SINCOR_LAUNCH_DATE is authoritative
_LAUNCH_DATE_DEFAULT = "2026-11-09"


def _launch_date() -> date:
    """UTC launch date. SINCOR_LAUNCH_DATE (YYYY-MM-DD); bad values fall back to the default, never 500."""
    raw = (os.environ.get("SINCOR_LAUNCH_DATE") or "").strip() or _LAUNCH_DATE_DEFAULT
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except Exception:
        return date(2026, 11, 9)


def _launch_at_iso() -> str:
    return _launch_date().strftime("%Y-%m-%dT00:00:00.000Z")


def _genesis_count() -> int:
    try:
        from sincor2.genesis_cohort import genesis_count
        return int(genesis_count())
    except Exception:
        return 0


def _template_exists(name: str) -> bool:
    try:
        app.jinja_env.get_template(name)
        return True
    except Exception:
        return False


def _support_email() -> str:
    return os.environ.get("SUPPORT_EMAIL") or os.environ.get("CONTACT_EMAIL") or "support@getsincor.com"


@bp.route("/")
def index():
    # Launch gate (recalibrated to 2026-11-09; override via SINCOR_LAUNCH_DATE).
    # Bypass order, all intentional and documented:
    #   1. ?inner=1 — the genesis gate embeds the live homepage as a blurred
    #      backdrop via an iframe to /?inner=1. Serving the gate here would
    #      recurse infinitely (gate inside gate), so the backdrop always gets
    #      the real homepage.
    #   2. Admin sessions — the operator previews the live homepage.
    #   3. Genesis claim cookie — claiming a genesis pass lifts the gate for
    #      that browser (the gate's own copy promises "the current site stays
    #      behind this gate until you claim").
    # Everything else (APIs, webhooks, /buy, auth, A2A) is deliberately NOT
    # gated here — only the public homepage is.
    if request.args.get("inner") == "1":
        return render_template("home.html")
    if _is_admin_session():
        return render_template("home.html")
    if request.cookies.get(GENESIS_COOKIE) == "claimed":
        return render_template("home.html")
    if datetime.now(timezone.utc).date() >= _launch_date():
        return render_template("home.html")
    return render_template(
        "genesis_gate.html",
        launch_at=_launch_at_iso(),
        genesis_count=_genesis_count(),
    )


@bp.route("/api/genesis/claim", methods=["POST"])
@limiter.limit("10 per minute")
def genesis_claim_api():
    """Genesis pass claim — the signup target of the launch gate's form.

    Public and intentionally ungated by launch date (it must work before
    launch). A successful claim sets the genesis cookie, which lifts the
    homepage gate for that browser.
    """
    try:
        from sincor2.genesis_cohort import claim as genesis_claim
    except Exception:
        return jsonify({"ok": False, "error": "Claim service unavailable."}), 503
    payload = request.get_json(silent=True) or {}
    result = genesis_claim(
        payload.get("email", ""),
        payload.get("password", ""),
        payload.get("wallet", ""),
    )
    status = 200 if result.get("ok") else 400
    resp = jsonify(result)
    if result.get("ok"):
        resp.set_cookie(
            GENESIS_COOKIE, "claimed",
            max_age=365 * 86400, httponly=True, samesite="Lax",
        )
    return resp, status


@bp.route("/genesis")
@bp.route("/claim")
def genesis_alias():
    return redirect("/sin-airdrop", code=302)


@bp.route("/login", methods=["GET", "POST"])
def login_page():
    next_url = request.values.get("next") or request.args.get("next") or ""
    identifier = (request.form.get("identifier") or request.form.get("email") or request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    oauth_google = False
    oauth_github = False
    try:
        oauth_google = bool(_oauth_provider_ready("google"))
        oauth_github = bool(_oauth_provider_ready("github"))
    except Exception:
        pass
    if request.method == "GET":
        if _is_admin_session():
            return redirect(_safe_next_url(next_url, "/admin"))
        if session.get("user_email"):
            return redirect(_safe_next_url(next_url, "/dashboard"))
        return render_template("login.html", next_url=next_url, identifier=identifier, oauth_google=oauth_google, oauth_github=oauth_github)
    if not identifier:
        return render_template("login.html", error="Email or username required.", next_url=next_url, identifier=identifier, oauth_google=oauth_google, oauth_github=oauth_github), 400
    if _admin_credentials_match(identifier, password):
        return _admin_cookie_response(identifier, _safe_next_url(next_url, "/admin"))
    customer = None
    try:
        customer = _resolve_customer(identifier)
    except Exception as exc:
        logger.warning("[AUTH] customer resolve failed: %s", exc)
    if customer and customer.get("email"):
        session["username"] = customer.get("username") or customer["email"].split("@")[0]
        return _auth_cookie_response(customer["email"], _safe_next_url(next_url, "/dashboard"))
    return render_template("login.html", error="Invalid credentials.", next_url=next_url, identifier=identifier, oauth_google=oauth_google, oauth_github=oauth_github), 401


@bp.route("/logout")
def logout_page():
    session.clear()
    resp = make_response(redirect("/"))
    resp.delete_cookie("access_token")
    return resp


@bp.route("/admin")
@bp.route("/admin/")
def admin_console():
    if not _is_admin_session():
        return redirect("/login?next=/admin")
    template = "admin_console.html" if _template_exists("admin_console.html") else "command_center.html"
    return render_template(template)


@bp.route("/dashboard")
@bp.route("/dashboard/")
def dashboard_page():
    if not (_is_admin_session() or session.get("user_email")):
        return redirect("/login?next=/dashboard")
    if _is_admin_session() and _template_exists("command_center.html"):
        return render_template("command_center.html")
    return render_template("dashboard.html")


@bp.route("/console")
def operator_console():
    if not _is_admin_session():
        return redirect("/login?next=/console")
    return render_template("command_center.html")


@bp.route("/command-center")
def command_center():
    # Canonical operator surface (dashboard links here). Same gate as /console.
    if not _is_admin_session():
        return redirect("/login?next=/command-center")
    return render_template("command_center.html")


def _admin_page(template: str, next_path: str, **ctx):
    """Render an admin-only page; non-admins bounce to login like /command-center."""
    if not _is_admin_session():
        return redirect("/login?next=" + next_path)
    return render_template(template, **ctx)


@bp.route("/admin-dashboard")
def admin_dashboard_page():
    # Jinja-resilient: metrics=None / activity=[] render as "—" placeholders.
    return _admin_page("admin_dashboard.html", "/admin-dashboard", metrics=None, activity=[])


@bp.route("/consciousness-dashboard")
def consciousness_dashboard_page():
    # Socket.IO-only interface; renders its 3D console and shows a
    # "Disconnected" state when the realtime backend is absent. No HTTP
    # data dependencies.
    return _admin_page("consciousness_transfer_dashboard.html", "/consciousness-dashboard")


@bp.route("/executive-dashboard")
def executive_dashboard_page():
    return _admin_page("executive_dashboard.html", "/executive-dashboard")


@bp.route("/professional-dashboard")
def professional_dashboard_page():
    # StrictUndefined is on: every {{ }} var must be passed. Values below are
    # honest placeholders (zeros / "—"), not live business data.
    return _admin_page(
        "professional_dashboard.html",
        "/professional-dashboard",
        company_name="SINCOR",
        industry="technology",
        current_date=datetime.now(timezone.utc).strftime("%B %d, %Y"),
        metrics={
            "new_leads_today": 0,
            "appointments_scheduled": 0,
            "completion_rate": 0,
            "customer_satisfaction": 0,
            "revenue_today": "—",
        },
        industry_metrics={
            "vehicles_completed": 0,
            "monthly_revenue": "—",
            "avg_service_value": "—",
            "booking_conversion": "—",
            "repeat_customers": "—",
            "next_available": "—",
        },
        agents={"coordination_score": 0, "active_count": 0},
    )


# ---------------------------------------------------------------------------
# Dashboard data APIs (admin-only). The executive dashboard fetches these on
# load; the values below are honest placeholders (zeros / "Unknown"), not
# telemetry — wire them to real sources before treating the numbers as live.
# ---------------------------------------------------------------------------

@bp.route("/api/executive-metrics")
def executive_metrics_api():
    if not _is_admin_session():
        return jsonify({"error": "Admin session required."}), 401
    return jsonify({
        "leads": {"total_leads": 0, "status": "No live feed"},
        "system": {"health_score": 0, "health_status": "Unknown", "uptime_days": 0, "uptime_percentage": 0},
        "agents": {"coordination_score": 0, "total_agents_available": 0, "status": "Unknown"},
        "database": {"total_databases": 0, "total_size_mb": 0, "status": "Unknown"},
        "performance": {"status": "Monitoring"},
    })


@bp.route("/api/recent-activity")
def recent_activity_api():
    if not _is_admin_session():
        return jsonify({"error": "Admin session required."}), 401
    return jsonify([])


# ---------------------------------------------------------------------------
# Professional-dashboard action stubs. The 8 endpoints below back the
# dashboard's lead-gen / integration buttons. No backend for any of them
# exists in the codebase (never did, not even legacy app.py), so these are
# explicit capability stubs: they return the JSON contract the page's JS
# expects with success=false and a plain-language reason, instead of 404ing
# into a cryptic parse error. They are NOT functional integrations.
# ---------------------------------------------------------------------------

_PROFESSIONAL_STUB_ERRORS = {
    "generate-leads": "Lead generation is not connected on this deployment.",
    "create-campaign": "Campaign automation is not connected on this deployment.",
    "analyze-opportunities": "Opportunity analysis is not connected on this deployment.",
    "connect-calendar": "Calendar integration is not connected on this deployment.",
    "connect-payments": "Payments integration is not connected on this deployment.",
    "connect-email": "Email integration is not connected on this deployment.",
    "connect-sms": "SMS integration is not connected on this deployment.",
    "test-email": "Email sending is not connected on this deployment.",
}


def _register_professional_stubs():
    for _path, _message in _PROFESSIONAL_STUB_ERRORS.items():
        def _view(_message=_message):
            if not _is_admin_session():
                return jsonify({"success": False, "error": "Admin session required."}), 401
            return jsonify({"success": False, "error": _message}), 501

        _view.__name__ = "professional_stub_" + _path.replace("-", "_")
        bp.route("/" + _path, methods=["POST"])(_view)


_register_professional_stubs()


@bp.route("/contact", methods=["GET", "POST"])
def contact_page():
    if request.method == "GET":
        return render_template("contact.html", support_email=_support_email(), sent=request.args.get("sent") == "1")
    name = request.form.get("name") or ""
    if "sanitize_string" in globals():
        name = sanitize_string(str(name), 80)
    else:
        name = name[:80]
    email = (request.form.get("email") or "").strip()[:254]
    message = (request.form.get("message") or "").strip()[:4000]
    if not email or not message:
        return render_template("contact.html", support_email=_support_email(), error="Email and message are required."), 400
    logger.info("[CONTACT] from=%s name=%s msg_len=%s", email, name, len(message))
    return redirect("/contact?sent=1")


@bp.route("/docs")
@bp.route("/docs/")
@bp.route("/guides")
def docs_page():
    return render_template("docs.html")


@bp.route("/guides/<path:_slug>")
def guides_alias(_slug):
    # Retired deep guide URLs keep working for old links/bookmarks.
    return redirect("/docs")


@bp.route("/whitepaper")
def whitepaper_page():
    return render_template("whitepaper.html")


@bp.route("/pitch")
def pitch_page():
    return render_template("pitch.html")


@bp.route("/pricing")
def pricing_page():
    return render_template("pricing.html")


@bp.route("/earn")
def earn_page():
    return render_template("earn.html")


@bp.route("/privacy")
def privacy_page():
    return render_template("privacy.html")


@bp.route("/terms")
def terms_page():
    return render_template("terms.html")


@bp.route("/security")
def security_page():
    return render_template("security.html")


@bp.route("/axiom")
def axiom_page():
    return render_template("axiom.html")


@bp.route("/operator")
def operator_page():
    if not _is_admin_session():
        return redirect("/login?next=/operator")
    return render_template("operator_dashboard.html")


@bp.route("/sitemap")
def sitemap_page():
    return render_template("sitemap.html")


@bp.route("/products/starter")
def product_starter():
    return render_template("product_starter.html")


@bp.route("/products/professional")
def product_professional():
    return render_template("product_professional.html")


@bp.route("/products/enterprise")
def product_enterprise():
    return render_template("product_enterprise.html")


@bp.route("/products/intel")
def product_intel():
    return render_template("product_intel.html")


@bp.route("/products/report")
def product_report():
    return render_template("product_report.html")


@bp.route("/dashboards")
def dashboards_menu():
    if not (_is_admin_session() or session.get("user_email")):
        return redirect("/login?next=/dashboards")
    return render_template("dashboards_menu.html")


@bp.route("/toa")
def toa_page():
    return render_template("toa.html")


@bp.route("/register-agent")
def register_agent_page():
    return render_template("register_agent.html")


@bp.route("/agent-card")
def agent_card_page():
    return render_template("agent_card.html")
