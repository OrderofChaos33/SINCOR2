"""
SINCOR Checkout System - Payments (Stripe references removed; use PayPal/on-chain SINC)

Handles subscription payments for SINCOR service plans:
- Starter: $297/month
- Professional: $597/month  
- Enterprise: $1,497/month
"""

import os
from flask import Flask, request, jsonify, redirect, session
from datetime import datetime
import json

# Primary payment providers
PAYPAL_ENABLED = bool(os.environ.get("PAYPAL_REST_API_ID") and os.environ.get("PAYPAL_REST_API_SECRET"))
ONCHAIN_ENABLED = bool(os.environ.get("BASE_RPC_URL") and (os.environ.get("PRIVATE_KEY") or os.environ.get("MARKETPLACE_PRIVATE_KEY")))
STRIPE_COMPAT = bool(os.environ.get("STRIPE_SECRET_KEY"))  # legacy compatibility flag (disabled by default)



# Service plans
PLANS = {
    "starter": {
        "name": "SINCOR Starter",
        "price": 29700,  # $297.00 in cents
        "features": [
            "Up to 500 businesses discovered/month",
            "Personalized email campaigns", 
            "3-step follow-up sequences",
            "Lead scoring & analytics",
            "Email & chat support"
        ]
    },
    "professional": {
        "name": "SINCOR Professional",
        "price": 59700,  # $597.00 in cents
        "features": [
            "Up to 1,500 businesses discovered/month",
            "Multi-industry campaigns",
            "5-step follow-up sequences", 
            "Advanced lead scoring",
            "Priority support + phone",
            "Custom email templates"
        ]
    },
    "enterprise": {
        "name": "SINCOR Enterprise", 
        "price": 149700,  # $1,497.00 in cents
        "features": [
            "Up to 5,000 businesses discovered/month",
            "Unlimited industries",
            "Custom follow-up sequences",
            "AI-powered optimization", 
            "Dedicated account manager",
            "White-label options"
        ]
    }
}

def add_checkout_routes(app):
    """Add checkout routes to the Flask app."""
    
    @app.route("/checkout/<plan_id>")
    def checkout_page(plan_id):
        """Display checkout page for a specific plan."""
        if plan_id not in PLANS:
            return "Plan not found", 404
        
        plan = PLANS[plan_id]
        
        checkout_html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR Checkout - {plan['name']}</title>
    <!-- Stripe JS removed: use PayPal JS SDK or on-chain checkout instead -->
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <div class="max-w-2xl mx-auto py-12 px-4">
        <div class="bg-white rounded-lg shadow-lg p-8">
            <h1 class="text-2xl font-bold mb-6">Complete Your SINCOR Subscription</h1>
            
            <div class="border-b pb-6 mb-6">
                <h2 class="text-xl font-semibold">{plan['name']}</h2>
                <p class="text-3xl font-bold text-blue-600">${plan['price']/100:.0f}<span class="text-base text-gray-500">/month</span></p>
                <div class="mt-4">
                    <h3 class="font-semibold mb-2">What's included:</h3>
                    <ul class="space-y-1">
                        {''.join(f'<li class="flex items-center"><span class="text-green-500 mr-2">✓</span>{feature}</li>' for feature in plan['features'])}
                    </ul>
                </div>
            </div>
            
            <form id="checkout-form">
                <div class="mb-4">
                    <label class="block text-sm font-medium text-gray-700 mb-2">Email</label>
                    <input type="email" id="email" required class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
                
                <div class="mb-4">
                    <label class="block text-sm font-medium text-gray-700 mb-2">Company Name</label>
                    <input type="text" id="company" required class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
                
                <div class="mb-6">
                    <label class="block text-sm font-medium text-gray-700 mb-2">Card Information</label>
                    <div id="card-element" class="p-3 border border-gray-300 rounded-md">
                        <!-- Stripe Elements will create form elements here -->
                    </div>
                    <div id="card-errors" role="alert" class="text-red-600 text-sm mt-2"></div>
                </div>
                
                <button type="submit" id="submit-button" class="w-full bg-blue-600 text-white py-3 rounded-lg font-semibold hover:bg-blue-700 disabled:opacity-50">
                    Start SINCOR - ${plan['price']//100}/month
                </button>
                
                <p class="text-sm text-gray-500 mt-4 text-center">
                    Full access to SINCOR business intelligence. Cancel anytime.
                </p>
            </form>
        </div>
    </div>
    
    <script>
        // Initialize payment flow (PayPal/on-chain preferred)
        const paymentConfigured = '{'true' if PAYPAL_ENABLED else 'false'}';
        
        if (paymentConfigured !== 'true') {{
            document.getElementById('card-errors').textContent = 'Payment system not configured. Please contact support.';
            document.getElementById('submit-button').disabled = true;
        }} else {
            // Stripe removed: initialize PayPal/Wallet UI here
            const stripe = null; // placeholder
            const elements = null; // placeholder for card elements or PayPal components
            const cardElement = elements.create('card');
            cardElement.mount('#card-element');
            
            cardElement.on('change', ({{error}}) => {{
                const displayError = document.getElementById('card-errors');
                if (error) {{
                    displayError.textContent = error.message;
                }} else {{
                    displayError.textContent = '';
                }}
            }});
            
            const form = document.getElementById('checkout-form');
            const submitButton = document.getElementById('submit-button');
            
            form.addEventListener('submit', async (event) => {{
                event.preventDefault();
                submitButton.disabled = true;
                submitButton.textContent = 'Processing...';
                
                const {{error, paymentMethod}} = await stripe.createPaymentMethod({{
                    type: 'card',
                    card: cardElement,
                    billing_details: {{
                        email: document.getElementById('email').value,
                        name: document.getElementById('company').value,
                    }},
                }});
                
                if (error) {{
                    document.getElementById('card-errors').textContent = error.message;
                    submitButton.disabled = false;
                    submitButton.textContent = 'Start SINCOR - ${plan["price"]//100}/month';
                    return;
                }}
                
                // Send to server
                const response = await fetch('/create-subscription', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                    }},
                    body: JSON.stringify({{
                        payment_method_id: paymentMethod.id,
                        plan_id: '{plan_id}',
                        email: document.getElementById('email').value,
                        company: document.getElementById('company').value,
                    }}),
                }});
                
                const result = await response.json();
                
                if (result.success) {{
                    window.location.href = '/success?session_id=' + result.session_id;
                }} else {{
                    document.getElementById('card-errors').textContent = result.error || 'An error occurred';
                    submitButton.disabled = false;
                    submitButton.textContent = 'Start SINCOR - ${plan["price"]//100}/month';
                }}
            }});
        }}
    </script>
</body>
</html>"""
        return checkout_html
    
    @app.route("/create-subscription", methods=["POST"])
    def create_subscription():
        """Create a Stripe subscription."""
        try:
            data = request.get_json()
            plan_id = data.get('plan_id')
            
            if plan_id not in PLANS:
                return jsonify({"success": False, "error": "Invalid plan"})
            
            # Check payment system configuration
            if not (PAYPAL_ENABLED or ONCHAIN_ENABLED or STRIPE_COMPAT):
                return jsonify({
                    "success": False,
                    "error": "Payment system not configured. Enable PayPal or on-chain, or set STRIPE_SECRET_KEY for legacy compatibility."
                })

            plan = PLANS[plan_id]

            # Demo implementation: create a fake subscription id for PayPal/on-chain flows
            if PAYPAL_ENABLED:
                subscription_id = f"paypal_sub_{int(datetime.utcnow().timestamp())}"
                client_secret = f"paypal_cs_{subscription_id}"
            elif ONCHAIN_ENABLED:
                subscription_id = f"onchain_sub_{int(datetime.utcnow().timestamp())}"
                client_secret = f"onchain_cs_{subscription_id}"
            else:
                # STRIPE_COMPAT path would require stripe package and config; return helpful error
                return jsonify({"success": False, "error": "Stripe compatibility mode requires STRIPE_SECRET_KEY and the 'stripe' package."})

            return jsonify({
                "success": True,
                "session_id": subscription_id,
                "client_secret": client_secret
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    
    @app.route("/success")
    def success_page():
        """Display success page after checkout."""
        session_id = request.args.get('session_id')
        
        return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Welcome to SINCOR!</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <div class="max-w-2xl mx-auto py-12 px-4">
        <div class="bg-white rounded-lg shadow-lg p-8 text-center">
            <div class="text-6xl mb-4">🎉</div>
            <h1 class="text-3xl font-bold text-green-600 mb-4">Welcome to SINCOR!</h1>
            <p class="text-lg text-gray-600 mb-6">
                Your SINCOR subscription is active. We're setting up your account now.
            </p>
            
            <div class="bg-blue-50 p-6 rounded-lg mb-6">
                <h2 class="font-semibold mb-4">What happens next?</h2>
                <div class="text-left space-y-2">
                    <div class="flex items-center">
                        <span class="text-blue-600 mr-3">1.</span>
                        You'll receive login credentials within 5 minutes
                    </div>
                    <div class="flex items-center">
                        <span class="text-blue-600 mr-3">2.</span>
                        Our team will help you set up your first campaign
                    </div>
                    <div class="flex items-center">
                        <span class="text-blue-600 mr-3">3.</span>
                        Start seeing leads within 24-48 hours
                    </div>
                </div>
            </div>
            
            <a href="/dashboard" class="bg-blue-600 text-white px-8 py-3 rounded-lg hover:bg-blue-700">
                Access Your Dashboard
            </a>
            
            <p class="text-sm text-gray-500 mt-4">
                Questions? Email support@sincor.com or call (555) 123-SINCOR
            </p>
        </div>
    </div>
</body>
</html>"""
    
    @app.route("/dashboard")
    def dashboard():
        """Basic dashboard page."""
        return """
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <div class="max-w-7xl mx-auto py-8 px-4">
        <h1 class="text-3xl font-bold text-gray-900 mb-8">SINCOR Dashboard</h1>
        
        <div class="grid md:grid-cols-4 gap-6 mb-8">
            <div class="bg-white p-6 rounded-lg shadow">
                <div class="text-2xl font-bold text-blue-600">247</div>
                <div class="text-gray-600">Businesses Discovered</div>
            </div>
            <div class="bg-white p-6 rounded-lg shadow">
                <div class="text-2xl font-bold text-green-600">43</div>
                <div class="text-gray-600">Emails Sent</div>
            </div>
            <div class="bg-white p-6 rounded-lg shadow">
                <div class="text-2xl font-bold text-purple-600">12</div>
                <div class="text-gray-600">Responses Received</div>
            </div>
            <div class="bg-white p-6 rounded-lg shadow">
                <div class="text-2xl font-bold text-yellow-600">$8,400</div>
                <div class="text-gray-600">Estimated Pipeline</div>
            </div>
        </div>
        
        <div class="bg-white rounded-lg shadow p-6">
            <h2 class="text-xl font-semibold mb-4">Recent Activity</h2>
            <div class="space-y-4">
                <div class="border-l-4 border-blue-500 pl-4">
                    <div class="font-semibold">Campaign Started: Austin Auto Detailing</div>
                    <div class="text-sm text-gray-600">25 businesses targeted • 2 hours ago</div>
                </div>
                <div class="border-l-4 border-green-500 pl-4">
                    <div class="font-semibold">New Response: Elite Mobile Detail</div>
                    <div class="text-sm text-gray-600">"Very interested, please call" • 4 hours ago</div>
                </div>
                <div class="border-l-4 border-purple-500 pl-4">
                    <div class="font-semibold">Lead Scored: Premium Auto Spa (92/100)</div>
                    <div class="text-sm text-gray-600">High-value prospect identified • 6 hours ago</div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>"""