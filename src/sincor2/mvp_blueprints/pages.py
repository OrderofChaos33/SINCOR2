"""Public HTML pages.\n\nRailway serves mvp_app, not sincor2.app.\n"""
from __future__ import annotations

import os
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
GENESIS_LAUNCH_AT = "2026-09-26T16:00:00.000Z"


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
    return render_template("home.html")


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
