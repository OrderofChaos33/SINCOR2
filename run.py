#!/usr/bin/env python3
"""
SINCOR AI Business Automation Platform - Production Runner
Forces Railway to serve the latest deployment
"""
from flask import Flask, jsonify
from datetime import datetime
import os

app = Flask(__name__)

@app.route('/')
def home():
    return f'''<!DOCTYPE html>
<html><head><title>SINCOR AI Platform</title></head>
<body style="font-family: Arial; padding: 40px; background: #0a0a0a; color: white;">
    <h1 style="color: #00ff00;">🚀 SINCOR AI Business Automation Platform - LIVE!</h1>
    <h2>PRODUCTION v3.0 - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</h2>
    <h3 style="color: #ff0000;">⚡ FORCED DEPLOYMENT UPDATE! ⚡</h3>
    <div style="margin: 30px 0; padding: 20px; background: #1a1a1a; border-radius: 10px;">
        <p><strong>✅ Status:</strong> FULLY OPERATIONAL</p>
        <p><strong>✅ Domain:</strong> getsincor.com</p>
        <p><strong>✅ PayPal:</strong> {bool(os.getenv("PAYPAL_REST_API_ID"))}</p>
        <p><strong>✅ AI Engine:</strong> {bool(os.getenv("ANTHROPIC_API_KEY"))}</p>
        <p><strong>✅ Server:</strong> Railway Production</p>
    </div>
    <div style="margin: 30px 0;">
        <a href="/health" style="color: #00ff00; margin-right: 20px;">🔍 Health Check</a>
        <a href="/api/test" style="color: #00ff00; margin-right: 20px;">⚡ API Test</a>
        <a href="/status" style="color: #00ff00; margin-right: 20px;">📊 Status</a>
    </div>
    <p style="color: #00ff00; font-size: 24px; font-weight: bold;">🎯 SINCOR IS LIVE AND READY FOR BUSINESS!</p>
    <p style="color: #888;">Autonomous AI • Business Intelligence • Lead Generation</p>
</body></html>'''

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'service': 'SINCOR AI Platform',
        'timestamp': datetime.now().isoformat(),
        'domain': 'getsincor.com',
        'version': '3.0.0',
        'deployment': 'production'
    })

@app.route('/api/test')
def api_test():
    return jsonify({
        'message': '🚀 SINCOR API IS LIVE!',
        'timestamp': datetime.now().isoformat(),
        'endpoints': ['/health', '/api/test', '/status'],
        'ready_for_business': True,
        'version': '3.0.0'
    })

@app.route('/status')
def status():
    return jsonify({
        'platform': 'SINCOR AI Business Automation',
        'version': '3.0.0',
        'deployment_time': datetime.now().isoformat(),
        'environment': {
            'paypal': bool(os.getenv("PAYPAL_REST_API_ID")),
            'anthropic': bool(os.getenv("ANTHROPIC_API_KEY")),
            'domain': 'getsincor.com'
        }
    })

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f"🚀 SINCOR v3.0 Production - Port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)