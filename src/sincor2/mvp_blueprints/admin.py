"""Admin content vault and calendar.

Extracted from mvp_app so the gunicorn entry stays the app factory.
Helpers and Flask `app` live in sincor2.mvp_app; this module binds them
after that module has finished constructing the application object.
"""
from __future__ import annotations

from flask import Blueprint

bp = Blueprint("mvp_admin", __name__)


def _bind_mvp():
    import sincor2.mvp_app as mvp
    g = globals()
    for key, value in vars(mvp).items():
        if key.startswith("__") or key in {"bp", "_bind_mvp"}:
            continue
        g[key] = value


_bind_mvp()


# ============================================================================
# CONTENT AGENT — STATUS + TRIGGER ENDPOINTS (Admin only)
# ============================================================================

@bp.route('/admin/content/status')
@limiter.limit("10 per minute")
def content_status():
    """Return content agent status: published posts, upcoming calendar, analytics."""
    if not _check_admin_token(request):
        return jsonify({'error': 'Admin access required'}), 403
    try:
        from sincor2.content_agent import get_db, CALENDAR_PATH, ContentAnalytics
        import json as _json

        with get_db() as conn:
            posts = conn.execute(
                "SELECT slug, title, keyword, status, word_count, published_at, wp_url "
                "FROM posts ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
            total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]

        calendar = []
        if CALENDAR_PATH.exists():
            all_items = _json.loads(CALENDAR_PATH.read_text())
            calendar = [i for i in all_items if i["status"] == "planned"][:10]

        analytics = ContentAnalytics()
        top = analytics.get_top_performers(3)

        scheduler_status = "running" if (content_scheduler and content_scheduler.running) else "stopped"

        return jsonify({
            "scheduler": scheduler_status,
            "total_posts": total_posts,
            "recent_posts": [dict(p) for p in posts],
            "upcoming_calendar": calendar,
            "top_performers": top,
        })
    except Exception as e:
        logger.error(f"[CONTENT] Status error: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/admin/content/generate', methods=['POST'])
@limiter.limit("10 per minute")
def content_generate():
    """Manually trigger post generation. Returns 202 + task_id; poll /api/tasks/<id>."""
    if not _check_admin_token(request):
        return jsonify({'error': 'Admin access required'}), 403
    try:
        data = request.get_json() or {}
        keyword = sanitize_string(data.get('keyword', ''), 200)
        ctype = data.get('type', 'how-to')
        do_publish = data.get('publish', False)

        if not keyword:
            return jsonify({'error': 'keyword required'}), 400
        if ctype not in ('how-to', 'comparison', 'alternatives', 'case-study', 'industry-trend'):
            return jsonify({'error': 'invalid type'}), 400

        from sincor2.task_queue import accepted_payload, enqueue

        job = enqueue('content.generate', {
            'keyword': keyword,
            'ctype': ctype,
            'do_publish': bool(do_publish),
            'model': os.environ.get('CONTENT_MODEL', 'claude-haiku-4-5'),
        })
        return jsonify(accepted_payload(job)), 202
    except Exception as e:
        logger.error(f"[CONTENT] Generate error: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/admin/content/calendar', methods=['GET', 'POST'])
@limiter.limit("20 per minute")
def content_calendar():
    """GET: return calendar JSON. POST: regenerate calendar."""
    if not _check_admin_token(request):
        return jsonify({'error': 'Admin access required'}), 403
    try:
        from sincor2.content_agent import generate_content_calendar, CALENDAR_PATH, init_db
        import json as _json
        if request.method == 'POST':
            init_db()
            cal = generate_content_calendar()
            return jsonify({"generated": len(cal), "calendar": cal[:20]})
        else:
            if not CALENDAR_PATH.exists():
                return jsonify({"error": "No calendar found. POST to generate."}), 404
            cal = _json.loads(CALENDAR_PATH.read_text())
            return jsonify({"total": len(cal), "calendar": cal})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route('/admin/content/analytics')
@limiter.limit("30 per minute")
def content_analytics():
    """Return content analytics report."""
    if not _check_admin_token(request):
        return jsonify({'error': 'Admin access required'}), 403
    try:
        from sincor2.content_agent import ContentAnalytics
        analytics = ContentAnalytics()
        return jsonify({
            "top_performers": analytics.get_top_performers(10),
            "low_performers": analytics.get_low_performers(),
            "cta_performance": analytics.get_best_cta(),
            "report": analytics.summary_report(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

