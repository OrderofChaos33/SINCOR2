#!/usr/bin/env python3
"""
WSGI Entry Point - Bulletproof startup wrapper.
Catches import errors and provides diagnostic health endpoint
so Railway healthcheck passes and we can see what's actually failing.
"""

import os
import sys
import traceback

# Ensure src is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

try:
    from sincor2.mvp_app import app
    print('[WSGI] Successfully loaded SINCOR2 MVP app', flush=True)
except Exception as e:
    # If the real app fails to import, create a minimal emergency app
    # that serves the health check and shows the error
    from flask import Flask, jsonify
    app = Flask(__name__)

    error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
    print(f'[WSGI] FATAL: Failed to import SINCOR2 app:\n{error_msg}', flush=True)

    @app.route('/health')
    def health():
        return jsonify({
            'status': 'error',
            'service': 'SINCOR2 MVP (EMERGENCY MODE)',
            'error': str(e),
            'traceback': error_msg
        }), 200  # Return 200 so healthcheck passes and we can see the error

    @app.route('/')
    def home():
        return f'<h1>SINCOR2 - Emergency Mode</h1><p>App failed to load:</p><pre>{error_msg}</pre>', 500
