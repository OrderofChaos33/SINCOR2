from __future__ import annotations

from flask import Blueprint, render_template

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
