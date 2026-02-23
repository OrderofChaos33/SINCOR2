#!/usr/bin/env python3
"""
SINCOR2 Application Entry Point
Bulletproof startup - catches import errors and shows diagnostics.
"""

import os
import sys
import traceback

# Force stdout/stderr flush for Railway logs
os.environ['PYTHONUNBUFFERED'] = '1'

# Add src directory to Python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

print('[SINCOR2] Starting application...', flush=True)
print(f'[SINCOR2] Python: {sys.version}', flush=True)
print(f'[SINCOR2] PYTHONPATH: {os.environ.get("PYTHONPATH", "not set")}', flush=True)
print(f'[SINCOR2] PORT: {os.environ.get("PORT", "not set")}', flush=True)
print(f'[SINCOR2] Working dir: {os.getcwd()}', flush=True)
print(f'[SINCOR2] Files in /app: {os.listdir("/app") if os.path.exists("/app") else "N/A"}', flush=True)
print(f'[SINCOR2] Files in /app/src: {os.listdir("/app/src") if os.path.exists("/app/src") else "N/A"}', flush=True)
print(f'[SINCOR2] Files in /app/src/sincor2: {os.listdir("/app/src/sincor2") if os.path.exists("/app/src/sincor2") else "N/A"}', flush=True)

try:
    from sincor2.mvp_app import app
    print('[SINCOR2] === MVP app loaded successfully ===', flush=True)
except Exception as e:
    print(f'[SINCOR2] FATAL: Failed to import mvp_app: {e}', flush=True)
    print(f'[SINCOR2] Traceback:\n{traceback.format_exc()}', flush=True)
    
    # Emergency fallback app
    from flask import Flask, jsonify
    app = Flask(__name__)
    
    _error = str(e)
    _tb = traceback.format_exc()
    
    @app.route('/health')
    def health():
        return jsonify({
            'status': 'error',
            'service': 'SINCOR2 MVP (EMERGENCY)',
            'error': _error,
            'traceback': _tb
        }), 200
    
    @app.route('/')
    def home():
        return f'<h1>SINCOR2 - Emergency Mode</h1><pre>{_tb}</pre>', 500
    
    print('[SINCOR2] Emergency fallback app created', flush=True)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print(f'[SINCOR2] Starting on 0.0.0.0:{port}', flush=True)
    app.run(host='0.0.0.0', port=port)
