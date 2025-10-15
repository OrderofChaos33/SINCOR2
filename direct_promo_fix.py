"""
Direct promo routes to add to sincor_app.py
Copy these routes directly into the main file if imports are failing
"""

# Add these routes directly to sincor_app.py before if __name__ == "__main__":

from flask import session, redirect
from datetime import datetime

PROMO_CODES = {
    "PROTOTYPE2025": {
        "description": "Full free access for prototype testing - friends & select testers",
        "trial_days": 90,
        "bypass_payment": True,
        "max_uses": 50
    },
    "COURTTESTER": {
        "description": "Court's personal testing account",
        "trial_days": 365,
        "bypass_payment": True,
        "max_uses": 10
    },
    "FRIENDSTEST": {
        "description": "Friends and family testing - 3 months free",
        "trial_days": 90,
        "bypass_payment": True,
        "max_uses": 100
    }
}

@app.route("/free-trial/<promo_code>")
def free_trial_activation(promo_code):
    """Direct free trial activation via URL - perfect for Railway."""
    promo_code = promo_code.upper()
    
    if promo_code not in PROMO_CODES:
        return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Invalid Promo Code</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
    <div class="bg-red-900 p-8 rounded-lg max-w-md text-center">
        <h1 class="text-2xl font-bold mb-4">❌ Invalid Promo Code</h1>
        <p>The promo code "{promo_code}" is not valid.</p>
        <a href="/" class="mt-4 inline-block bg-blue-600 px-4 py-2 rounded">← Back to Home</a>
    </div>
</body>
</html>"""
    
    # Set promo session
    promo_data = PROMO_CODES[promo_code]
    session['promo_active'] = True
    session['promo_code'] = promo_code
    session['promo_trial_days'] = promo_data['trial_days']
    session['promo_bypass_payment'] = promo_data['bypass_payment']
    session['promo_activated_at'] = datetime.now().isoformat()
    
    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>Free Trial Activated!</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
    <div class="bg-green-900 p-8 rounded-lg max-w-lg text-center">
        <h1 class="text-3xl font-bold mb-6">🎉 FREE TRIAL ACTIVATED!</h1>
        <div class="bg-black p-6 rounded-lg mb-6">
            <h2 class="text-xl font-bold text-green-400 mb-4">Your SINCOR Access:</h2>
            <div class="space-y-2 text-left">
                <div class="flex justify-between">
                    <span>Promo Code:</span>
                    <span class="font-mono text-green-400">{promo_code}</span>
                </div>
                <div class="flex justify-between">
                    <span>Trial Period:</span>
                    <span class="text-green-400">{promo_data['trial_days']} days FREE</span>
                </div>
                <div class="flex justify-between">
                    <span>Access Level:</span>
                    <span class="text-green-400">Full SINCOR System</span>
                </div>
                <div class="flex justify-between">
                    <span>42 AI Agents:</span>
                    <span class="text-green-400">✅ Activated</span>
                </div>
            </div>
        </div>
        
        <div class="space-y-4">
            <a href="/" class="block bg-green-600 hover:bg-green-500 px-6 py-3 rounded-lg font-semibold">
                🚀 Start Getting Started Tour
            </a>
            <a href="/admin" class="block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
                🎯 Access Admin Dashboard
            </a>
        </div>
        
        <p class="text-sm text-gray-300 mt-6">
            You now have full access to SINCOR's 42-agent AI business automation system.
            <br>Explore all features - no payment required during trial period!
        </p>
    </div>
</body>
</html>"""