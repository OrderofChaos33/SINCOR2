"""Health and readiness probes.

Extracted from mvp_app so the gunicorn entry stays the app factory.
Helpers and Flask `app` live in sincor2.mvp_app; this module binds them
after that module has finished constructing the application object.
"""
from __future__ import annotations

from flask import Blueprint

bp = Blueprint("mvp_health", __name__)


def _bind_mvp():
    import sincor2.mvp_app as mvp
    g = globals()
    for key, value in vars(mvp).items():
        if key.startswith("__") or key in {"bp", "_bind_mvp"}:
            continue
        g[key] = value


_bind_mvp()


# ==============================================================================
# HEALTH & STATUS ENDPOINTS
# ==============================================================================

# DEBUG ENDPOINT REMOVED - was leaking env var status to public


@bp.route('/health', methods=['GET'])
def health():
    """Health check endpoint for Railway and monitoring."""
    return jsonify(_build_runtime_health_report(include_optional=True)), 200


@bp.route('/ready', methods=['GET'])
def readiness():
    """Readiness endpoint for deep infrastructure checks."""
    payload = _build_runtime_health_report(include_optional=True)
    status_code = 200 if payload.get('readiness', {}).get('ready') else 503
    return jsonify(payload), status_code


def _probe_database() -> tuple[bool, str]:
    """Validate DB connectivity for runtime health."""
    try:
        with sqlite3.connect(DB_PATH, timeout=3) as conn:
            conn.execute('SELECT 1').fetchone()
        return True, 'ok'
    except sqlite3.Error:
        logger.exception('[HEALTH] database probe failed')
        return False, 'db_error'


def _probe_jsonrpc(url: str, method: str = 'eth_chainId', timeout: int = 3) -> tuple[bool, str]:
    """Probe JSON-RPC endpoint and return readiness result."""
    payload = json.dumps({'jsonrpc': '2.0', 'id': 'health', 'method': method, 'params': []}).encode('utf-8')
    req = urllib_request.Request(
        url,
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'SINCOR2-health/1.0',
        },
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode('utf-8'))
        result = body.get('result')
        if result:
            return True, str(result)
        return False, 'missing_result'
    except (urllib_error.URLError, TimeoutError, ValueError, OSError) as err:
        logger.warning('[HEALTH] jsonrpc probe failed url=%s err=%s', url, err)
        return False, 'rpc_error'


_PUBLIC_BASE_RPCS = (
    'https://mainnet.base.org',
    'https://base-rpc.publicnode.com',
    'https://base.drpc.org',
    'https://base.meowrpc.com',
    'https://base.gateway.tenderly.co',
    'https://base.llamarpc.com',
    'https://1rpc.io/base',
)


def _probe_base_rpc() -> tuple[bool, str, str]:
    env = (os.environ.get('BASE_RPC_URL') or os.environ.get('BASE_RPC') or '').strip()
    candidates = []
    if env:
        candidates.append(('env', env))
    for url in _PUBLIC_BASE_RPCS:
        if url != env:
            candidates.append(('public_default', url))
    last = (False, 'not_configured', 'none')
    for source, url in candidates:
        ok, detail = _probe_jsonrpc(url)
        if ok:
            return True, detail, source
        last = (False, detail, source)
    return last


def _build_runtime_health_report(include_optional: bool = True) -> dict:
    """Build runtime health and readiness payload with component checks."""
    run_id = request.headers.get('X-Run-ID', '')
    request_id = request.headers.get('X-Request-ID', '')
    correlation_id = request.headers.get('X-Correlation-ID', request_id or run_id)
    now = datetime.utcnow().isoformat()

    db_ready, db_detail = _probe_database()
    base_ready, base_detail, base_source = _probe_base_rpc()

    stripe_configured = bool((os.environ.get('STRIPE_SECRET_KEY') or '').strip())
    paypal_configured = bool((os.environ.get('PAYPAL_REST_API_ID') or '').strip())
    anthropic_configured = bool((os.environ.get('ANTHROPIC_API_KEY') or '').strip())

    checks = {
        'database': {'ready': db_ready, 'critical': True, 'detail': db_detail},
        'base_rpc': {
            'ready': base_ready,
            'critical': False,
            'detail': base_detail,
            'source': base_source,
        },
        'stripe': {'ready': (not stripe_configured) or bool(STRIPE_AVAILABLE), 'critical': False, 'detail': 'configured' if stripe_configured else 'not_configured'},
        'paypal': {'ready': True, 'critical': False, 'detail': 'configured' if paypal_configured else 'not_configured'},
        'anthropic': {'ready': True, 'critical': False, 'detail': 'configured' if anthropic_configured else 'not_configured'},
    }
    try:
        from sincor2.task_queue import queue_health
        qh = queue_health()
        checks['task_queue'] = {
            'ready': True,
            'critical': False,
            'detail': qh.get('backend', 'unknown'),
            'redis': qh.get('redis'),
        }
    except Exception as qexc:
        checks['task_queue'] = {'ready': True, 'critical': False, 'detail': f'unavailable:{qexc}'}
    try:
        from sincor2.a2a_inbound import health_snapshot as _a2a_inbound_health
        checks['a2a_inbound'] = _a2a_inbound_health()
    except Exception as _a2a_h:
        checks['a2a_inbound'] = {'ready': False, 'critical': False, 'detail': f'unavailable:{_a2a_h}'}
    if not include_optional:
        checks = {k: v for k, v in checks.items() if v.get('critical')}

    critical_ready = all(check['ready'] for check in checks.values() if check.get('critical'))
    overall_ready = all(check['ready'] for check in checks.values())
    degraded = critical_ready and not overall_ready
    payload = {
        'status': 'healthy' if critical_ready else 'degraded',
        'service': 'SINCOR2 MVP',
        'timestamp': now,
        'version': '1.0.0-mvp',
        'checks': checks,
        'readiness': {
            'ready': critical_ready,
            'degraded': degraded,
            'confidence': 1.0 if critical_ready else 0.2,
        },
        'context': {
            'run_id': run_id or f'health-{uuid.uuid4().hex[:12]}',
            'agent_id': 'mvp_runtime',
            'task_id': request_id or correlation_id or '-',
            'correlation_id': correlation_id or '-',
        },
    }
    logger.info(
        '[HEALTH] %s',
        json.dumps(
            {
                'event': 'runtime_health_probe',
                'outcome': payload['status'],
                'readiness': payload['readiness'],
                'checks': {key: val.get('detail') for key, val in checks.items()},
                'context': payload['context'],
                'ts': now,
            }
        ),
    )
    return payload
