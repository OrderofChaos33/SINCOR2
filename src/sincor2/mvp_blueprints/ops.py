"""Ops, outreach, polyclaw, signup.

Extracted from mvp_app so the gunicorn entry stays the app factory.
Helpers and Flask `app` live in sincor2.mvp_app; this module binds them
after that module has finished constructing the application object.
"""
from __future__ import annotations

from flask import Blueprint

bp = Blueprint("mvp_ops", __name__)


def _bind_mvp():
    import sincor2.mvp_app as mvp
    g = globals()
    for key, value in vars(mvp).items():
        if key.startswith("__") or key in {"bp", "_bind_mvp"}:
            continue
        g[key] = value


_bind_mvp()


# ---------------------------------------------------------------------------
# One-scheduler consolidation (2026-10-11): all background jobs live on the
# single central scheduler in sincor2.scheduler. These helpers query it by
# canonical job id instead of the removed per-domain scheduler objects.
# ---------------------------------------------------------------------------

def _central_scheduler():
    import sincor2.mvp_app as mvp
    return getattr(mvp, 'central_scheduler', None)


def _central_job_next(job_id):
    sched = _central_scheduler()
    if not sched or not sched.running:
        return None
    try:
        job = sched.get_job(job_id)
        return str(job.next_run_time) if job else None
    except Exception:
        return None


def _central_job_running(job_id):
    sched = _central_scheduler()
    if not sched or not sched.running:
        return False
    try:
        return sched.get_job(job_id) is not None
    except Exception:
        return False


def _job_enabled(job_id):
    """Env gate for a canonical job, using the single SCHEDULER_DEFAULTS table."""
    from sincor2.scheduler import SCHEDULER_DEFAULTS
    spec = SCHEDULER_DEFAULTS[job_id]
    default = 'true' if spec['enabled'] else 'false'
    return os.environ.get(spec['env_enabled'], default).lower() == 'true'


@bp.route('/api/ops/schedulers', methods=['GET'])
def ops_schedulers_status():
    """Overview of background schedulers and env gates (central scheduler)."""
    return jsonify({
        'launch_ops': {
            'enabled': _job_enabled('launch_cycle'),
            'running': _central_job_running('launch_cycle'),
            'next_run': _central_job_next('launch_cycle'),
            'review_url': '/launch/review',
        },
        'review_reminder': {
            'enabled': _job_enabled('launch_review_notify'),
            'alert_email': os.environ.get('LAUNCH_REVIEW_ALERT_EMAIL', 'court@getsincor.com'),
            'running': _central_job_running('launch_review_notify'),
            'next_run': _central_job_next('launch_review_notify'),
        },
        'partner_reminder': {
            'enabled': _job_enabled('partner_notify'),
            'alert_email': (
                os.environ.get('PARTNER_OUTREACH_ALERT_EMAIL')
                or os.environ.get('LAUNCH_REVIEW_ALERT_EMAIL', 'court@getsincor.com')
            ),
            'running': _central_job_running('partner_notify'),
            'next_run': _central_job_next('partner_notify'),
            'partners_url': '/launch/partners',
        },
        'daily_ops': {
            'enabled': _job_enabled('daily_ops'),
            'running': _central_job_running('daily_ops'),
            'next_run': _central_job_next('daily_ops'),
            'latest_report': '/logs/ops/daily_latest.json',
        },
        'content_agent': {
            'enabled': _job_enabled('content_cycle'),
            'running': _central_job_running('content_cycle'),
            'next_run': _central_job_next('content_cycle'),
        },
        'outreach': {
            'enabled': _job_enabled('outreach_cycle'),
            'running': _central_job_running('outreach_cycle'),
            'next_run': _central_job_next('outreach_cycle'),
        },
        'compliance': (
            {
                'enabled': _job_enabled('compliance_checks'),
                'confidential': True,
                'running': _central_job_running('compliance_checks'),
                'next_run': _central_job_next('compliance_checks'),
            }
            if _check_admin_token(request) or _check_admin_key(request)
            else {
                'enabled': _job_enabled('compliance_checks'),
                'confidential': True,
            }
        ),
        'polyclaw': {
            'enabled': _job_enabled('polyclaw_scan'),
            'running': _central_job_running('polyclaw_scan'),
            'next_run': _central_job_next('polyclaw_scan'),
        },
        'windows_tasks': [
            'SINCOR Launch Daemons (logon)',
            'SINCOR Launch Content (daily)',
            'SINCOR Daily Ops (daily)',
            'SINCOR Weekly Buyers (weekly)',
        ],
        'timestamp': datetime.utcnow().isoformat(),
    }), 200


@bp.route('/api/outreach/status', methods=['GET'])
def outreach_status():
    """Show outreach engine status (admin use)."""
    try:
        from sincor2.outreach_engine import get_outreach_engine
        engine = get_outreach_engine()
        scheduler_running = _central_job_running('outreach_cycle')
        return jsonify({
            'enabled': engine.enabled,
            'scheduler_running': scheduler_running,
            'yelp_configured': bool(engine.yelp_key),
            'places_configured': bool(engine.places_key),
            'resend_configured': bool(os.environ.get('RESEND_API_KEY')),
            'daily_limit': engine.daily_limit,
            'total_sent_ever': len(engine._sent_ids),
            'next_run': _central_job_next('outreach_cycle') if scheduler_running else None,
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/polyclaw/status', methods=['GET'])
def polyclaw_status():
    """Show Polyclaw autonomous trading agent status."""
    try:
        from pathlib import Path
        trades_log = Path.home() / ".openclaw" / "workspace" / "polyclaw_trades.jsonl"

        scheduler_running = _central_job_running('polyclaw_scan')
        
        total_trades = 0
        total_profit = 0.0
        if trades_log.exists():
            for line in trades_log.read_text().strip().split('\n'):
                if line:
                    trade = json.loads(line)
                    total_trades += 1
                    total_profit += trade.get('net_profit_percent', 0)
        
        return jsonify({
            'enabled': os.getenv('POLYCLAW_ENABLED', 'true').lower() == 'true',
            'scheduler_running': scheduler_running,
            'auto_execute': os.getenv('POLYCLAW_AUTO_EXECUTE', 'true').lower() == 'true',
            'risk_level': os.getenv('POLYCLAW_RISK_LEVEL', 'medium'),
            'alert_threshold': float(os.getenv('POLYCLAW_ALERT_THRESHOLD', '0.5')),
            'scan_interval': int(os.getenv('POLYCLAW_SCAN_INTERVAL', '60')),
            'total_trades_executed': total_trades,
            'total_profit_percent': round(total_profit, 2),
            'wallet_address': '0x35cb3bf1b29F81d325EB9A7225a3E87fE7B37D6f',
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/signup', methods=['POST'])
def api_signup():
    """Signup endpoint — collect email + name, persist lead, return confirmation."""
    try:
        data = request.get_json() or {}
        email = data.get('email', '').strip().lower()
        name = data.get('name', '').strip()
        plan = (data.get('plan') or '').strip().lower()

        if not email or not name:
            return jsonify({'error': 'Email and name required'}), 400

        if '@' not in email:
            return jsonify({'error': 'Invalid email address'}), 400

        _upsert_lead(email, name)
        session['user_email'] = email
        session['user_name'] = name
        logger.info('[SIGNUP] New lead: %s (%s) plan=%s', name, email, plan or 'none')

        if email_sender:
            try:
                email_sender.send_welcome_email(
                    customer_email=email,
                    customer_name=name,
                    company_name='',
                    use_case='signup',
                    order_id='',
                )
            except Exception as exc:
                logger.warning('[SIGNUP] Welcome email failed: %s', exc)

        return jsonify({
            'success': True,
            'message': 'Signup successful! Redirecting to checkout...',
            'email': email,
            'name': name,
            'plan': plan,
        }), 200

    except Exception as e:
        logger.error('[SIGNUP] Error: %s', e)
        return jsonify({'error': str(e)}), 500


@bp.route('/api/outreach/run', methods=['POST'])
def outreach_run_now():
    """Manually trigger one outreach cycle (admin use)."""
    denied = _require_admin(request)
    if denied:
        return denied
    try:
        from sincor2.outreach_engine import get_outreach_engine
        import threading
        engine = get_outreach_engine()
        thread = threading.Thread(target=engine.run_cycle, daemon=True)
        thread.start()
        return jsonify({'status': 'started', 'message': 'Outreach cycle triggered in background'}), 202
    except Exception as e:
        return jsonify({'error': str(e)}), 500

