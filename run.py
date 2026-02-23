#!/usr/bin/env python3
"""SINCOR2 Application Entry Point - Build v6
Bulletproof startup with diagnostics and graceful fallback.
"""

import os
import sys
import traceback

os.environ['PYTHONUNBUFFERED'] = '1'

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

print('=' * 60, flush=True)
print('[SINCOR2] Starting application - build v6', flush=True)
print(f'[SINCOR2] Python: {sys.version}', flush=True)
print(f'[SINCOR2] PORT: {os.environ.get("PORT", "not set")}', flush=True)
print(f'[SINCOR2] PYTHONPATH: {os.environ.get("PYTHONPATH", "not set")}', flush=True)
print(f'[SINCOR2] Working dir: {os.getcwd()}', flush=True)

# List key directories for debugging
for d in ['/app', '/app/src', '/app/src/sincor2', '/app/templates']:
    if os.path.exists(d):
        items = os.listdir(d)
        print(f'[SINCOR2] {d}: {len(items)} items', flush=True)
    else:
        print(f'[SINCOR2] {d}: NOT FOUND', flush=True)

try:
    from sincor2.mvp_app import app
    print('[SINCOR2] MVP app loaded successfully', flush=True)
except Exception as e:
    print(f'[SINCOR2] FATAL: Failed to import mvp_app: {e}', flush=True)
    print(f'[SINCOR2] Traceback:\n{traceback.format_exc()}', flush=True)

    # Fallback: serve a minimal app so healthcheck passes and we can read the error
    from flask import Flask, jsonify
    app = Flask(__name__)

    _error = str(e)
    _tb = traceback.format_exc()

    @app.route('/health')
    def health():
        return jsonify({
            'status': 'error',
            'service': 'SINCOR2 (EMERGENCY)',
            'error': _error,
            'traceback': _tb,
            'build': 'v6'
        }), 200

    @app.route('/')
    def home():
        return (f'<html><body style="background:#111;color:#fff;font-family:monospace;padding:2em">'
                f'<h1>SINCOR2 - Emergency Mode</h1>'
                f'<p style="color:#f88">Error: {_error}</p>'
                f'<pre style="background:#222;padding:1em;border-radius:8px;overflow:auto">{_tb}</pre>'
                f'</body></html>'), 500

    print('[SINCOR2] Emergency fallback app created', flush=True)

print('=' * 60, flush=True)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print(f'[SINCOR2] Starting Flask dev server on 0.0.0.0:{port}', flush=True)
    app.run(host='0.0.0.0', port=port)
