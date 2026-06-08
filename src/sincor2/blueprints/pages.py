from __future__ import annotations

from flask import Blueprint, redirect, render_template

pages_bp = Blueprint("pages", __name__)


@pages_bp.get("/")
def home():
    return render_template("home.html")


@pages_bp.get("/sinc")
def sinc_gateway():
    return render_template("sinc_gateway.html")


@pages_bp.get("/mvp")
def legacy_mvp_redirect():
    return redirect("/", code=302)


@pages_bp.get("/signup")
def signup():
    return render_template("signup.html")


@pages_bp.get("/wallet-connect")
def wallet_connect():
    return redirect("/sinc", code=302)


@pages_bp.get("/buy")
def buy():
    return render_template("buy.html")


@pages_bp.get("/privacy")
def privacy():
    return render_template("privacy.html")


@pages_bp.get("/terms")
def terms():
    return render_template("terms.html")


@pages_bp.get("/axiom")
def axiom():
    return render_template("axiom.html")
