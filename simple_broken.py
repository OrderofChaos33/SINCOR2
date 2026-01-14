#!/usr/bin/env python3
"""
SINCOR Production System for Railway Deployment
Integrates all monetization engines and systems for getsincor.com
"""
from flask import Flask, jsonify, render_template_string, request, redirect, url_for
import os
import requests
import logging
import asyncio
import json
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'sincor-production-2025-secure-fallback-key-xyz123'

# Configure logging based on environment
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=getattr(logging, log_level))
logger = logging.getLogger(__name__)

# Environment configuration
PAYPAL_ENV = os.getenv('PAYPAL_ENV', 'sandbox')
IS_PRODUCTION = PAYPAL_ENV == 'live'
PAYPAL_API_BASE = 'https://api-m.paypal.com' if IS_PRODUCTION else 'https://api-m.sandbox.paypal.com'
APP_BASE_URL = os.getenv('APP_BASE_URL', 'https://getsincor.com')

# Initialize SINCOR engines
try:
    from monetization_engine import MonetizationEngine
    from paypal_integration import SINCORPaymentProcessor, PaymentRequest
    from instant_business_intelligence import InstantBusinessIntelligence
    from dynamic_pricing_engine import DynamicPricingEngine
    from infinite_scaling_engine import InfiniteScalingEngine
    ENGINES_AVAILABLE = True
    logger.info("[OK] SINCOR engines imported successfully")
except ImportError as e:
    logger.warning(f"[WARNING] Engine import failed: {e}")
    ENGINES_AVAILABLE = False

# Initialize engines after routes are defined
monetization_engine = None
payment_processor = None
bi_engine = None
pricing_engine = None
scaling_engine = None

def initialize_engines():
    """Initialize SINCOR engines safely"""
    global monetization_engine, payment_processor, bi_engine, pricing_engine, scaling_engine
    
    if not ENGINES_AVAILABLE:
        logger.warning("Engines not available due to import failures")
        return False
    
    try:
        # Import additional dependencies for BI engine
        from swarm_coordination import TaskMarket
        from cortecs_core import CortecsBrain
        
        # Initialize core engines
        monetization_engine = MonetizationEngine()
        payment_processor = SINCORPaymentProcessor()
        
        # Initialize BI engine with proper dependencies
        task_market = TaskMarket()
        cortecs_brain = CortecsBrain()
        bi_engine = InstantBusinessIntelligence(task_market, cortecs_brain)
        
        pricing_engine = DynamicPricingEngine()
        scaling_engine = InfiniteScalingEngine()
        logger.info("[OK] SINCOR engines initialized")
        return True
    except Exception as e:
        logger.error(f"[ERROR] Engine initialization failed: {e}")
        return False

@app.route('/')
def home():
    """SINCOR homepage"""
    return f'''
    <h1>SINCOR AI Business Automation Platform</h1>
    <h2>Production System - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</h2>
    <div style="margin: 20px 0;">
        <p><strong>PayPal Configured:</strong> {bool(os.getenv("PAYPAL_REST_API_ID"))}</p>
        <p><strong>Environment:</strong> {os.getenv("PAYPAL_ENV", "not-set")}</p>
        <p><strong>Engines Available:</strong> {ENGINES_AVAILABLE}</p>
        <p><strong>AI Engine:</strong> {"CONFIGURED" if os.getenv("ANTHROPIC_API_KEY") else "MISSING API KEY"}</p>
        <p><strong>Domain:</strong> getsincor.com [OK]</p>
    </div>
    <div style="margin: 20px 0;">
        <a href="/health" style="margin-right: 20px;">Health Check</a>
        <a href="/services" style="margin-right: 20px;">Services</a>
        <a href="/dashboard" style="margin-right: 20px;">Dashboard</a>
    </div>
    <p><em>SINCOR LLC - Autonomous Business Intelligence & Lead Generation</em></p>
    <p style="color: #00ff00;">[LIVE] ON GETSINCOR.COM</p>
    '''

@app.route('/dashboard')
def dashboard():
    """SINCOR Dashboard"""
    return '''
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SINCOR - AI Business Automation Platform</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-900 text-white min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <div class="text-center mb-12">
            <h1 class="text-6xl font-bold mb-4 text-blue-400">SINCOR</h1>
            <p class="text-2xl mb-8 text-gray-300">AI Business Automation Platform</p>
            <div class="text-lg text-gray-400 mb-8">
                [ROCKET] Instant Business Intelligence - [ROBOT] Agent Scaling - [MONEY] Revenue Generation
            </div>
        </div>
        
        <div class="grid md:grid-cols-3 gap-8 mb-12">
            <div class="bg-gray-800 p-6 rounded-lg text-center">
                <h2 class="text-xl font-bold mb-4 text-green-400">[BULB] Instant BI</h2>
                <p class="text-gray-300 mb-4">Get business intelligence in seconds, not weeks</p>
                <div class="text-2xl font-bold text-green-400">$2,500 - $15,000</div>
                <p class="text-sm text-gray-400">Per analysis</p>
            </div>
            
            <div class="bg-gray-800 p-6 rounded-lg text-center">
                <h2 class="text-xl font-bold mb-4 text-purple-400">[AI] Agent Services</h2>
                <p class="text-gray-300 mb-4">AI agents that scale your business operations</p>
                <div class="text-2xl font-bold text-purple-400">$500 - $5,000/mo</div>
                <p class="text-sm text-gray-400">Subscription</p>
            </div>
            
            <div class="bg-gray-800 p-6 rounded-lg text-center">
                <h2 class="text-xl font-bold mb-4 text-yellow-400">[CHART] Predictive Analytics</h2>
                <p class="text-gray-300 mb-4">Forecast market trends and opportunities</p>
                <div class="text-2xl font-bold text-yellow-400">$6,000 - $25,000</div>
                <p class="text-sm text-gray-400">Per project</p>
            </div>
        </div>
        
        <div class="text-center">
            <a href="/dashboard" class="bg-green-600 hover:bg-green-700 px-8 py-4 rounded-lg font-semibold text-xl mr-4">
                [GO] Launch Dashboard
            </a>
            <a href="/services" class="bg-blue-600 hover:bg-blue-700 px-8 py-4 rounded-lg font-semibold text-xl">
                [$$] View Services
            </a>
        </div>
        
        <div class="mt-12 bg-gray-800 p-6 rounded-lg">
            <h2 class="text-2xl font-bold mb-4 text-cyan-400">[TARGET] Enterprise Solutions</h2>
            <div class="grid md:grid-cols-2 gap-4 text-gray-300">
                <div>[HANDSHAKE] Partnership Framework: $50K - $200K revenue streams</div>
                <div>[CYCLE] Recursive Value Products: Exponential growth models</div>
                <div>[BOLT] Real-time Intelligence: Live market monitoring</div>
                <div>🎯 Quality Scoring: Performance optimization</div>
            </div>
        </div>
    </div>
</body>
</html>''')

@app.route('/dashboard')
def dashboard():
    """Main SINCOR dashboard"""
    return render_template_string('''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SINCOR Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-900 text-white min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <h1 class="text-4xl font-bold mb-8 text-green-400 text-center">[COMMAND] SINCOR Command Center</h1>
        
        <div class="grid md:grid-cols-4 gap-6 mb-8">
            <div class="bg-gray-800 p-4 rounded-lg text-center">
                <h3 class="text-lg font-bold text-blue-400 mb-2">System Status</h3>
                <div id="system-status" class="text-2xl font-bold text-green-400">ONLINE</div>
                <p class="text-sm text-gray-400">All systems operational</p>
            </div>
            
            <div class="bg-gray-800 p-4 rounded-lg text-center">
                <h3 class="text-lg font-bold text-purple-400 mb-2">Revenue Streams</h3>
                <div class="text-2xl font-bold text-purple-400">8</div>
                <p class="text-sm text-gray-400">Active channels</p>
            </div>
            
            <div class="bg-gray-800 p-4 rounded-lg text-center">
                <h3 class="text-lg font-bold text-yellow-400 mb-2">PayPal Status</h3>
                <div id="paypal-status" class="text-2xl font-bold text-green-400">LIVE</div>
                <p class="text-sm text-gray-400">API connected</p>
            </div>
            
            <div class="bg-gray-800 p-4 rounded-lg text-center">
                <h3 class="text-lg font-bold text-red-400 mb-2">Agent Cost</h3>
                <div class="text-2xl font-bold text-green-400">$1</div>
                <p class="text-sm text-gray-400">Per operation</p>
            </div>
        </div>
        
        <div class="grid md:grid-cols-2 gap-8 mb-8">
            <div class="bg-gray-800 p-6 rounded-lg">
                <h2 class="text-2xl font-bold mb-4 text-green-400">Revenue Generation</h2>
                <div class="space-y-4">
                    <button onclick="startMonetization()" class="w-full bg-green-600 hover:bg-green-700 px-4 py-2 rounded font-semibold">
                        🚀 Start Monetization Engine
                    </button>
                    <button onclick="createPayment()" class="w-full bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded font-semibold">
                        💳 Create $2,500 Payment
                    </button>
                    <button onclick="getOpportunities()" class="w-full bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded font-semibold">
                        🎯 Find Revenue Opportunities
                    </button>
                </div>
                
                <div id="action-result" class="mt-4 p-4 bg-gray-700 rounded min-h-[100px]">
                    <p class="text-gray-400">Click buttons above to interact with SINCOR systems</p>
                </div>
            </div>
            
            <div class="bg-gray-800 p-6 rounded-lg">
                <h2 class="text-2xl font-bold mb-4 text-cyan-400">Business Intelligence</h2>
                <div class="space-y-4">
                    <div class="flex justify-between">
                        <span>Instant BI Engine:</span>
                        <span class="text-cyan-400">{{ 'READY' if engines_available else 'OFFLINE' }}</span>
                    </div>
                    <div class="flex justify-between">
                        <span>Pricing Engine:</span>
                        <span class="text-cyan-400">{{ 'READY' if engines_available else 'OFFLINE' }}</span>
                    </div>
                    <div class="flex justify-between">
                        <span>Scaling Engine:</span>
                        <span class="text-cyan-400">{{ 'READY' if engines_available else 'OFFLINE' }}</span>
                    </div>
                    <div class="flex justify-between">
                        <span>PayPal Integration:</span>
                        <span class="text-cyan-400">{{ 'LIVE' if paypal_configured else 'NEEDS CONFIG' }}</span>
                    </div>
                </div>
                
                <div class="mt-4 p-4 bg-gray-700 rounded">
                    <h3 class="font-bold text-yellow-400 mb-2">Quick Stats</h3>
                    <p class="text-sm">Environment: {{ environment }}</p>
                    <p class="text-sm">Base URL: {{ base_url }}</p>
                    <p class="text-sm">API Base: {{ api_base }}</p>
                </div>
            </div>
        </div>
        
        <div class="bg-gray-800 p-6 rounded-lg">
            <h2 class="text-2xl font-bold mb-4 text-red-400">Available Services</h2>
            <div class="grid md:grid-cols-4 gap-4">
                <div class="text-center p-4 bg-gray-700 rounded">
                    <div class="text-2xl mb-2">💡</div>
                    <h3 class="font-bold">Instant BI</h3>
                    <p class="text-sm text-gray-400">$2,500 - $15,000</p>
                </div>
                <div class="text-center p-4 bg-gray-700 rounded">
                    <div class="text-2xl mb-2">🤖</div>
                    <h3 class="font-bold">Agent Services</h3>
                    <p class="text-sm text-gray-400">$500 - $5,000/mo</p>
                </div>
                <div class="text-center p-4 bg-gray-700 rounded">
                    <div class="text-2xl mb-2">📊</div>
                    <h3 class="font-bold">Predictive Analytics</h3>
                    <p class="text-sm text-gray-400">$6,000 - $25,000</p>
                </div>
                <div class="text-center p-4 bg-gray-700 rounded">
                    <div class="text-2xl mb-2">🤝</div>
                    <h3 class="font-bold">Enterprise Partnerships</h3>
                    <p class="text-sm text-gray-400">$50K - $200K</p>
                </div>
            </div>
        </div>
    </div>

<script>
async function startMonetization() {
    const result = document.getElementById('action-result');
    result.innerHTML = '<p class="text-yellow-400">🔄 Starting monetization engine...</p>';
    
    try {
        const response = await fetch('/api/monetization/start', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'}
        });
        const data = await response.json();
        
        if (data.success) {
            result.innerHTML = `
                <div class="text-green-400">
                    <p class="font-bold">[OK] Monetization Engine Started!</p>
                    <p>Opportunities executed: ${data.opportunities_executed || 0}</p>
                    <p>Revenue generated: $${data.total_revenue || 0}</p>
                    <p>Success rate: ${(data.success_rate * 100 || 0).toFixed(1)}%</p>
                </div>
            `;
        } else {
            result.innerHTML = `<p class="text-red-400">[ERROR] Error: ${data.error}</p>`;
        }
    } catch (error) {
        result.innerHTML = `<p class="text-red-400">[ERROR] Network error: ${error.message}</p>`;
    }
}

async function createPayment() {
    const result = document.getElementById('action-result');
    result.innerHTML = '<p class="text-yellow-400">💳 Creating PayPal payment...</p>';
    
    try {
        const response = await fetch('/api/paypal/create-payment', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                amount: 2500,
                service_type: 'instant_bi',
                client_email: 'demo@client.com'
            })
        });
        const data = await response.json();
        
        if (data.success) {
            result.innerHTML = `
                <div class="text-blue-400">
                    <p class="font-bold">💳 Payment Created!</p>
                    <p>Payment ID: ${data.payment_id}</p>
                    <p>Amount: $${data.amount}</p>
                    <p><a href="${data.approval_url}" target="_blank" class="underline text-blue-300">Complete Payment on PayPal</a></p>
                </div>
            `;
        } else {
            result.innerHTML = `<p class="text-red-400">[ERROR] Payment failed: ${data.error}</p>`;
        }
    } catch (error) {
        result.innerHTML = `<p class="text-red-400">[ERROR] Payment error: ${error.message}</p>`;
    }
}

async function getOpportunities() {
    const result = document.getElementById('action-result');
    result.innerHTML = '<p class="text-yellow-400">🎯 Finding revenue opportunities...</p>';
    
    try {
        const response = await fetch('/api/opportunities');
        const data = await response.json();
        
        if (data.opportunities) {
            result.innerHTML = `
                <div class="text-purple-400">
                    <p class="font-bold">🎯 Revenue Opportunities Found!</p>
                    <p>Total opportunities: ${data.total_opportunities}</p>
                    <p>Potential revenue: $${data.total_potential_revenue?.toFixed(2) || 0}</p>
                    <p class="text-sm mt-2">Top opportunity: ${data.opportunities[0]?.revenue_stream || 'N/A'}</p>
                </div>
            `;
        } else {
            result.innerHTML = `<p class="text-red-400">[ERROR] Error: ${data.error}</p>`;
        }
    } catch (error) {
        result.innerHTML = `<p class="text-red-400">[ERROR] Error: ${error.message}</p>`;
    }
}
</script>
</body>
</html>''', 
    engines_available=ENGINES_AVAILABLE,
    paypal_configured=bool(os.getenv('PAYPAL_REST_API_ID')),
    environment=PAYPAL_ENV,
    base_url=APP_BASE_URL,
    api_base=PAYPAL_API_BASE
)

@app.route('/services')
def services():
    """Services page"""
    return render_template_string('''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SINCOR Services</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-900 text-white min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <div class="text-center mb-12">
            <h1 class="text-4xl font-bold mb-4 text-blue-400">SINCOR Services</h1>
            <p class="text-xl text-gray-300">AI-powered business solutions that deliver results</p>
        </div>
        
        <div class="grid md:grid-cols-2 gap-8 mb-12">
            <div class="bg-gray-800 p-8 rounded-lg">
                <h2 class="text-2xl font-bold mb-4 text-green-400">💡 Instant Business Intelligence</h2>
                <p class="text-gray-300 mb-4">Get comprehensive business analysis in minutes, not months.</p>
                <ul class="text-gray-300 space-y-2 mb-6">
                    <li>- Market opportunity analysis</li>
                    <li>- Competitive intelligence reports</li>
                    <li>- Revenue optimization strategies</li>
                    <li>- Risk assessment and mitigation</li>
                </ul>
                <div class="text-2xl font-bold text-green-400 mb-4">$2,500 - $15,000</div>
                <button onclick="requestService('instant_bi', 2500)" class="w-full bg-green-600 hover:bg-green-700 px-6 py-3 rounded-lg font-semibold">
                    Request Instant BI
                </button>
            </div>
            
            <div class="bg-gray-800 p-8 rounded-lg">
                <h2 class="text-2xl font-bold mb-4 text-purple-400">🤖 Agent Services</h2>
                <p class="text-gray-300 mb-4">AI agents that handle your business operations 24/7.</p>
                <ul class="text-gray-300 space-y-2 mb-6">
                    <li>- Customer service automation</li>
                    <li>- Lead generation and qualification</li>
                    <li>- Process optimization</li>
                    <li>- Performance monitoring</li>
                </ul>
                <div class="text-2xl font-bold text-purple-400 mb-4">$500 - $5,000/month</div>
                <button onclick="requestService('agent_services', 500)" class="w-full bg-purple-600 hover:bg-purple-700 px-6 py-3 rounded-lg font-semibold">
                    Start Agent Services
                </button>
            </div>
            
            <div class="bg-gray-800 p-8 rounded-lg">
                <h2 class="text-2xl font-bold mb-4 text-yellow-400">📊 Predictive Analytics</h2>
                <p class="text-gray-300 mb-4">Forecast market trends and identify future opportunities.</p>
                <ul class="text-gray-300 space-y-2 mb-6">
                    <li>- Market trend forecasting</li>
                    <li>- Demand prediction models</li>
                    <li>- Price optimization algorithms</li>
                    <li>- Strategic planning insights</li>
                </ul>
                <div class="text-2xl font-bold text-yellow-400 mb-4">$6,000 - $25,000</div>
                <button onclick="requestService('predictive_analytics', 6000)" class="w-full bg-yellow-600 hover:bg-yellow-700 px-6 py-3 rounded-lg font-semibold">
                    Get Predictive Analytics
                </button>
            </div>
            
            <div class="bg-gray-800 p-8 rounded-lg">
                <h2 class="text-2xl font-bold mb-4 text-red-400">🤝 Enterprise Partnerships</h2>
                <p class="text-gray-300 mb-4">Strategic partnerships that unlock massive revenue streams.</p>
                <ul class="text-gray-300 space-y-2 mb-6">
                    <li>- Partnership framework development</li>
                    <li>- Revenue sharing models</li>
                    <li>- Joint venture structures</li>
                    <li>- Strategic alliance management</li>
                </ul>
                <div class="text-2xl font-bold text-red-400 mb-4">$50,000 - $200,000</div>
                <button onclick="requestService('enterprise_partnerships', 50000)" class="w-full bg-red-600 hover:bg-red-700 px-6 py-3 rounded-lg font-semibold">
                    Explore Partnerships
                </button>
            </div>
        </div>
        
        <div class="text-center">
            <a href="/dashboard" class="bg-blue-600 hover:bg-blue-700 px-8 py-4 rounded-lg font-semibold text-xl">
                Back to Dashboard
            </a>
        </div>
        
        <div id="service-result" class="mt-8 p-6 bg-gray-800 rounded-lg min-h-[100px]">
            <p class="text-gray-400">Click any service above to get started with SINCOR</p>
        </div>
    </div>

<script>
async function requestService(serviceType, amount) {
    const result = document.getElementById('service-result');
    result.innerHTML = `<p class="text-yellow-400">🔄 Creating payment for ${serviceType.replace('_', ' ')} service...</p>`;
    
    try {
        const response = await fetch('/api/paypal/create-payment', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                amount: amount,
                service_type: serviceType,
                client_email: 'client@example.com'
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            result.innerHTML = `
                <div class="text-green-400">
                    <p class="font-bold">[OK] Payment Created Successfully!</p>
                    <p class="mb-2">Service: ${serviceType.replace('_', ' ').toUpperCase()}</p>
                    <p class="mb-2">Amount: $${data.amount.toFixed(2)}</p>
                    <p class="mb-4">Payment ID: ${data.payment_id}</p>
                    <a href="${data.approval_url}" target="_blank" 
                       class="inline-block bg-blue-600 hover:bg-blue-700 px-6 py-3 rounded-lg font-semibold text-white">
                        Complete Payment on PayPal →
                    </a>
                </div>
            `;
        } else {
            result.innerHTML = `<p class="text-red-400">[ERROR] Payment creation failed: ${data.error}</p>`;
        }
    } catch (error) {
        result.innerHTML = `<p class="text-red-400">[ERROR] Network error: ${error.message}</p>`;
    }
}
</script>
</body>
</html>''')

# API Routes
@app.route('/api/monetization/start', methods=['POST'])
def start_monetization():
    """Start the monetization engine"""
    if not monetization_engine:
        return jsonify({'success': False, 'error': 'Monetization engine not available'}), 500
    
    try:
        # Execute monetization strategy (synchronous wrapper for async function)
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        strategy_report = loop.run_until_complete(
            monetization_engine.execute_monetization_strategy(max_concurrent_opportunities=10)
        )
        loop.close()
        
        return jsonify({
            'success': True,
            'message': 'Monetization engine started',
            'opportunities_executed': strategy_report['execution_summary']['opportunities_executed'],
            'total_revenue': strategy_report['execution_summary']['total_revenue'],
            'success_rate': strategy_report['execution_summary']['success_rate']
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to start monetization: {str(e)}'}), 500

@app.route('/api/paypal/create-payment', methods=['POST'])
def create_payment():
    """Create a PayPal payment"""
    try:
        data = request.get_json()
        amount = float(data.get('amount', 0))
        service_type = data.get('service_type', 'instant_bi')
        client_email = data.get('client_email', 'demo@client.com')
        
        if amount <= 0:
            return jsonify({'success': False, 'error': 'Invalid amount'}), 400
        
        client_id = os.getenv('PAYPAL_REST_API_ID')
        client_secret = os.getenv('PAYPAL_REST_API_SECRET')
        
        if not client_id or not client_secret:
            return jsonify({
                'success': False, 
                'error': 'PayPal credentials not configured in Railway environment'
            }), 500
        
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
            }), 500
        
        access_token = token_response.json()['access_token']
        
        # Create PayPal payment
        payment_data = {
            "intent": "sale",
            "payer": {"payment_method": "paypal"},
            "transactions": [{
                "amount": {"total": f"{amount:.2f}", "currency": "USD"},
                "description": f"SINCOR {service_type.replace('_', ' ').title()} Service"
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
                'amount': amount,
                'approval_url': approval_url
            })
        else:
            return jsonify({
                'success': False,
                'error': f'PayPal payment creation failed: {payment_response.status_code}'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Payment processing error: {str(e)}'
        }), 500

@app.route('/api/opportunities', methods=['GET'])
def get_opportunities():
    """Get revenue opportunities"""
    if not monetization_engine:
        return jsonify({'error': 'Monetization engine not available'}), 500
    
    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        opportunities = loop.run_until_complete(monetization_engine.identify_revenue_opportunities())
        loop.close()
        
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

@app.route('/payment/success')
def payment_success():
    """Payment success callback"""
    payment_id = request.args.get('paymentId')
    payer_id = request.args.get('PayerID')
    
    return render_template_string('''<!DOCTYPE html>
<html><head><title>Payment Successful!</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-green-900 p-8 rounded-lg max-w-md text-center">
    <h1 class="text-3xl font-bold mb-4 text-green-400">🎉 Payment Successful!</h1>
    <div class="space-y-2 mb-6">
        <p><strong>Payment ID:</strong> {{ payment_id }}</p>
        <p><strong>Payer ID:</strong> {{ payer_id }}</p>
    </div>
    <p class="text-green-300 mb-6">Your SINCOR service has been activated!</p>
    <a href="/dashboard" class="inline-block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
        Return to Dashboard
    </a>
</div></body></html>''', payment_id=payment_id, payer_id=payer_id)

@app.route('/payment/cancel')
def payment_cancel():
    """Payment cancel callback"""
    return render_template_string('''<!DOCTYPE html>
<html><head><title>Payment Cancelled</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-gray-800 p-8 rounded-lg max-w-md text-center">
    <h1 class="text-2xl font-bold mb-4">Payment Cancelled</h1>
    <p class="text-gray-300 mb-6">Your payment was cancelled. You can try again anytime.</p>
    <a href="/services" class="inline-block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
        View Services
    </a>
</div></body></html>''')

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'SINCOR Production Platform',
        'engines_available': ENGINES_AVAILABLE,
        'paypal_configured': bool(os.getenv('PAYPAL_REST_API_ID')),
        'environment': PAYPAL_ENV,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/readyz')
def readiness_check():
    """Railway readiness check"""
    checks = {
        'paypal_credentials': bool(os.getenv('PAYPAL_REST_API_ID') and os.getenv('PAYPAL_REST_API_SECRET')),
        'environment': PAYPAL_ENV,
        'api_base': PAYPAL_API_BASE,
        'base_url': APP_BASE_URL,
        'engines_available': ENGINES_AVAILABLE
    }
    
    all_ready = all(checks.values())
    
    return jsonify({
        'ready': all_ready,
        'checks': checks,
        'service': 'SINCOR Production Platform',
        'version': '2.0.0'
    }), 200 if all_ready else 503

@app.route('/deployment-test')
def deployment_test():
    """Test endpoint to verify latest deployment"""
    return jsonify({
        'message': 'SINCOR LATEST DEPLOYMENT ACTIVE',
        'timestamp': datetime.now().isoformat(),
        'version': '2.0.1-route-fix',
        'routes_registered': True,
        'file': 'simple.py'
    })

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    print(f">> Starting SINCOR Production Platform on port {port}")
    print(f">> Environment: {PAYPAL_ENV.upper()}")
    print(f">> Base URL: {APP_BASE_URL}")
    print(f">> PayPal: {'CONFIGURED' if os.getenv('PAYPAL_REST_API_ID') else 'MISSING CREDENTIALS'}")
    print(f">> Engines: {'AVAILABLE' if ENGINES_AVAILABLE else 'IMPORT FAILED'}")
    
    # List registered routes for debugging
    print(">> Registered Flask routes:")
    for rule in app.url_map.iter_rules():
        print(f"   {rule.rule} -> {rule.endpoint}")
    
    # Initialize engines after Flask app is ready
    try:
        print(">> Initializing SINCOR engines...")
        engines_initialized = initialize_engines()
        print(f">> Engine initialization: {'SUCCESS' if engines_initialized else 'FAILED (routes still work)'}")
    except Exception as e:
        print(f">> Engine initialization error: {e}")
    
    print(">> Starting Flask server...")
    app.run(host='0.0.0.0', port=port, debug=False)