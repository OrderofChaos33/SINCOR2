#!/usr/bin/env python3
"""
SINCOR MVP - Minimal Viable Product
Simplified version focusing on core money-making features
"""

import os
from flask import Flask, render_template, request, jsonify
from datetime import datetime

# Import core systems
from waitlist_system import waitlist_manager
from monetization_engine import MonetizationEngine
from paypal_integration import PayPalIntegration, PaymentRequest

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'sincor-mvp-secret-key-change-this')

# Initialize core engines
monetization = MonetizationEngine()
paypal = PayPalIntegration()

print("=" * 60)
print("SINCOR MVP Starting...")
print("=" * 60)
print(f"Monetization Engine: {'LOADED' if monetization else 'FAILED'}")
print(f"PayPal Integration: {'LOADED' if paypal else 'FAILED'}")
print(f"Waitlist System: {'LOADED' if waitlist_manager else 'FAILED'}")
print("=" * 60)

@app.route('/')
def index():
    """Homepage - Product showcase"""
    try:
        return render_template('index.html')
    except:
        # Fallback if template missing
        return '''
        <html>
        <head><title>SINCOR - AI Business Automation</title></head>
        <body style="font-family: Arial; max-width: 800px; margin: 50px auto; padding: 20px;">
            <h1>SINCOR - AI Business Automation Platform</h1>
            <p>42+ AI Agents | Swarm Intelligence | Enterprise Analytics</p>

            <h2>Join the Waitlist</h2>
            <form id="waitlistForm">
                <input type="email" id="email" placeholder="your@email.com" required style="padding: 10px; width: 300px;">
                <button type="submit" style="padding: 10px 20px; background: #007bff; color: white; border: none; cursor: pointer;">Join Waitlist</button>
            </form>
            <div id="message" style="margin-top: 20px; color: green;"></div>

            <h2>Services</h2>
            <ul>
                <li><strong>Instant Business Intelligence:</strong> $2,500 - $15,000</li>
                <li><strong>Predictive Analytics:</strong> $6,000 - $25,000</li>
                <li><strong>Agent Subscriptions:</strong> $500 - $5,000/month</li>
                <li><strong>Enterprise Partnerships:</strong> $50,000 - $200,000</li>
            </ul>

            <script>
                document.getElementById('waitlistForm').addEventListener('submit', async (e) => {
                    e.preventDefault();
                    const email = document.getElementById('email').value;
                    const response = await fetch('/api/waitlist/join', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({email: email})
                    });
                    const data = await response.json();
                    document.getElementById('message').textContent = data.message || 'Added to waitlist!';
                    document.getElementById('email').value = '';
                });
            </script>
        </body>
        </html>
        '''

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'version': '2.0.0-mvp',
        'services': {
            'monetization': monetization is not None,
            'paypal': paypal is not None,
            'waitlist': waitlist_manager is not None
        }
    })

@app.route('/api/waitlist/join', methods=['POST'])
def join_waitlist():
    """Join waitlist endpoint"""
    try:
        data = request.get_json()
        email = data.get('email')

        if not email:
            return jsonify({'success': False, 'message': 'Email required'}), 400

        # Add to waitlist
        result = waitlist_manager.add_to_waitlist(email)

        return jsonify({
            'success': True,
            'message': 'Successfully added to waitlist!',
            'position': result.get('position', 'N/A')
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/pricing/<service>')
def get_pricing(service):
    """Get dynamic pricing for a service"""
    try:
        pricing = monetization.calculate_price(
            service_type=service,
            client_tier='standard'
        )
        return jsonify({
            'service': service,
            'pricing': pricing
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/payment/create', methods=['POST'])
def create_payment():
    """Create PayPal payment"""
    try:
        data = request.get_json()

        payment_request = PaymentRequest(
            amount=data.get('amount', 100.0),
            currency=data.get('currency', 'USD'),
            description=data.get('description', 'SINCOR Service'),
            return_url=data.get('return_url', request.host_url + 'payment/success'),
            cancel_url=data.get('cancel_url', request.host_url + 'payment/cancel')
        )

        result = paypal.create_payment(payment_request)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/payment/execute', methods=['POST'])
def execute_payment():
    """Execute PayPal payment"""
    try:
        data = request.get_json()
        payment_id = data.get('payment_id')
        payer_id = data.get('payer_id')

        result = paypal.execute_payment(payment_id, payer_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/pricing')
def pricing_page():
    """Pricing page"""
    return '''
    <html>
    <head><title>SINCOR Pricing</title></head>
    <body style="font-family: Arial; max-width: 1000px; margin: 50px auto; padding: 20px;">
        <h1>SINCOR Pricing</h1>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-top: 30px;">
            <div style="border: 1px solid #ddd; padding: 20px; border-radius: 8px;">
                <h3>Instant BI</h3>
                <p style="font-size: 24px; font-weight: bold;">$2,500 - $15,000</p>
                <p>Strategic insights delivered in 24-48 hours</p>
            </div>
            <div style="border: 1px solid #ddd; padding: 20px; border-radius: 8px;">
                <h3>Agent Subscription</h3>
                <p style="font-size: 24px; font-weight: bold;">$500 - $5,000/mo</p>
                <p>AI agents working for your business 24/7</p>
            </div>
            <div style="border: 1px solid #ddd; padding: 20px; border-radius: 8px;">
                <h3>Predictive Analytics</h3>
                <p style="font-size: 24px; font-weight: bold;">$6,000 - $25,000</p>
                <p>Forecasting & trend analysis with AI</p>
            </div>
            <div style="border: 1px solid #ddd; padding: 20px; border-radius: 8px;">
                <h3>Enterprise</h3>
                <p style="font-size: 24px; font-weight: bold;">$50,000+</p>
                <p>Full platform integration & custom solutions</p>
            </div>
        </div>
        <div style="margin-top: 40px; text-align: center;">
            <a href="/" style="padding: 15px 30px; background: #007bff; color: white; text-decoration: none; border-radius: 5px; font-size: 18px;">Get Started</a>
        </div>
    </body>
    </html>
    '''

@app.route('/dashboard')
def dashboard():
    """Simple dashboard"""
    return '''
    <html>
    <head><title>SINCOR Dashboard</title></head>
    <body style="font-family: Arial; max-width: 1200px; margin: 50px auto; padding: 20px;">
        <h1>SINCOR Dashboard (MVP)</h1>
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-top: 30px;">
            <div style="border: 1px solid #ddd; padding: 20px; border-radius: 8px; background: #f8f9fa;">
                <h3>Waitlist</h3>
                <p style="font-size: 32px; font-weight: bold;" id="waitlistCount">Loading...</p>
            </div>
            <div style="border: 1px solid #ddd; padding: 20px; border-radius: 8px; background: #f8f9fa;">
                <h3>Revenue (MTD)</h3>
                <p style="font-size: 32px; font-weight: bold;">$0</p>
            </div>
            <div style="border: 1px solid #ddd; padding: 20px; border-radius: 8px; background: #f8f9fa;">
                <h3>Active Agents</h3>
                <p style="font-size: 32px; font-weight: bold;">42</p>
            </div>
        </div>
        <script>
            fetch('/api/waitlist/stats')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('waitlistCount').textContent = data.count || 0;
                })
                .catch(e => {
                    document.getElementById('waitlistCount').textContent = '0';
                });
        </script>
    </body>
    </html>
    '''

@app.route('/api/waitlist/stats')
def waitlist_stats():
    """Get waitlist statistics"""
    try:
        stats = waitlist_manager.get_stats()
        return jsonify(stats)
    except:
        return jsonify({'count': 0})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f"\nStarting SINCOR MVP on port {port}...")
    print(f"Visit: http://localhost:{port}")
    print(f"Health: http://localhost:{port}/health")
    print(f"Pricing: http://localhost:{port}/pricing")
    print(f"Dashboard: http://localhost:{port}/dashboard")
    print("=" * 60)

    app.run(
        host='0.0.0.0',
        port=port,
        debug=True
    )
