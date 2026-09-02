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


@bp.route('/api/ops/a2a/adoption-kpi', methods=['GET'])
def a2a_adoption_kpi():
    """North-star A2A adoption metric + launch-surface contract snapshot."""
    try:
        from sincor2.a2a_adoption_metrics import launch_surface_contract, weekly_adoption_kpi
    except Exception as exc:
        return jsonify({'status': 'error', 'error': f'adoption_metrics_unavailable:{exc}'}), 503

    window_days = request.args.get('window_days', '7')
    try:
        parsed_window = max(1, min(90, int(window_days)))
    except (TypeError, ValueError):
        parsed_window = 7

    payload = {
        'status': 'ok',
        'a2a_adoption': weekly_adoption_kpi(window_days=parsed_window),
        'launch_surface': launch_surface_contract(),
        'timestamp': datetime.utcnow().isoformat(),
    }
    return jsonify(payload), 200


@bp.route('/api/ops/schedulers', methods=['GET'])
def ops_schedulers_status():
    """Overview of background schedulers and env gates."""
    def _job_next(sched, job_id):
        if not sched or not sched.running:
            return None
        try:
            job = sched.get_job(job_id)
            return str(job.next_run_time) if job else None
        except Exception:
            return None

    return jsonify({
        'launch_ops': {
            'enabled': os.environ.get('LAUNCH_OPS_ENABLED', 'false').lower() == 'true',
            'running': bool(launch_ops_scheduler and launch_ops_scheduler.running),
            'next_run': _job_next(launch_ops_scheduler, 'launch_ops_content'),
            'review_url': '/launch/review',
        },
        'review_reminder': {
            'enabled': os.environ.get('LAUNCH_REVIEW_REMINDER_ENABLED', 'true').lower() != 'false',
            'alert_email': os.environ.get('LAUNCH_REVIEW_ALERT_EMAIL', 'court@getsincor.com'),
            'running': bool(review_reminder_scheduler and review_reminder_scheduler.running),
            'next_run': _job_next(review_reminder_scheduler, 'launch_review_reminder'),
        },
        'partner_reminder': {
            'enabled': os.environ.get('PARTNER_OUTREACH_ENABLED', 'false').lower() == 'true',
            'alert_email': (
                os.environ.get('PARTNER_OUTREACH_ALERT_EMAIL')
                or os.environ.get('LAUNCH_REVIEW_ALERT_EMAIL', 'court@getsincor.com')
            ),
            'running': bool(partner_reminder_scheduler and partner_reminder_scheduler.running),
            'next_run': _job_next(partner_reminder_scheduler, 'partner_outreach_reminder'),
            'partners_url': '/launch/partners',
        },
        'daily_ops': {
            'enabled': os.environ.get('DAILY_OPS_ENABLED', 'false').lower() == 'true',
            'running': bool(daily_ops_scheduler and daily_ops_scheduler.running),
            'next_run': _job_next(daily_ops_scheduler, 'daily_ops'),
            'latest_report': '/logs/ops/daily_latest.json',
        },
        'content_agent': {
            'enabled': os.environ.get('CONTENT_AGENT_ENABLED', 'false').lower() == 'true',
            'running': bool(content_scheduler and content_scheduler.running),
            'next_run': _job_next(content_scheduler, 'content_cycle'),
        },
        'outreach': {
            'enabled': os.environ.get('OUTREACH_ENABLED', 'false').lower() == 'true',
            'running': bool(outreach_scheduler and outreach_scheduler.running),
            'next_run': _job_next(outreach_scheduler, 'outreach_cycle'),
        },
        'compliance': (
            {
                'enabled': os.environ.get('COMPLIANCE_MONITOR_ENABLED', 'false').lower() == 'true',
                'confidential': True,
                'running': bool(compliance_scheduler and compliance_scheduler.running),
                'next_run': _job_next(compliance_scheduler, 'compliance_monitor'),
            }
            if _check_admin_token(request) or _check_admin_key(request)
            else {
                'enabled': os.environ.get('COMPLIANCE_MONITOR_ENABLED', 'false').lower() == 'true',
                'confidential': True,
            }
        ),
        'polyclaw': {
            'enabled': os.environ.get('POLYCLAW_ENABLED', 'false').lower() == 'true',
            'running': bool(polyclaw_scheduler and polyclaw_scheduler.running),
            'next_run': _job_next(polyclaw_scheduler, 'polyclaw_scan'),
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
        scheduler_running = outreach_scheduler is not None and outreach_scheduler.running if outreach_scheduler else False
        return jsonify({
            'enabled': engine.enabled,
            'scheduler_running': scheduler_running,
            'yelp_configured': bool(engine.yelp_key),
            'places_configured': bool(engine.places_key),
            'resend_configured': bool(os.environ.get('RESEND_API_KEY')),
            'daily_limit': engine.daily_limit,
            'total_sent_ever': len(engine._sent_ids),
            'next_run': str(outreach_scheduler.get_job('outreach_cycle').next_run_time) if scheduler_running else None,
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/polyclaw/status', methods=['GET'])
def polyclaw_status():
    """Show Polyclaw autonomous trading agent status."""
    try:
        from pathlib import Path
        trades_log = Path.home() / ".openclaw" / "workspace" / "polyclaw_trades.jsonl"
        
        scheduler_running = polyclaw_scheduler is not None and polyclaw_scheduler.running if polyclaw_scheduler else False
        
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
