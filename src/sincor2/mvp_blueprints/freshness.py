"""Public metric freshness probe."""
from __future__ import annotations

from flask import Blueprint, jsonify

bp = Blueprint("metrics_freshness", __name__)


@bp.route("/api/metrics/freshness", methods=["GET"])
def metrics_freshness():
    from sincor2.metrics_freshness import freshness_report
    report = freshness_report()
    return jsonify(report), 200 if report.get("ok") else 503
