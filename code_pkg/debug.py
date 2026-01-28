#!/usr/bin/env python3
"""
Debug version to test Railway deployment
"""
from flask import Flask, jsonify
from datetime import datetime
import os

app = Flask(__name__)

@app.route('/')
def home():
    return f'<h1>SINCOR DEBUG VERSION</h1><p>Time: {datetime.now()}</p><p>Environment: RAILWAY</p><p>PayPal ID: {bool(os.getenv("PAYPAL_REST_API_ID"))}</p><a href="/test">Test Route</a>'

@app.route('/test')
def test():
    return jsonify({
        'message': 'DEBUG ROUTE WORKS',
        'timestamp': datetime.now().isoformat(),
        'paypal_configured': bool(os.getenv('PAYPAL_REST_API_ID')),
        'environment': os.getenv('PAYPAL_ENV', 'not-set')
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'version': 'debug'})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f">> Starting DEBUG version on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)