"""WebBuilder studio, preview, and publish.

Extracted from mvp_app so the gunicorn entry stays the app factory.
Helpers and Flask `app` live in sincor2.mvp_app; this module binds them
after that module has finished constructing the application object.
"""
from __future__ import annotations

from flask import Blueprint

bp = Blueprint("mvp_webbuilder", __name__)


def _bind_mvp():
    import sincor2.mvp_app as mvp
    g = globals()
    for key, value in vars(mvp).items():
        if key.startswith("__") or key in {"bp", "_bind_mvp"}:
            continue
        g[key] = value


_bind_mvp()


@bp.route('/verticals/webbuilder')
@bp.route('/webbuilder')
def vertical_webbuilder():
    """WebBuilder swarm vertical — find SMBs, build sites, market autonomously."""
    return render_template('vertical_webbuilder.html')


@bp.route('/verticals/webbuilder/studio')
@bp.route('/webbuilder/studio')
def webbuilder_studio_page():
    """Dedicated WebBuilder workspace — projects, preview, migration planner."""
    return render_template('webbuilder_studio.html')


def _webbuilder_html_response(html: str):
    resp = make_response(html)
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    return resp


@bp.route('/preview/<slug>')
def webbuilder_preview_page(slug):
    """Staging preview lane — serves Orion HTML from disk."""
    from sincor2.webbuilder_studio import get_site_html, project_by_slug

    html = get_site_html(slug, 'preview') or get_site_html(slug, 'draft')
    if html:
        return _webbuilder_html_response(html)
    p = project_by_slug(slug)
    if p:
        active = next((s for s in p.get('migration', []) if s.get('status') == 'active'), None)
        label = active['title'] if active else (p.get('status') or 'preview')
        return render_template('webbuilder_preview.html', project=p, migration_label=label)
    return render_template('error.html', code=404, title='Preview Not Found',
                           message='This staging preview does not exist or was removed.'), 404


@bp.route('/site/<slug>')
def webbuilder_live_page(slug):
    """Live lane — published production HTML (draft-safe)."""
    from sincor2.webbuilder_studio import get_site_html, project_by_slug

    html = get_site_html(slug, 'live')
    if html:
        return _webbuilder_html_response(html)
    p = project_by_slug(slug)
    if p:
        return redirect(p.get('preview_url') or '/verticals/webbuilder/studio', code=302)
    return render_template('error.html', code=404, title='Site Not Found',
                           message='This site has not been published to the live lane yet.'), 404


@bp.route('/api/webbuilder/studio')
def api_webbuilder_studio_home():
    denied = _require_admin(request)
    if denied:
        return denied
    from sincor2.webbuilder_studio import studio_home
    return jsonify(studio_home())


@bp.route('/api/webbuilder/projects', methods=['GET', 'POST'])
def api_webbuilder_projects():
    from sincor2.webbuilder_studio import create_project, list_projects

    denied = _require_admin(request)
    if denied:
        return denied
    if request.method == 'GET':
        return jsonify({'ok': True, 'projects': list_projects()})
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name_required'}), 400
    project = create_project(
        name=name,
        niche=data.get('niche', ''),
        source_type=data.get('source_type', 'none'),
        source_url=data.get('source_url', ''),
        territory=data.get('territory', ''),
        owner_email=data.get('owner_email', ''),
        prompt=data.get('prompt', ''),
    )
    return jsonify({'ok': True, 'project': project}), 201


@bp.route('/api/webbuilder/projects/<project_id>')
def api_webbuilder_project_get(project_id):
    denied = _require_admin(request)
    if denied:
        return denied
    from sincor2.webbuilder_studio import get_project

    p = get_project(project_id)
    if not p:
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    return jsonify(p)


@bp.route('/api/webbuilder/projects/<project_id>/migration')
def api_webbuilder_migration(project_id):
    denied = _require_admin(request)
    if denied:
        return denied
    from sincor2.webbuilder_studio import migration_status

    status = migration_status(project_id)
    if not status.get('ok'):
        return jsonify(status), 404
    return jsonify(status)


@bp.route('/api/webbuilder/projects/<project_id>/run', methods=['POST'])
def api_webbuilder_run(project_id):
    denied = _require_admin(request)
    if denied:
        return denied
    from sincor2.task_queue import accepted_payload, enqueue

    job = enqueue('webbuilder.run', {'project_id': project_id})
    return jsonify(accepted_payload(job)), 202


@bp.route('/api/webbuilder/projects/<project_id>/approve', methods=['POST'])
def api_webbuilder_approve(project_id):
    denied = _require_admin(request)
    if denied:
        return denied
    from sincor2.webbuilder_studio import approve_preview

    p = approve_preview(project_id)
    if not p:
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    return jsonify(p)


@bp.route('/api/webbuilder/projects/<project_id>/domain', methods=['POST'])
def api_webbuilder_domain(project_id):
    denied = _require_admin(request)
    if denied:
        return denied
    from sincor2.webbuilder_studio import connect_domain

    data = request.get_json(silent=True) or {}
    result = connect_domain(
        project_id,
        data.get('domain', ''),
        include_www=bool(data.get('include_www', True)),
    )
    if not result.get('ok'):
        return jsonify(result), 400
    return jsonify(result)


@bp.route('/api/webbuilder/projects/<project_id>/verify-dns', methods=['POST'])
def api_webbuilder_verify_dns(project_id):
    denied = _require_admin(request)
    if denied:
        return denied
    from sincor2.webbuilder_studio import verify_dns

    return jsonify(verify_dns(project_id))


@bp.route('/api/webbuilder/projects/<project_id>/rebuild', methods=['POST'])
def api_webbuilder_rebuild(project_id):
    denied = _require_admin(request)
    if denied:
        return denied
    from sincor2.task_queue import accepted_payload, enqueue

    data = request.get_json(silent=True) or {}
    job = enqueue('webbuilder.rebuild', {'project_id': project_id, 'prompt': data.get('prompt')})
    return jsonify(accepted_payload(job)), 202


@bp.route('/api/webbuilder/projects/<project_id>/republish-preview', methods=['POST'])
def api_webbuilder_republish_preview(project_id):
    denied = _require_admin(request)
    if denied:
        return denied
    from sincor2.webbuilder_studio import republish_preview

    return jsonify(republish_preview(project_id))


@bp.route('/api/webbuilder/projects/<project_id>/publish-live', methods=['POST'])
def api_webbuilder_publish_live(project_id):
    denied = _require_admin(request)
    if denied:
        return denied
    from sincor2.webbuilder_studio import publish_live

    return jsonify(publish_live(project_id))


@bp.route('/api/webbuilder/projects/<project_id>/contacts')
def api_webbuilder_contacts(project_id):
    denied = _require_admin(request)
    if denied:
        return denied
    from sincor2.webbuilder_crm import list_contacts
    from sincor2.webbuilder_studio import data_dir, get_project

    if not get_project(project_id):
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    return jsonify({'ok': True, 'contacts': list_contacts(data_dir(), project_id)})


@bp.route('/api/webbuilder/contact', methods=['POST'])
def api_webbuilder_contact():
    from sincor2.webbuilder_studio import submit_contact

    data = request.get_json(silent=True) or {}
    project_id = (data.get('project_id') or '').strip()
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    if not project_id or not name or not email:
        return jsonify({'ok': False, 'error': 'missing_fields'}), 400
    return jsonify(submit_contact(
        project_id=project_id,
        name=name,
        email=email,
        phone=data.get('phone', ''),
        message=data.get('message', ''),
    ))

