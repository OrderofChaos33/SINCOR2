#!/usr/bin/env python3
"""
SINCOR2 Application Entry Point
Runs the Flask web application for local development.

Serves sincor2.mvp_app — the same app object production serves via
railway_start:app (Procfile). The legacy sincor2.app module is deprecated;
do not add new routes or imports against it.
"""

import os
import sys

# Add src directory to Python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from sincor2.mvp_app import app

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    host = os.environ.get('HOST', '0.0.0.0')
    # Never run debug in production (Railway sets RAILWAY_ENVIRONMENT)
    debug = os.environ.get('FLASK_ENV') == 'development' and not os.environ.get('RAILWAY_ENVIRONMENT')

    print(f'[SINCOR2] Starting on {host}:{port} (debug={debug})')
    app.run(host=host, port=port, debug=debug)
