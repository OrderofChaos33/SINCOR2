"""
Railway-compatible SINCOR Monetization App
Integrates the monetization engine with Flask routes for real revenue generation
"""

import os
import asyncio
from flask import Flask, request, jsonify, render_template, redirect, url_for
from datetime import datetime
import json

# Import SINCOR monetization systems
try:
    from monetization_engine import MonetizationEngine
    from paypal_integration import SINCORPaymentProcessor, PaymentRequest
    MONETIZATION_AVAILABLE = True
except ImportError as e:
    print(f"Monetization modules not available: {e}")
    MONETIZATION_AVAILABLE = False

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'sincor-monetization-2025')

# Initialize monetization engine
if MONETIZATION_AVAILABLE:
    try:
        monetization_engine = MonetizationEngine()
        payment_processor = SINCORPaymentProcessor()
        print("✅ SINCOR Monetization Engine initialized")
    except Exception as e:
        print(f"❌ Error initializing monetization: {e}")
        monetization_engine = None
        payment_processor = None
else:
    monetization_engine = None
    payment_processor = None

@app.route('/monetization/start', methods=['POST'])
async def start_monetization():
    """Start the monetization engine"""
    if not monetization_engine:
        return jsonify({'error': 'Monetization engine not available'}), 500
    
    try:
        # Execute monetization strategy
        strategy_report = await monetization_engine.execute_monetization_strategy(
            max_concurrent_opportunities=10
        )
        
        return jsonify({
            'status': 'success',
            'message': 'Monetization engine started',
            'opportunities_executed': strategy_report['execution_summary']['opportunities_executed'],
            'total_revenue': strategy_report['execution_summary']['total_revenue'],
            'success_rate': strategy_report['execution_summary']['success_rate']
        })
    
    except Exception as e:
        return jsonify({'error': f'Failed to start monetization: {str(e)}'}), 500

@app.route('/api/create-payment', methods=['POST'])
async def create_payment():
    """Create a PayPal payment"""
    if not payment_processor:
        return jsonify({'error': 'Payment processor not available'}), 500
    
    try:
        data = request.get_json()
        amount = float(data.get('amount', 0))
        service_type = data.get('service_type', 'instant_bi')
        client_email = data.get('client_email', 'demo@client.com')
        urgency = data.get('urgency', 'standard')
        
        if amount <= 0:
            return jsonify({'error': 'Invalid amount'}), 400
        
        # Process payment based on service type
        if service_type == 'instant_bi':
            result = await payment_processor.process_instant_bi_payment(
                amount=amount,
                client_email=client_email,
                urgency_level=urgency
            )
        else:
            # General payment processing
            payment_request = PaymentRequest(
                amount=amount,
                description=f"SINCOR {service_type.replace('_', ' ').title()} Service",
                customer_email=client_email,
                order_id=f"SINCOR-{service_type.upper()}-{int(datetime.now().timestamp())}"
            )
            result = await payment_processor.paypal.create_payment(payment_request)
        
        if result.success:
            return jsonify({
                'success': True,
                'payment_id': result.payment_id,
                'approval_url': result.approval_url,
                'amount': result.amount
            })
        else:
            return jsonify({
                'success': False,
                'error': result.error_message
            }), 400
    
    except Exception as e:
        return jsonify({'error': f'Payment creation failed: {str(e)}'}), 500

@app.route('/api/revenue-opportunities', methods=['GET'])
async def get_revenue_opportunities():
    """Get current revenue opportunities"""
    if not monetization_engine:
        return jsonify({'error': 'Monetization engine not available'}), 500
    
    try:
        opportunities = await monetization_engine.identify_revenue_opportunities()
        
        # Convert to JSON-serializable format
        opportunities_data = []
        for opp in opportunities[:20]:  # Limit to top 20
            opportunities_data.append({
                'opportunity_id': opp.opportunity_id,
                'revenue_stream': opp.revenue_stream.value,
                'client_segment': opp.client_segment,
                'revenue_potential': opp.revenue_potential,
                'confidence_score': opp.confidence_score,
                'time_to_close': opp.time_to_close,
                'strategic_value': opp.strategic_value
            })
        
        return jsonify({
            'opportunities': opportunities_data,
            'total_opportunities': len(opportunities),
            'total_potential_revenue': sum(opp['revenue_potential'] for opp in opportunities_data)
        })
    
    except Exception as e:
        return jsonify({'error': f'Failed to get opportunities: {str(e)}'}), 500

@app.route('/api/revenue-metrics', methods=['GET'])
async def get_revenue_metrics():
    """Get current revenue metrics"""
    if not payment_processor:
        return jsonify({'error': 'Payment processor not available'}), 500
    
    try:
        metrics = await payment_processor.get_revenue_metrics()
        return jsonify(metrics)
    
    except Exception as e:
        return jsonify({'error': f'Failed to get metrics: {str(e)}'}), 500

@app.route('/monetization/dashboard')
def monetization_dashboard():
    """Monetization dashboard"""
    return '''<!DOCTYPE html>
<html><head><title>SINCOR Monetization Dashboard</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen">
<div class="container mx-auto px-4 py-8">
    <div class="text-center mb-8">
        <h1 class="text-4xl font-bold mb-4 text-green-400">SINCOR Monetization Dashboard</h1>
        <p class="text-xl text-gray-300">Real-time revenue generation and payment processing</p>
    </div>
    
    <div class="grid md:grid-cols-3 gap-6 mb-8">
        <div class="bg-gray-800 p-6 rounded-lg">
            <h2 class="text-xl font-bold mb-4 text-blue-400">Revenue Opportunities</h2>
            <div id="opportunities-count" class="text-3xl font-bold text-blue-400">Loading...</div>
            <p class="text-gray-400">Active opportunities</p>
        </div>
        
        <div class="bg-gray-800 p-6 rounded-lg">
            <h2 class="text-xl font-bold mb-4 text-green-400">Total Revenue</h2>
            <div id="total-revenue" class="text-3xl font-bold text-green-400">Loading...</div>
            <p class="text-gray-400">Generated revenue</p>
        </div>
        
        <div class="bg-gray-800 p-6 rounded-lg">
            <h2 class="text-xl font-bold mb-4 text-purple-400">PayPal Status</h2>
            <div id="paypal-status" class="text-3xl font-bold text-purple-400">Testing...</div>
            <p class="text-gray-400">API connection</p>
        </div>
    </div>
    
    <div class="grid md:grid-cols-2 gap-8 mb-8">
        <div class="bg-gray-800 p-6 rounded-lg">
            <h2 class="text-2xl font-bold mb-4 text-yellow-400">Quick Actions</h2>
            <div class="space-y-4">
                <button onclick="startMonetization()" class="w-full bg-green-600 hover:bg-green-700 px-4 py-2 rounded font-semibold">
                    🚀 Start Monetization Engine
                </button>
                <button onclick="createTestPayment()" class="w-full bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded font-semibold">
                    💳 Create Test Payment
                </button>
                <button onclick="loadOpportunities()" class="w-full bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded font-semibold">
                    🎯 Load Revenue Opportunities
                </button>
            </div>
        </div>
        
        <div class="bg-gray-800 p-6 rounded-lg">
            <h2 class="text-2xl font-bold mb-4 text-cyan-400">System Status</h2>
            <div class="space-y-2">
                <div class="flex justify-between">
                    <span>Monetization Engine:</span>
                    <span id="engine-status" class="text-cyan-400">Ready</span>
                </div>
                <div class="flex justify-between">
                    <span>PayPal Integration:</span>
                    <span id="paypal-integration" class="text-cyan-400">Connected</span>
                </div>
                <div class="flex justify-between">
                    <span>Revenue Streams:</span>
                    <span class="text-cyan-400">8 Active</span>
                </div>
                <div class="flex justify-between">
                    <span>Agent Scaling:</span>
                    <span class="text-cyan-400">$1 Cost Point</span>
                </div>
            </div>
        </div>
    </div>
    
    <div class="bg-gray-800 p-6 rounded-lg">
        <h2 class="text-2xl font-bold mb-4 text-red-400">Recent Activity</h2>
        <div id="recent-activity" class="space-y-2 text-gray-300">
            <p>Waiting for monetization engine startup...</p>
        </div>
    </div>
</div>

<script>
async function startMonetization() {
    try {
        document.getElementById('recent-activity').innerHTML = '<p class="text-yellow-400">Starting monetization engine...</p>';
        
        const response = await fetch('/monetization/start', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'}
        });
        
        const result = await response.json();
        
        if (result.success) {
            document.getElementById('recent-activity').innerHTML = 
                `<p class="text-green-400">✅ Monetization started: ${result.opportunities_executed} opportunities executed</p>
                 <p class="text-green-400">💰 Revenue generated: $${result.total_revenue.toFixed(2)}</p>
                 <p class="text-green-400">📊 Success rate: ${(result.success_rate * 100).toFixed(1)}%</p>`;
        } else {
            document.getElementById('recent-activity').innerHTML = `<p class="text-red-400">❌ Error: ${result.error}</p>`;
        }
    } catch (error) {
        document.getElementById('recent-activity').innerHTML = `<p class="text-red-400">❌ Network error: ${error.message}</p>`;
    }
}

async function createTestPayment() {
    try {
        const response = await fetch('/api/create-payment', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                amount: 2500.00,
                service_type: 'instant_bi',
                client_email: 'test@client.com',
                urgency: 'priority'
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            document.getElementById('recent-activity').innerHTML = 
                `<p class="text-blue-400">💳 Payment created: ${result.payment_id}</p>
                 <p class="text-blue-400">💰 Amount: $${result.amount.toFixed(2)}</p>
                 <p class="text-blue-400">🔗 <a href="${result.approval_url}" target="_blank" class="underline">PayPal Approval Link</a></p>`;
        } else {
            document.getElementById('recent-activity').innerHTML = `<p class="text-red-400">❌ Payment failed: ${result.error}</p>`;
        }
    } catch (error) {
        document.getElementById('recent-activity').innerHTML = `<p class="text-red-400">❌ Payment error: ${error.message}</p>`;
    }
}

async function loadOpportunities() {
    try {
        const response = await fetch('/api/revenue-opportunities');
        const result = await response.json();
        
        if (result.opportunities) {
            document.getElementById('opportunities-count').textContent = result.total_opportunities;
            document.getElementById('recent-activity').innerHTML = 
                `<p class="text-purple-400">🎯 Found ${result.total_opportunities} revenue opportunities</p>
                 <p class="text-purple-400">💰 Total potential: $${result.total_potential_revenue.toFixed(2)}</p>`;
        }
    } catch (error) {
        document.getElementById('recent-activity').innerHTML = `<p class="text-red-400">❌ Opportunities error: ${error.message}</p>`;
    }
}

// Load initial data
window.onload = function() {
    loadOpportunities();
};
</script>

</body></html>'''

@app.route('/payment/success')
def payment_success():
    """Payment success callback"""
    payment_id = request.args.get('paymentId')
    payer_id = request.args.get('PayerID')
    
    return f'''<!DOCTYPE html>
<html><head><title>Payment Successful</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-green-900 p-8 rounded-lg max-w-md text-center">
    <h1 class="text-3xl font-bold mb-4 text-green-400">Payment Successful! 🎉</h1>
    <div class="space-y-2 mb-6">
        <p><strong>Payment ID:</strong> {payment_id}</p>
        <p><strong>Payer ID:</strong> {payer_id}</p>
    </div>
    <p class="text-green-300 mb-6">Your SINCOR service has been activated!</p>
    <a href="/monetization/dashboard" class="inline-block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
        Return to Dashboard
    </a>
</div></body></html>'''

@app.route('/payment/cancel')
def payment_cancel():
    """Payment cancel callback"""
    return '''<!DOCTYPE html>
<html><head><title>Payment Cancelled</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-gray-800 p-8 rounded-lg max-w-md text-center">
    <h1 class="text-2xl font-bold mb-4">Payment Cancelled</h1>
    <p class="text-gray-300 mb-6">Your payment was cancelled. You can try again anytime.</p>
    <a href="/monetization/dashboard" class="inline-block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
        Return to Dashboard
    </a>
</div></body></html>'''

@app.route('/health')
def health():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'monetization_available': MONETIZATION_AVAILABLE,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/')
def home():
    """Redirect to monetization dashboard"""
    return redirect('/monetization/dashboard')

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    host = '0.0.0.0'
    print(f"🚀 Starting SINCOR Monetization App on {host}:{port}")
    print(f"💰 Monetization Engine: {'✅ Available' if MONETIZATION_AVAILABLE else '❌ Not Available'}")
    
    app.run(host=host, port=port, debug=False)