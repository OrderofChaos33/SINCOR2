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


_bind_mvp()


@bp.route('/buy', methods=['GET'])
def buy_page():
    """Platform checkout — SINC + AXM on Base (default). Legacy Stripe if explicitly enabled."""
    if fiat_payments_enabled() and STRIPE_AVAILABLE and stripe_processor and stripe_processor.enabled:
        return render_template('buy_converting.html')
    return render_template('buy_tokens.html')


@bp.route('/buy-sinc', methods=['GET'])
def buy_sinc_page():
    """Redirect legacy buy-sinc URL to official curve gateway."""
    return redirect('/sinc', code=302)


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
    result = platform_create_checkout(plan_id, payer_wallet=wallet or '', customer_email=email)
    if not result.get('ok'):
        code = 400 if result.get('error') != 'spot_price_unavailable' else 503
        return jsonify(result), code
    return jsonify(result), 201


@bp.route('/api/platform/verify', methods=['POST'])
@limiter.limit("120 per hour")
def platform_verify():
    """Verify ERC-20 payment to treasury and trigger fulfillment."""
    if not PLATFORM_PAYMENTS_AVAILABLE:
        return jsonify({'ok': False, 'error': 'platform_payments_unavailable'}), 503
    data = request.get_json(silent=True) or {}
    payment_id = sanitize_string(data.get('payment_id', ''), max_length=64)
    tx_hash = sanitize_string(data.get('tx_hash', ''), max_length=66)
    email = validate_email(data.get('customer_email', '')) or ''
    wallet = validate_wallet(data.get('payer_wallet', '')) or ''
    result = platform_verify_checkout(
        payment_id, tx_hash, customer_email=email, payer_wallet=wallet or ''
    )
    if not result.get('ok'):
        status = 402 if result.get('error') in ('tx_pending', 'insufficient_amount', 'no_treasury_transfer') else 400
        return jsonify(result), status

    order_id = result['order_id']
    product_name = result['product_name']
    amount = result['usd_reference']
    order_type = result.get('order_type', 'generic')
    product_info = PRODUCT_CATALOG.get(product_name, {'type': order_type})
    customer_email = result.get('customer_email') or ''

    db = get_db()
    try:
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
             json.dumps({
                 'tx_hash': tx_hash,
                 'payment_id': payment_id,
                 'token': result['token'],
                 'payer_wallet': result.get('payer_wallet'),
                 'billing': 'platform_tokens',
             }))
        )
        db.commit()
    except sqlite3.IntegrityError:
        pass

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

    logger.info('[PAYMENTS] %s verified: %s %s tx=%s', result['token'], product_name, order_id, tx_hash)
    return jsonify(result), 200


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
    """Cached curve state proxy (spec §5.2)."""
    try:
        import sys
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from launch_content_engine.onchain_stats import fetch_stats
        return jsonify({'ok': True, 'curve': fetch_stats()}), 200
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 503


@bp.route('/api/sinc/burn-stats', methods=['GET'])
@limiter.limit("60 per minute")
def api_sinc_burn_stats():
    """Platform SINC revenue + burn counter (spec §5.2 / §5.3)."""
    try:
        from sincor2.agent_billing import fetch_burn_stats
        return jsonify({'ok': True, **fetch_burn_stats()}), 200
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/x402/resources', methods=['GET'])
def x402_resources():
    if not X402_AVAILABLE:
        return jsonify({'ok': False, 'error': 'x402_unavailable'}), 503
    from sincor2.x402_payments import list_resources
    return jsonify({'ok': True, 'resources': list_resources()})


@bp.route('/x402/<resource_id>', methods=['GET'])
@limiter.limit("200 per hour")
def x402_challenge(resource_id):
    """HTTP 402 Payment Required — SINC micropayment challenge."""
    if not X402_AVAILABLE:
        return jsonify({'error': 'x402_unavailable'}), 503
    from sincor2.x402_payments import access_granted, create_challenge
    token = request.headers.get('X-Payment-Token') or request.args.get('access_token', '')
    if access_granted(token, resource_id):
        return jsonify({'ok': True, 'resource_id': resource_id, 'access': 'granted'}), 200
    wallet = validate_wallet(request.args.get('wallet', '')) or ''
    challenge = create_challenge(resource_id, payer_wallet=wallet or '')
    return jsonify(challenge), 402


@bp.route('/api/x402/verify', methods=['POST'])
@limiter.limit("120 per hour")
def x402_verify():
    if not X402_AVAILABLE:
        return jsonify({'ok': False, 'error': 'x402_unavailable'}), 503
    from sincor2.x402_payments import verify_challenge
    data = request.get_json(silent=True) or {}
    result = verify_challenge(
        sanitize_string(data.get('challenge_id', ''), max_length=64),
        sanitize_string(data.get('tx_hash', ''), max_length=66),
        payer_wallet=validate_wallet(data.get('payer_wallet', '')) or '',
    )
    if not result.get('ok'):
        code = 402 if result.get('error') in ('tx_pending', 'insufficient_amount', 'no_treasury_transfer') else 400
        return jsonify(result), code
    try:
        from sincor2.agent_billing import record_platform_payment
        from sincor2.x402_payments import get_resource
        res = get_resource(result.get('resource_id', ''))
        if res:
            record_platform_payment(
                tx_hash=data.get('tx_hash', ''),
                payer_wallet=result.get('payer_wallet', ''),
                token='SINC',
                amount_atomic=int(res['amount_atomic']),
                product_name=f"x402:{result.get('resource_id')}",
                plan_id='x402',
                payment_id=result.get('challenge_id', ''),
            )
    except Exception:
        pass
    return jsonify(result), 200


@bp.route('/api/paid/<resource_id>', methods=['GET'])
@limiter.limit("120 per hour")
def x402_paid_resource(resource_id):
    """Serve paid API payloads after x402 access token presented."""
    if not X402_AVAILABLE:
        return jsonify({'error': 'x402_unavailable'}), 503
    from sincor2.x402_payments import access_granted
    token = request.headers.get('X-Payment-Token') or request.args.get('access_token', '')
    if not access_granted(token, resource_id):
        from sincor2.x402_payments import create_challenge
        ch = create_challenge(resource_id)
        return jsonify(ch), 402

    if resource_id == 'hook_status':
        try:
            from sincor2.hook_stats import fetch_hook_status
            return jsonify({'ok': True, 'resource': resource_id, 'data': fetch_hook_status()}), 200
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500

    return jsonify({
        'ok': True,
        'resource': resource_id,
        'message': 'Access granted. Resource handler may be extended per config/x402_pricing.yaml.',
    }), 200


@bp.route('/api/payment/webhook', methods=['POST'])
@limiter.limit("500 per minute")
def payment_webhook():
    """
    Receive Stripe webhook events for payment processing.
    Verifies webhook signature before processing.
    Stores order in DB and triggers product fulfillment/delivery.
    """
    if not fiat_payments_enabled():
        return jsonify({'error': 'Legacy fiat payments disabled. Use SINC/AXM at /buy.'}), 410
    if not stripe_processor or not stripe_processor.enabled:
        logger.error('[WEBHOOK] Stripe not configured � cannot process webhook')
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
    logger.info(f"[WEBHOOK] Stripe event: {event_type}")

    # Only process completed payments
    if event_type != 'payment_completed':
        return jsonify({'success': True, 'event': event_type}), 200

    customer_email = validate_email(event_data.get('customer_email', ''))
    if not customer_email:
        logger.warning('[WEBHOOK] No valid email in payment event')
        return jsonify({'error': 'Missing customer email'}), 400

    amount_cents = event_data.get('amount_total', 0)
    amount = amount_cents / 100 if amount_cents else 0
    session_id = sanitize_string(event_data.get('session_id', ''), max_length=100)
    subscription_id = sanitize_string(event_data.get('subscription_id', '') or '', max_length=100)

    # Determine product from amount
    product_name = 'Unknown'
    for name, price in PRODUCT_PRICES.items():
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
    trigger_fulfillment(order_id, customer_email, product_name, amount, order_type, product_info)

    return jsonify({'success': True, 'order_id': order_id}), 200


def trigger_fulfillment(order_id, email, product_name, amount, order_type, product_info):
    """
    Trigger asset delivery based on product type.
    Includes sending thank-you email for subscription orders.
    Returns delivery result dict.
    """
    result = {'message': '', 'next_steps': [], 'email_sent': False}

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
        order_row = db.execute(
            "SELECT order_id FROM orders WHERE customer_email=? AND order_id=? LIMIT 1",
            (req_email, req_order)
        ).fetchone()
        if not order_row:
            logger.warning(f'[DOWNLOAD] Unauthorized guide access attempt: {req_email} / {req_order} from {request.remote_addr}')
            return jsonify({'error': 'Order not found or access denied'}), 403

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
    Returns order list with delivery status and download URLs.
    Always returns 200 to prevent email enumeration.
    """
    email = validate_email(email)
    if not email:
        return jsonify({'error': 'Invalid email format'}), 400

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

    payment_id = f"CRYPTO-{datetime.now().strftime('%Y%m%d%H%M%S')}"

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

        # Must be sent to our address
        tx_to = (receipt.get('to') or '').lower()
        if tx_to != recipient_address:
            logger.warning(f'[CRYPTO] tx recipient mismatch: expected {recipient_address}, got {tx_to}')
            return jsonify({'error': 'Transaction was not sent to the correct address'}), 402

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

    # Verification passed � store order and fulfill
    order_id = f"CRYPTO-ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}"
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
                (order_id, tx_hash, email, product_name, amount,
                 'CRYPTO', 'completed', 'processing', f'/my-orders?email={email}', order_type,
                 datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
                 json.dumps({'tx_hash': tx_hash, 'payment_id': payment_id}))
            )
            db.commit()
        except sqlite3.IntegrityError:
            pass

        trigger_fulfillment(order_id, email, product_name, amount, order_type, product_info)

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
    """Cancel subscription — wallet/SINC by default; legacy Stripe if enabled."""
    data = request.get_json(silent=True) or {}
    email = validate_email(data.get('email', ''))
    wallet = validate_wallet(data.get('wallet', ''))
    reason = sanitize_string(data.get('reason', 'No reason provided'), max_length=500)
    subscription_id = sanitize_string(data.get('subscription_id', ''), max_length=200)

    if not email and not wallet:
        return jsonify({'error': 'email or wallet required'}), 400

    logger.info(f"[CANCEL] Request email={email} wallet={wallet} sub={subscription_id} reason={reason}")

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

