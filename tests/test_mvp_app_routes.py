"""Smoke test: mvp_app must expose the routes Railway's healthcheck depends on.

This guards against the recurring 'placeholder overwrite' regression where
src/sincor2/mvp_app.py gets replaced by a skeleton with no route registration
and every path (including /health) starts returning 404 on Railway.
"""

import os

os.environ.setdefault('FLASK_ENV', 'test')
os.environ.setdefault('SINCOR_TASK_QUEUE', 'eager')


def _rules():
    from sincor2.mvp_app import app
    return {r.rule for r in app.url_map.iter_rules()}


def test_mvp_app_registers_blueprints():
    rules = _rules()
    assert len(rules) > 5, f'mvp_app has only {len(rules)} routes - skeleton regression?'


def test_mvp_app_has_health_and_ready():
    rules = _rules()
    assert '/health' in rules, '/health missing - Railway healthcheck will 404'
    assert '/ready' in rules, '/ready missing - Docker HEALTHCHECK will fail'
