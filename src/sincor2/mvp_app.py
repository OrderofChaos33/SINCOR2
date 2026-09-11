"""
SINCOR2 MVP - Minimal Flask Application
Platform billing: SINC (subscriptions) + AXM (one-off intel). Legacy Stripe/PayPal gated off by default.
"""

import os
import re
import json
import time
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request

from flask import Flask, render_template, request, jsonify, g, make_response, send_file, redirect, session, url_for
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from werkzeug.middleware.proxy_fix import ProxyFix
try:
    from authlib.integrations.flask_client import OAuth
    OAUTH_AVAILABLE = True
except ImportError:
    OAUTH_AVAILABLE = False
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

from sincor2.data_paths import data_dir, migrate_legacy_orders_db
from sincor2.pdf_loader import get_pdf_generator
from sincor2.email_sender import get_email_sender

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('sincor2')


def _fiat_payments_unavailable() -> bool:
    return False


def _env_first(*keys: str, default: str = '') -> str:
    for key in keys:
        val = (os.environ.get(key) or '').strip()
        if val:
            return val
    return default


load_dotenv()

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
template_dir = os.path.join(project_root, 'templates')
static_dir = os.path.join(project_root, 'static')
app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)

if os.environ.get('RAILWAY_ENVIRONMENT') or os.environ.get('TRUST_PROXY', '').lower() in ('1', 'true', 'yes'):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.config['PREFERRED_URL_SCHEME'] = 'https'


@app.context_processor
def inject_social_links():
    from sincor2.social_links import SOCIAL_LINKS
    return {'social_links': SOCIAL_LINKS}


@app.context_processor
def inject_onchain_addresses():
    from sincor2.onchain.constants import AXIOM_TOKEN, SINC_TOKEN, TREASURY
    return {
        'sinc_token': SINC_TOKEN,
        'axiom_token': AXIOM_TOKEN,
        'treasury_address': TREASURY,
    }


@app.context_processor
def inject_auth_state():
    return {
        'is_admin': bool(session.get('is_admin')),
        'admin_username': session.get('admin_username') or '',
        'is_customer': bool(session.get('user_email')) and not session.get('is_admin'),
        'username': session.get('username') or session.get('admin_username') or '',
    }


from sincor2.runtime_secrets import resolve_flask_secret, resolve_jwt_secret

app.config['JWT_SECRET_KEY'] = resolve_jwt_secret()
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=8)
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024
app.config['SECRET_KEY'] = resolve_flask_secret()
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = bool(os.environ.get('RAILWAY_ENVIRONMENT'))
jwt = JWTManager(app)
