#!/usr/bin/env python3
"""
Railway deployment script - ensures SINCOR runs properly on Railway
"""

import os
import sys
from pathlib import Path

def setup_railway():
    """Set up SINCOR for Railway deployment."""
    
    print("Setting up SINCOR for Railway deployment...")
    
    # Check if we're in Railway environment
    if os.getenv('RAILWAY_ENVIRONMENT'):
        print("OK: Detected Railway environment")
        
        # Use railway-compatible app
        app_file = Path(__file__).parent / "sincor_app_railway.py"
        if app_file.exists():
            print("OK: Railway-compatible app found")
            
            # Import and run the Railway app
            sys.path.insert(0, str(Path(__file__).parent))
            from sincor_app_railway import app
            
            port = int(os.environ.get("PORT", 5001))
            host = "0.0.0.0"
            
            print(f"STARTING: SINCOR on Railway - {host}:{port}")
            app.run(host=host, port=port, debug=False)
        else:
            print("ERROR: Railway app not found")
            sys.exit(1)
    else:
        print("INFO: Not in Railway environment, using professional app")
        
        # Use professional app for local development
        from sincor_app_professional import app
        
        port = int(os.environ.get("PORT", 5001))
        host = "0.0.0.0"
        
        print(f"STARTING: SINCOR Professional - {host}:{port}")
        app.run(host=host, port=port, debug=False)

if __name__ == "__main__":
    setup_railway()