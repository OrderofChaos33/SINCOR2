"""CHROMA shop dashboard — Flask blueprint mounted at /chroma."""

from __future__ import annotations

import hmac
import os
import secrets
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional

from flask import (
    Blueprint,
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .booking_agent import DetailingBookingAgent, quote_matrix
from .config import (
    admin_identities,
    admin_password,
    calendly_handle_from_url,
    demo_mode,
    live_send_enabled,
)
from .pipeline import health_payload, run_pipeline
from .protocols import PACKAGES
from .seed import ensure_demo
from .send_gate import approve, edit, enqueue, kill
from .store import get_store

bp = Blueprint(
    "chroma",
    __name__,
    url_prefix="/chroma",
    template_folder=str(Path(__file__).resolve().parents[2] / "templates"),
)


def _store():
    return get_store()


def _shop() -> Dict[str, Any]:
    return _store().get_settings()


def _packages(shop: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    shop = shop or _shop()
    pkgs = shop.get("packages")
    return pkgs if isinstance(pkgs, dict) else PACKAGES


def _contact(lead: Dict[str, Any]) -> Dict[str, Optional[str]]:
    nested = lead.get("contact") if isinstance(lead.get("contact"), dict) else {}
    payload = lead.get("payload") if isinstance(lead.get("payload"), dict) else {}
    pcontact = payload.get("contact") if isinstance(payload.get("contact"), dict) else {}
    return {
        "email": lead.get("email") or nested.get("email") or payload.get("email") or pcontact.get("email"),
        "phone": lead.get("phone") or nested.get("phone") or payload.get("phone") or pcontact.get("phone"),
    }


def _latest_quote(lead_id: str) -> Optional[Dict[str, Any]]:
    for quote in _store().list_quotes():
        if quote.get("lead_id") == lead_id:
            return quote
    return None


NEXT_ACTION = {
    "book_now": "Price it and book",
    "qualify": "Ask the car",
    "sequence": "Follow up",
    "winback": "Win them back",
}


def _is_authed() -> bool:
    if demo_mode():
        return True
    if session.get("chroma_shop"):
        return True
    if session.get("admin_username") or session.get("is_admin"):
        return True
    try:
        from flask import has_request_context

        if has_request_context():
            # Sept 9 operator session used by /admin
            if session.get("user_email") and session.get("role") == "admin":
                return True
    except Exception:
        pass
    return False


def shop_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if demo_mode():
            ensure_demo(_store())
            return fn(*args, **kwargs)
        if _is_authed():
            return fn(*args, **kwargs)
        nxt = request.path
        return redirect(url_for("chroma.login", next=nxt))

    return wrapper


def _ctx(**extra: Any) -> Dict[str, Any]:
    counts = _store().counts()
    shop = _shop()
    ctx = {
        "shop": shop,
        "shop_name": shop.get("shop_name") or "CHROMA",
        "counts": counts,
        "money": _store().books_totals(),
        "demo": demo_mode(),
        "live_send": live_send_enabled(),
        "nav": request.endpoint or "",
    }
    ctx.update(extra)
    return ctx


@bp.route("/health")
def health():
    return jsonify(health_payload(_store())), 200


@bp.route("/login", methods=["GET", "POST"])
def login():
    if demo_mode():
        return redirect(url_for("chroma.leads"))
    error = None
    if request.method == "POST":
        ident = (request.form.get("identifier") or request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        expected = admin_password()
        identities = admin_identities()
        if (
            ident.lower() in identities
            and expected
            and hmac.compare_digest(password, expected)
        ):
            session["chroma_shop"] = ident
            session["admin_username"] = ident
            dest = request.args.get("next") or request.form.get("next") or url_for("chroma.leads")
            if not dest.startswith("/chroma"):
                dest = url_for("chroma.leads")
            return redirect(dest)
        error = "Wrong shop login. Use the same staff username as the rest of SINCOR."
    return render_template("chroma/login.html", **_ctx(error=error, next_url=request.args.get("next") or ""))


@bp.route("/logout")
def logout():
    session.pop("chroma_shop", None)
    return redirect(url_for("chroma.login"))


@bp.route("/")
@bp.route("/leads")
@shop_required
def leads():
    rows = _store().list_leads()
    return render_template("chroma/leads.html", **_ctx(leads=rows))


@bp.route("/leads/<lead_id>")
@shop_required
def lead_detail(lead_id: str):
    lead = _store().get_lead(lead_id)
    if not lead:
        flash("Lead not found.", "error")
        return redirect(url_for("chroma.leads"))
    quotes = [q for q in _store().list_quotes() if q.get("lead_id") == lead_id]
    bookings = [b for b in _store().list_bookings() if b.get("lead_id") == lead_id]
    outbound = [o for o in _store().list_outbound() if o.get("lead_id") == lead_id]
    ranking = lead.get("ranking") or {}
    return render_template(
        "chroma/lead_detail.html",
        **_ctx(
            lead=lead,
            timeline=_store().timeline(lead_id),
            quotes=quotes,
            bookings=bookings,
            outbound=outbound,
            packages=_packages(),
            contact=_contact(lead),
            next_label=NEXT_ACTION.get(ranking.get("next_action"), "Price it"),
            booking_url=_shop().get("booking_url") or "",
        ),
    )


@bp.route("/leads/ingest", methods=["POST"])
@shop_required
def ingest_lead():
    payload = {
        "name": request.form.get("name") or "Walk-in",
        "source": request.form.get("source") or "website",
        "email": request.form.get("email") or None,
        "phone": request.form.get("phone") or None,
        "message": request.form.get("message") or "",
        "vehicle": {
            "year": int(request.form["year"]) if request.form.get("year") else None,
            "make": request.form.get("make") or None,
            "model": request.form.get("model") or None,
            "size": request.form.get("size") or None,
            "body_style": request.form.get("body_style") or None,
        },
    }
    result = run_pipeline(payload, store=_store())
    return redirect(url_for("chroma.lead_detail", lead_id=result["lead"]["lead_id"]))


@bp.route("/leads/<lead_id>/run", methods=["POST"])
@shop_required
def rerun_lead(lead_id: str):
    lead = _store().get_lead(lead_id)
    if not lead:
        return redirect(url_for("chroma.leads"))
    payload = lead.get("payload") or {}
    payload["lead_id"] = lead_id
    run_pipeline(payload, store=_store())
    flash("Scored again.", "ok")
    return redirect(url_for("chroma.lead_detail", lead_id=lead_id))


@bp.route("/leads/<lead_id>/quote", methods=["POST"])
@shop_required
def quote_lead(lead_id: str):
    store = _store()
    lead = store.get_lead(lead_id)
    if not lead:
        flash("Lead not found.", "error")
        return redirect(url_for("chroma.leads"))
    agent = DetailingBookingAgent()
    quote = agent.quote(
        {
            "package_id": request.form.get("package_id") or "full_detail",
            "vehicle": lead.get("vehicle") or {},
            "packages": _packages(),
        }
    )
    saved = store.save_quote(quote, lead_id=lead_id)
    store.set_lead_status(lead_id, "quoted")
    store.add_event(
        lead_id,
        "quoted",
        f"{quote.get('label')} · ${float(quote.get('total') or 0):.0f}",
    )
    flash(f"Priced at ${float(saved['total']):.0f}. Queue a text when you're ready.", "ok")
    return redirect(url_for("chroma.lead_detail", lead_id=lead_id))


@bp.route("/leads/<lead_id>/text", methods=["POST"])
@shop_required
def text_lead(lead_id: str):
    store = _store()
    lead = store.get_lead(lead_id)
    if not lead:
        return redirect(url_for("chroma.leads"))
    shop = _shop()
    contact = _contact(lead)
    quote = _latest_quote(lead_id)
    if not quote:
        flash("Price the job first, then queue the text.", "error")
        return redirect(url_for("chroma.lead_detail", lead_id=lead_id))
    label = (quote.get("payload") or {}).get("label") or quote.get("package_id") or "detail"
    total = float(quote.get("total") or 0)
    booking = shop.get("booking_url") or shop.get("url") or ""
    vehicle = lead.get("vehicle") or {}
    car = " ".join(
        str(p) for p in (vehicle.get("year"), vehicle.get("make"), vehicle.get("model")) if p
    ) or "your car"
    body = (
        f"{lead.get('name') or 'Hey'}, {shop.get('shop_name')}. "
        f"{label} on the {car} is ${total:.0f}. "
        f"Grab a slot: {booking} or call {shop.get('phone')}."
    )
    to = contact.get("phone") or contact.get("email")
    channel = "sms" if contact.get("phone") else "email"
    enqueue(
        channel=channel,
        kind="quote_followup",
        body=body,
        to=to,
        subject=f"{label} quote · ${total:.0f}",
        lead_id=lead_id,
        band=lead.get("band"),
        store=store,
    )
    store.add_event(lead_id, "outreach_queued", channel)
    flash("Draft is in Send. Nothing leaves until you Approve.", "ok")
    return redirect(url_for("chroma.lead_detail", lead_id=lead_id))


@bp.route("/leads/<lead_id>/book", methods=["POST"])
@shop_required
def book_lead(lead_id: str):
    store = _store()
    lead = store.get_lead(lead_id)
    if not lead:
        return redirect(url_for("chroma.leads"))
    shop = _shop()
    quote = _latest_quote(lead_id)
    url = shop.get("booking_url") or shop.get("calendly_url") or shop.get("url")
    store.save_booking(
        {
            "lead_id": lead_id,
            "quote_id": (quote or {}).get("quote_id"),
            "calendly_url": url,
            "event": "square",
            "status": "link_ready",
        }
    )
    store.set_lead_status(lead_id, "ready_to_book")
    store.add_event(lead_id, "booking_link", url or "")
    flash("Booking link is on the card. Send it from Send or text it yourself.", "ok")
    return redirect(url_for("chroma.lead_detail", lead_id=lead_id))


@bp.route("/leads/<lead_id>/paid", methods=["POST"])
@shop_required
def paid_lead(lead_id: str):
    store = _store()
    lead = store.get_lead(lead_id)
    if not lead:
        return redirect(url_for("chroma.leads"))
    quote = _latest_quote(lead_id)
    try:
        amount = float(request.form.get("amount") or (quote or {}).get("total") or 0)
    except ValueError:
        amount = 0
    if amount <= 0:
        flash("Need a dollar amount. Price the job first or type what they paid.", "error")
        return redirect(url_for("chroma.lead_detail", lead_id=lead_id))
    label = (quote or {}).get("package_id") or "job"
    if quote and isinstance(quote.get("payload"), dict):
        label = quote["payload"].get("label") or label
    store.add_book(
        {
            "direction": "in",
            "amount": amount,
            "method": request.form.get("method") or "square",
            "note": f"{lead.get('name')} · {label}",
            "lead_id": lead_id,
        }
    )
    store.set_lead_status(lead_id, "paid")
    store.add_event(lead_id, "paid", f"${amount:.0f}")
    flash(f"${amount:.0f} on the books.", "ok")
    return redirect(url_for("chroma.lead_detail", lead_id=lead_id))


@bp.route("/quotes", methods=["GET", "POST"])
@shop_required
def quotes():
    store = _store()
    shop = _shop()
    from .protocols import PACKAGES

    packages = shop.get("packages") if isinstance(shop.get("packages"), dict) else None
    packages = packages or PACKAGES
    preview = None
    if request.method == "POST":
        agent = DetailingBookingAgent()
        preview = agent.quote(
            {
                "package_id": request.form.get("package_id") or "full_detail",
                "vehicle": {
                    "size": request.form.get("size") or "sedan",
                    "make": request.form.get("make") or None,
                    "body_style": request.form.get("body_style") or None,
                },
                "mobile": request.form.get("mobile") == "on",
                "addons": request.form.getlist("addons"),
                "packages": packages,
            }
        )
        if request.form.get("save"):
            lead_id = request.form.get("lead_id") or None
            saved = store.save_quote(preview, lead_id=lead_id)
            flash(f"Quote {saved['quote_id']} saved.", "ok")
            return redirect(url_for("chroma.quotes"))
    return render_template(
        "chroma/quotes.html",
        **_ctx(
            quotes=store.list_quotes(),
            matrix=quote_matrix(packages=packages),
            preview=preview,
            packages=packages,
        ),
    )


@bp.route("/quotes/<quote_id>/sent", methods=["POST"])
@shop_required
def mark_quote_sent(quote_id: str):
    _store().mark_quote_sent(quote_id, True)
    flash("Marked sent.", "ok")
    return redirect(url_for("chroma.quotes"))


@bp.route("/bookings")
@shop_required
def bookings():
    return render_template(
        "chroma/bookings.html",
        **_ctx(bookings=_store().list_bookings(), shop=_shop()),
    )


@bp.route("/books", methods=["GET", "POST"])
@shop_required
def books():
    store = _store()
    if request.method == "POST":
        try:
            amount = float(request.form.get("amount") or 0)
        except ValueError:
            amount = 0
        if amount > 0:
            store.add_book(
                {
                    "direction": request.form.get("direction") or "in",
                    "amount": amount,
                    "method": request.form.get("method") or "cash",
                    "note": request.form.get("note") or "",
                    "lead_id": request.form.get("lead_id") or None,
                }
            )
            flash("Logged.", "ok")
        else:
            flash("Need a dollar amount.", "error")
        return redirect(url_for("chroma.books"))
    return render_template(
        "chroma/books.html",
        **_ctx(entries=store.list_books()),
    )


@bp.route("/books/<item_id>/delete", methods=["POST"])
@shop_required
def books_delete(item_id: str):
    _store().delete_book(item_id)
    flash("Pulled that line.", "ok")
    return redirect(url_for("chroma.books"))


@bp.route("/outreach")
@shop_required
def outreach():
    items = _store().list_outbound()
    return render_template("chroma/outreach.html", **_ctx(items=items))


@bp.route("/outreach/<item_id>/approve", methods=["POST"])
@shop_required
def outreach_approve(item_id: str):
    try:
        result = approve(item_id, store=_store())
        if result["status"] == "approved_dry_run":
            flash("Approved — held. Flip CHROMA_LIVE_SEND to actually send.", "ok")
        else:
            flash("Approved and logged as sent.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("chroma.outreach"))


@bp.route("/outreach/<item_id>/kill", methods=["POST"])
@shop_required
def outreach_kill(item_id: str):
    try:
        kill(item_id, store=_store())
        flash("Killed. It will not send.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("chroma.outreach"))


@bp.route("/outreach/<item_id>/edit", methods=["POST"])
@shop_required
def outreach_edit(item_id: str):
    try:
        edit(
            item_id,
            body=request.form.get("body"),
            subject=request.form.get("subject"),
            store=_store(),
        )
        flash("Edited. Needs a fresh Approve.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("chroma.outreach"))


@bp.route("/settings", methods=["GET", "POST"])
@shop_required
def settings():
    store = _store()
    shop = _shop()
    if request.method == "POST":
        packages = shop.get("packages") if isinstance(shop.get("packages"), dict) else {}
        for pkg_id, pkg in list(packages.items()):
            raw = request.form.get(f"price_{pkg_id}")
            if raw:
                try:
                    pkg["price"] = float(raw)
                except ValueError:
                    pass
        calendly_url = request.form.get("calendly_url") or shop.get("calendly_url")
        updated = {
            "shop_name": request.form.get("shop_name") or shop.get("shop_name"),
            "address": request.form.get("address") or shop.get("address"),
            "city": request.form.get("city") or shop.get("city"),
            "region": request.form.get("region") or shop.get("region"),
            "zip": request.form.get("zip") or shop.get("zip"),
            "phone": request.form.get("phone") or shop.get("phone"),
            "email": request.form.get("email") or shop.get("email"),
            "url": request.form.get("url") or shop.get("url"),
            "booking_url": request.form.get("booking_url") or shop.get("booking_url"),
            "menu_url": request.form.get("menu_url") or shop.get("menu_url"),
            "gift_url": request.form.get("gift_url") or shop.get("gift_url"),
            "gbp_url": request.form.get("gbp_url") or shop.get("gbp_url"),
            "hours": request.form.get("hours") or shop.get("hours"),
            "service_area": request.form.get("service_area") or shop.get("service_area"),
            "tagline": request.form.get("tagline") or shop.get("tagline"),
            "calendly_url": calendly_url,
            "calendly_handle": calendly_handle_from_url(calendly_url or ""),
            "packages": packages,
        }
        store.save_settings(updated)
        flash("Shop saved.", "ok")
        return redirect(url_for("chroma.settings"))
    return render_template("chroma/settings.html", **_ctx())


def register_chroma(app: Flask) -> None:
    """Idempotent mount used by mvp_app, create_app, and the standalone factory."""
    if "chroma" in app.blueprints:
        return
    secret = app.config.get("SECRET_KEY") or os.environ.get("SECRET_KEY")
    if not secret:
        app.config["SECRET_KEY"] = secrets.token_hex(16)
    repo_root = Path(__file__).resolve().parents[2]
    templates = repo_root / "templates"
    static = repo_root / "static"
    if not app.template_folder:
        app.template_folder = str(templates)
    if not getattr(app, "static_folder", None):
        app.static_folder = str(static)
    app.register_blueprint(bp)
    if demo_mode():
        try:
            ensure_demo(get_store())
        except Exception:
            pass
