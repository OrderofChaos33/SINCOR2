"""Public HTML pages.

Extracted from mvp_app so the gunicorn entry stays the app factory.
Helpers and Flask `app` live in sincor2.mvp_app; this module binds them
after that module has finished constructing the application object.
"""
from __future__ import annotations

from flask import Blueprint

bp = Blueprint("mvp_pages", __name__)


def _bind_mvp():
    import sincor2.mvp_app as mvp
    g = globals()
    for key, value in vars(mvp).items():
        if key.startswith("__") or key in {"bp", "_bind_mvp"}:
            continue
        g[key] = value


_bind_mvp()


@bp.route('/', methods=['GET'])
def home():
    """Home page."""
    price_ctx = {'sinc_spot_usd': None, 'sinc_spot_label': '$1.50 floor'}
    try:
        from launch_content_engine.onchain_stats import SINC_FLOOR_USD
        price_ctx['sinc_spot_usd'] = SINC_FLOOR_USD
        price_ctx['sinc_spot_label'] = f'${SINC_FLOOR_USD:.2f} floor'
    except Exception as e:
        logger.debug('[HOME] floor price unavailable: %s', e)
    return render_template('home.html', **price_ctx)


# ============================================================================
# STUB PAGES
# ============================================================================

@bp.route('/contact', methods=['GET', 'POST'])
def contact():
    """Contact / sales page."""
    if request.method == 'POST':
        data = request.form or {}
        email = (data.get('email') or '').strip().lower()
        message = (data.get('message') or '').strip()[:2000]
        name = (data.get('name') or '').strip()[:120]
        if not email or '@' not in email or not message:
            return render_template(
                'contact.html',
                error='Email and message are required.',
                support_email=os.environ.get('SUPPORT_EMAIL', 'support@getsincor.com'),
            )
        logger.info('[CONTACT] %s <%s>: %s', name or 'anon', email, message[:200])
        if email_sender:
            try:
                support = os.environ.get('SUPPORT_EMAIL', 'support@getsincor.com')
                body = f'From: {name} <{email}>\n\n{message}'
                email_sender.send_email(
                    to_email=support,
                    to_name='SINCOR Support',
                    subject=f'SINCOR contact from {email}',
                    html_content=f'<pre>{body}</pre>',
                    text_content=body,
                )
            except Exception as exc:
                logger.warning('[CONTACT] Forward failed: %s', exc)
        return render_template(
            'contact.html',
            success='Message received. We reply within one business day.',
            support_email=os.environ.get('SUPPORT_EMAIL', 'support@getsincor.com'),
        )
    return render_template(
        'contact.html',
        support_email=os.environ.get('SUPPORT_EMAIL', 'support@getsincor.com'),
    )


@bp.route('/pricing')
def pricing():
    """Pricing page."""
    return render_template('pricing.html')


@bp.route('/docs')
def docs():
    """Product documentation."""
    return render_template('docs.html')


@bp.route('/dashboard')
def dashboard():
    """Customer dashboard — shows agent activity, profile, and account status."""
    email = _session_email()

    # Load profile if email provided
    profile = {}
    order = {}
    if email:
        db = get_db()
        p = db.execute('SELECT * FROM customer_profiles WHERE email=?', (email,)).fetchone()
        if p:
            cols = [d[0] for d in db.execute('SELECT * FROM customer_profiles LIMIT 0').description]
            profile = dict(zip(cols, p))
        o = db.execute('SELECT * FROM orders WHERE customer_email=? ORDER BY created_at DESC LIMIT 1', (email,)).fetchone()
        if o:
            order = dict(o)

    # Agent activity feed — what the 6 autonomous agents have been doing
    import random
    random.seed(42)  # Consistent demo data
    tier = order.get('product_name', 'Starter')
    try:
        from sincor2.platform_payments import PLATFORM_PLANS
        agent_counts = {
            p['product_name']: p.get('agents', 10)
            for p in PLATFORM_PLANS.values()
        }
    except Exception:
        agent_counts = {'Starter': 10, 'Professional': 25, 'Enterprise': 42}
    num_agents = agent_counts.get(tier, 10)
    demo_mode = not bool(
        order.get('order_id')
        and order.get('payment_status') in ('completed', 'paid', 'verified')
    )

    use_case = profile.get('primary_use_case', 'Lead Generation & Outreach')
    company  = profile.get('company_name', 'Your Company')
    fname    = profile.get('first_name', '')
    industry = profile.get('industry', 'your industry')

    # Build agent roster with live telemetry (role, utilization %, last-active, task).
    # Sample values until per-account agent telemetry is wired to the swarm bus.
    agents = [
        {'name': 'Scout Agent',       'role': 'Discovery',   'icon': '🔍', 'status': 'active', 'util': 84, 'last': '2m ago',  'task': f'Identified 47 qualified leads in {industry} this week'},
        {'name': 'Outreach Agent',    'role': 'Negotiator',  'icon': '📧', 'status': 'active', 'util': 71, 'last': '6m ago',  'task': 'Sent 23 personalized outreach sequences today — 4 replies received'},
        {'name': 'Content Agent',     'role': 'Builder',     'icon': '✍️',  'status': 'active', 'util': 63, 'last': '18m ago', 'task': 'Published 2 SEO blog posts — targeting 3 high-volume keywords'},
        {'name': 'Social Agent',      'role': 'Negotiator',  'icon': '📱', 'status': 'idle',   'util': 22, 'last': '41m ago', 'task': 'Scheduled 14 posts across LinkedIn and Twitter for this week'},
        {'name': 'Analytics Agent',   'role': 'Auditor',     'icon': '📊', 'status': 'active', 'util': 78, 'last': '4m ago',  'task': 'Tracking 12 competitor signals — 2 pricing changes detected'},
        {'name': 'Partnership Agent', 'role': 'Director',    'icon': '🤝', 'status': 'active', 'util': 55, 'last': '27m ago', 'task': 'Identified 8 partnership opportunities — 3 outreach drafts ready'},
    ]
    if num_agents >= 24:
        agents += [
            {'name': 'Sales Agent',    'role': 'Negotiator',  'icon': '💰', 'status': 'active', 'util': 69, 'last': '9m ago',  'task': 'Followed up on 11 warm leads — 2 moved to proposal stage'},
            {'name': 'Research Agent', 'role': 'Synthesizer', 'icon': '🧠', 'status': 'active', 'util': 91, 'last': '1m ago',  'task': 'Compiled market intelligence report — 34 data sources analyzed'},
        ]
    active_agents = sum(1 for a in agents if a['status'] == 'active')
    avg_util = round(sum(a['util'] for a in agents) / len(agents)) if agents else 0

    # KPI tiles with 12-point trend sparklines (most-recent last).
    stats = [
        {'label': 'Leads Identified',  'value': '47',    'delta': '+12',  'trend': 'up',   'unit': 'this week', 'icon': '🎯', 'spark': [21,24,23,29,31,28,34,37,35,41,44,47]},
        {'label': 'Outreach Sent',     'value': '156',   'delta': '+23',  'trend': 'up',   'unit': 'today',     'icon': '📨', 'spark': [90,98,110,104,121,130,127,138,142,149,151,156]},
        {'label': 'Content Published', 'value': '8',     'delta': '+2',   'trend': 'up',   'unit': 'this week', 'icon': '✍️',  'spark': [2,2,3,3,4,4,5,6,6,7,7,8]},
        {'label': 'Agent Tasks Run',   'value': '1,247', 'delta': '+186', 'trend': 'up',   'unit': 'this week', 'icon': '⚡', 'spark': [820,870,910,955,990,1030,1075,1110,1150,1190,1220,1247]},
    ]

    # Live activity timeline (most recent first). kind → colour accent in template.
    timeline = [
        {'t': '2m ago',  'kind': 'lead',      'text': f'Scout Agent qualified 3 new leads in {industry}'},
        {'t': '14m ago', 'kind': 'outreach',  'text': 'Outreach Agent sent 8 sequences — 2 opened within the hour'},
        {'t': '38m ago', 'kind': 'content',   'text': 'Content Agent published "5 Ways to Automate Your Pipeline"'},
        {'t': '1h ago',  'kind': 'analytics', 'text': 'Analytics Agent flagged a competitor price drop of 12%'},
        {'t': '2h ago',  'kind': 'partner',   'text': 'Partnership Agent drafted 2 warm intro emails for review'},
        {'t': '4h ago',  'kind': 'system',    'text': 'Daily agent budget replenished — full capacity available'},
    ]

    # Resource usage — SINC / AXIOM daily budgets + task throughput.
    usage = {
        'sinc_used': 3420,  'sinc_cap': 5000,
        'axiom_used': 128,  'axiom_cap': 500,
        'tasks_today': 47,  'tasks_cap': 120,
    }

    member_since = order.get('created_at', '')[:10] if order.get('created_at') else ''
    try:
        base_dt = datetime.fromisoformat(order['created_at']) if order.get('created_at') else datetime.utcnow()
    except Exception:
        base_dt = datetime.utcnow()
    renewal = (base_dt + timedelta(days=30)).strftime('%b %d, %Y')

    return render_template(
        'dashboard.html',
        profile=profile, order=order, agents=agents, stats=stats,
        timeline=timeline, usage=usage,
        tier=tier, num_agents=num_agents, active_agents=active_agents, avg_util=avg_util,
        company=company, fname=fname, use_case=use_case,
        member_since=member_since, renewal=renewal,
        email=email, demo_mode=demo_mode,
        order_id=order.get('order_id', ''),
    )


@bp.route('/privacy')
def privacy():
    """Privacy policy page."""
    return render_template('privacy.html')


@bp.route('/terms')
def terms():
    """Terms of service page."""
    return render_template('terms.html')


@bp.route('/security')
def security():
    """Security information page."""
    return render_template('security.html')


@bp.route('/products/starter')
def product_starter():
    """Starter plan landing page."""
    return render_template('product_starter.html')


@bp.route('/products/professional')
def product_professional():
    """Professional plan landing page."""
    return render_template('product_professional.html')


@bp.route('/products/enterprise')
def product_enterprise():
    """Enterprise plan landing page."""
    return render_template('product_enterprise.html')


@bp.route('/media-packs')
def media_packs():
    """Media packs showcase page."""
    return render_template('media-packs.html')


@bp.route('/enterprise-dashboard')
def enterprise_dashboard():
    """Enterprise dashboard page."""
    return render_template('enterprise-dashboard.html')


@bp.route('/affiliate-program')
def affiliate_program():
    """Affiliate program page."""
    return render_template('affiliate-program.html')


@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("8 per minute", exempt_when=lambda: request.method != 'POST')
def login_page():
    """Login page — email or username. Staff accounts also send a password."""
    err_key = request.args.get('error', '')
    requested_next = request.args.get('next') or request.form.get('next') or ''
    ctx = {
        'oauth_error': OAUTH_ERROR_MESSAGES.get(err_key, ''),
        'oauth_google': _oauth_provider_ready('google'),
        'oauth_github': _oauth_provider_ready('github'),
        'error': None,
        'success': None,
        'next_url': requested_next,
        'identifier': '',
    }
    if request.method == 'POST':
        identifier = (
            request.form.get('identifier')
            or request.form.get('username')
            or request.form.get('email')
            or ''
        ).strip()
        password = request.form.get('password') or ''
        ctx['identifier'] = identifier
        if not identifier:
            ctx['error'] = 'Enter your email or username.'
            return render_template('login.html', **ctx)

        if _is_admin_identity(identifier):
            if not password:
                ctx['error'] = 'Password required for this account.'
                return render_template('login.html', **ctx)
            if _admin_credentials_match(identifier, password):
                logger.info('[AUTH] Staff login: %s', _admin_username())
                dest = _safe_next_url(requested_next, '/admin')
                return _admin_cookie_response(identifier, dest)
            logger.warning('[AUTH] Failed staff login for %s from %s', identifier, request.remote_addr)
            ctx['error'] = 'Invalid email, username, or password.'
            return render_template('login.html', **ctx)

        customer = _resolve_customer(identifier)
        if customer:
            session['username'] = customer.get('username') or ''
            dest = _safe_next_url(requested_next, '/dashboard')
            if dest in ('/admin', '/operator', '/command-center'):
                dest = '/dashboard'
            logger.info('[AUTH] Customer login: %s', customer['email'])
            return _auth_cookie_response(customer['email'], dest)
        if password:
            ctx['error'] = 'Invalid email, username, or password.'
        else:
            ctx['error'] = 'No account found. Sign up first or continue with Google/GitHub.'
        return render_template('login.html', **ctx)
    if _is_admin_session():
        return redirect(_safe_next_url(requested_next, '/admin'))
    if _session_email():
        return redirect('/dashboard')
    return render_template('login.html', **ctx)


@bp.route('/admin')
@bp.route('/operator')
def admin_console():
    """Protected operator command console."""
    if not _is_admin_session():
        return redirect('/login?next=/admin')
    username = session.get('admin_username') or _admin_username() or 'operator'
    return render_template('admin_console.html', username=username)


@bp.route('/command-center')
def command_center_page():
    """Protected swarm command center."""
    if not _is_admin_session():
        return redirect('/login?next=/command-center')
    try:
        return render_template('command_center.html')
    except Exception:
        return redirect('/admin')


@bp.route('/logout')
def logout_alias():
    return redirect('/auth/logout')


@bp.route('/forgot-password')
def forgot_password():
    """Passwordless product — direct users to email login or support."""
    return redirect('/login')


@bp.route('/business-setup')
def business_setup():
    return redirect('/signup')


@bp.route('/free-trial/<path:_slug>')
@bp.route('/free-trial')
def free_trial():
    return redirect('/signup')


@bp.route('/admin/executive')
def admin_executive():
    return redirect('/dashboard')


@bp.route('/billing')
def billing():
    """Stripe Customer Portal � lets subscribers manage their plan/billing."""
    customer_email = request.args.get('email', '')
    if not fiat_payments_enabled():
        return render_template('billing_tokens.html')
    if STRIPE_AVAILABLE and stripe_processor and stripe_processor.enabled:
        try:
            import stripe as stripe_lib
            stripe_lib.api_key = stripe_processor.api_key
            customers = stripe_lib.Customer.list(email=customer_email, limit=1)
            if customers and customers.data:
                session = stripe_lib.billing_portal.Session.create(
                    customer=customers.data[0].id,
                    return_url=os.environ.get('STRIPE_PORTAL_RETURN_URL', 'https://getsincor.com/billing'),
                )
                return redirect(session.url, code=303)
        except Exception as e:
            logger.warning(f'[BILLING] Stripe portal error: {e}')
    return render_template('error.html', code=200, title='Manage Your Subscription',
                           message='To manage your subscription, email us at support@getsincor.com '
                                   'or visit your Stripe billing portal link in your confirmation email.'), 200


@bp.route('/discovery-dashboard')
def discovery_dashboard():
    """Discovery dashboard page."""
    return render_template('discovery-dashboard.html')


@bp.route('/franchise-empire')
def franchise_empire():
    """Franchise empire page."""
    return render_template('franchise-empire.html')


@bp.route('/robots.txt')
def robots_txt():
    """robots.txt � allow crawlers, block sensitive paths."""
    content = (
        'User-agent: *\n'
        'Allow: /\n'
        'Disallow: /api/\n'
        'Disallow: /admin/\n'
        'Disallow: /files/\n'
        'Disallow: /my-orders\n'
        'Sitemap: https://getsincor.com/sitemap.xml\n'
    )
    return make_response(content, 200, {'Content-Type': 'text/plain'})


@bp.route('/sitemap.xml')
def sitemap_xml():
    """XML sitemap for SEO."""
    base = 'https://getsincor.com'
    pages = [
        ('/', '1.0', 'weekly'),
        ('/signup', '0.9', 'weekly'),
        ('/login', '0.8', 'weekly'),
        ('/buy', '0.9', 'weekly'),
        ('/sinc', '0.9', 'weekly'),
        ('/pricing', '0.9', 'weekly'),
        ('/pitch', '0.8', 'monthly'),
        ('/whitepaper', '0.7', 'monthly'),
        ('/onboarding', '0.6', 'monthly'),
        ('/verticals/webbuilder', '0.7', 'weekly'),
        ('/products/starter', '0.8', 'weekly'),
        ('/contact', '0.6', 'monthly'),
        ('/privacy', '0.4', 'monthly'),
        ('/terms', '0.4', 'monthly'),
        ('/security', '0.4', 'monthly'),
    ]
    urls = '\n'.join(
        f'  <url><loc>{base}{loc}</loc><priority>{pri}</priority><changefreq>{freq}</changefreq></url>'
        for loc, pri, freq in pages
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}\n</urlset>'
    return make_response(xml, 200, {'Content-Type': 'application/xml'})


# ============================================================================
# WHITEPAPER & DOCUMENTATION
# ============================================================================

@bp.route('/axiom')
def axiom():
    """AXIOM (AXM) token page."""
    return render_template('axiom.html')

@bp.route('/site-index')
@bp.route('/pages')
def site_index():
    """Full site index / directory of all pages."""
    return render_template('sitemap.html')

@bp.route('/go')
@bp.route('/start')
@bp.route('/sales')
def sales_landing():
    """High-conversion sales landing page."""
    return render_template('sales.html')

@bp.route('/whitepaper')
def whitepaper():
    """Render whitepaper page."""
    return render_template('whitepaper.html')


@bp.route('/pitch')
def pitch_deck():
    """Autonomous Swarm deck — 15 slides embedded from static/docs/swarm/."""
    return render_template('pitch.html')


@bp.route('/docs/whitepaper.pdf')
def whitepaper_pdf():
    """Redirect to markdown whitepaper download."""
    return redirect('/static/docs/SINCOR_whitepaper.md', code=302)


# ============================================================================
# CRYPTO PAYMENT ENDPOINTS (Ethereum/Base)
# ============================================================================
