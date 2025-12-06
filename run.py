#!/usr/bin/env python3
"""
SINCOR2 Application Entry Point
Runs the Flask web application for production deployment.
"""

import os
import sys

# Add src directory to Python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from sincor2.app import app

if __name__ == '__main__':
    # Get port from environment or default to 8080 for Railway
    port = int(os.environ.get('PORT', 8080))
    host = os.environ.get('HOST', '0.0.0.0')
    
    # Run Flask app
    app.run(host=host, port=port, debug=False)
