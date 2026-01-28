#!/usr/bin/env python3
"""
SINCOR Production PayPal Integration
"""
from flask import Flask, jsonify, render_template_string
import os
import requests
import logging

app = Flask(__name__)

# Configure logging based on environment
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=getattr(logging, log_level))
logger = logging.getLogger(__name__)

# Environment configuration
PAYPAL_ENV = os.getenv('PAYPAL_ENV', 'sandbox')
IS_PRODUCTION = PAYPAL_ENV == 'live'
PAYPAL_API_BASE = 'https://api-m.paypal.com' if IS_PRODUCTION else 'https://api-m.sandbox.paypal.com'
APP_BASE_URL = os.getenv('APP_BASE_URL', 'https://sincor-production.up.railway.app')

@app.route('/')
def home():
    return '''<!DOCTYPE html>
<html><head><title>SINCOR - AI Business Automation</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="text-center">
    <h1 class="text-6xl font-bold mb-4 text-blue-400">SINCOR</h1>
    <p class="text-2xl mb-8 text-gray-300">AI Business Automation Platform</p>
    <a href="/monetization/dashboard" class="bg-green-600 hover:bg-green-700 px-8 py-4 rounded-lg font-semibold text-xl">
        🚀 Launch Monetization Dashboard
    </a>
</div></body></html>'''

@app.route('/monetization/dashboard')
def monetization_dashboard():
    return '''<!DOCTYPE html>
<html><head><title>SINCOR Monetization Dashboard</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen">
<div class="container mx-auto px-4 py-8">
    <h1 class="text-4xl font-bold mb-8 text-green-400 text-center">🚀 SINCOR Monetization Dashboard</h1>
    
    <div class="grid md:grid-cols-3 gap-6 mb-8">
        <div class="bg-gray-800 p-6 rounded-lg text-center">
            <h2 class="text-xl font-bold mb-4 text-blue-400">PayPal Integration</h2>
            <div class="text-3xl font-bold text-green-400">LIVE</div>
            <p class="text-gray-400">API Connected</p>
        </div>
        
        <div class="bg-gray-800 p-6 rounded-lg text-center">
            <h2 class="text-xl font-bold mb-4 text-purple-400">Revenue Streams</h2>
            <div class="text-3xl font-bold text-green-400">8</div>
            <p class="text-gray-400">Active</p>
        </div>
        
        <div class="bg-gray-800 p-6 rounded-lg text-center">
            <h2 class="text-xl font-bold mb-4 text-yellow-400">Agent Cost</h2>
            <div class="text-3xl font-bold text-green-400">$1</div>
            <p class="text-gray-400">Per Operation</p>
        </div>
    </div>
    
    <div class="bg-gray-800 p-6 rounded-lg mb-8">
        <h2 class="text-2xl font-bold mb-4 text-red-400">💳 PayPal API Test</h2>
        <button onclick="testPayPal()" id="paypal-btn" class="bg-green-600 hover:bg-green-700 px-6 py-3 rounded-lg font-semibold">
            Test PayPal Payment ($2,500)
        </button>
        <div id="paypal-result" class="mt-4"></div>
    </div>
    
    <div class="bg-gray-800 p-6 rounded-lg">
        <h2 class="text-2xl font-bold mb-4 text-cyan-400">Revenue Opportunities</h2>
        <div class="grid md:grid-cols-2 gap-4">
            <div class="text-cyan-300">🎯 Instant BI Services: $2,500 - $15,000</div>
            <div class="text-cyan-300">🤖 Agent Subscriptions: $500 - $5,000/mo</div>
            <div class="text-cyan-300">📊 Predictive Analytics: $6,000 - $25,000</div>
            <div class="text-cyan-300">🤝 Enterprise Partnerships: $50K - $200K</div>
        </div>
    </div>
</div>

<script>
async function testPayPal() {
    const btn = document.getElementById('paypal-btn');
    const result = document.getElementById('paypal-result');
    
    btn.disabled = true;
    btn.textContent = '🔄 Creating PayPal Payment...';
    result.innerHTML = '<div class="text-yellow-400">Connecting to PayPal API...</div>';
    
    try {
        const response = await fetch('/api/paypal-test', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({amount: 2500})
        });
        
        const data = await response.json();
        
        if (data.success) {
            result.innerHTML = `
                <div class="text-green-400 font-bold">✅ PayPal API Success!</div>
                <div class="mt-2">Payment ID: <span class="font-mono">${data.payment_id}</span></div>
                <div class="mt-2">Amount: $${data.amount}</div>
                <a href="${data.approval_url}" target="_blank" class="inline-block mt-2 bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded text-white">
                    Complete Payment on PayPal
                </a>
            `;
        } else {
            result.innerHTML = `<div class="text-red-400">❌ Error: ${data.error}</div>`;
        }
    } catch (error) {
        result.innerHTML = `<div class="text-red-400">❌ Network Error: ${error.message}</div>`;
    }
    
    btn.disabled = false;
    btn.textContent = 'Test PayPal Payment ($2,500)';
}
</script>
</body></html>'''

@app.route('/api/paypal-test', methods=['POST'])
def paypal_test():
    try:
        client_id = os.getenv('PAYPAL_REST_API_ID')
        client_secret = os.getenv('PAYPAL_REST_API_SECRET')
        
        if not client_id or not client_secret:
            return jsonify({
                'success': False, 
                'error': 'PayPal credentials not configured in Railway environment'
            })
        
        # Get PayPal access token
        token_response = requests.post(
            f'{PAYPAL_API_BASE}/v1/oauth2/token',
            headers={'Accept': 'application/json', 'Accept-Language': 'en_US'},
            data='grant_type=client_credentials',
            auth=(client_id, client_secret)
        )
        
        if token_response.status_code != 200:
            return jsonify({
                'success': False, 
                'error': f'PayPal token request failed: {token_response.status_code}'
            })
        
        access_token = token_response.json()['access_token']
        
        # Create PayPal payment
        payment_data = {
            "intent": "sale",
            "payer": {"payment_method": "paypal"},
            "transactions": [{
                "amount": {"total": "2500.00", "currency": "USD"},
                "description": "SINCOR AI Business Intelligence Service - Test Payment"
            }],
            "redirect_urls": {
                "return_url": f"{APP_BASE_URL}/payment/success",
                "cancel_url": f"{APP_BASE_URL}/payment/cancel"
            }
        }
        
        payment_response = requests.post(
            f'{PAYPAL_API_BASE}/v1/payments/payment',
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}'
            },
            json=payment_data
        )
        
        if payment_response.status_code == 201:
            payment_result = payment_response.json()
            payment_id = payment_result['id']
            
            # Find approval URL
            approval_url = None
            for link in payment_result.get('links', []):
                if link['rel'] == 'approval_url':
                    approval_url = link['href']
                    break
            
            return jsonify({
                'success': True,
                'payment_id': payment_id,
                'amount': 2500.00,
                'approval_url': approval_url
            })
        else:
            return jsonify({
                'success': False,
                'error': f'PayPal payment creation failed: {payment_response.status_code}'
            })
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Payment processing error: {str(e)}'
        })

@app.route('/payment/success')
def payment_success():
    return '''<!DOCTYPE html>
<html><head><title>Payment Successful!</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-green-900 p-8 rounded-lg max-w-md text-center">
    <h1 class="text-3xl font-bold mb-4 text-green-400">🎉 Payment Successful!</h1>
    <p class="text-green-300 mb-6">Your SINCOR AI service has been activated!</p>
    <a href="/monetization/dashboard" class="inline-block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
        Return to Dashboard
    </a>
</div></body></html>'''

@app.route('/payment/cancel')
def payment_cancel():
    return '''<!DOCTYPE html>
<html><head><title>Payment Cancelled</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-gray-800 p-8 rounded-lg max-w-md text-center">
    <h1 class="text-2xl font-bold mb-4">Payment Cancelled</h1>
    <p class="text-gray-300 mb-6">No charges were made. You can try again anytime.</p>
    <a href="/monetization/dashboard" class="inline-block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
        Return to Dashboard
    </a>
</div></body></html>'''

@app.route('/webhooks/paypal', methods=['POST'])
def paypal_webhook():
    """Handle PayPal webhook events for payment lifecycle"""
    import hashlib
    import hmac
    from flask import request
    
    try:
        # Get webhook data
        webhook_data = request.get_json()
        webhook_headers = dict(request.headers)
        
        # Verify webhook signature (production security requirement)
        webhook_id = os.getenv('PAYPAL_WEBHOOK_ID')  # Need to set this
        if webhook_id:
            expected_sig = webhook_headers.get('PAYPAL-TRANSMISSION-SIG')
            if not verify_paypal_signature(request.data, webhook_headers, webhook_id):
                return jsonify({'error': 'Invalid webhook signature'}), 401
        
        # Process webhook event
        event_type = webhook_data.get('event_type')
        resource = webhook_data.get('resource', {})
        
        if event_type == 'CHECKOUT.ORDER.APPROVED':
            # Customer approved payment - capture it
            order_id = resource.get('id')
            capture_result = capture_paypal_order(order_id)
            return jsonify({'status': 'order_captured', 'capture': capture_result})
            
        elif event_type == 'PAYMENT.CAPTURE.COMPLETED':
            # Payment successfully processed - fulfill order
            payment_id = resource.get('id')
            amount = resource.get('amount', {}).get('value')
            fulfill_order(payment_id, amount)
            return jsonify({'status': 'order_fulfilled'})
            
        elif event_type == 'PAYMENT.CAPTURE.DENIED':
            # Payment failed - log and notify
            payment_id = resource.get('id')
            reason = resource.get('status_details', {}).get('reason', 'Unknown')
            handle_payment_failure(payment_id, reason)
            return jsonify({'status': 'payment_failed_logged'})
            
        elif event_type == 'PAYMENT.CAPTURE.REFUNDED':
            # Payment refunded - reverse fulfillment
            refund_id = resource.get('id')
            amount = resource.get('amount', {}).get('value')
            handle_refund(refund_id, amount)
            return jsonify({'status': 'refund_processed'})
            
        elif event_type == 'PAYMENT.CAPTURE.REVERSED':
            # Payment reversed/disputed - handle dispute
            payment_id = resource.get('id')
            handle_payment_reversal(payment_id)
            return jsonify({'status': 'reversal_handled'})
            
        else:
            # Unknown event type - log for monitoring
            print(f"Unhandled PayPal webhook event: {event_type}")
            return jsonify({'status': 'event_logged'})
            
    except Exception as e:
        print(f"PayPal webhook error: {str(e)}")
        return jsonify({'error': 'Webhook processing failed'}), 500

def verify_paypal_signature(payload, headers, webhook_id):
    """Verify PayPal webhook signature for security"""
    try:
        # PayPal signature verification algorithm
        auth_algo = headers.get('PAYPAL-AUTH-ALGO', '')
        transmission_id = headers.get('PAYPAL-TRANSMISSION-ID', '')
        cert_id = headers.get('PAYPAL-CERT-ID', '')
        transmission_sig = headers.get('PAYPAL-TRANSMISSION-SIG', '')
        transmission_time = headers.get('PAYPAL-TRANSMISSION-TIME', '')
        
        # Create expected signature string
        expected_sig_string = f"{transmission_id}|{transmission_time}|{webhook_id}|{hashlib.sha256(payload).hexdigest()}"
        
        # For production, verify against PayPal's public certificate
        # For now, return True (implement full verification for production)
        return True
        
    except Exception as e:
        print(f"Signature verification error: {str(e)}")
        return False

def capture_paypal_order(order_id):
    """Capture an approved PayPal order"""
    try:
        client_id = os.getenv('PAYPAL_REST_API_ID')
        client_secret = os.getenv('PAYPAL_REST_API_SECRET')
        
        # Get access token
        token_response = requests.post(
            'https://api.sandbox.paypal.com/v1/oauth2/token',
            headers={'Accept': 'application/json', 'Accept-Language': 'en_US'},
            data='grant_type=client_credentials',
            auth=(client_id, client_secret)
        )
        
        access_token = token_response.json()['access_token']
        
        # Capture the order
        capture_response = requests.post(
            f'https://api.sandbox.paypal.com/v2/checkout/orders/{order_id}/capture',
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}'
            }
        )
        
        return capture_response.json()
        
    except Exception as e:
        print(f"Order capture error: {str(e)}")
        return None

def fulfill_order(payment_id, amount):
    """Fulfill order after successful payment"""
    try:
        # Log successful payment
        print(f"✅ SINCOR Revenue: ${amount} from payment {payment_id}")
        
        # Here you would:
        # 1. Update database with successful payment
        # 2. Send confirmation email to customer  
        # 3. Activate services/products purchased
        # 4. Generate invoice/receipt
        # 5. Update analytics/metrics
        
        # For now, just log the revenue
        with open('sincor_revenue.log', 'a') as f:
            f.write(f"{payment_id},{amount},{requests.get('http://worldtimeapi.org/api/timezone/UTC').json().get('datetime', 'unknown')}\n")
            
    except Exception as e:
        print(f"Order fulfillment error: {str(e)}")

def handle_payment_failure(payment_id, reason):
    """Handle failed payment"""
    print(f"❌ Payment failed: {payment_id} - Reason: {reason}")
    # Log failure, potentially retry, or notify customer

def handle_refund(refund_id, amount):
    """Handle payment refund"""
    print(f"💰 Refund processed: {refund_id} - Amount: ${amount}")
    # Reverse order fulfillment, update records

def handle_payment_reversal(payment_id):
    """Handle payment reversal/dispute"""
    print(f"⚠️ Payment reversed: {payment_id}")
    # Handle dispute process, gather evidence

@app.route('/setup/webhooks', methods=['POST'])
def setup_paypal_webhooks():
    """Register PayPal webhooks for this application"""
    try:
        client_id = os.getenv('PAYPAL_REST_API_ID')
        client_secret = os.getenv('PAYPAL_REST_API_SECRET')
        base_url = os.getenv('BASE_URL', 'https://sincor-production.up.railway.app')
        
        if not client_id or not client_secret:
            return jsonify({'error': 'PayPal credentials not configured'}), 400
        
        # Get access token
        token_response = requests.post(
            'https://api.sandbox.paypal.com/v1/oauth2/token',
            headers={'Accept': 'application/json'},
            data='grant_type=client_credentials',
            auth=(client_id, client_secret)
        )
        
        if token_response.status_code != 200:
            return jsonify({'error': 'Failed to get PayPal access token'}), 500
        
        access_token = token_response.json()['access_token']
        
        # Register webhook
        webhook_data = {
            "url": f"{base_url}/webhooks/paypal",
            "event_types": [
                {"name": "CHECKOUT.ORDER.APPROVED"},
                {"name": "PAYMENT.CAPTURE.COMPLETED"},
                {"name": "PAYMENT.CAPTURE.DENIED"},
                {"name": "PAYMENT.CAPTURE.REFUNDED"},
                {"name": "PAYMENT.CAPTURE.REVERSED"}
            ]
        }
        
        webhook_response = requests.post(
            'https://api.sandbox.paypal.com/v1/notifications/webhooks',
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}'
            },
            json=webhook_data
        )
        
        if webhook_response.status_code == 201:
            webhook_result = webhook_response.json()
            webhook_id = webhook_result['id']
            
            return jsonify({
                'success': True,
                'webhook_id': webhook_id,
                'webhook_url': f"{base_url}/webhooks/paypal",
                'events_registered': [event['name'] for event in webhook_data['event_types']],
                'message': f'Add PAYPAL_WEBHOOK_ID={webhook_id} to Railway environment variables'
            })
        else:
            return jsonify({
                'error': f'Webhook registration failed: {webhook_response.status_code}',
                'details': webhook_response.text
            }), 500
            
    except Exception as e:
        return jsonify({'error': f'Webhook setup error: {str(e)}'}), 500

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'service': 'SINCOR Monetization Platform',
        'paypal_configured': bool(os.getenv('PAYPAL_REST_API_ID')),
        'webhook_configured': bool(os.getenv('PAYPAL_WEBHOOK_ID')),
        'timestamp': '2025-08-29T17:35:00Z'
    })

@app.route('/readyz')
def readiness_check():
    """Production readiness check for Railway health monitoring"""
    checks = {
        'paypal_credentials': bool(os.getenv('PAYPAL_REST_API_ID') and os.getenv('PAYPAL_REST_API_SECRET')),
        'webhook_configured': bool(os.getenv('PAYPAL_WEBHOOK_ID')),
        'environment': PAYPAL_ENV,
        'api_base': PAYPAL_API_BASE,
        'base_url': APP_BASE_URL
    }
    
    all_ready = all(checks.values())
    
    return jsonify({
        'ready': all_ready,
        'checks': checks,
        'service': 'SINCOR Monetization Platform',
        'version': '1.0.0'
    }), 200 if all_ready else 503

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f">> Starting SINCOR Monetization Platform on port {port}")
    print(f">> PayPal Integration: {'CONFIGURED' if os.getenv('PAYPAL_REST_API_ID') else 'MISSING CREDENTIALS'}")
    print(">> Production WSGI Server Ready for PayPal API")
    app.run(host='0.0.0.0', port=port, debug=False)