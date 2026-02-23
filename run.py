#!/usr/bin/env python3
"""
SINCOR2 Application Entry Point
Bulletproof startup wrapper with diagnostic fallback.
If the real app fails to import, serves a diagnostic health endpoint
so Railway healthcheck passes and we can see the actual error.
"""

import os
import sys
import traceback

# Add src directory to Python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

try:
    from sincor2.mvp_app import app
    print('[SINCOR2] MVP app loaded successfully', flush=True)
except Exception as e:
    # Emergency fallback - serve diagnostic health endpoint
    from flask import Flask, jsonify
    app = Flask(__name__)

    _error = str(e)
    _traceback = traceback.format_exc()
    print(f'[SINCOR2] FATAL import error:\n{_traceback}', flush=True)

    @app.route('/health')
    def health():
        return jsonify({
            'status': 'error',
            'service': 'SINCOR2 MVP (EMERGENCY MODE)',
            'error': _error,
            'traceback': _traceback
        }), 200

    @app.route('/')
    def home():
        return f'<h1>SINCOR2 Emergency Mode</h1><p>App failed to load:</p><pre>{_traceback}</pre>', 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    host = os.environ.get('HOST', '0.0.0.0')
    debug = os.environ.get('FLASK_ENV') == 'development'
    print(f'[SINCOR2] Starting on {host}:{port} (debug={debug})', flush=True)
    app.run(host=host, port=port, debug=debug)
