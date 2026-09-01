"""SINC token pages and metadata.

Extracted from mvp_app so the gunicorn entry stays the app factory.
Helpers and Flask `app` live in sincor2.mvp_app; this module binds them
after that module has finished constructing the application object.
"""
from __future__ import annotations

from flask import Blueprint

bp = Blueprint("mvp_sinc", __name__)


def _bind_mvp():
    import sincor2.mvp_app as mvp
    g = globals()
    for key, value in vars(mvp).items():
        if key.startswith("__") or key in {"bp", "_bind_mvp"}:
            continue
        g[key] = value


_bind_mvp()


@bp.route('/sinc')
@bp.route('/sinc/acceptance')
@bp.route('/sinc/vs-agent-tokens')
@bp.route('/refer')
def sinc_token():
    """Bonding-curve gateway is retired. Send traffic to platform checkout."""
    return redirect('/buy', code=302)


@bp.route('/sinc/recover-hook')
def sinc_recover_hook():
    """MetaMask-signed hook floor cancel — Account 6, no private key export."""
    return render_template('hook_recover.html')


@bp.route('/api/price/official')
def api_price_official():
    """Canonical pricing — bonding curve spot + hook USDC walls (separate buy paths)."""
    try:
        from launch_content_engine.onchain_stats import build_official_price_payload
        payload = build_official_price_payload()
        resp = make_response(jsonify(payload))
        resp.headers['Cache-Control'] = 'public, max-age=60'
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp, 200
    except Exception as e:
        logger.warning('[PRICE] official error: %s', e)
        return jsonify({'error': str(e)}), 500


@bp.route('/api/hook/status')
def api_hook_status():
    """Live curve + hook inventory for gateway widgets."""
    try:
        from sincor2.hook_stats import fetch_hook_status
        return jsonify(fetch_hook_status()), 200
    except Exception as e:
        logger.warning('[HOOK] status error: %s', e)
        return jsonify({'error': str(e)}), 500


@bp.route('/api/acceptance/status')
def api_acceptance_status():
    """Whitelist / wallet acceptance checklist."""
    try:
        from sincor2.acceptance_status import fetch_acceptance
        return jsonify(fetch_acceptance()), 200
    except Exception as e:
        logger.warning('[ACCEPTANCE] status error: %s', e)
        return jsonify({'error': str(e)}), 500


from sincor2.onchain.constants import SINC_TOKEN as _CANONICAL_SINC

SINC_TOKEN = _CANONICAL_SINC
SINC_LOGO_URL = 'https://raw.githubusercontent.com/OrderofChaos33/SINCOR2/main/static/tokenlists/assets/logo-256.png'
SINC_LOGO_URL_MIRROR = 'https://getsincor.com/static/tokenlists/assets/logo-256.png'


def _cors_static_response(path, mimetype='application/octet-stream'):
    if not os.path.isfile(path):
        return None
    resp = make_response(send_file(path, mimetype=mimetype))
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp


def _token_list_response():
    path = os.path.join(static_dir, 'tokenlists', 'sincor.tokenlist.json')
    resp = _cors_static_response(path, 'application/json')
    if resp is None:
        return jsonify({'error': 'token list not found'}), 404
    return resp


@bp.route('/tokenlists/sincor.tokenlist.json')
@bp.route('/.well-known/tokenlist.json')
@bp.route('/static/tokenlists/sincor.tokenlist.json')
def token_list_json():
    """Uniswap-format token list — wallet import (MetaMask, Rabby, 1inch)."""
    return _token_list_response()


@bp.route('/static/tokenlists/assets/<path:filename>')
@bp.route('/tokenlists/assets/<path:filename>')
def token_list_assets(filename):
    """Token logos for wallets and explorers (Blockscout, Trust Wallet, TKN)."""
    safe = os.path.basename(filename)
    path = os.path.join(static_dir, 'tokenlists', 'assets', safe)
    if safe.endswith('.svg'):
        mimetype = 'image/svg+xml'
    elif safe.endswith('.png'):
        mimetype = 'image/png'
    else:
        return jsonify({'error': 'unsupported asset'}), 404
    resp = _cors_static_response(path, mimetype)
    if resp is None:
        return jsonify({'error': 'asset not found'}), 404
    return resp


@bp.route('/api/token/security')
def sinc_token_security():
    """GoPlus + Blockscout signals explaining wallet suspicious UI."""
    try:
        from sincor2.token_security import diagnose
        resp = make_response(jsonify(diagnose()))
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Cache-Control'] = 'public, max-age=120'
        return resp
    except Exception as e:
        logger.warning('[TOKEN] security error: %s', e)
        return jsonify({'error': str(e)}), 500


@bp.route('/.well-known/sinc-token.json')
@bp.route('/api/token/metadata')
def sinc_token_metadata():
    """Machine-readable token metadata for explorers and compliance tooling."""
    meta_path = os.path.join(project_root, 'scripts', 'token_metadata.json')
    payload = {
        'chainId': 8453,
        'address': SINC_TOKEN,
        'name': 'SINC',
        'symbol': 'SINC',
        'decimals': 8,
        'logoURI': SINC_LOGO_URL,
        'logoURIMirror': SINC_LOGO_URL_MIRROR,
        'website': 'https://getsincor.com',
        'explorer': f'https://basescan.org/token/{SINC_TOKEN}',
        'blockscout': f'https://base.blockscout.com/token/{SINC_TOKEN}',
        'tokenList': 'https://getsincor.com/tokenlists/sincor.tokenlist.json',
    }
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding='utf-8') as f:
                payload.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    # JSON extras may add copy. They cannot change the live contract identity
    # or resurrect a retired address (that is how the old SINC leaked onto getsincor.com).
    payload['address'] = SINC_TOKEN
    payload['chainId'] = 8453
    payload['decimals'] = 8
    payload['explorer'] = f'https://basescan.org/token/{SINC_TOKEN}'
    payload['blockscout'] = f'https://base.blockscout.com/token/{SINC_TOKEN}'
    sec = payload.get('security')
    if isinstance(sec, dict):
        sec['sourcify'] = (
            f'https://repo.sourcify.dev/contracts/full_match/8453/{SINC_TOKEN}/'
        )
    try:
        from launch_content_engine.onchain_stats import build_official_price_payload
        payload['pricing'] = build_official_price_payload()
    except Exception as e:
        logger.debug('[TOKEN] live pricing unavailable: %s', e)
    resp = make_response(jsonify(payload))
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Cache-Control'] = 'public, max-age=300'
    return resp

