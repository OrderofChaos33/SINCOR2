"""Checkout, x402, orders, crypto, PayPal.

Extracted from mvp_app so the gunicorn entry stays the app factory.
Helpers and Flask `app` live in sincor2.mvp_app; this module binds them
after that module has finished constructing the application object.
"""
from __future__ import annotations

from flask import Blueprint

bp = Blueprint("mvp_billing", __name__)


def _bind_mvp():
    import sincor2.mvp_app as mvp
    g = globals()
    for key, value in vars(mvp).items():
        if key.startswith("__") or key in {"bp", "_bind_mvp"}:
            continue
        g[key] = value


# B4: canonical paid/active order states. The single source of truth is
# sincor2.mvp_app._PAID_STATUSES, bound into this namespace by _bind_mvp()
# (called below). Vault materials are served ONLY for orders whose canonical
# record carries one of these states. Anything else (pending, failed,
# refunded, expired, complete-without-payment) is denied — the check re-reads
# the order row on every request; client-supplied flags and cached statuses
# are never trusted.
#
# NOTE: _VAULT_PAID_STATUSES is defined AFTER the _bind_mvp() call below,
# because the binding populates _PAID_STATUSES.


def _order_is_paid(order) -> bool:
    """B4: is this order record canonically paid/active?"""
    try:
        status = order["payment_status"] if not isinstance(order, dict) \
            else order.get("payment_status")
    except Exception:
        status = ""
    return str(status or "").strip().lower() in _VAULT_PAID_STATUSES


def _vault_denied_response(order) -> tuple:
    """B4: denial response for an order that exists but is not paid."""
    status = str((order["payment_status"] if not isinstance(order, dict)
                  else order.get("payment_status")) or "").strip().lower()
    if status == "pending":
        return render_template(
            'error.html', code=402, title='Payment Pending',
            message="Your payment has not completed yet. Once it is confirmed, "
                    "your training materials will be available here."), 402
    logger.warning('[VAULT] denied vault access for unpaid order: %s (status=%s)',
                   order["order_id"] if not isinstance(order, dict) else order.get("order_id"),
                   status or "unknown")
    return render_template(
        'error.html', code=403, title='Access Denied',
        message="This order is not in a paid state, so training materials "
                "cannot be served. Please contact support if you believe this "
                "is an error."), 403


_bind_mvp()

# Bound above: mvp_app._PAID_STATUSES is now in this namespace.
_VAULT_PAID_STATUSES = frozenset(s.lower() for s in _PAID_STATUSES)


# ---------------------------------------------------------------------------
# Wave 1 agent hook — daily free ration + free peek for the 7 x402 skills
# ---------------------------------------------------------------------------
#: The 7 skill resources (config/x402_pricing.yaml) that carry the Wave 1
#: daily free ration and free peek. Other x402 resources are untouched.
X402_FREE_RATION_RESOURCES = frozenset({
    'web-search-read',
    'local-business-site-check',
    'market-data-read',
    'toa-micro-decision',
    'polymarket-whale-signal',
    'kya-trust-score',
    'wallet-sanctions-screen',
})

#: Canonical sample payloads for the x402 handlers that run cheaply and
#: synchronously in-process. toa-micro-decision is deterministic local
#: compute; kya-trust-score is a local registry read (an unknown agent is
#: an honest real result). The other five handlers need a live external
#: read (web fetch, Chainlink round, OFAC snapshot freshness, whale
#: feed), so their peeks return labelled metadata — never a fake sample.
_X402_PEEK_SAMPLE_PAYLOADS = {
    'toa-micro-decision': {
        'decision_id': 'wave1-peek-sample',
        'options': [
            {'id': 'a', 'label': 'Option A', 'signals': [1.0, 2.0, 3.0, 4.0]},
            {'id': 'b', 'label': 'Option B', 'signals': [4.0, 3.0, 2.0, 1.0]},
        ],
    },
    'kya-trust-score': {'agent_id': 'wave1-peek-unknown-agent'},
}


def _x402_token_payer_wallet(token, resource_id):
    """Bound payer wallet for an access token, or None (read-only).

    A presented payment token is bound at challenge-bind time to the
    payer wallet that proved ownership by EIP-191 signature, so that
    wallet — not the source IP — is the token bearer's ration identity.
    Read-only SELECT through the x402 store's own connection helper;
    any failure (unknown token, store unavailable) yields None.
    """
    if not token:
        return None
    try:
        import sincor2.x402_payments as _x402

        with _x402._conn() as conn:
            row = conn.execute(
                "SELECT payer_wallet FROM x402_challenges "
                "WHERE access_token = :tok AND resource_id = :rid",
                {"tok": token, "rid": resource_id},
            ).fetchone()
        if row:
            wallet = validate_wallet(str(row[0] or ''))
            if wallet:
                return wallet.lower()
    except Exception:  # noqa: BLE001 - identity falls back gracefully
        return None
    return None


def _x402_free_identity(body, token=''):
    """Free-ration identity for this request (see sincor2.free_ration).

    Precedence: the bound payer wallet of a presented access token;
    else the wallet param (query ``wallet`` or body
    ``payer_wallet``/``wallet``) when present and well-formed; else
    ``agent:<agent_id>`` from query, body, or the ``X-Agent-Id``
    header; else ``ip:<remote_addr>``.
    """
    from sincor2.free_ration import abuse_client_ip, resolve_caller_identity

    body = body or {}
    wallet = validate_wallet(
        request.args.get('wallet', '')
        or body.get('payer_wallet', '')
        or body.get('wallet', '')
        or request.headers.get('X-Agent-Wallet', ''))
    agent_id = (
        request.args.get('agent_id', '')
        or body.get('agent_id', '')
        or request.headers.get('X-Agent-Id', ''))
    return resolve_caller_identity(
        wallet=wallet, agent_id=agent_id,
        ip=abuse_client_ip(request.remote_addr, environ=request.environ))


def _x402_peek_body(resource_id):
    """Free-peek body: real bounded sample where honestly possible, else
    the resource's live pricing metadata labelled as metadata."""
    from sincor2.free_ration import hook_payload, truncate_sample

    base = {'ok': True, 'resource': resource_id, 'free': True,
            'hook': hook_payload()}
    payload = _X402_PEEK_SAMPLE_PAYLOADS.get(resource_id)
    if payload is not None:
        status, body = 0, {}
        try:
            if resource_id == 'toa-micro-decision':
                from sincor2.toa_micro_decision import serve_toa_micro_decision
                status, body = serve_toa_micro_decision(dict(payload))
            elif resource_id == 'kya-trust-score':
                from sincor2.kya_trust_score import serve_kya_trust_score
                status, body = serve_kya_trust_score(dict(payload))
        except Exception:  # noqa: BLE001 - fall back to honest metadata
            status, body = 0, {}
        if status == 200:
            sample, truncated = truncate_sample(body)
            return {**base, 'sample': True, 'peek_kind': 'sample',
                    'label': 'Real sample output from this resource\'s '
                             'handler on a canonical sample input '
                             '(bounded).',
                    'truncated': truncated, 'result': sample}
    metadata = None
    try:
        from sincor2.x402_payments import get_resource
        metadata = get_resource(resource_id)
    except Exception:  # noqa: BLE001 - metadata is best-effort
        metadata = None
    return {**base, 'sample': False, 'peek_kind': 'metadata',
            'label': 'Metadata only — this resource\'s handler needs a '
                     'live external read, so there is no cheap '
                     'synchronous sample; this is its live metadata, '
                     'not a sample output.',
            'metadata': metadata or {'id': resource_id}}


def _x402_peek_response(resource_id):
    """No-auth, no-wallet free peek, rate-limited per IP per day."""
    if not X402_AVAILABLE:
        return jsonify({'error': 'x402_unavailable'}), 503
    from sincor2.free_ration import abuse_client_ip, daily_ration_tracker
    from sincor2.x402_payments import get_resource

    if resource_id not in X402_FREE_RATION_RESOURCES \
            and get_resource(resource_id) is None:
        return jsonify({'ok': False, 'error': 'unknown_resource'}), 404
    ip = abuse_client_ip(request.remote_addr, environ=request.environ)
    if not daily_ration_tracker.try_consume_peek(ip):
        return jsonify({'ok': False, 'error': 'peek_rate_limited',
                        'resource': resource_id,
                        'note': 'Free peek is rate-limited per IP per '
                                'day; paid and free-ration paths are '
                                'unaffected.'}), 429
    resp = jsonify(_x402_peek_body(resource_id))
    resp.headers['X-Free-Peek'] = 'true'
    return resp, 200


@bp.route('/buy', methods=['GET'])
def buy_page():
    """Platform checkout — SINC + AXM on Base (default). Legacy Stripe if explicitly enabled."""
    if fiat_payments_enabled() and STRIPE_AVAILABLE and stripe_processor and stripe_processor.enabled:
        return render_template('buy_converting.html')
    # D2 item 6: buy.html is the rewired rail offer (10 /rail/pay links);
    # buy_tokens.html (0 rail links) was the legacy token page.
    return render_template('buy.html')


@bp.route('/buy-sinc', methods=['GET'])
def buy_sinc_page():
    """Legacy curve URL — retired."""
    return redirect('/buy', code=302)


# ============================================================================
# PAYMENT WEBHOOK - Called by Stripe after successful payment
# This is the CORE endpoint that triggers asset delivery
# ============================================================================

@bp.route('/api/platform/plans', methods=['GET'])
def platform_plans():
    """List SINC/AXM-priced platform plans with live spot quotes."""
    if not PLATFORM_PAYMENTS_AVAILABLE:
        return jsonify({'ok': False, 'error': 'platform_payments_unavailable'}), 503
    return jsonify({'ok': True, 'plans': platform_list_plans(), 'fiat_enabled': fiat_payments_enabled()})


@bp.route('/api/platform/checkout', methods=['POST'])
@limiter.limit("60 per hour")
def platform_checkout():
    """Create a SINC or AXM checkout quote."""
    if not PLATFORM_PAYMENTS_AVAILABLE:
        return jsonify({'ok': False, 'error': 'platform_payments_unavailable'}), 503
    data = request.get_json(silent=True) or {}
    plan_id = sanitize_string(data.get('plan_id', 'intel'), max_length=32)
    email = validate_email(data.get('customer_email', '')) or ''
    wallet = validate_wallet(data.get('payer_wallet', '')) or ''
    idem = sanitize_string(data.get('idempotency_key', ''), max_length=128)
    result = platform_create_checkout(
        plan_id,
        payer_wallet=wallet or '',
        customer_email=email,
        idempotency_key=idem,
    )
    if not result.get('ok'):
        code = 400 if result.get('error') != 'spot_price_unavailable' else 503
        return jsonify(result), code
    return jsonify(result), 201


@bp.route('/api/platform/verify', methods=['POST'])
@limiter.limit("120 per hour")
def platform_verify():
    """Verify ERC-20 payment to treasury and trigger fulfillment (post-finality).

    B1: a valid receipt alone never provisions. While the tx is below the
    confirmation-depth gate the order stays 'pending' (HTTP 202); provisioning
    (order completion, fulfillment, subscription) happens only once the
    checkout is 'finalized'.
    """
    if not PLATFORM_PAYMENTS_AVAILABLE:
        return jsonify({'ok': False, 'error': 'platform_payments_unavailable'}), 503
    data = request.get_json(silent=True) or {}
    payment_id = sanitize_string(data.get('payment_id', ''), max_length=64)
    tx_hash = sanitize_string(data.get('tx_hash', ''), max_length=66)
    email = validate_email(data.get('customer_email', '')) or ''
    wallet = validate_wallet(data.get('payer_wallet', '')) or ''
    # W-113: payer proof — the caller must sign the platform bind
    # message with the payer wallet (EIP-191 personal_sign); a bare
    # tx_hash no longer verifies anything.
    signature = sanitize_string(data.get('payer_signature', ''), max_length=256)
    try:
        result = platform_verify_checkout(
            payment_id, tx_hash, customer_email=email, payer_wallet=wallet or '',
            payer_signature=signature,
        )
    except Exception as exc:
        # Item 307 round 2 (belt-and-braces): a raising verifier has
        # settled nothing — answer the payment-required shape (402 +
        # this endpoint's JSON envelope), never a 500 HTML page.
        logger.exception('[PAYMENTS] platform verify raised for %s',
                         payment_id)
        return jsonify({'ok': False, 'error': 'rpc_failed',
                        'detail': str(exc)}), 402
    if not result.get('ok'):
        error = result.get('error')
        if error == 'sanctions_hit':
            status = 403
        elif error in ('payer_wallet_required', 'payer_wallet_mismatch',
                       'ownership_signature_required',
                       'ownership_proof_failed'):
            status = 403
        elif error == 'ofac_screening_unavailable':
            status = 503
        elif error in ('tx_pending', 'insufficient_amount', 'no_treasury_transfer',
                       'confirmations_unavailable', 'receipt_malformed',
                       # Item 307 round 3: the real verifier RETURNS
                       # rpc_failed for a failed receipt fetch (it
                       # does not raise), so the returned-dict path
                       # needs the entry too — the except-branch above
                       # only covers a verifier that raises.
                       'rpc_failed'):
            status = 402
        elif error in ('reorg_detected', 'tx_failed', 'tx_already_used',
                       'checkout_blocked', 'checkout_reverted',
                       'checkout_disputed', 'tx_hash_mismatch'):
            status = 409
        else:
            status = 400
        return jsonify(result), status

    checkout_status = result.get('status')
    order_id = result.get('order_id')
    customer_email = result.get('customer_email') or ''

    if checkout_status == 'awaiting_finality':
        # Receipt valid but below the confirmation-depth gate: record a
        # pending order and provision NOTHING.
        _upsert_pending_platform_order(result, customer_email)
        return jsonify({
            'ok': True,
            'status': 'awaiting_finality',
            'payment_id': payment_id,
            'order_id': order_id,
            'tx_hash': tx_hash,
            'confirmations': result.get('confirmations'),
            'required_confirmations': result.get('required_confirmations'),
            'message': 'Payment seen on-chain; awaiting confirmation depth '
                       'before fulfillment.',
        }), 202

    if checkout_status == 'already_fulfilled':
        # Idempotent re-poll: never provision twice.
        return jsonify(result), 200

    if checkout_status != 'finalized':
        logger.error('[PAYMENTS] unexpected checkout status %r for %s',
                     checkout_status, payment_id)
        return jsonify({'ok': False, 'error': 'unexpected_checkout_status',
                        'status': checkout_status}), 500

    # 'finalized': confirmation depth reached and sanctions screen passed.
    # Provision exactly once, then record fulfillment on the checkout.
    product_name = result['product_name']
    amount = result['usd_reference']
    order_type = result.get('order_type', 'generic')
    product_info = PRODUCT_CATALOG.get(product_name, {'type': order_type})

    db = get_db()
    order_meta = json.dumps({
        'tx_hash': tx_hash,
        'payment_id': payment_id,
        'token': result['token'],
        'payer_wallet': result.get('payer_wallet'),
        'billing': 'platform_tokens',
        'confirmations': result.get('confirmations'),
        'required_confirmations': result.get('required_confirmations'),
        'ofac_verdict': result.get('ofac_verdict'),
    })
    try:
        # A 202 'pending' row may already exist from an awaiting_finality
        # poll — promote it instead of inserting a duplicate.
        cur = db.execute(
            """UPDATE orders SET payment_status='completed',
               delivery_status='processing', paypal_order_id=?,
               customer_email=COALESCE(NULLIF(?, ''), customer_email),
               updated_at=?, metadata=?
               WHERE order_id=?""",
            (tx_hash, customer_email,
             datetime.utcnow().isoformat(), order_meta, order_id),
        )
        if cur.rowcount == 0:
            db.execute(
                '''INSERT INTO orders
                   (order_id, paypal_order_id, customer_email, product_name, amount,
                    currency, payment_status, delivery_status, delivery_url, order_type,
                    created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (order_id, tx_hash, customer_email or result.get('payer_wallet', ''),
                 product_name, amount, result['token'], 'completed', 'processing',
                 f'/my-orders?email={customer_email}' if customer_email else '/dashboard',
                 order_type, datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
                 order_meta)
            )
        db.commit()
    except sqlite3.IntegrityError:
        logger.warning('[PAYMENTS] order row race for %s', order_id)

    if customer_email:
        trigger_fulfillment(order_id, customer_email, product_name, amount, order_type, product_info)

    payer = result.get('payer_wallet', '')
    if result.get('token') == 'SINC' and result.get('amount_atomic'):
        try:
            from sincor2.agent_billing import record_platform_payment
            record_platform_payment(
                tx_hash=tx_hash,
                payer_wallet=payer,
                token='SINC',
                amount_atomic=int(result['amount_atomic']),
                product_name=product_name,
                plan_id=result.get('plan_id', ''),
                payment_id=payment_id,
            )
        except Exception as e:
            logger.warning('[BILLING] agent_billing log failed: %s', e)

    if result.get('billing') == 'month' and payer:
        sub = activate_subscription(
            wallet=payer,
            plan_id=result.get('plan_id', ''),
            product_name=product_name,
            token=result['token'],
            tx_hash=tx_hash,
            payment_id=payment_id,
            email=customer_email,
        )
        result['subscription'] = sub

    if not platform_mark_checkout_fulfilled(payment_id):
        logger.warning('[PAYMENTS] %s already fulfilled (fulfillment race)',
                       payment_id)
    result['status'] = 'fulfilled'
    logger.info('[PAYMENTS] %s verified+finalized: %s %s tx=%s', result['token'], product_name, order_id, tx_hash)
    return jsonify(result), 200


def _upsert_pending_platform_order(result, customer_email):
    """Record a pending (not yet final) platform order.

    INSERT OR IGNORE keyed on order_id: re-polls must never downgrade or
    duplicate the row. The row is promoted to 'completed' once the checkout
    is finalized.
    """
    db = get_db()
    try:
        db.execute(
            '''INSERT OR IGNORE INTO orders
               (order_id, paypal_order_id, customer_email, product_name, amount,
                currency, payment_status, delivery_status, delivery_url, order_type,
                created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', 'pending', ?, ?, ?, ?, ?)''',
            (result['order_id'], result.get('tx_hash'),
             customer_email or result.get('payer_wallet', ''),
             result['product_name'], result['usd_reference'], result['token'],
             f'/my-orders?email={customer_email}' if customer_email else '/dashboard',
             result.get('order_type', 'generic'),
             datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
             json.dumps({
                 'tx_hash': result.get('tx_hash'),
                 'payment_id': result.get('payment_id'),
                 'token': result['token'],
                 'payer_wallet': result.get('payer_wallet'),
                 'billing': 'platform_tokens',
                 'awaiting_finality': True,
                 'confirmations': result.get('confirmations'),
                 'required_confirmations': result.get('required_confirmations'),
             }))
        )
        db.commit()
    except Exception as e:
        logger.warning('[PAYMENTS] pending order upsert failed for %s: %s',
                       result.get('order_id'), e)


@bp.route('/api/platform/subscription', methods=['GET'])
@limiter.limit("120 per hour")
def platform_subscription_status():
    """Wallet-linked SINC subscription status."""
    wallet = validate_wallet(request.args.get('wallet', ''))
    if not wallet:
        return jsonify({'ok': False, 'error': 'wallet_required'}), 400
    plan_id = sanitize_string(request.args.get('plan_id', ''), max_length=32) or None
    if plan_id:
        sub = get_subscription(wallet, plan_id)
        subs = [sub] if sub else []
    else:
        subs = list_subscriptions(wallet)
    return jsonify({'ok': True, 'wallet': wallet, 'subscriptions': subs})


@bp.route('/api/sinc/curve', methods=['GET'])
@limiter.limit("120 per minute")
def api_sinc_curve():
    """Bonding curve API retired."""
    return jsonify({'ok': False, 'error': 'bonding_curve_retired'}), 410


@bp.route('/api/sinc/burn-stats', methods=['GET'])
@limiter.limit("60 per minute")
def api_sinc_burn_stats():
    """Burn-stats API retired. Locked policy: 5% platform fee to treasury, no burn."""
    return jsonify({
        'ok': False,
        'error': 'burn_stats_retired',
        'note': 'Burn mechanics are retired. Platform policy: 5% platform fee to treasury, converted to USDC/WETH before deposit. No burn.',
    }), 410


# ---------------------------------------------------------------------------
# x402 paid-surface checklist (docs/ops/X402_PAID_SURFACE_CHECKLIST.md).
# R1: every 402 carries `Cache-Control: no-store`. R2: every challenge 402
# emits a PAYMENT-REQUIRED header whose decoded envelope deep-equals the
# body's `accepts`. R3: CORS exposes PAYMENT-REQUIRED and preflight allows
# the payment headers. R4: undeclared methods get a JSON 405, never HTML.
# Implemented as ONE blueprint after_request hook + ONE errorhandler so no
# per-resource serve block's return flow is disturbed.
# ---------------------------------------------------------------------------

#: Verify failures that mean "the payment has NOT settled" — the x402
#: answer is 402 (retry once settled), never a generic 400, and the 402
#: carries the payment envelope (see _paid_surface_envelope). Item 307:
#: a settlement that fails mid-flow — the tx reverted on-chain
#: (tx_failed) or the finality gate refuses below 12 confirmations
#: (awaiting_finality) — belongs to this family exactly like a still-
#: pending transfer. Item 307 round 2: verification that could not be
#: COMPLETED — the RPC layer failed (rpc_failed) or the receipt was
#: malformed (receipt_malformed) — is the same family: the payment is
#: not settled, so the answer is 402 on every surface (rail/settle
#: already answered 402 for rpc_failed; this tuple is what made
#: /api/x402/verify drift to 400). x402_verify consults this same
#: tuple for its status mapping, so the two can never drift apart.
#: Item 307 round 3: the chain head unreachable while counting
#: confirmations (confirmations_unavailable) is the third member of
#: the could-not-complete family — Builder B reproduced it answering
#: 400 here while both sibling surfaces answered 402.
_PAID_SURFACE_VERIFY_402_ERRORS = ('tx_pending', 'insufficient_amount',
                                   'no_treasury_transfer', 'tx_failed',
                                   'awaiting_finality', 'rpc_failed',
                                   'receipt_malformed',
                                   'confirmations_unavailable',
                                   # Item 307 tidy: claim-step store
                                   # races — the transfer verified, the
                                   # atomic claim lost a transient lock
                                   # race; a clean retry settles, so the
                                   # answer is 402 (rail agrees), not a
                                   # 400 client-error label.
                                   'store_busy', 'fulfill_conflict')


def _merge_header_tokens(existing, *tokens):
    """Merge comma-separated header tokens without duplicating any."""
    merged = [t.strip() for t in (existing or '').split(',') if t.strip()]
    lowered = {t.lower() for t in merged}
    for token in tokens:
        if token.lower() not in lowered:
            merged.append(token)
            lowered.add(token.lower())
    return ', '.join(merged)


def _paid_surface_envelope(body):
    """Payment envelope for a 402 body, or None when not derivable.

    Challenge 402s already carry the envelope under ``payment``. Verify
    402s (tx_pending & friends) return the bare transfer-verification
    error with no envelope, so theirs is rebuilt from the still-pending
    challenge: build_payment_payload is deterministic in
    (resource, challenge_id), so the rebuilt envelope is identical to
    the one issued at challenge time. Read-only lookup; any failure
    returns None and the caller simply skips R2 for that response.
    """
    payment = body.get('payment')
    if isinstance(payment, dict):
        # Every `payment` value on a paid-surface 402 body is a
        # build_payment_payload envelope (challenge bodies carry a
        # challenge_id; the wallet-less discovery offer deliberately
        # does not — it is still the body's own envelope).
        return payment
    if body.get('error') not in _PAID_SURFACE_VERIFY_402_ERRORS:
        return None
    try:
        import sqlite3

        from sincor2.data_paths import orders_db_path
        from sincor2.x402_payments import build_payment_payload, get_resource

        data = request.get_json(silent=True) or {}
        challenge_id = sanitize_string(
            data.get('challenge_id', ''), max_length=64)
        if not challenge_id:
            return None
        conn = sqlite3.connect(orders_db_path())
        try:
            row = conn.execute(
                'SELECT resource_id FROM x402_challenges '
                'WHERE challenge_id=?', (challenge_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        resource = get_resource(row[0])
        if not resource:
            return None
        return build_payment_payload(resource, row[0], challenge_id)
    except Exception:
        return None


#: The paid surface proper (checklist "Our paid surface"): only these
#: paths get CORS origin reflection. The billing blueprint also serves
#: non-paid routes (payment/paypal webhooks, orders, platform checkout)
#: that must never reflect a caller-supplied Origin.
_PAID_SURFACE_PATH_PREFIXES = ('/x402/', '/api/x402/', '/api/paid/')


def _is_paid_surface_path(path):
    return bool(path) and path.startswith(_PAID_SURFACE_PATH_PREFIXES)


@bp.after_request
def _paid_surface_headers(response):
    # R3b: a CORS preflight against a PAID route must succeed and allow
    # the payment headers a browser buyer sends. Reflection is narrowed
    # to the paid surface only — non-paid billing routes (webhooks,
    # orders, checkout) get no CORS reflection at all.
    if request.method == 'OPTIONS':
        if _is_paid_surface_path(request.path) \
                and request.headers.get('Origin'):
            response.headers['Access-Control-Allow-Origin'] = \
                request.headers['Origin']
            response.headers['Access-Control-Allow-Headers'] = \
                _merge_header_tokens(
                    response.headers.get('Access-Control-Allow-Headers'),
                    'Content-Type', 'Authorization', 'X-Payment',
                    'X-Payment-Token')
            response.headers['Access-Control-Allow-Methods'] = \
                _merge_header_tokens(
                    response.headers.get('Access-Control-Allow-Methods'),
                    'GET', 'POST', 'OPTIONS')
        return response
    if response.status_code != 402:
        return response
    # R1: a 402 is a per-challenge, per-payer, expiring offer — a cached
    # copy replays a dead challenge. Never store it.
    response.headers['Cache-Control'] = _merge_header_tokens(
        response.headers.get('Cache-Control'), 'no-store')
    # R3c: a cross-origin browser buyer cannot even READ the 402 unless
    # the origin is reflected on the response itself (Expose-Headers
    # alone is not enough). Paid routes only; never Allow-Credentials.
    _origin = request.headers.get('Origin')
    if _origin and _is_paid_surface_path(request.path):
        response.headers['Access-Control-Allow-Origin'] = _origin
        response.headers['Vary'] = _merge_header_tokens(
            response.headers.get('Vary'), 'Origin')
    # R2: mirror the envelope into the x402-v2 `accepts` body key and the
    # PAYMENT-REQUIRED header (base64 JSON) so header readers and body
    # readers authorize identical terms. The envelope's Bazaar discovery
    # extension is additionally mirrored to a top-level `extensions` body
    # key (the x402 discovery shape; sibling of `accepts`).
    if response.is_json:
        body = response.get_json(silent=True)
        if isinstance(body, dict):
            envelope = _paid_surface_envelope(body)
            if envelope is not None:
                import base64

                accepts = [envelope]
                new_body = None
                if body.get('accepts') != accepts:
                    new_body = dict(body)
                    new_body['accepts'] = accepts
                # Discovery Step 0: the x402 discovery shape also puts
                # `extensions` at the TOP LEVEL of the 402 body (sibling
                # of `accepts`). The envelope already carries the
                # complete Bazaar extension (dev-watch 317) nested
                # inside it; mirror that exact object top-level so a
                # discovery reader scanning the body finds
                # extensions.bazaar without unwrapping `payment`.
                # Mirror only — no extension is built or altered here.
                ext = envelope.get('extensions')
                if isinstance(ext, dict) and body.get('extensions') != ext:
                    if new_body is None:
                        new_body = dict(body)
                    new_body['extensions'] = ext
                if new_body is not None:
                    raw = json.dumps(new_body).encode('utf-8')
                    response.set_data(raw)
                    response.headers['Content-Length'] = str(len(raw))
                response.headers['PAYMENT-REQUIRED'] = base64.b64encode(
                    json.dumps(accepts).encode('utf-8')).decode('ascii')
                # R3a: browser buyers may only read the header cross-origin
                # if it is explicitly exposed.
                response.headers['Access-Control-Expose-Headers'] = \
                    _merge_header_tokens(
                        response.headers.get(
                            'Access-Control-Expose-Headers'),
                        'PAYMENT-REQUIRED')
    return response


@bp.app_errorhandler(405)
def _paid_surface_method_not_allowed(error):
    """R4: undeclared methods return a JSON 405, never Werkzeug's HTML.

    Registered app-wide (via the blueprint) on purpose: a routing-raised
    405 is raised before any blueprint is active, so a blueprint-scoped
    errorhandler never sees it — verified on Flask 3.0.3, where
    ``bp.errorhandler(405)`` still serves the default HTML page.

    RFC 9110 §15.5.6: a 405 MUST carry an ``Allow`` header listing the
    valid methods. Werkzeug's MethodNotAllowed carries them on
    ``valid_methods``; restore the header the default HTML page would
    have sent (the JSON body alone dropped it).
    """
    response = jsonify({'ok': False, 'error': 'method_not_allowed'})
    valid_methods = getattr(error, 'valid_methods', None)
    if valid_methods:
        response.headers['Allow'] = ', '.join(valid_methods)
    return response, 405


@bp.route('/api/x402/resources', methods=['GET'])
def x402_resources():
    if not X402_AVAILABLE:
        return jsonify({'ok': False, 'error': 'x402_unavailable'}), 503
    from sincor2.free_ration import DAILY_RATION_LIMIT, hook_payload
    from sincor2.x402_payments import list_resources
    resources = list_resources()
    for res in resources:
        if res.get('id') in X402_FREE_RATION_RESOURCES:
            res['free_daily_ration'] = DAILY_RATION_LIMIT
            res['peek_url'] = f"/api/x402/peek/{res['id']}"
    return jsonify({'ok': True, 'resources': resources,
                    'hook': hook_payload()})


@bp.route('/api/x402/peek/<resource_id>', methods=['GET'])
def x402_peek(resource_id):
    """Wave 1 free peek — no auth, no wallet, no token (per-IP capped)."""
    return _x402_peek_response(resource_id)


@bp.route('/api/kya/verify', methods=['GET'])
def kya_verify():
    """Free KYA verify tier (G2): known/status/verified/tier only.

    The deep read (score + component breakdown) is the paid
    ``kya-trust-score`` x402 resource; this route never returns it.
    """
    from sincor2.kya_trust_score import serve_kya_verify
    status, body = serve_kya_verify(
        request.args.get('agent_id', ''), request.args.get('wallet', ''))
    return jsonify(body), status


def _x402_walletless_offer(resource_id):
    """D2 item 7a: the 402 offer envelope for a wallet-less discovery
    probe, or None when the resource is unknown.

    A probe with no wallet hint cannot receive a payable challenge:
    minting one requires the wallet to build the EIP-191 bind message,
    and an unbound draft minted for nobody would be a bearer
    instrument. So the probe gets the standard 402 challenge terms —
    the payment envelope (resource, price, payTo, asset) — with NO
    challenge_id and NO row written. The buyer re-requests with
    ?wallet=<address> to mint the payable (unbound-draft) challenge,
    exactly as before.
    """
    from sincor2.x402_payments import build_payment_payload, get_resource
    resource = get_resource(resource_id)
    if not resource:
        return None
    payment = build_payment_payload(resource, resource_id, '')
    # The envelope for an unminted offer carries no challenge binding.
    payment = dict(payment)
    extra = dict(payment.get('extra') or {})
    extra.pop('challenge_id', None)
    payment['extra'] = extra
    return {
        'ok': False,
        'error': 'payment_required',
        'resource': resource_id,
        'resource_id': resource_id,
        'price': resource['amount_display'],
        'token': resource['token'],
        'payTo': payment['payTo'],
        'asset': payment['asset'],
        'payment': payment,
        'accepts': [payment],
        'note': 'Discovery offer only — pass ?wallet=<your 0x address> '
                '(or payer_wallet in the JSON body) to receive a '
                'payable challenge.',
    }


@bp.route('/x402/<resource_id>', methods=['GET'])
@limiter.limit("200 per hour")
def x402_challenge(resource_id):
    """HTTP 402 Payment Required — token micropayment challenge."""
    if not X402_AVAILABLE:
        return jsonify({'error': 'x402_unavailable'}), 503
    from sincor2.x402_payments import (
        access_granted, challenge_bind_message, create_challenge)
    token = request.headers.get('X-Payment-Token') or request.args.get('access_token', '')
    # Informational status check: must not burn the single-use token.
    # The actual serve path (x402_paid_resource below) consumes it.
    if access_granted(token, resource_id, consume=False):
        return jsonify({'ok': True, 'resource_id': resource_id, 'access': 'granted'}), 200
    wallet = validate_wallet(request.args.get('wallet', ''))
    # W-102 round 3: a bare address string is no longer a capability.
    # create_challenge mints an UNBOUND draft only; the payer must prove
    # wallet ownership by signing the bind message (EIP-191 personal_sign
    # over challenge_id | resource_id | payer_wallet) and POSTing it to
    # /api/x402/challenge-bind. The draft can never be fulfilled before
    # that (verify rejects unbound challenges as challenge_unbound).
    if not wallet:
        # D2 item 7a: a wallet-less discovery probe gets the 402 offer
        # envelope, not a 400 — no challenge is minted (see
        # _x402_walletless_offer).
        offer = _x402_walletless_offer(resource_id)
        if offer is None:
            return jsonify({'ok': False, 'error': 'unknown_resource'}), 404
        return jsonify(offer), 402
    challenge = create_challenge(resource_id)
    if challenge.get('challenge_id'):
        challenge['sign_message'] = challenge_bind_message(
            challenge['challenge_id'], resource_id, wallet)
        challenge['payer_wallet_hint'] = wallet
    return jsonify(challenge), 402


@bp.route('/api/x402/challenge-bind', methods=['POST'])
@limiter.limit("120 per hour")
def x402_challenge_bind():
    """Bind a payer wallet to a challenge draft after ownership proof.

    Body: {challenge_id, payer_wallet, signature} where signature is an
    EIP-191 personal_sign by payer_wallet over
    SINCOR-X402-CHALLENGE-BIND|<challenge_id>|<resource_id>|<payer_wallet>.
    Without the wallet's private key the binding cannot be created — this
    is what stops the W-102 round-3 front-run (an attacker could mint a
    challenge naming the victim's wallet and claim the victim's payment
    under it, because the binding was a bare unverified string).
    """
    if not X402_AVAILABLE:
        return jsonify({'error': 'x402_unavailable'}), 503
    from sincor2.x402_payments import bind_challenge_payer
    data = request.get_json(silent=True) or {}
    payer_wallet = validate_wallet(data.get('payer_wallet', ''))
    if not payer_wallet:
        return jsonify({'ok': False, 'error': 'payer_wallet_required'}), 400
    challenge_id = sanitize_string(data.get('challenge_id', ''), max_length=64)
    if not challenge_id:
        return jsonify({'ok': False, 'error': 'challenge_id_required'}), 400
    signature = data.get('signature', '')
    if not signature:
        return jsonify({'ok': False, 'error': 'ownership_signature_required'}), 400
    result = bind_challenge_payer(challenge_id, payer_wallet, signature)
    if result.get('error') == 'payment_required':
        # D2 item 2 (P0): bind_challenge_payer reports a SUCCESSFUL
        # bind as error='payment_required' (the challenge is now bound
        # and awaiting payment) with ownership_proven=True — but a 402
        # here is a browser-buyer blocker: rail_pay.html's postJSON
        # throws on any non-2xx, so "Pay" never enabled. Translate the
        # successful bind into the 2xx success response the page (and
        # any API buyer) accepts. The binding itself is unchanged:
        # the payment envelope is carried through, ownership_proven
        # stays True, and every failure shape below (409 second bind,
        # 403 wrong signer, expired, not found) is untouched.
        return jsonify({
            'ok': True,
            'ownership_proven': True,
            'bound': True,
            'status': 'bound',
            'challenge_id': result.get('challenge_id'),
            'resource_id': result.get('resource_id'),
            'payer_wallet': result.get('payer_wallet'),
            'expires_at': result.get('expires_at'),
            'payment': result.get('payment'),
            'message': result.get('message'),
        }), 200
    status = {
        'challenge_not_found': 404,
        'challenge_expired': 410,
        'challenge_already_bound': 409,
        'ownership_proof_failed': 403,
        'ownership_signature_required': 400,
        'payer_wallet_required': 400,
        'challenge_id_required': 400,
        'unknown_resource': 404,
    }.get(result.get('error'), 400)
    return jsonify(result), status


@bp.route('/api/x402/verify', methods=['POST'])
@limiter.limit("120 per hour")
def x402_verify():
    if not X402_AVAILABLE:
        return jsonify({'ok': False, 'error': 'x402_unavailable'}), 503
    from sincor2.x402_payments import finalize_challenge_payment, verify_challenge
    data = request.get_json(silent=True) or {}
    # W-102: payer_wallet is mandatory on verify. Without it, an observer of
    # a confirmed treasury transfer could claim the victim's tx_hash under
    # their own challenge_id — first-claim-wins would grant the attacker the
    # victim's entitlement. Missing, blank, or malformed wallets are rejected.
    # W-102 round 2: require_bound_challenge=True — a challenge with no
    # recorded payer is a bearer instrument and can never be claimed through
    # this route (error: challenge_unbound).
    payer_wallet = validate_wallet(data.get('payer_wallet', ''))
    if not payer_wallet:
        return jsonify({'ok': False, 'error': 'payer_wallet_required'}), 400
    # W-103: replay credentials for re-issuing an already-fulfilled
    # challenge's token. Sanitized like the existing body fields and passed
    # through into verify_challenge(); replay_ts_ms parses to int or None
    # (malformed -> rejected as replay_auth_failed downstream). Accepts a
    # JSON number or a numeric string for replay_ts_ms.
    replay_ts_in = data.get('replay_ts_ms', '')
    if isinstance(replay_ts_in, int) and not isinstance(replay_ts_in, bool):
        replay_ts_ms = replay_ts_in
    else:
        replay_ts_raw = sanitize_string(replay_ts_in, max_length=16)
        try:
            replay_ts_ms = int(replay_ts_raw)
        except (TypeError, ValueError):
            replay_ts_ms = None
    try:
        result = verify_challenge(
            sanitize_string(data.get('challenge_id', ''), max_length=64),
            sanitize_string(data.get('tx_hash', ''), max_length=66),
            payer_wallet=payer_wallet,
            require_bound_challenge=True,
            replay_signature=sanitize_string(
                data.get('replay_signature', ''), max_length=140),
            replay_ts_ms=replay_ts_ms,
        )
    except Exception as exc:
        # Item 307 round 2 (belt-and-braces): a verifier that RAISES
        # has not settled anything — answer 402 (rpc_failed family)
        # with the JSON envelope, never Flask's 500 HTML page. The
        # challenge stays pending; no token is minted.
        logger.exception('[X402] verify_challenge raised')
        result = {'ok': False, 'error': 'rpc_failed', 'detail': str(exc)}
    if not result.get('ok'):
        # Item 307 (settle-then-serve): every "payment has not settled"
        # failure — pending, reverted, or below the finality gate — is
        # answered 402 with the payment envelope, from the one canonical
        # list the paid-surface headers also consult. Client/auth faults
        # (unbound challenge, payer mismatch, replay failures) stay 400.
        code = 402 if result.get('error') in _PAID_SURFACE_VERIFY_402_ERRORS else 400
        return jsonify(result), code
    try:
        result.update(finalize_challenge_payment(result))
    except Exception:
        pass
    return jsonify(result), 200


# ---------------------------------------------------------------------------
# W-107 single-flight guard for the paid-token pipeline.
#
# The P1-1 peek -> dispatch -> consume split (PR #533) deliberately
# runs the provider dispatch BEFORE the atomic token consume, so a
# failed dispatch (400/503) never burns the buyer's token. That left
# a race: concurrent requests carrying the SAME single-use token all
# passed the peek, all dispatched (spending provider money / egress),
# and only the consume winner was served — the losers' provider
# results were discarded (W-107, verified 8/8 synchronised trials:
# provider_calls == N for N concurrent duplicates).
#
# This guard makes the pipeline single-flight PER TOKEN: the first
# in-flight request (the leader) proceeds; a concurrent duplicate is
# refused with the existing 409 access_token_already_consumed
# semantics WITHOUT dispatching. It is NOT consume-before-dispatch:
# the leader still consumes only on a 200, and the flight entry is
# released on EVERY exit path (success, 400, 503, exception) via
# try/finally, so a failed leader leaves no stale flight and the
# legitimate retry dispatches fresh (P1-1 preservation intact).
# Different tokens hold different flights and dispatch concurrently.
# ---------------------------------------------------------------------------
import hashlib as _hashlib
import threading as _threading

_PAID_FLIGHT_GUARD = _threading.Lock()
# flight key -> [lock, refcount]; entries are dropped at refcount 0.
_PAID_TOKEN_FLIGHTS: dict = {}


def _paid_flight_key(token, resource_id):
    # Hash the bearer token so the registry never stores it raw.
    digest = _hashlib.sha256(
        (resource_id + '\x00' + token).encode('utf-8')).hexdigest()
    return digest


def _free_flight_key(identity, resource_id):
    # W-111: hash the free-ration identity so the registry never
    # stores it raw (identities can be wallet addresses). The
    # "free\x00" namespace prefix guarantees this key can never
    # collide with a token flight key, which is hashed from
    # resource_id + "\x00" + token without the prefix.
    digest = _hashlib.sha256(
        ('free\x00' + resource_id + '\x00' + identity).encode('utf-8')
    ).hexdigest()
    return digest


def _drop_paid_flight_ref(key, entry):
    with _PAID_FLIGHT_GUARD:
        entry[1] -= 1
        if entry[1] <= 0 and _PAID_TOKEN_FLIGHTS.get(key) is entry:
            _PAID_TOKEN_FLIGHTS.pop(key, None)


def _acquire_paid_token_flight(token, resource_id):
    """Try to become the flight leader for (token, resource_id).

    Returns the flight handle for the leader, or None when another
    request already leads this token's flight (caller must refuse
    without dispatching).
    """
    key = _paid_flight_key(token, resource_id)
    with _PAID_FLIGHT_GUARD:
        entry = _PAID_TOKEN_FLIGHTS.get(key)
        if entry is None:
            entry = [_threading.Lock(), 0]
            _PAID_TOKEN_FLIGHTS[key] = entry
        entry[1] += 1
    if entry[0].acquire(blocking=False):
        return (key, entry)
    _drop_paid_flight_ref(key, entry)
    return None


def _release_paid_token_flight(flight):
    key, entry = flight
    _drop_paid_flight_ref(key, entry)
    entry[0].release()


def _acquire_free_ration_flight(identity, resource_id):
    """Try to become the flight leader for (identity, resource_id).

    W-111 counterpart of _acquire_paid_token_flight, reusing the
    same registry (_PAID_TOKEN_FLIGHTS / _PAID_FLIGHT_GUARD) with a
    namespaced hashed key. Returns the flight handle for the leader,
    or None when another request already leads this identity's free
    flight (caller must NOT dispatch; it falls through to the paid
    flow instead).
    """
    key = _free_flight_key(identity, resource_id)
    with _PAID_FLIGHT_GUARD:
        entry = _PAID_TOKEN_FLIGHTS.get(key)
        if entry is None:
            entry = [_threading.Lock(), 0]
            _PAID_TOKEN_FLIGHTS[key] = entry
        entry[1] += 1
    if entry[0].acquire(blocking=False):
        return (key, entry)
    _drop_paid_flight_ref(key, entry)
    return None


def _release_free_ration_flight(flight):
    key, entry = flight
    _drop_paid_flight_ref(key, entry)
    entry[0].release()


@bp.route('/api/paid/<resource_id>', methods=['GET', 'POST'])
@limiter.limit("120 per hour")
def x402_paid_resource(resource_id):
    """Serve paid API payloads after x402 access token presented."""
    if not X402_AVAILABLE:
        return jsonify({'error': 'x402_unavailable'}), 503
    # Wave 1 free peek via param: /api/paid/<resource>?peek=1 — no auth,
    # no wallet, no token; rate-limited per IP (see _x402_peek_response).
    if request.args.get('peek', '').strip().lower() in ('1', 'true', 'yes'):
        return _x402_peek_response(resource_id)
    from sincor2.x402_payments import access_granted
    token = request.headers.get('X-Payment-Token') or request.args.get('access_token', '')
    # W-107: claim this token's flight BEFORE the peek, so a duplicate
    # that arrives at any point during the leader's peek -> dispatch ->
    # consume pipeline is refused without dispatching. Token-less
    # requests skip the guard entirely (they can only fail the peek
    # and receive a challenge; there is no token to single-flight).
    flight = None
    if token:
        flight = _acquire_paid_token_flight(token, resource_id)
        if flight is None:
            return jsonify({'ok': False,
                            'error': 'access_token_already_consumed'}), 409
    try:
        return _x402_paid_resource_leader(resource_id, token, access_granted)
    finally:
        if flight is not None:
            _release_paid_token_flight(flight)


def _x402_paid_resource_leader(resource_id, token, access_granted):
    """The peek -> dispatch -> consume pipeline (flight leader only).

    Sequential behavior is exactly the pre-W-107 flow plus the Wave 1
    daily free ration check at its head; the caller
    (x402_paid_resource) owns the single-flight guard + release, so
    the flight is claimed before the ration dispatch and the peek.
    """
    # Wave 1 daily free ration (the 7 skill resources only): BEFORE the
    # paid-token flow, serve free while the caller's daily ration
    # remains. Identity = wallet param, else agent id, else source IP
    # (sincor2.free_ration). The ration is consumed ONLY when the
    # dispatch returns a 200 — a 400/503 free attempt consumes nothing
    # and falls through to the exact existing paid path below, and a
    # free serve never consumes a payment token and never writes fee,
    # pool, or settlement records (the dispatch handlers are read-only).
    if resource_id in X402_FREE_RATION_RESOURCES:
        from sincor2.free_ration import abuse_client_ip, daily_ration_tracker

        _ration_body = request.get_json(silent=True) or {}
        _identity = _x402_token_payer_wallet(token, resource_id) \
            or _x402_free_identity(_ration_body)
        _ip = abuse_client_ip(request.remote_addr, environ=request.environ)
        if daily_ration_tracker.remaining(_identity, resource_id) > 0 \
                and daily_ration_tracker.ip_remaining(_ip) > 0:
            # W-111: single-flight the free dispatch per
            # (identity, resource_id). The token flight guard above
            # only covers token-bearing requests, so concurrent
            # token-less requests for the same identity+resource
            # could all dispatch before any of them consumed the
            # ration, paying the provider cost repeatedly for one
            # logical free serve. Acquire the free flight BEFORE
            # dispatch; if another request already leads it, do NOT
            # dispatch — fall through to the unchanged paid flow
            # below. Consume-on-200 semantics are unchanged, so a
            # failed leader burns no ration (P1-1 property).
            _free_flight = _acquire_free_ration_flight(
                _identity, resource_id)
            if _free_flight is not None:
                try:
                    _status, _body = _paid_skill_dispatch(resource_id)
                    if _status == 200 and daily_ration_tracker.try_consume(
                            _identity, resource_id, ip=_ip):
                        _body = dict(_body)
                        _body['x402_free_ration'] = True
                        _body['free_ration_remaining'] = \
                            daily_ration_tracker.remaining(
                                _identity, resource_id)
                        _resp = jsonify(_body)
                        _resp.headers['X-Free-Ration'] = 'true'
                        return _resp, 200
                finally:
                    _release_free_ration_flight(_free_flight)
            # Non-200 dispatch, a lost consume race, or a lost free
            # flight: fall through to the unchanged paid flow below.
    # P1-1: PEEK first (consume=False) — never burn the buyer's
    # single-use token before a deliverable exists. A 400 bad payload
    # or a 503/500 upstream failure previously consumed the token up
    # front and charged the buyer for no service. The token is
    # consumed atomically below, only when the dispatch returns a 200.
    if not access_granted(token, resource_id, consume=False):
        from sincor2.x402_payments import challenge_bind_message, create_challenge
        # W-102 round 3: mint an unbound draft; the payer proves wallet
        # ownership by signing the bind message and POSTing it to
        # /api/x402/challenge-bind (see x402_challenge). Accept the wallet
        # from the query string or the JSON body to build the exact
        # sign_message; refuse to issue a draft with no wallet hint.
        body = request.get_json(silent=True) or {}
        wallet = validate_wallet(
            request.args.get('wallet', '')
            or body.get('payer_wallet', '')
            or body.get('wallet', ''))
        if not wallet:
            # D2 item 7a: wallet-less discovery probe — the 402 offer
            # envelope, no challenge minted (see _x402_walletless_offer).
            offer = _x402_walletless_offer(resource_id)
            if offer is None:
                return jsonify({'ok': False,
                                'error': 'unknown_resource'}), 404
            return jsonify(offer), 402
        ch = create_challenge(resource_id)
        if ch.get('challenge_id'):
            ch['sign_message'] = challenge_bind_message(
                ch['challenge_id'], resource_id, wallet)
            ch['payer_wallet_hint'] = wallet
        return jsonify(ch), 402
    status, body = _paid_skill_dispatch(resource_id)
    if status == 200:
        # Deliverable in hand: burn the token now. The consume
        # re-checks expiry/consumption and stamps token_consumed_at
        # via a conditional UPDATE, so the token still grants exactly
        # one delivered 200 even under concurrency; a caller that
        # loses the race is refused rather than served twice.
        if not access_granted(token, resource_id, consume=True):
            return jsonify({'ok': False,
                            'error': 'access_token_already_consumed'}), 409
    return jsonify(body), status


def _paid_skill_dispatch(resource_id):
    """Run the paid handler for ``resource_id`` -> (status, body).

    Called only after the access token has been PEEK-verified
    (x402_paid_resource); this function never touches token state.
    Non-200 outcomes leave the token unconsumed so the buyer can fix
    the payload (or wait out an upstream outage) and retry (P1-1).
    """
    from sincor2.x402_payments import execute_paid_resource
    if resource_id == 'web-search-read':
        # Web search + page read (G5). The buyer's {mode, query|url}
        # payload is validated by the handler; the caller consumes
        # the token only when this returns a 200 (P1-1). Stateless:
        # every call re-runs through the module's provider seam.
        from sincor2.web_search_read import serve_web_search_read
        wsr_payload = request.get_json(silent=True)
        if wsr_payload is None:
            wsr_payload = request.args.to_dict(flat=True)
            wsr_payload.pop('access_token', None)
        status, body = serve_web_search_read(wsr_payload)
        return status, body
    if resource_id == 'local-business-site-check':
        # Local-business site check (G8). The business URL/name
        # arrive in the request payload, validated by the handler;
        # the caller consumes the token only on a 200 (P1-1).
        # Stateless: every call re-fetches through the module's
        # fetch seam.
        from sincor2.site_check import serve_site_check
        sc_payload = request.get_json(silent=True)
        if sc_payload is None:
            sc_payload = request.args.to_dict(flat=True)
            sc_payload.pop('access_token', None)
        status, body = serve_site_check(sc_payload)
        return status, body
    if resource_id == 'market-data-read':
        # Market-data single price read (G6). The pair/mode arrive
        # in the request payload, validated by the handler; the
        # caller consumes the token only on a 200 (P1-1). Stateless:
        # every call re-reads the Chainlink feed seam.
        from sincor2.market_data_read import serve_market_data_read
        md_payload = request.get_json(silent=True)
        if md_payload is None:
            md_payload = request.args.to_dict(flat=True)
            md_payload.pop('access_token', None)
        status, body = serve_market_data_read(md_payload)
        return status, body
    if resource_id == 'polymarket-whale-signal':
        # Polymarket whale signal single read (G3). Optional
        # wallet/market filters come from the request payload; the
        # caller consumes the token only on a 200 (P1-1) — an empty
        # registry's 503 never consumes it (P1-3b).
        from sincor2.whale_signal import serve_whale_signal
        signal_payload = request.get_json(silent=True)
        if signal_payload is None:
            signal_payload = request.args.to_dict(flat=True)
            signal_payload.pop('access_token', None)
        status, body = serve_whale_signal(signal_payload)
        return status, body
    if resource_id == 'kya-trust-score':
        # KYA trust score deep read (G2). The agent to score comes
        # from the request payload; the caller consumes the token
        # only on a 200 (P1-1).
        from sincor2.kya_trust_score import serve_kya_trust_score
        score_payload = request.get_json(silent=True)
        if score_payload is None:
            score_payload = request.args.to_dict(flat=True)
            score_payload.pop('access_token', None)
        status, body = serve_kya_trust_score(score_payload)
        return status, body
    if resource_id == 'wallet-sanctions-screen':
        # Single-read OFAC screen (G1). The address to screen comes
        # from the request payload; the caller consumes the token
        # only on a 200 (P1-1) — a stale snapshot's 503 never
        # consumes it (P1-3a).
        from sincor2.sanctions_screen import serve_sanctions_screen
        screen_payload = request.get_json(silent=True)
        if screen_payload is None:
            screen_payload = request.args.to_dict(flat=True)
            screen_payload.pop('access_token', None)
        status, body = serve_sanctions_screen(screen_payload)
        return status, body
    if resource_id == 'hook_status':
        try:
            from sincor2.hook_stats import fetch_hook_status
            return 200, {'ok': True, 'resource': resource_id,
                         'data': fetch_hook_status()}
        except Exception as e:
            return 500, {'ok': False, 'error': str(e)}
    if resource_id == 'toa-micro-decision':
        # TOA micro-decision (G7). The decision question arrives as
        # structured options+signals in the request payload,
        # validated by the handler; the caller consumes the token
        # only on a 200 (P1-1).
        from sincor2.toa_micro_decision import serve_toa_micro_decision
        toa_payload = request.get_json(silent=True)
        if toa_payload is None:
            toa_payload = request.args.to_dict(flat=True)
            toa_payload.pop('access_token', None)
        status, body = serve_toa_micro_decision(toa_payload)
        return status, body
    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.args.to_dict(flat=True)
        payload.pop('access_token', None)
    status, body = execute_paid_resource(resource_id, payload)
    return status, body


@bp.route('/api/payment/webhook', methods=['POST'])
@limiter.limit("500 per minute")
def payment_webhook():
    """
    Receive Stripe webhook events for payment processing.
    Verifies webhook signature before processing.
    Stores order in DB and triggers product fulfillment/delivery.
    """
    if not fiat_payments_enabled():
        # B3: log loudly — a webhook arriving while the integration is
        # disabled is either a misconfiguration or a probe; never silent.
        logger.error('[WEBHOOK] Stripe webhook received while legacy fiat payments '
                     'are DISABLED (founder decision D3: Stripe is legacy/not-supported). '
                     'Event dropped; no processing performed.')
        return jsonify({'error': 'Legacy fiat payments disabled. Use SINC/AXM at /buy.'}), 410
    if not stripe_processor or not stripe_processor.enabled:
        logger.error('[WEBHOOK] Stripe webhook received but the Stripe processor is '
                     'not configured/enabled. Event dropped; no processing performed.')
        return jsonify({'error': 'Payment processor not configured'}), 503

    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')

    if not sig_header:
        logger.warning('[WEBHOOK] Missing Stripe-Signature header')
        return jsonify({'error': 'Missing signature'}), 400

    success, event_data = stripe_processor.verify_webhook(payload, sig_header)
    if not success:
        logger.warning('[WEBHOOK] Stripe webhook verification failed')
        return jsonify({'error': 'Webhook verification failed'}), 400

    event_type = event_data.get('event', 'unknown')

    # B3: persist the Stripe event ID with a UNIQUE constraint BEFORE
    # processing (insert-or-ignore claim). Duplicate deliveries are
    # acknowledged without re-fulfillment; fulfillment is idempotent keyed
    # on the event ID.
    event_id = (event_data.get('event_id') or '').strip()
    if not event_id:
        logger.error('[WEBHOOK] Stripe event without event_id — refusing to process')
        return jsonify({'error': 'Missing Stripe event id'}), 400
    claim = claim_stripe_webhook_event(event_id, event_type)
    if claim in ('duplicate_processed', 'duplicate_inflight'):
        logger.warning('[WEBHOOK] Duplicate Stripe event %s (%s) — acknowledged '
                       'without re-fulfillment', event_id, claim)
        return jsonify({'success': True, 'event': event_type, 'duplicate': True}), 200
    if claim == 'reclaimed':
        logger.warning('[WEBHOOK] Reclaimed stale Stripe event %s from a crashed '
                       'attempt — reprocessing', event_id)

    logger.info(f"[WEBHOOK] Stripe event: {event_type}")

    # Only process completed payments
    if event_type != 'payment_completed':
        mark_stripe_webhook_event_processed(event_id)
        return jsonify({'success': True, 'event': event_type}), 200

    customer_email = validate_email(event_data.get('customer_email', ''))
    if not customer_email:
        logger.warning('[WEBHOOK] No valid email in payment event')
        return jsonify({'error': 'Missing customer email'}), 400

    amount_cents = event_data.get('amount_total', 0)
    amount = amount_cents / 100 if amount_cents else 0
    session_id = sanitize_string(event_data.get('session_id', ''), max_length=100)
    subscription_id = sanitize_string(event_data.get('subscription_id', '') or '', max_length=100)

    # Determine product from amount.
    # NOTE: PRODUCT_PRICES lives on the auth blueprint; bind lazily so the
    # legacy webhook can never NameError at runtime if module wiring changes.
    try:
        from sincor2.mvp_blueprints.auth import PRODUCT_PRICES as _STRIPE_PRICES
    except ImportError:
        _STRIPE_PRICES = {'Starter': 297, 'Professional': 997, 'Enterprise': 2997}
    product_name = 'Unknown'
    for name, price in _STRIPE_PRICES.items():
        if abs(amount - price) < 1:  # Allow for rounding
            product_name = name
            break

    order_id = f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{session_id[:8]}"
    product_info = PRODUCT_CATALOG.get(product_name, {'type': 'generic'})
    order_type = product_info.get('type', 'generic')

    if order_type == 'subscription':
        delivery_url = f"/dashboard?email={customer_email}&plan={product_name}"
        delivery_status = 'delivered'
    else:
        delivery_url = f"/my-orders?email={customer_email}"
        delivery_status = 'processing'

    # Store order in database
    db = get_db()
    try:
        db.execute(
            '''INSERT INTO orders
               (order_id, paypal_order_id, customer_email, product_name, amount,
                currency, payment_status, delivery_status, delivery_url, order_type,
                created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (order_id, session_id, customer_email, product_name, amount,
             'USD', 'completed', delivery_status, delivery_url, order_type,
             datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
             json.dumps({'stripe_session_id': session_id, 'subscription_id': subscription_id}))
        )
        db.commit()
        logger.info(f"[ORDER] Saved: {order_id} | {product_name} | ${amount} | {customer_email}")
    except sqlite3.IntegrityError:
        logger.warning(f"[ORDER] Duplicate order: {session_id}")

    # Trigger product delivery
    try:
        trigger_fulfillment(order_id, customer_email, product_name, amount, order_type, product_info)
    except Exception:
        # Processing failed: release the claim so a redelivery can retry
        # instead of being acked as a duplicate.
        release_stripe_webhook_event_claim(event_id)
        raise

    # B3: fulfillment done — mark the event processed so redeliveries are
    # acknowledged as duplicates instead of re-fulfilling.
    mark_stripe_webhook_event_processed(event_id)

    return jsonify({'success': True, 'order_id': order_id}), 200


def trigger_fulfillment(order_id, email, product_name, amount, order_type, product_info):
    """
    Trigger asset delivery based on product type.
    Includes sending thank-you email for subscription orders.
    Returns delivery result dict.
    """
    result = {'message': '', 'next_steps': [], 'email_sent': False}

    # C4: route value-granting fulfillment through the effect boundary BEFORE
    # any delivery. Fail-closed: a denied intent aborts fulfillment.
    # W-80: the treasury HALT file is consulted explicitly — a tripped
    # (or unreadable/tampered) halt denies fulfillment via the money gate.
    try:
        from sincor2.governance.money_gate import (
            check_money_effect,
            treasury_halt_tripped,
        )
    except ImportError:
        try:
            from src.sincor2.governance.money_gate import (  # type: ignore
                check_money_effect,
                treasury_halt_tripped,
            )
        except ImportError as exc:
            logger.critical(
                "[FULFILL] money gate unavailable (%s) — fulfillment DENIED "
                "for %s (fail closed)", exc, order_id)
            result['message'] = 'Fulfillment blocked by governance gate.'
            return result
    allowed, gate_reason = check_money_effect(
        effect_type="payment.transfer",
        payload={"order_id": order_id, "amount": amount,
                 "order_type": order_type, "product_name": product_name},
        agent_id="billing_fulfillment",
        risk_tier="high",
        idempotency_key=f"fulfill-{order_id}",
        target="order_fulfillment",
        estimated_cost=float(amount or 0),
        kill_switch_tripped=treasury_halt_tripped(),
    )
    if not allowed:
        logger.critical(
            "[FULFILL] effect boundary denied fulfillment for %s: %s",
            order_id, gate_reason)
        result['message'] = 'Fulfillment blocked by governance gate.'
        return result

    # First, determine all the delivery details
    agent_count = product_info.get('agents', 10)

    if order_type == 'subscription':
        # Subscription: Activate account + agents immediately
        result['message'] = f'Your {product_name} plan is ACTIVE! {agent_count} AI agents are now working for you.'
        result['next_steps'] = [
            'Check your email for login credentials and training guides',
            f'Access your dashboard with {agent_count} active AI agents',
            'Your agents will begin generating leads and content within 24 hours'
        ]
        logger.info(f"[FULFILL] Subscription activated: {product_name} ({agent_count} agents) for {email}")

        # Update delivery status to delivered
        db = get_db()
        db.execute(
            "UPDATE orders SET delivery_status='delivered', updated_at=? WHERE order_id=?",
            (datetime.utcnow().isoformat(), order_id)
        )
        db.commit()

        # Send thank-you email with training guides
        if email_sender:
            try:
                customer_name = email.split('@')[0].title()
                tier = product_name if product_name in ['Starter', 'Professional', 'Enterprise'] else 'Professional'

                # Build download URLs
                download_urls = {
                    'starter': f'/files/guides/sincor-starter-guide-{order_id}.pdf',
                    'professional': f'/files/guides/sincor-professional-guide-{order_id}.pdf',
                    'enterprise': f'/files/guides/sincor-enterprise-guide-{order_id}.pdf',
                    'quickstart': f'/files/guides/quickstart-checklist-{order_id}.pdf'
                }

                email_result = email_sender.send_thank_you_email(
                    customer_email=email,
                    customer_name=customer_name,
                    tier=tier,
                    order_id=order_id,
                    download_urls=download_urls
                )

                result['email_sent'] = email_result.get('status') in ['sent', 'stub']
                logger.info(f"[EMAIL] Thank-you email for {email}: {email_result.get('status')}")

            except Exception as e:
                logger.error(f"[EMAIL] Error sending thank-you email for {order_id}: {e}")
                result['email_sent'] = False

    elif order_type == 'bi_report':
        # BI Report: Queue for generation (delivered within 48h)
        sections = product_info.get('sections', [])
        pages = product_info.get('pages', 20)
        result['message'] = f'Your {product_name} is being generated! {pages}-page report with {len(sections)} sections.'
        result['next_steps'] = [
            'Report generation started automatically',
            f'You will receive a {pages}-page report within 48 hours',
            'Download link will be emailed and available at /my-orders'
        ]
        logger.info(f"[FULFILL] BI Report queued: {product_name} ({pages} pages) for {email}")

    elif order_type == 'content':
        # Content Package: Queue for creation
        pieces = product_info.get('pieces', '1-5')
        days = product_info.get('delivery_days', 7)
        result['message'] = f'Your content package is in production! {pieces} pieces being created.'
        result['next_steps'] = [
            f'Content creation started - {pieces} professional pieces',
            f'Expected delivery within {days} business days',
            'Download link will be emailed and available at /my-orders'
        ]
        logger.info(f"[FULFILL] Content Package queued: {product_name} ({pieces} pieces) for {email}")

    else:
        result['message'] = 'Order received and being processed.'
        result['next_steps'] = ['Check /my-orders for delivery status']
        logger.info(f"[FULFILL] Generic order: {order_id} for {email}")

    return result


# ============================================================================
# PAYMENT SUCCESS PAGE
# ============================================================================

@bp.route('/payment/success')
def payment_success():
    """
    Render payment success page after Stripe checkout.
    """
    session_id = request.args.get('session_id', '')
    order_id = request.args.get('order_id', '')

    # Try to find order in DB
    lookup_id = order_id or session_id
    if lookup_id:
        db = get_db()
        row = db.execute(
            "SELECT * FROM orders WHERE paypal_order_id=? OR order_id=? ORDER BY created_at DESC LIMIT 1",
            (lookup_id, lookup_id)
        ).fetchone()
        if row:
            email = row['customer_email'] if isinstance(row, dict) else row[3]
            oid = row['order_id'] if isinstance(row, dict) else row[1]
            # Redirect to onboarding intake form first, then thank-you
            return redirect(f'/onboarding?email={email}&order_id={oid}')

    return render_template('payment_success.html', order_data=None)


@bp.route('/thank-you/<order_id>')
def thank_you_email(order_id):
    """
    Render the thank-you email template with order and customer personalization.
    This can be used for both email rendering and live preview.
    """
    # Fetch order data from database
    db = get_db()
    row = db.execute(
        "SELECT * FROM orders WHERE order_id=? OR paypal_order_id=? ORDER BY created_at DESC LIMIT 1",
        (order_id, order_id)
    ).fetchone()

    if not row:
        return render_template('error.html', code=404, title='Order Not Found',
                             message=f"Order {order_id} not found."), 404

    order_data = dict(row)
    product_name = order_data.get('product_name', '').strip()

    # Determine tier (Starter, Professional, Enterprise)
    tier_name = product_name if product_name in ['Starter', 'Professional', 'Enterprise'] else 'Enterprise'
    tier_slug = tier_name.lower()

    # Extract customer details
    customer_name = order_data.get('customer_email', 'Customer').split('@')[0].title()

    # Get product info for page count and feature count
    product_info = PRODUCT_CATALOG.get(product_name, {})
    agent_count = product_info.get('agents', 10)
    features = product_info.get('features', [])
    feature_list = ', '.join(features) if features else 'All core features'

    # Determine which tier sections are visible
    tier_flags = {
        'STARTER_SELECTED': tier_name == 'Starter',
        'PROFESSIONAL_SELECTED': tier_name == 'Professional',
        'ENTERPRISE_SELECTED': tier_name == 'Enterprise',
    }

    # Template variables for personalization
    template_vars = {
        'CUSTOMER_NAME': customer_name,
        'CUSTOMER_EMAIL': order_data.get('customer_email', ''),
        'TIER_NAME': tier_name,
        'TIER_SLUG': tier_slug,
        'AGENT_COUNT': agent_count,
        'FEATURE_LIST': feature_list,
        'ACTIVATION_DATE': order_data.get('created_at', '').split('T')[0],
        'PAGE_COUNT': {'Starter': 30, 'Professional': 60, 'Enterprise': 120}.get(tier_name, 30),
        'INTEGRATION_COUNT': {'Starter': 5, 'Professional': 15, 'Enterprise': 25}.get(tier_name, 5),
        'DOWNLOAD_STARTER_GUIDE': f'/files/guides/sincor-starter-guide-{order_data.get("order_id")}.pdf',
        'DOWNLOAD_PROFESSIONAL_GUIDE': f'/files/guides/sincor-professional-guide-{order_data.get("order_id")}.pdf',
        'DOWNLOAD_ENTERPRISE_GUIDE': f'/files/guides/sincor-enterprise-guide-{order_data.get("order_id")}.pdf',
        'DOWNLOAD_QUICKSTART': f'/files/guides/quickstart-checklist-{order_data.get("order_id")}.pdf',
        'DASHBOARD_URL': f'/dashboard?email={order_data.get("customer_email", "")}&order={order_id}',
        'HELP_URL': 'https://help.sincor.com',
        'STATUS_URL': 'https://status.sincor.com',
        'UNSUBSCRIBE': 'mailto:support@getsincor.com?subject=Unsubscribe',
        'COMPANY_ADDRESS': '123 Innovation Drive, Tech City, TC 12345',
        **tier_flags
    }

    logger.info(f"[EMAIL] Rendering thank-you email for {order_id} | {tier_name} | {order_data.get('customer_email')}")

    return render_template('thank_you_purchase_email.html', **template_vars)


@bp.route('/admin/training-vault')
@limiter.limit("500 per minute")
def admin_training_vault():
    """
    Render the training vault dashboard for logged-in customers.
    Shows tier-specific guides, videos, industry guides, and onboarding progress.
    SECURITY: Requires a valid order token (order_id tied to email) or an admin session.
    Email alone is NOT sufficient — prevents trivial enumeration access.
    """
    is_admin = _is_admin_session()
    customer_email = request.args.get('email') or request.args.get('customer_email')
    order_token = sanitize_string((request.args.get('order_id') or '').strip(), max_length=64)

    if is_admin:
        if customer_email and not validate_email(customer_email):
            # Staff usernames are not customer emails — ignore them.
            customer_email = None
    else:
        if not customer_email or not validate_email(customer_email):
            return render_template('error.html', code=401, title='Authentication Required',
                                 message="Please log in to access your training vault."), 401
        if not order_token:
            return render_template('error.html', code=401, title='Authentication Required',
                                 message="Access token required. Check your confirmation email for your order link."), 401

    db = get_db()
    rows = None
    if is_admin and customer_email:
        rows = db.execute(
            "SELECT * FROM orders WHERE customer_email=? AND product_name IN ('Starter', 'Professional', 'Enterprise') "
            "ORDER BY created_at DESC LIMIT 1",
            (customer_email,)
        ).fetchone()
    elif not is_admin:
        rows = db.execute(
            "SELECT * FROM orders WHERE customer_email=? AND order_id=? "
            "AND product_name IN ('Starter', 'Professional', 'Enterprise') LIMIT 1",
            (customer_email, order_token)
        ).fetchone()

    if not rows:
        if is_admin:
            customer_email = customer_email or 'operator@getsincor.com'
            order_data = {
                'order_id': 'operator',
                'customer_email': customer_email,
                'product_name': 'Enterprise',
                'created_at': datetime.utcnow().isoformat(),
            }
        else:
            return render_template('error.html', code=404, title='No Active Subscription',
                                 message="You don't have an active SINCOR subscription. Please purchase one to access training materials."), 404
    else:
        order_data = dict(rows)
        # B4: confirm the order's canonical paid/active state before serving
        # vault materials. Re-reads the order record; never trusts a
        # client-supplied flag or cached status. Admin sessions bypass (server
        # -side trust domain, not a customer claim).
        if not is_admin and not _order_is_paid(order_data):
            return _vault_denied_response(order_data)

    product_name = order_data.get('product_name', 'Enterprise')
    tier_name = product_name if product_name in ['Starter', 'Professional', 'Enterprise'] else 'Enterprise'
    tier_slug = tier_name.lower()

    # Get product info
    product_info = PRODUCT_CATALOG.get(product_name, {})
    agent_count = product_info.get('agents', 10)

    # Determine onboarding progress (default all pending; update based on customer activity)
    onboarding_steps = {
        'GUIDE_DOWNLOADED': False,
        'CONFIG_COMPLETE': False,
        'INTEGRATIONS_ACTIVE': False,
        'WORKFLOW_ACTIVE': False,
        'MULTI_AGENT_ENABLED': tier_name in ['Professional', 'Enterprise'],
        'WHITE_LABEL_ENABLED': tier_name == 'Enterprise',
        'CUSTOM_AGENTS_ENABLED': tier_name == 'Enterprise',
    }

    # Template variables for training vault
    template_vars = {
        'TIER': tier_name,
        'TIER_SLUG': tier_slug,
        'CUSTOMER_EMAIL': customer_email,
        'CUSTOMER_NAME': (customer_email or 'operator').split('@')[0].title(),
        'AGENT_COUNT': agent_count,
        'PAGE_COUNT': {'Starter': 30, 'Professional': 60, 'Enterprise': 120}.get(tier_name, 30),
        'INTEGRATION_COUNT': {'Starter': 5, 'Professional': 15, 'Enterprise': 25}.get(tier_name, 5),
        # Tier conditional flags for template
        'STARTER': tier_name == 'Starter',
        'PROFESSIONAL': tier_name == 'Professional',
        'ENTERPRISE': tier_name == 'Enterprise',
        # Onboarding status flags
        'GUIDE_DOWNLOADED': onboarding_steps['GUIDE_DOWNLOADED'],
        'CONFIG_COMPLETE': onboarding_steps['CONFIG_COMPLETE'],
        'INTEGRATIONS_ACTIVE': onboarding_steps['INTEGRATIONS_ACTIVE'],
        'WORKFLOW_ACTIVE': onboarding_steps['WORKFLOW_ACTIVE'],
        # Download URLs (these would point to actual PDF files in production)
        'DOWNLOAD_GUIDE_URL': f'/files/guides/sincor-{tier_slug}-guide-{order_data.get("order_id")}.pdf',
        'VIEW_GUIDE_URL': f'/guides/{tier_slug}-guide-online',
        'DOWNLOAD_QUICKSTART': f'/files/guides/quickstart-{order_data.get("order_id")}.pdf',
        'DOWNLOAD_CONFIG_TEMPLATE': f'/files/templates/config-template-{tier_slug}-{order_data.get("order_id")}.xlsx',
    }

    logger.info(f"[VAULT] Training vault accessed: {customer_email} | {tier_name}")

    return render_template('admin_training_vault.html', **template_vars)


@bp.route('/files/guides/<filename>', methods=['GET'])
@limiter.limit("2000 per hour")
def download_guide(filename):
    """
    Serve training guide PDF files.
    Generates PDF on first request, caches for subsequent requests.
    SECURITY: Requires email + order_id query params to verify the requester
    actually owns an order � prevents downloading guides without paying.
    """
    # Verify ownership: email + order_id must match a real order
    req_email = validate_email(request.args.get('email', ''))
    req_order = sanitize_string(request.args.get('order_id', ''), max_length=64)

    if not _is_admin_session():
        if not req_email or not req_order:
            return jsonify({'error': 'Authentication required. Include email and order_id params.'}), 401

        db = get_db()
        # B4: re-read the canonical order record and require a paid/active
        # payment_status before serving any vault material.
        order_row = db.execute(
            "SELECT order_id, payment_status FROM orders WHERE customer_email=? AND order_id=? LIMIT 1",
            (req_email, req_order)
        ).fetchone()
        if not order_row:
            logger.warning(f'[DOWNLOAD] Unauthorized guide access attempt: {req_email} / {req_order} from {request.remote_addr}')
            return jsonify({'error': 'Order not found or access denied'}), 403
        if not _order_is_paid(order_row):
            order_status = str(order_row["payment_status"] or "").strip().lower()
            logger.warning(f'[DOWNLOAD] Denied guide for unpaid order: {req_email} / {req_order} (status={order_status})')
            if order_status == "pending":
                return jsonify({'error': 'Payment pending. Materials unlock once payment completes.'}), 402
            return jsonify({'error': 'Order is not in a paid state. Access denied.'}), 403

        # Verify the filename contains this order_id (so you can only download your own guide)
        if req_order not in filename:
            logger.warning(f'[DOWNLOAD] Order/filename mismatch: {req_order} vs {filename} from {request.remote_addr}')
            return jsonify({'error': 'Access denied'}), 403

    # Validate filename to prevent directory traversal
    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({'error': 'Invalid filename'}), 400

    if not filename.endswith('.pdf'):
        return jsonify({'error': 'Only PDF files allowed'}), 400

    # Whitelist allowed filename patterns
    ALLOWED_GUIDE_PATTERNS = [
        'sincor-starter-guide-',
        'sincor-professional-guide-',
        'sincor-enterprise-guide-',
        'quickstart-checklist-'
    ]
    if not any(filename.startswith(pattern) for pattern in ALLOWED_GUIDE_PATTERNS):
        return jsonify({'error': 'Invalid guide filename'}), 400

    # Check if file already exists
    filepath = Path(pdf_guides_dir) / filename
    if filepath.exists():
        logger.info(f"[DOWNLOAD] Serving cached guide: {filename}")
        try:
            return send_file(
                filepath,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=filename
            )
        except Exception as e:
            logger.error(f"[DOWNLOAD] Error serving file {filename}: {e}")
            return jsonify({'error': 'Error serving file'}), 500

    # PDF doesn't exist, try to generate it
    if not pdf_generator:
        return jsonify({'error': 'PDF generation not available'}), 503

    try:
        # Extract tier and order_id from filename
        if 'starter' in filename:
            order_id = filename.replace('sincor-starter-guide-', '').replace('.pdf', '')
            filepath, pages = pdf_generator.generate_starter_guide(order_id)
        elif 'professional' in filename:
            order_id = filename.replace('sincor-professional-guide-', '').replace('.pdf', '')
            filepath, pages = pdf_generator.generate_professional_guide(order_id)
        elif 'enterprise' in filename:
            order_id = filename.replace('sincor-enterprise-guide-', '').replace('.pdf', '')
            filepath, pages = pdf_generator.generate_enterprise_guide(order_id)
        elif 'quickstart' in filename:
            order_id = filename.replace('quickstart-checklist-', '').replace('.pdf', '')
            filepath, pages = pdf_generator.generate_quickstart_checklist(order_id)
        else:
            return jsonify({'error': 'Unknown guide type'}), 400

        logger.info(f"[DOWNLOAD] Generated and serving guide: {filename} ({pages} pages)")

        return send_file(
            filepath,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"[DOWNLOAD] Error generating guide {filename}: {e}")
        return jsonify({'error': 'Could not generate PDF. Please contact support.'}), 500


@bp.route('/payment/cancel')
def payment_cancel():
    """Render payment cancelled page."""
    return render_template('payment_cancel.html')


# ============================================================================
# ORDER MANAGEMENT
# ============================================================================

@bp.route('/my-orders')
def my_orders_page():
    """Render My Orders page where customers can view/download purchases."""
    return render_template('my_orders.html')


@bp.route('/api/orders/<email>', methods=['GET'])
@limiter.limit("200 per minute")
def get_customer_orders(email):
    """
    Get all orders for a customer by email.
    AUDIT P1 finding 2: requires authentication. The caller must present a
    JWT (Authorization header or access_token cookie) whose identity is the
    requested email (case-insensitive); admins may query any email.
    """
    identity = _request_jwt_identity()
    if not identity:
        return jsonify({'error': 'Authentication required'}), 401
    email = validate_email(email)
    if not email:
        return jsonify({'error': 'Invalid email format'}), 400
    if not (_is_admin_identity(identity) or str(identity).lower() == email.lower()):
        logger.warning(
            "[ORDERS] Forbidden order lookup: identity=%r requested=%r from %s",
            identity, email, request.remote_addr,
        )
        return jsonify({'error': 'Forbidden'}), 403

    db = get_db()
    rows = db.execute(
        "SELECT * FROM orders WHERE customer_email=? ORDER BY created_at DESC",
        (email,)
    ).fetchall()

    orders = []
    for row in rows:
        orders.append({
            'order_id': row['order_id'],
            'product_name': row['product_name'],
            'amount': row['amount'],
            'currency': row['currency'],
            'payment_status': row['payment_status'],
            'delivery_status': row['delivery_status'],
            'delivery_url': row['delivery_url'],
            'order_type': row['order_type'],
            'created_at': row['created_at']
        })

    # Always return 200 to prevent email enumeration
    return jsonify({
        'success': True,
        'email': email,
        'orders': orders,
        'count': len(orders)
    }), 200


@bp.route('/api/orders', methods=['GET'])
@jwt_required()
def list_all_orders():
    """Admin endpoint: list all orders. Requires valid admin JWT."""
    current_user = get_jwt_identity()
    if not _ct_eq(str(current_user or '').lower(), _admin_username().lower()):
        return jsonify({'error': 'Forbidden'}), 403
    db = get_db()
    rows = db.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 100").fetchall()
    orders = [dict(row) for row in rows]
    return jsonify({'success': True, 'orders': orders, 'count': len(orders)}), 200


# ============================================================================
# SIN TOKEN AIRDROP
# ============================================================================

@bp.route('/sin-airdrop')
def sin_airdrop():
    """SIN Token Airdrop funnel page."""
    return render_template('sin-airdrop.html')


@bp.route('/api/airdrop/register', methods=['POST'])
@limiter.limit("200 per minute")
def register_airdrop():
    """Register wallet for SIN token airdrop."""
    data = request.get_json(silent=True) or {}
    raw_wallet = data.get('wallet', '')

    wallet = validate_wallet(raw_wallet)
    if not wallet:
        return jsonify({'error': 'Invalid or missing wallet address (must be 0x + 40 hex chars)'}), 400

    logger.info(f"[AIRDROP] New registration: {wallet}")
    return jsonify({
        'status': 'success',
        'message': 'Successfully registered for SIN airdrop',
        'wallet': wallet
    }), 201



@bp.route('/api/crypto/checkout', methods=['POST'])
def crypto_checkout():
    """Create crypto payment checkout (ETH/USDC on Base)."""
    data = request.get_json(silent=True) or {}
    currency = sanitize_string(data.get('currency', 'ETH'), max_length=10).upper()

    try:
        amount = float(data.get('amount', 0))
        if amount <= 0 or amount > 100000:
            return jsonify({'error': 'Amount must be between 0 and 100,000'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid amount'}), 400

    wallet = validate_wallet(data.get('wallet', ''))
    if not wallet:
        return jsonify({'error': 'Invalid wallet address'}), 400

    # DW-308 REPAIR 3 (S1b): the product named at checkout is persisted
    # with the expectation and governs verify/fulfillment — the verify
    # caller's product_name never overrides a recorded checkout.
    product_name = sanitize_string(data.get('product_name', ''), max_length=100)

    recipient_address = os.environ.get('BASE_PAYMENT_ADDRESS')
    if not recipient_address:
        logger.error('[CRYPTO] BASE_PAYMENT_ADDRESS not configured')
        return jsonify({'error': 'Crypto payments not configured'}), 503

    # Fetch live ETH price from CoinGecko (free, no key required)
    # Falls back to a conservative floor price if API is unavailable
    eth_price = None
    try:
        import urllib.request as _urllib2
        import json as _json2
        cg_req = _urllib2.Request(
            'https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd',
            headers={'User-Agent': 'sincor-payment/1.0'}
        )
        with _urllib2.urlopen(cg_req, timeout=5) as cg_resp:  # nosec B310 — hardcoded CoinGecko URL
            cg_data = _json2.loads(cg_resp.read().decode('utf-8'))
            eth_price = float(cg_data['ethereum']['usd'])
            logger.info(f'[CRYPTO] Live ETH price: ${eth_price}')
    except Exception as e:
        logger.warning(f'[CRYPTO] Could not fetch live ETH price: {e}. Using env fallback.')
        # Allow operator to set a floor via env; default conservative
        eth_price = float(os.environ.get('ETH_PRICE_FALLBACK', '10000'))

    if currency == 'ETH':
        crypto_amount = amount / eth_price
    elif currency == 'USDC':
        crypto_amount = amount / 1.0
    else:
        return jsonify({'error': f'unsupported currency: {currency}'}), 400

    # DW-308 REPAIR 3 (S1): the timestamp alone has one-second
    # resolution and is fully predictable — two checkouts in the same
    # second minted the SAME payment_id and the later persist silently
    # replaced the earlier expectation (INSERT OR REPLACE). The id now
    # carries a random suffix, and the persist below is a plain INSERT
    # that refuses to overwrite: on the astronomically unlikely suffix
    # collision the id is regenerated and the insert retried.
    payment_id = None

    # DW-308 REPAIR (Builder B): persist the expected payment server-side,
    # keyed by payment_id. Verify must compare the on-chain Transfer value
    # against THIS record — never against an amount the verify caller
    # claims — and the record also binds the token: USDC checkouts bind
    # the canonical Base USDC contract; ETH checkouts bind no ERC-20
    # token (native value emits no Transfer event, so verify fails
    # closed for them, as it has since the original DW-308 fix).
    from sincor2.onchain.constants import USDC_DECIMALS, USDC_TOKEN

    if currency == 'USDC':
        token_address = USDC_TOKEN
        expected_atomic = int(round(crypto_amount * (10 ** USDC_DECIMALS)))
    else:  # ETH — native value, no token contract to bind
        token_address = ''
        expected_atomic = int(round(crypto_amount * (10 ** 18)))
    try:
        for _attempt in range(5):
            candidate = (
                f"CRYPTO-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                f"-{_unique_id_suffix()}"
            )
            try:
                _persist_crypto_checkout(candidate, currency, token_address,
                                         expected_atomic, amount, wallet,
                                         product_name)
            except sqlite3.IntegrityError:
                continue  # id collision — regenerate and retry the insert
            payment_id = candidate
            break
    except Exception as e:
        logger.error(f'[CRYPTO] Could not persist checkout: {e}')
        return jsonify({'error': 'Could not create checkout. Please try again.'}), 503
    if payment_id is None:
        logger.error('[CRYPTO] Could not mint a unique payment_id after retries')
        return jsonify({'error': 'Could not create checkout. Please try again.'}), 503

    return jsonify({
        'payment_id': payment_id,
        'status': 'pending',
        'amount_usd': amount,
        'amount_crypto': round(crypto_amount, 8),
        'currency': currency,
        'wallet': wallet,
        'network': 'Base',
        'chain_id': 8453,
        'recipient_address': recipient_address,
        'message': f'Send {round(crypto_amount, 8)} {currency} to complete purchase'
    }), 201


def _unique_id_suffix() -> str:
    """Random suffix for timestamp-based ids (DW-308 REPAIR 3, S1/S2).

    A bare second-resolution timestamp is predictable and collides for
    any two ids minted in the same second; the random suffix makes the
    id unique and unguessable while keeping the readable prefix.
    """
    import secrets

    return secrets.token_hex(4)


def _ensure_crypto_checkout_table(db):
    db.execute(
        '''CREATE TABLE IF NOT EXISTS crypto_checkouts
           (payment_id TEXT PRIMARY KEY,
            currency TEXT NOT NULL,
            token_address TEXT NOT NULL DEFAULT '',
            expected_atomic INTEGER NOT NULL,
            amount_usd REAL NOT NULL,
            wallet TEXT,
            product_name TEXT NOT NULL DEFAULT '',
            settled_tx_hash TEXT,
            settled_at TEXT,
            created_at TEXT NOT NULL)'''
    )
    # DW-308 REPAIR 3: columns added after the table first shipped —
    # add them to pre-existing tables (S1b product binding, S3
    # consumption marker).
    columns = {row[1] for row in db.execute(
        'PRAGMA table_info(crypto_checkouts)')}
    altered = False
    if 'product_name' not in columns:
        db.execute(
            "ALTER TABLE crypto_checkouts "
            "ADD COLUMN product_name TEXT NOT NULL DEFAULT ''")
        altered = True
    if 'settled_tx_hash' not in columns:
        db.execute(
            'ALTER TABLE crypto_checkouts ADD COLUMN settled_tx_hash TEXT')
        altered = True
    if 'settled_at' not in columns:
        db.execute(
            'ALTER TABLE crypto_checkouts ADD COLUMN settled_at TEXT')
        altered = True
    if altered:
        db.commit()


def _persist_crypto_checkout(payment_id, currency, token_address,
                             expected_atomic, amount_usd, wallet,
                             product_name=''):
    """Record the server-side expectation for a crypto checkout.

    DW-308 REPAIR 3 (S1): plain INSERT — an existing record is NEVER
    overwritten. A payment_id collision raises sqlite3.IntegrityError
    (after rolling back the failed statement); the caller regenerates
    the id and retries.
    """
    db = get_db()
    _ensure_crypto_checkout_table(db)
    try:
        db.execute(
            '''INSERT INTO crypto_checkouts
               (payment_id, currency, token_address, expected_atomic,
                amount_usd, wallet, product_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (payment_id, currency, token_address, int(expected_atomic),
             float(amount_usd), wallet, product_name or '',
             datetime.utcnow().isoformat()),
        )
    except sqlite3.IntegrityError:
        db.rollback()
        raise
    db.commit()


def _crypto_checkout_record(payment_id):
    """The persisted checkout expectation for *payment_id*, or None."""
    db = get_db()
    _ensure_crypto_checkout_table(db)
    row = db.execute(
        '''SELECT currency, token_address, expected_atomic, amount_usd,
                  product_name, settled_tx_hash
           FROM crypto_checkouts WHERE payment_id=?''',
        (payment_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        'currency': row[0],
        'token_address': row[1],
        'expected_atomic': int(row[2]),
        'amount_usd': row[3],
        'product_name': row[4] or '',
        'settled_tx_hash': row[5] or '',
    }


def _settled_transfer_total(receipt, token_address, recipient_address) -> int:
    """Summed value (atomic units) of *receipt*'s qualifying payment
    Transfer logs.

    DW-308: a mined receipt with status 0x1 is NOT settlement proof by
    itself — the payment recipient appears ONLY in the Transfer event
    (the receipt's own `to` names the token contract for ERC-20
    payments), and a bare value-less call to the recipient produces a
    successful receipt with no Transfer event at all.

    DW-308 REPAIR (Builder B): a Transfer event is not proof either,
    unless it is the EXPECTED one. A log qualifies only when it was
    emitted by *token_address* (the expected payment token contract —
    a Transfer from any other contract settles nothing), pays
    *recipient_address*, and is not flagged `removed` (reorged out).
    Log parsing mirrors the in-repo reference,
    ``platform_payments._parse_transfer_logs``; the caller compares the
    SUM against the expected amount, so split payments across several
    logs settle exactly like one full payment.
    """
    if not token_address or not recipient_address:
        return 0
    from sincor2.platform_payments import _parse_transfer_logs

    live_logs = [log for log in receipt.get('logs') or []
                 if not log.get('removed')]
    # DW-308 REPAIR 3 (S4): a log listed twice in one receipt (same
    # transactionHash + logIndex) is ONE transfer — the reference
    # parser sums every entry it is handed, so duplicates are dropped
    # HERE (platform_payments is shared with other consumers and keeps
    # its current behavior). Logs without a logIndex cannot be told
    # apart this way and are kept as-is.
    seen_logs = set()
    unique_logs = []
    for log in live_logs:
        log_index = log.get('logIndex')
        if log_index is not None:
            key = (
                str(log.get('transactionHash')
                    or receipt.get('transactionHash') or '').lower(),
                _log_index_key(log_index),
            )
            if key in seen_logs:
                continue
            seen_logs.add(key)
        unique_logs.append(log)
    try:
        transfers = _parse_transfer_logs(
            {**receipt, 'logs': unique_logs}, token_address, recipient_address)
    except (ValueError, TypeError, AttributeError):
        return 0
    return sum(t['amount_atomic'] for t in transfers)


def _log_index_key(value):
    """Normalize a receipt logIndex (int, decimal or 0x-hex) so the
    same index compares equal in any representation."""
    if isinstance(value, int):
        return ('n', value)
    text = str(value).strip().lower()
    try:
        return ('n', int(text, 16) if text.startswith('0x') else int(text))
    except ValueError:
        return ('s', text)


@bp.route('/api/crypto/verify-payment', methods=['POST'])
@limiter.limit("200 per minute")
def crypto_verify_payment():
    """
    Verify crypto payment on blockchain and trigger fulfillment.
    SECURITY: tx_hash must be verified on-chain before fulfillment is triggered.
    We check the Base blockchain via public RPC to confirm the tx exists,
    is confirmed, and was sent to our recipient address with sufficient value.
    """
    data = request.get_json() or {}
    payment_id = sanitize_string(data.get('payment_id', ''), max_length=64)
    tx_hash = sanitize_string(data.get('tx_hash', ''), max_length=66)
    email = data.get('email', '')
    product_name = sanitize_string(data.get('product_name', 'Crypto Purchase'), max_length=100)
    amount = data.get('amount', 0)

    if not payment_id or not tx_hash:
        return jsonify({'error': 'payment_id and tx_hash required'}), 400

    # Validate tx_hash format (0x + 64 hex chars)
    import re as _re
    if not _re.match(r'^0x[0-9a-fA-F]{64}$', tx_hash):
        logger.warning(f'[CRYPTO] Invalid tx_hash format from {request.remote_addr}')
        return jsonify({'error': 'Invalid transaction hash format'}), 400

    # Validate email
    email = validate_email(email) if email else ''

    # Validate amount
    try:
        amount = float(amount)
        if amount <= 0 or amount > 100000:
            return jsonify({'error': 'Invalid amount'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid amount'}), 400

    # BLOCKCHAIN VERIFICATION � check tx on Base via public RPC
    recipient_address = os.environ.get('BASE_PAYMENT_ADDRESS', '').lower()
    if not recipient_address:
        logger.error('[CRYPTO] BASE_PAYMENT_ADDRESS not configured')
        return jsonify({'error': 'Crypto payments not configured'}), 503

    try:
        import urllib.request as _urllib
        import json as _json

        rpc_url = os.environ.get('BASE_RPC_URL', 'https://mainnet.base.org')
        payload = _json.dumps({
            'jsonrpc': '2.0',
            'method': 'eth_getTransactionReceipt',
            'params': [tx_hash],
            'id': 1
        }).encode('utf-8')

        req = _urllib.Request(rpc_url, data=payload,
                              headers={'Content-Type': 'application/json'})
        with _urllib.urlopen(req, timeout=10) as resp:  # nosec B310 — hardcoded Alchemy RPC URL
            rpc_result = _json.loads(resp.read().decode('utf-8'))

        receipt = rpc_result.get('result')
        if not receipt:
            logger.warning(f'[CRYPTO] tx not found on chain: {tx_hash}')
            return jsonify({'error': 'Transaction not found on blockchain. It may still be pending.'}), 402

        # Must be confirmed (status 0x1)
        if receipt.get('status') != '0x1':
            logger.warning(f'[CRYPTO] tx failed on chain: {tx_hash}')
            return jsonify({'error': 'Transaction failed or was reverted on blockchain'}), 402

        # DW-308: status 0x1 alone is not settlement. The Transfer
        # event must be present in the receipt logs, paying our
        # recipient address; a bare call to the recipient (status 0x1,
        # no Transfer event) moves no tokens and must never fulfill.
        #
        # DW-308 REPAIR (Builder B): the event must also be the EXPECTED
        # payment, in full. The expectation is the amount + token that
        # /api/crypto/checkout persisted server-side for this
        # payment_id; with no checkout record (direct verify), the
        # order's declared USD amount in canonical Base USDC is the
        # expectation. The summed value of qualifying Transfer logs
        # (expected token contract only, recipient-bound, `removed`
        # logs excluded) must reach the expected amount within the
        # reference tolerance (platform_payments.AMOUNT_TOLERANCE_BPS,
        # 1.5% — the same slack verify_treasury_transfer allows). An
        # underpayment is a 402 with zero orders and zero fulfillments.
        record = _crypto_checkout_record(payment_id)
        if record is not None:
            # DW-308 REPAIR 3 (S3): a checkout settles exactly once.
            # Settlement consumes the record (settled_tx_hash, marked
            # in the settle transaction below); any later verify
            # against a consumed payment_id is refused like a replay.
            if record.get('settled_tx_hash'):
                logger.warning(f'[CRYPTO] payment_id already settled: {payment_id}')
                return jsonify({'error': 'Payment already settled for this checkout'}), 409
            expected_atomic = record['expected_atomic']
            expected_token = record['token_address']
            order_amount = record['amount_usd']
            # DW-308 REPAIR 3 (S1b): the persisted product governs —
            # the verify caller's product_name never overrides the
            # product the checkout was created for.
            if record.get('product_name'):
                product_name = record['product_name']
        else:
            from sincor2.onchain.constants import (
                USDC_DECIMALS as _USDC_DECIMALS,
                USDC_TOKEN as _USDC_TOKEN,
            )
            expected_token = _USDC_TOKEN
            expected_atomic = int(round(amount * (10 ** _USDC_DECIMALS)))
            order_amount = amount

        paid_atomic = _settled_transfer_total(
            receipt, expected_token, recipient_address)
        if paid_atomic <= 0:
            logger.warning(f'[CRYPTO] no qualifying Transfer event to {recipient_address} in tx: {tx_hash}')
            return jsonify({'error': 'Transaction does not show a payment transfer to the correct address'}), 402

        from sincor2.platform_payments import (
            AMOUNT_TOLERANCE_BPS as _AMOUNT_TOLERANCE_BPS,
        )
        min_atomic = expected_atomic * (10000 - _AMOUNT_TOLERANCE_BPS) // 10000
        if paid_atomic < min_atomic:
            logger.warning(f'[CRYPTO] underpayment for {payment_id}: paid {paid_atomic}, expected {expected_atomic} (min {min_atomic}) in tx: {tx_hash}')
            return jsonify({'error': 'Transaction amount is below the expected payment amount'}), 402

        # Check tx isn't already used (replay protection)
        db = get_db()
        existing = db.execute(
            "SELECT order_id FROM orders WHERE paypal_order_id=?", (tx_hash,)
        ).fetchone()
        if existing:
            logger.warning(f'[CRYPTO] Replay attempt for tx_hash: {tx_hash}')
            return jsonify({'error': 'Transaction already used for a previous order'}), 409

    except Exception as e:
        logger.error(f'[CRYPTO] Blockchain verification error: {e}')
        # Do NOT fulfill if verification fails
        return jsonify({'error': 'Blockchain verification failed. Please try again or contact support.'}), 503

    # Verification passed � store order, consume the checkout, fulfill.
    # DW-308 REPAIR 3 (S2): the order_id carries a random suffix — the
    # bare second-resolution timestamp collided for two settlements in
    # the same second, and because the insert's IntegrityError was
    # swallowed while fulfillment ran unconditionally, the second
    # payment fulfilled with NO order row (so its tx replayed into a
    # second fulfillment later). Now: unique ids, an insert failure is
    # a logged 5xx with ZERO fulfillments, and fulfillment fires only
    # after the order row (and the S3 consumption mark) have committed.
    order_id = (
        f"CRYPTO-ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        f"-{_unique_id_suffix()}"
    )
    product_info = PRODUCT_CATALOG.get(product_name, {'type': 'generic'})
    order_type = product_info.get('type', 'generic')

    if email:
        try:
            db.execute(
                '''INSERT INTO orders
                   (order_id, paypal_order_id, customer_email, product_name, amount,
                    currency, payment_status, delivery_status, delivery_url, order_type,
                    created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (order_id, tx_hash, email, product_name, order_amount,
                 'CRYPTO', 'completed', 'processing', f'/my-orders?email={email}', order_type,
                 datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
                 json.dumps({'tx_hash': tx_hash, 'payment_id': payment_id}))
            )
        except sqlite3.Error as e:
            db.rollback()
            logger.error(f'[CRYPTO] Order insert failed for {payment_id} ({order_id}): {e}')
            return jsonify({'error': 'Could not record this order. Please contact support.'}), 500

    # DW-308 REPAIR 3 (S3): consume the checkout record in the same
    # transaction as the order insert — mark it settled with the tx
    # that paid it, only if it is still unconsumed (the early check
    # above refused the already-consumed case; rowcount guards the
    # race). The record-less fallback path has nothing to consume.
    if record is not None:
        try:
            consumed = db.execute(
                "UPDATE crypto_checkouts "
                "SET settled_tx_hash=?, settled_at=? "
                "WHERE payment_id=? "
                "AND COALESCE(settled_tx_hash, '') = ''",
                (tx_hash, datetime.utcnow().isoformat(), payment_id),
            )
        except sqlite3.Error as e:
            db.rollback()
            logger.error(f'[CRYPTO] Could not consume checkout {payment_id}: {e}')
            return jsonify({'error': 'Could not record this order. Please contact support.'}), 500
        if consumed.rowcount != 1:
            db.rollback()
            logger.warning(f'[CRYPTO] Checkout {payment_id} was already consumed')
            return jsonify({'error': 'Payment already settled for this checkout'}), 409

    if email or record is not None:
        db.commit()

    if email:
        trigger_fulfillment(order_id, email, product_name, order_amount, order_type, product_info)

    logger.info(f'[CRYPTO] Payment verified and fulfilled: {tx_hash} ? {order_id}')
    return jsonify({
        'status': 'verified',
        'payment_id': payment_id,
        'tx_hash': tx_hash,
        'order_id': order_id,
        'network': 'Base',
        'message': 'Payment confirmed on blockchain. Fulfillment triggered.'
    }), 200


# ============================================================================
# SUBSCRIPTION CANCELLATION
# ============================================================================

@bp.route('/api/cancel-subscription', methods=['POST'])
@limiter.limit("20 per minute")
def cancel_subscription():
    """Cancel subscription — wallet/SINC by default; legacy Stripe if enabled.

    AUDIT P1 finding 3: requires authentication. The caller's JWT identity
    must own the subscription being cancelled (email match, case-insensitive);
    admins may cancel any subscription. Returns 403 on owner mismatch.
    """
    identity = _request_jwt_identity()
    if not identity:
        return jsonify({'error': 'Authentication required'}), 401
    is_admin = bool(_is_admin_identity(identity))
    identity_email = str(identity).lower()

    data = request.get_json(silent=True) or {}
    email = validate_email(data.get('email', ''))
    wallet = validate_wallet(data.get('wallet', ''))
    reason = sanitize_string(data.get('reason', 'No reason provided'), max_length=500)
    subscription_id = sanitize_string(data.get('subscription_id', ''), max_length=200)

    if not email and not wallet:
        return jsonify({'error': 'email or wallet required'}), 400

    if not is_admin:
        # Email-scoped requests must target the caller's own email.
        if email and email != identity_email:
            logger.warning(
                "[CANCEL] Forbidden: identity=%r tried to cancel email=%r from %s",
                identity, email, request.remote_addr,
            )
            return jsonify({'error': 'Forbidden'}), 403
        # Wallet-scoped requests must target a wallet whose active
        # subscription is registered to the caller's email.
        if wallet:
            try:
                from sincor2.platform_payments import list_subscriptions
                subs = list_subscriptions(wallet)
                active = [s for s in subs if s.get('status') == 'active']
                owned = [
                    s for s in active
                    if str(s.get('email') or '').lower() == identity_email
                ]
            except Exception as e:
                logger.error(f"[CANCEL] Subscription ownership lookup failed: {e}")
                return jsonify({'error': 'Could not verify subscription ownership'}), 503
            if not active:
                return jsonify({'error': 'No active subscription found for this wallet'}), 404
            if not owned:
                logger.warning(
                    "[CANCEL] Forbidden: identity=%r tried to cancel wallet=%r from %s",
                    identity, wallet, request.remote_addr,
                )
                return jsonify({'error': 'Forbidden'}), 403

    logger.info(f"[CANCEL] Request by={identity} email={email} wallet={wallet} sub={subscription_id} reason={reason}")

    if not fiat_payments_enabled() and wallet and PLATFORM_PAYMENTS_AVAILABLE:
        try:
            from sincor2.platform_payments import cancel_wallet_subscriptions
            n = cancel_wallet_subscriptions(wallet)
            return jsonify({
                'ok': True,
                'cancelled': n,
                'message': 'SINC subscription marked cancelled. No further renewals required.',
            }), 200
        except Exception as e:
            logger.error(f"[CANCEL] Wallet cancel error: {e}")

    if not fiat_payments_enabled():
        return jsonify({
            'ok': True,
            'message': 'SINC billing: simply do not renew at /buy. Email support@getsincor.com to confirm.',
        }), 200

    # Legacy Stripe cancellation
    if STRIPE_AVAILABLE and stripe_processor and stripe_processor.enabled:
        try:
            import stripe as stripe_lib
            stripe_lib.api_key = stripe_processor.api_key

            # Find the customer
            customers = stripe_lib.Customer.list(email=email, limit=1)
            if customers and customers.data:
                customer = customers.data[0]
                # Find active subscriptions
                subs = stripe_lib.Subscription.list(customer=customer.id, status='active', limit=10)
                if subs and subs.data:
                    cancelled = []
                    for sub in subs.data:
                        if not subscription_id or sub.id == subscription_id:
                            stripe_lib.Subscription.cancel(sub.id)
                            cancelled.append(sub.id)
                            logger.info(f"[CANCEL] Stripe sub cancelled: {sub.id} for {email}")

                    if cancelled:
                        # Notify customer
                        email_sender = get_email_sender()
                        if email_sender:
                            try:
                                email_sender.send_email(
                                    to=email,
                                    subject='Your SINCOR subscription has been cancelled',
                                    html_content=f'''
                                        <h2>Subscription Cancelled</h2>
                                        <p>Your SINCOR subscription has been cancelled successfully.</p>
                                        <p>You will retain access until the end of your current billing period.</p>
                                        <p>If you cancelled by mistake or have questions, reply to this email or
                                        contact <a href="mailto:support@getsincor.com">support@getsincor.com</a>.</p>
                                        <p>We'd love to know how we can improve: {reason}</p>
                                    '''
                                )
                            except Exception as mail_err:
                                logger.warning(f"[CANCEL] Could not send cancellation email: {mail_err}")

                        return jsonify({
                            'success': True,
                            'message': 'Subscription cancelled successfully. You retain access until the end of your billing period.',
                            'cancelled_subscriptions': cancelled
                        }), 200

            # No active subscription found via Stripe
            logger.info(f"[CANCEL] No active Stripe subscription found for {email}")

        except Exception as e:
            logger.error(f"[CANCEL] Stripe cancellation error: {e}")

    # Fallback: log request and notify support
    db = get_db()
    try:
        db.execute(
            '''INSERT OR IGNORE INTO orders
               (order_id, paypal_order_id, customer_email, product_name, amount,
                currency, payment_status, delivery_status, delivery_url, order_type,
                created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                f"CANCEL-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}", subscription_id or 'manual',
                email, 'CANCELLATION REQUEST', 0, 'USD', 'cancellation_requested',
                'pending', '', 'cancellation',
                datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
                json.dumps({'reason': reason, 'subscription_id': subscription_id})
            )
        )
        db.commit()
    except Exception as db_err:
        logger.warning(f"[CANCEL] Could not log cancellation to DB: {db_err}")

    # Notify support
    email_sender = get_email_sender()
    if email_sender:
        try:
            support_email = os.environ.get('SUPPORT_EMAIL', 'support@getsincor.com')
            email_sender.send_email(
                to=support_email,
                subject=f'[ACTION REQUIRED] Cancellation Request: {email}',
                html_content=f'''
                    <h2>Subscription Cancellation Request</h2>
                    <p><strong>Customer:</strong> {email}</p>
                    <p><strong>Subscription ID:</strong> {subscription_id or "Not provided"}</p>
                    <p><strong>Reason:</strong> {reason}</p>
                    <p><strong>Time:</strong> {datetime.utcnow().isoformat()} UTC</p>
                    <p>Please cancel this subscription in PayPal and confirm with the customer.</p>
                '''
            )
        except Exception as mail_err:
            logger.warning(f"[CANCEL] Could not notify support: {mail_err}")

    return jsonify({
        'success': True,
        'message': 'Cancellation request received. Our team will process it within 24 hours and confirm via email. You retain access until cancelled.',
    }), 200


# ============================================================================
# PAYPAL DISPUTE / IPN WEBHOOK
# ============================================================================

@bp.route('/api/paypal/webhook', methods=['POST'])
@limiter.limit("500 per minute")
def paypal_webhook():
    """
    PayPal IPN / Webhook handler.
    Handles: payment completed, subscription cancelled, refund, dispute opened.
    PayPal sends form-encoded IPN or JSON webhook events depending on integration type.
    Verification: IPN verification via PayPal IPN verification endpoint.
    """
    if not fiat_payments_enabled():
        return jsonify({'error': 'Legacy PayPal disabled. Use SINC/AXM at /buy.'}), 410
    content_type = request.content_type or ''

    # Determine if this is a JSON webhook (newer API) or IPN (form-encoded)
    if 'application/json' in content_type:
        event_data = request.get_json(silent=True) or {}
        event_type = event_data.get('event_type', '')
        resource = event_data.get('resource', {})
        logger.info(f"[PAYPAL-WH] JSON event received: {event_type}")

        if event_type == 'PAYMENT.SALE.COMPLETED':
            _handle_paypal_payment_completed(resource)
        elif event_type in ('BILLING.SUBSCRIPTION.CANCELLED', 'BILLING.SUBSCRIPTION.EXPIRED'):
            _handle_paypal_subscription_cancelled(resource)
        elif event_type == 'PAYMENT.SALE.REFUNDED':
            _handle_paypal_refund(resource)
        elif event_type == 'CUSTOMER.DISPUTE.CREATED':
            _handle_paypal_dispute(resource)
        elif event_type == 'CUSTOMER.DISPUTE.RESOLVED':
            logger.info(f"[PAYPAL-WH] Dispute resolved: {resource.get('dispute_id', 'unknown')}")
        else:
            logger.info(f"[PAYPAL-WH] Unhandled event type: {event_type}")

        return jsonify({'success': True}), 200

    else:
        # Legacy IPN handling (form-encoded)
        ipn_data = request.form.to_dict()
        txn_type = ipn_data.get('txn_type', '')
        payment_status = ipn_data.get('payment_status', '')
        logger.info(f"[PAYPAL-IPN] txn_type={txn_type} | payment_status={payment_status}")

        # Verify IPN with PayPal
        try:
            import urllib.request as _ur
            import urllib.parse as _up
            verify_payload = b'cmd=_notify-validate&' + _up.urlencode(ipn_data).encode('utf-8')
            paypal_sandbox = os.environ.get('PAYPAL_SANDBOX', 'false').lower() == 'true'
            paypal_env = os.environ.get('PAYPAL_ENV', 'live')
            if paypal_sandbox or paypal_env == 'sandbox':
                ipn_url = 'https://ipnpb.sandbox.paypal.com/cgi-bin/webscr'
            else:
                ipn_url = 'https://ipnpb.paypal.com/cgi-bin/webscr'

            req = _ur.Request(ipn_url, data=verify_payload,
                              headers={'Content-Type': 'application/x-www-form-urlencoded',
                                       'User-Agent': 'sincor-ipn/1.0'})
            with _ur.urlopen(req, timeout=10) as resp:  # nosec B310 — hardcoded PayPal IPN URL
                ipn_response = resp.read().decode('utf-8')

            if ipn_response.strip() != 'VERIFIED':
                logger.warning(f"[PAYPAL-IPN] Verification FAILED. Response: {ipn_response[:50]}")
                return make_response('INVALID', 200)

        except Exception as verify_err:
            logger.error(f"[PAYPAL-IPN] Could not verify IPN: {verify_err}")
            return make_response('ERROR', 200)

        # Process verified IPN
        if payment_status == 'Completed':
            _handle_paypal_ipn_payment(ipn_data)
        elif txn_type in ('subscr_cancel', 'subscr_eot', 'subscr_failed'):
            _handle_paypal_ipn_subscription_event(ipn_data)
        elif payment_status == 'Refunded':
            logger.info(f"[PAYPAL-IPN] Refund for payer {ipn_data.get('payer_email', 'unknown')}")

        return make_response('OK', 200)


def _handle_paypal_payment_completed(resource: dict):
    """Handle PayPal PAYMENT.SALE.COMPLETED webhook event."""
    sale_id = resource.get('id', '')
    amount = float(resource.get('amount', {}).get('total', 0))
    currency = resource.get('amount', {}).get('currency', 'USD')
    payer_email = resource.get('payer', {}).get('payer_info', {}).get('email', '')
    logger.info(f"[PAYPAL-WH] Payment completed: ${amount} {currency} | payer={payer_email} | id={sale_id}")

    if payer_email and amount > 0:
        order_id = f"PP-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{sale_id[:8]}"
        db = get_db()
        try:
            db.execute(
                '''INSERT OR IGNORE INTO orders
                   (order_id, paypal_order_id, customer_email, product_name, amount,
                    currency, payment_status, delivery_status, delivery_url, order_type,
                    created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (order_id, sale_id, payer_email, 'PayPal Purchase', amount, currency,
                 'completed', 'processing', f'/my-orders?email={payer_email}', 'paypal',
                 datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
                 json.dumps({'sale_id': sale_id, 'source': 'paypal_webhook'}))
            )
            db.commit()
            logger.info(f"[PAYPAL-WH] Order stored: {order_id}")
        except Exception as e:
            logger.error(f"[PAYPAL-WH] DB error: {e}")


def _handle_paypal_subscription_cancelled(resource: dict):
    """Handle PayPal subscription cancellation / expiration."""
    sub_id = resource.get('id', '')
    payer_email = resource.get('subscriber', {}).get('email_address', '')
    logger.info(f"[PAYPAL-WH] Subscription cancelled: {sub_id} | payer={payer_email}")

    db = get_db()
    try:
        db.execute(
            "UPDATE orders SET payment_status='cancelled', updated_at=? WHERE paypal_order_id=?",
            (datetime.utcnow().isoformat(), sub_id)
        )
        db.commit()
    except Exception as e:
        logger.warning(f"[PAYPAL-WH] Could not update subscription record: {e}")


def _handle_paypal_refund(resource: dict):
    """Handle PayPal refund event."""
    sale_id = resource.get('sale_id', resource.get('id', ''))
    amount = resource.get('amount', {}).get('total', 'unknown')
    logger.info(f"[PAYPAL-WH] Refund processed: sale_id={sale_id} | amount=${amount}")

    db = get_db()
    try:
        db.execute(
            "UPDATE orders SET payment_status='refunded', updated_at=? WHERE paypal_order_id=?",
            (datetime.utcnow().isoformat(), sale_id)
        )
        db.commit()
    except Exception as e:
        logger.warning(f"[PAYPAL-WH] Could not update refund record: {e}")


def _handle_paypal_dispute(resource: dict):
    """Handle PayPal CUSTOMER.DISPUTE.CREATED event."""
    dispute_id = resource.get('dispute_id', 'unknown')
    disputed_amount = resource.get('disputed_amount', {})
    reason = resource.get('reason', 'unknown')
    payer_email = ''
    for item in resource.get('disputed_transactions', []):
        buyer = item.get('buyer', {})
        payer_email = buyer.get('email', '')
        if payer_email:
            break

    logger.warning(f"[PAYPAL-DISPUTE] New dispute: {dispute_id} | reason={reason} | payer={payer_email} | amount={disputed_amount}")

    # Notify support immediately
    email_sender = get_email_sender()
    if email_sender:
        try:
            support_email = os.environ.get('SUPPORT_EMAIL', 'support@getsincor.com')
            email_sender.send_email(
                to=support_email,
                subject=f'[URGENT] PayPal Dispute Filed: {dispute_id}',
                html_content=f'''
                    <h2 style="color:red;">PayPal Dispute Filed</h2>
                    <p><strong>Dispute ID:</strong> {dispute_id}</p>
                    <p><strong>Reason:</strong> {reason}</p>
                    <p><strong>Customer:</strong> {payer_email}</p>
                    <p><strong>Amount:</strong> {disputed_amount}</p>
                    <p><strong>Time:</strong> {datetime.utcnow().isoformat()} UTC</p>
                    <p><strong>Action Required:</strong> Respond in PayPal Resolution Center within 10 days.
                    <a href="https://www.paypal.com/disputes">PayPal Resolution Center</a></p>
                '''
            )
            logger.info(f"[PAYPAL-DISPUTE] Support notified for dispute {dispute_id}")
        except Exception as mail_err:
            logger.error(f"[PAYPAL-DISPUTE] Could not notify support: {mail_err}")


def _handle_paypal_ipn_payment(ipn_data: dict):
    """Handle verified PayPal IPN payment_status=Completed."""
    txn_id = ipn_data.get('txn_id', '')
    payer_email = validate_email(ipn_data.get('payer_email', ''))
    amount = float(ipn_data.get('mc_gross', 0))
    currency = ipn_data.get('mc_currency', 'USD')

    logger.info(f"[PAYPAL-IPN] Payment completed: txn_id={txn_id} | ${amount} | {payer_email}")

    if payer_email and amount > 0:
        order_id = f"IPN-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{txn_id[:8]}"
        db = get_db()
        try:
            db.execute(
                '''INSERT OR IGNORE INTO orders
                   (order_id, paypal_order_id, customer_email, product_name, amount,
                    currency, payment_status, delivery_status, delivery_url, order_type,
                    created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (order_id, txn_id, payer_email, 'PayPal IPN Purchase', amount, currency,
                 'completed', 'processing', f'/my-orders?email={payer_email}', 'paypal',
                 datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
                 json.dumps({'txn_id': txn_id, 'source': 'paypal_ipn'}))
            )
            db.commit()
        except Exception as e:
            logger.error(f"[PAYPAL-IPN] DB error: {e}")


def _handle_paypal_ipn_subscription_event(ipn_data: dict):
    """Handle PayPal IPN subscription cancellation / EOT / failure."""
    txn_type = ipn_data.get('txn_type', '')
    subscr_id = ipn_data.get('subscr_id', '')
    payer_email = ipn_data.get('payer_email', '')
    logger.info(f"[PAYPAL-IPN] Subscription event: {txn_type} | subscr_id={subscr_id} | {payer_email}")

    db = get_db()
    try:
        db.execute(
            "UPDATE orders SET payment_status='cancelled', updated_at=? WHERE paypal_order_id=?",
            (datetime.utcnow().isoformat(), subscr_id)
        )
        db.commit()
    except Exception as e:
        logger.warning(f"[PAYPAL-IPN] Could not update subscription record: {e}")
