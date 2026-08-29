"""Launch review and partner ops.

Extracted from mvp_app so the gunicorn entry stays the app factory.
Helpers and Flask `app` live in sincor2.mvp_app; this module binds them
after that module has finished constructing the application object.
"""
from __future__ import annotations

from flask import Blueprint

bp = Blueprint("mvp_launch", __name__)


def _bind_mvp():
    import sincor2.mvp_app as mvp
    g = globals()
    for key, value in vars(mvp).items():
        if key.startswith("__") or key in {"bp", "_bind_mvp"}:
            continue
        g[key] = value


_bind_mvp()


@bp.route('/launch/review')
def launch_review_page():
    """Human review queue for agent-drafted launch content."""
    return render_template('launch_review.html')


@bp.route('/launch/partners')
def launch_partners_page():
    """KOL / curator partner outreach pipeline for July 7 launch."""
    return render_template('launch_partners.html')


@bp.route('/api/launch/partners', methods=['GET'])
def launch_partners_api():
    """Partner CRM summary + today's due outreach."""
    denied = _require_admin(request)
    if denied:
        return denied
    try:
        from sincor2.partner_outreach import (
            due_outreach,
            list_partners,
            pipeline_summary,
        )
        return jsonify({
            'summary': pipeline_summary(),
            'due': due_outreach(limit=15),
            'partners': list_partners(),
        })
    except Exception as e:
        logger.error('[PARTNERS] API error: %s', e)
        return jsonify({'error': str(e)}), 500


@bp.route('/api/launch/partners/<partner_id>', methods=['POST'])
def launch_partners_update(partner_id):
    """Mark partner status after outreach."""
    denied = _require_admin(request)
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    status = (data.get('status') or '').strip()
    notes = (data.get('notes') or '').strip()
    try:
        from sincor2.partner_outreach import update_status
        if not update_status(partner_id, status, notes=notes):
            return jsonify({'ok': False, 'error': 'invalid_status_or_partner'}), 400
        return jsonify({'ok': True, 'partner_id': partner_id, 'status': status})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


def _launch_review_modules():
    import sys
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from launch_content_engine.review_queue import (
        approve_and_post,
        list_drafts,
        set_status,
    )
    return list_drafts, set_status, approve_and_post


@bp.route('/api/launch/review')
def launch_review_list():
    denied = _require_admin(request)
    if denied:
        return denied
    status = request.args.get('status', 'pending')
    list_drafts, _, _ = _launch_review_modules()
    return jsonify(list_drafts(status=status or None))


@bp.route('/api/launch/review/<draft_id>', methods=['POST'])
def launch_review_action(draft_id):
    denied = _require_admin(request)
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    action = data.get('action', '')
    list_drafts, set_status, approve_and_post = _launch_review_modules()

    if action == 'reject':
        ok = set_status(draft_id, 'rejected')
        return jsonify({'ok': ok, 'status': 'rejected'})
    if action == 'approve':
        result = approve_and_post(draft_id)
        return jsonify(result)
    return jsonify({'ok': False, 'error': 'invalid_action'}), 400

