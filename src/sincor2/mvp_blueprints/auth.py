"""Login, OAuth, onboarding, profile.

Extracted from mvp_app so the gunicorn entry stays the app factory.
Helpers and Flask `app` live in sincor2.mvp_app; this module binds them
after that module has finished constructing the application object.
"""
from __future__ import annotations

from flask import Blueprint

bp = Blueprint("mvp_auth", __name__)


def _bind_mvp():
    import sincor2.mvp_app as mvp
    g = globals()
    for key, value in vars(mvp).items():
        if key.startswith("__") or key in {"bp", "_bind_mvp"}:
            continue
        g[key] = value


_bind_mvp()


@bp.route('/api/auth/login', methods=['POST'])
@limiter.limit("5 per minute")  # Brute-force protection
def login():
    """
    Admin login endpoint. Validates credentials against ADMIN_USERNAME / ADMIN_PASSWORD
    environment variables. Accepts `username` (preferred) or `email` as the identity field.
    Returns a signed JWT on success.
    """
    if not _admin_username() or not _admin_password():
        logger.error('[AUTH] ADMIN_USERNAME or ADMIN_PASSWORD not configured')
        return jsonify({'error': 'Authentication not configured on this server'}), 503

    data = request.get_json(silent=True) or {}
    username = sanitize_string(
        str(
            data.get('username')
            or data.get('email')
            or data.get('identifier')
            or data.get('user')
            or ''
        ),
        max_length=254,
    ).strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'error': 'username and password required'}), 400

    if not _admin_credentials_match(username, password):
        logger.warning(f'[AUTH] Failed login attempt for: {username} from {request.remote_addr}')
        return jsonify({'error': 'Invalid credentials'}), 401

    identity = _admin_username()
    access_token = create_access_token(identity=identity, additional_claims={'role': 'admin'})
    logger.info(f'[AUTH] Successful login: {identity}')
    return jsonify({
        'access_token': access_token,
        'user': {
            'username': identity,
            'email': username if '@' in username else identity,
            'role': 'admin',
        },
        'expires_in': 86400
    }), 200


@bp.route('/api/protected', methods=['GET'])
@jwt_required()
def protected():
    """Protected endpoint that requires a valid JWT token."""
    user_identity = get_jwt_identity()
    return jsonify({
        'message': 'You have access to protected data',
        'user': user_identity,
        'timestamp': datetime.utcnow().isoformat()
    }), 200


# ============================================================================
# BUY / PAYMENT ENDPOINTS
# ============================================================================

# Product pricing � server-side validation of amounts
PRODUCT_PRICES = {
    'Starter': 297,
    'Professional': 997,
    'Enterprise': 2997,
}


@bp.route('/signup', methods=['GET'])
def signup_page():
    """Render signup page."""
    err_key = request.args.get('error', '')
    return render_template(
        'signup.html',
        oauth_error=OAUTH_ERROR_MESSAGES.get(err_key, ''),
        oauth_google=_oauth_provider_ready('google'),
        oauth_github=_oauth_provider_ready('github'),
    )


# ── OAuth Routes ────────────────────────────────────────────────────────────
@bp.route('/api/auth/oauth-status', methods=['GET'])
def oauth_status():
    """Report which OAuth providers are configured (no secrets)."""
    return jsonify({
        'available': OAUTH_AVAILABLE,
        'google': _oauth_provider_ready('google'),
        'github': _oauth_provider_ready('github'),
        'redirect_base': OAUTH_REDIRECT_BASE or None,
    }), 200


@bp.route('/auth/google')
def auth_google():
    """Redirect to Google OAuth."""
    if not _oauth_provider_ready('google'):
        return redirect('/signup?error=oauth_unavailable')
    redirect_uri = _oauth_redirect_uri('auth_google_callback')
    return oauth.google.authorize_redirect(redirect_uri)


@bp.route('/auth/google/callback')
def auth_google_callback():
    """Handle Google OAuth callback."""
    try:
        if not _oauth_provider_ready('google'):
            return redirect('/signup?error=oauth_unavailable')
        token = oauth.google.authorize_access_token()
        user_info = _google_userinfo(token)
        email = (user_info.get('email') or '').strip()
        name = (user_info.get('name') or email.split('@')[0]).strip()
        if not email:
            return redirect('/signup?error=no_email')
        return _oauth_finish_login(email, name, 'Google')
    except Exception as e:
        logger.error('[OAUTH] Google callback error: %s', e, exc_info=True)
        return redirect('/signup?error=oauth_failed')


@bp.route('/auth/github')
def auth_github():
    """Redirect to GitHub OAuth."""
    if not _oauth_provider_ready('github'):
        return redirect('/signup?error=oauth_unavailable')
    redirect_uri = _oauth_redirect_uri('auth_github_callback')
    return oauth.github.authorize_redirect(redirect_uri)


@bp.route('/auth/github/callback')
def auth_github_callback():
    """Handle GitHub OAuth callback."""
    try:
        if not _oauth_provider_ready('github'):
            return redirect('/signup?error=oauth_unavailable')
        oauth.github.authorize_access_token()
        resp = oauth.github.get('user')
        if not resp.ok:
            logger.error('[OAUTH] GitHub /user failed: %s', resp.status_code)
            return redirect('/signup?error=oauth_failed')
        user_info = resp.json()
        email = (user_info.get('email') or '').strip()
        if not email:
            email = _github_primary_email()
        name = (user_info.get('name') or user_info.get('login') or '').strip()
        if not email:
            return redirect('/signup?error=no_email')
        return _oauth_finish_login(email, name, 'GitHub')
    except Exception as e:
        logger.error('[OAUTH] GitHub callback error: %s', e, exc_info=True)
        return redirect('/signup?error=oauth_failed')


@bp.route('/auth/logout')
def auth_logout():
    """Clear session and JWT cookie."""
    session.clear()
    resp = make_response(redirect('/'))
    resp.delete_cookie('access_token')
    return resp


@bp.route('/onboarding', methods=['GET'])
def onboarding_page():
    """Customer intake form — shown after signup/payment."""
    import secrets
    email = request.args.get('email', '')
    order_id = request.args.get('order_id', '')
    # Simple CSRF token via session
    csrf = secrets.token_hex(32)
    from flask import session
    session['csrf_token'] = csrf
    return render_template('onboarding.html', email=email, order_id=order_id, csrf_token=csrf)


@bp.route('/api/onboarding', methods=['POST'])
@limiter.limit('10 per hour')
def submit_onboarding():
    """Securely save customer profile. GDPR/CCPA compliant."""
    import hashlib
    import secrets
    from flask import session

    data = request.get_json(silent=True) or {}

    # CSRF check
    csrf_token = data.get('csrf_token', '')
    if not csrf_token or csrf_token != session.get('csrf_token', ''):
        return jsonify({'error': 'Invalid request'}), 403

    # Input sanitization
    def clean(v, maxlen=200):
        if not isinstance(v, str):
            return ''
        return re.sub(r'[<>"\']', '', v.strip())[:maxlen]

    email = clean(data.get('email', ''), 254)
    first_name = clean(data.get('first_name', ''), 50)
    last_name = clean(data.get('last_name', ''), 50)
    company_name = clean(data.get('company_name', ''), 100)
    industry = clean(data.get('industry', ''), 100)
    team_size = clean(data.get('team_size', ''), 50)
    primary_use_case = clean(data.get('primary_use_case', ''), 100)
    growth_challenge = clean(data.get('growth_challenge', ''), 500)
    revenue_target = clean(data.get('revenue_target', ''), 50)
    consent = bool(data.get('consent_data'))

    if not all([first_name, last_name, company_name, industry, team_size, primary_use_case]):
        return jsonify({'error': 'Please fill in all required fields'}), 400

    if not consent:
        return jsonify({'error': 'You must agree to the Terms of Service'}), 400

    # Hash IP for fraud detection (never store raw IP)
    raw_ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    ip_hash = hashlib.sha256(raw_ip.encode()).hexdigest()[:16] if raw_ip else ''

    profile_id = 'prof_' + secrets.token_urlsafe(16)
    now = datetime.utcnow().isoformat()

    db = get_db()
    try:
        # Upsert by email
        existing = db.execute('SELECT profile_id FROM customer_profiles WHERE email=?', (email,)).fetchone()
        if existing:
            db.execute('''UPDATE customer_profiles SET
                first_name=?, last_name=?, company_name=?, industry=?, team_size=?,
                primary_use_case=?, growth_challenge=?, revenue_target=?,
                consent_given=?, consent_timestamp=?, ip_hash=?, updated_at=?
                WHERE email=?''',
                (first_name, last_name, company_name, industry, team_size,
                 primary_use_case, growth_challenge, revenue_target,
                 1, now, ip_hash, now, email))
            profile_id = existing[0]
        else:
            db.execute('''INSERT INTO customer_profiles
                (profile_id, email, first_name, last_name, company_name, industry,
                 team_size, primary_use_case, growth_challenge, revenue_target,
                 consent_given, consent_timestamp, ip_hash, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)''',
                (profile_id, email, first_name, last_name, company_name, industry,
                 team_size, primary_use_case, growth_challenge, revenue_target,
                 now, ip_hash, now, now))
        db.commit()
        logger.info(f'[ONBOARDING] Profile saved: {profile_id} company={company_name} use_case={primary_use_case}')
        session.pop('csrf_token', None)  # Consume token

        # Send personalized welcome email if email_sender available
        order_id_ref = clean(data.get('order_id', ''), 100)
        if email_sender and email:
            try:
                email_sender.send_welcome_email(
                    customer_email=email,
                    customer_name=f'{first_name} {last_name}'.strip(),
                    company_name=company_name,
                    use_case=primary_use_case,
                    order_id=order_id_ref
                )
                logger.info(f'[ONBOARDING] Welcome email sent to {email}')
            except Exception as e:
                logger.warning(f'[ONBOARDING] Welcome email failed: {e}')

        return jsonify({'status': 'ok', 'profile_id': profile_id,
                        'redirect': f'/thank-you/{order_id_ref}' if order_id_ref else '/dashboard'})
    except Exception as e:
        logger.error(f'[ONBOARDING] DB error: {e}')
        return jsonify({'error': 'Server error, please try again'}), 500


@bp.route('/api/profile', methods=['GET'])
@jwt_required()
def get_profile():
    """Return logged-in user profile data."""
    email = get_jwt_identity()
    db = get_db()
    row = db.execute(
        'SELECT first_name, last_name, company_name, industry, team_size, primary_use_case, created_at FROM customer_profiles WHERE email=?',
        (email,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Profile not found'}), 404
    return jsonify({
        'first_name': row[0], 'last_name': row[1], 'company_name': row[2],
        'industry': row[3], 'team_size': row[4], 'primary_use_case': row[5],
        'member_since': row[6]
    })


@bp.route('/api/profile/delete', methods=['DELETE'])
@jwt_required()
@limiter.limit('3 per day')
def delete_profile():
    """GDPR right-to-erasure: permanently delete customer profile."""
    email = get_jwt_identity()
    db = get_db()
    db.execute('DELETE FROM customer_profiles WHERE email=?', (email,))
    db.commit()
    logger.info(f'[GDPR] Profile deleted for {email}')
    return jsonify({'status': 'deleted', 'message': 'Your data has been permanently removed.'})

