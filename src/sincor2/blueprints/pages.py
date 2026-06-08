from __future__ import annotations

from flask import Blueprint, redirect, render_template, request

pages_bp = Blueprint("pages", __name__)


@pages_bp.get("/")
def home():
    return render_template("home.html")


@pages_bp.get("/sinc")
def sinc_gateway():
    return render_template("sinc_gateway.html")


@pages_bp.get("/mvp")
def mvp_dashboard():
    return render_template("index_mvp.html")


@pages_bp.get("/signup")
def signup():
    return render_template("signup.html")


@pages_bp.get("/wallet-connect")
def wallet_connect():
    return redirect("/sinc", code=302)


@pages_bp.get("/buy")
def buy():
    return render_template("buy.html")


@pages_bp.get("/pricing")
def pricing():
    return render_template("pricing.html")


@pages_bp.get("/login")
def login():
    return render_template("login.html")


@pages_bp.get("/terms")
def terms():
    return render_template("terms.html")


@pages_bp.get("/privacy")
def privacy():
    return render_template("privacy.html")


@pages_bp.get("/axiom")
def axiom():
    return render_template("axiom.html")


@pages_bp.get("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@pages_bp.get("/my-orders")
def my_orders():
    return render_template("my_orders.html")


@pages_bp.get("/payment/success")
def payment_success():
    session_id = request.args.get("session_id", "")
    return render_template("payment_success.html", session_id=session_id)


@pages_bp.get("/payment/cancel")
def payment_cancel():
    return render_template("payment_cancel.html")


@pages_bp.get("/onboarding")
def onboarding():
    return render_template("onboarding.html")


@pages_bp.get("/security")
def security():
    return render_template("security.html")


@pages_bp.get("/billing")
def billing():
    return redirect("/dashboard", code=302)
