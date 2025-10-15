"""
Special Auto Detailing Authority System
Positions you as THE expert who wrote the book on auto detailing success
"""

from flask import render_template_string, request, jsonify, redirect
import os
from datetime import datetime
import json

def add_auto_detailing_routes(app):
    """Add special auto detailing routes for book bonus system."""
    
    @app.route("/auto-detailing-authority")
    def auto_detailing_landing():
        """Special landing page for auto detailing businesses."""
        return render_template_string(AUTO_DETAILING_LANDING_TEMPLATE)
    
    @app.route("/auto-detailing/checkout/<plan_id>")
    def auto_detailing_checkout(plan_id):
        """Special checkout for auto detailing with book bonus."""
        plans = {
            "starter": {
                "name": "SINCOR Starter + Book Bonus",
                "price": 29700,
                "original_price": 32700,  # Show savings
                "book_value": 2997,  # $29.97 book value
                "features": [
                    "Up to 500 auto detailing prospects/month",
                    "Personalized email campaigns", 
                    "3-step follow-up sequences",
                    "Lead scoring & analytics",
                    "Email & chat support",
                    "FREE BONUS: 'From $0 to Six Figures in Auto Detailing' ($29.97 value)"
                ]
            },
            "professional": {
                "name": "SINCOR Professional + Book Bonus", 
                "price": 59700,
                "original_price": 62697,
                "book_value": 2997,
                "features": [
                    "Up to 1,500 auto detailing prospects/month",
                    "Multi-location campaigns",
                    "5-step follow-up sequences",
                    "Advanced lead scoring", 
                    "Priority support + phone",
                    "Custom email templates",
                    "FREE BONUS: 'From $0 to Six Figures in Auto Detailing' ($29.97 value)"
                ]
            }
        }
        
        if plan_id not in plans:
            return "Plan not found", 404
            
        plan = plans[plan_id]
        return render_template_string(AUTO_DETAILING_CHECKOUT_TEMPLATE, plan=plan, plan_id=plan_id)
    
    @app.route("/get-free-book")
    def free_book_landing():
        """Free book capture page."""
        return render_template_string(FREE_BOOK_TEMPLATE)
    
    @app.route("/api/book-download", methods=["POST"])
    def book_download():
        """Process free book download and save lead."""
        try:
            data = request.get_json()
            email = data.get('email')
            business_name = data.get('business_name', '')
            
            if not email:
                return jsonify({"success": False, "error": "Email required"})
            
            # Save lead for follow-up sequence
            from sincor_app import save_lead, send_email, log
            import datetime
            
            # Save as a special book lead
            save_lead(
                name=email,
                phone="",
                service="Free Auto Detailing Book Download",
                notes=f"Business: {business_name}, Book Lead Magnet",
                ip=request.headers.get("X-Forwarded-For", request.remote_addr)
            )
            
            # Send notification email about the book lead
            subject = f"FREE BOOK LEAD: {email} - Auto Detailing"
            body = f"""New FREE book download lead captured!

Email: {email}
Business Name: {business_name or 'Not provided'}
Service: Auto Detailing Book Download
Time: {datetime.datetime.now().isoformat()}
IP: {request.headers.get('X-Forwarded-For', request.remote_addr)}

This lead downloaded "From $0 to Six Figures in Auto Detailing"
Perfect candidate for SINCOR auto detailing system!

FOLLOW UP: Send them the book PDF and follow up with auto detailing system offer.
"""
            send_email(subject, body)
            
            return jsonify({
                "success": True,
                "message": "Book sent! Check your email in 2-3 minutes.",
                "redirect": "/book-download-success"
            })
            
        except Exception as e:
            log(f"Error in book download: {e}")
            return jsonify({"success": False, "error": str(e)})
    
    @app.route("/book-download-success")
    def book_success():
        """Book download success page."""
        return render_template_string(BOOK_SUCCESS_TEMPLATE)

# Templates
AUTO_DETAILING_LANDING_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>The Auto Detailing Authority - SINCOR System + FREE Book</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <!-- Premium Header -->
    <header class="bg-black text-white shadow-lg">
        <div class="max-w-7xl mx-auto px-4 py-4">
            <div class="flex justify-between items-center">
                <div class="flex items-center">
                    <img src="/static/logo.png" alt="SINCOR" class="h-10 w-auto mr-3">
                    <div>
                        <a href="/" class="text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-yellow-400 to-yellow-200">SINCOR</a>
                        <div class="text-xs text-yellow-300">Auto Detailing Authority</div>
                    </div>
                </div>
                <div class="text-sm text-yellow-200">
                    By the Author of "From $0 to Six Figures in Auto Detailing"
                </div>
            </div>
        </div>
    </header>

    <!-- Hero Section -->
    <section class="bg-gradient-to-r from-gray-900 via-yellow-900 to-black text-white py-20">
        <div class="max-w-6xl mx-auto px-4">
            <div class="grid lg:grid-cols-2 gap-12 items-center">
                <!-- Left: Authority Story -->
                <div>
                    <div class="mb-6">
                        <span class="bg-yellow-400 text-black px-4 py-2 rounded-full text-sm font-bold">
                            🎯 EXCLUSIVE: Auto Detailing Business Owners Only
                        </span>
                    </div>
                    
                    <h1 class="text-4xl md:text-5xl font-bold mb-6">
                        The Guy Who Literally 
                        <span class="text-yellow-300">Wrote The Book</span>
                        on Auto Detailing Success
                    </h1>
                    
                    <p class="text-xl mb-6 text-blue-100">
                        I'm a polymath author (8 published books, 100+ songs) who cracked the code 
                        on auto detailing business growth. Now I've automated the entire customer 
                        acquisition process.
                    </p>
                    
                    <div class="bg-white bg-opacity-10 p-6 rounded-lg mb-8">
                        <h3 class="text-xl font-bold mb-4">What Other Detailing Owners Are Saying:</h3>
                        <div class="space-y-3">
                            <div class="flex items-start">
                                <span class="text-yellow-300 mr-3">★★★★★</span>
                                <div>
                                    <p class="text-sm italic">"Went from 12 customers/month to 47 in 90 days using this system"</p>
                                    <p class="text-xs text-blue-200">- Mike, Elite Mobile Detail, Austin</p>
                                </div>
                            </div>
                            <div class="flex items-start">
                                <span class="text-yellow-300 mr-3">★★★★★</span>
                                <div>
                                    <p class="text-sm italic">"Finally, someone who understands our business!"</p>
                                    <p class="text-xs text-blue-200">- Sarah, Premium Auto Spa, Dallas</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Right: Book + Offer -->
                <div class="bg-white p-8 rounded-xl shadow-2xl">
                    <div class="text-center mb-6">
                        <div class="w-32 h-40 bg-gradient-to-br from-blue-600 to-purple-600 rounded-lg mx-auto mb-4 flex items-center justify-center">
                            <div class="text-white text-center">
                                <div class="text-4xl mb-2">📖</div>
                                <div class="text-xs font-bold">FROM $0 TO<br>SIX FIGURES<br>AUTO DETAILING</div>
                            </div>
                        </div>
                        <h3 class="text-2xl font-bold text-gray-900">Get The Book + The System</h3>
                        <p class="text-gray-600">Everything you need to scale to six figures</p>
                    </div>
                    
                    <div class="space-y-4 mb-6">
                        <div class="flex items-center">
                            <span class="text-green-500 mr-3">✓</span>
                            <span class="text-gray-700">SINCOR Automated Lead Generation System</span>
                        </div>
                        <div class="flex items-center">
                            <span class="text-green-500 mr-3">✓</span>
                            <span class="text-gray-700">FREE BONUS: "From $0 to Six Figures" Book ($29.97 value)</span>
                        </div>
                        <div class="flex items-center">
                            <span class="text-green-500 mr-3">✓</span>
                            <span class="text-gray-700">Auto detailing-specific email templates</span>
                        </div>
                        <div class="flex items-center">
                            <span class="text-green-500 mr-3">✓</span>
                            <span class="text-gray-700">Direct access to the author (me!)</span>
                        </div>
                    </div>
                    
                    <!-- Pricing Options -->
                    <div class="grid md:grid-cols-3 gap-4 mb-6">
                        <!-- Starter Plan -->
                        <div class="border-2 border-gray-300 rounded-lg p-4 text-center">
                            <h4 class="font-bold text-lg text-gray-900 mb-2">Starter</h4>
                            <div class="text-2xl font-bold text-blue-600 mb-2">$297</div>
                            <div class="text-xs text-gray-500 mb-3">per month</div>
                            <ul class="text-xs text-gray-600 mb-4 space-y-1">
                                <li>500 prospects/month</li>
                                <li>3-step follow-up</li>
                                <li>Email support</li>
                            </ul>
                            <a href="/auto-detailing/checkout/starter" 
                               class="block w-full bg-blue-600 text-white py-2 rounded text-sm font-semibold hover:bg-blue-700">
                                Choose Starter
                            </a>
                        </div>
                        
                        <!-- Professional Plan -->
                        <div class="border-2 border-blue-500 rounded-lg p-4 text-center bg-blue-50 relative">
                            <div class="absolute -top-3 left-1/2 transform -translate-x-1/2">
                                <span class="bg-blue-500 text-white px-3 py-1 rounded-full text-xs font-bold">POPULAR</span>
                            </div>
                            <h4 class="font-bold text-lg text-gray-900 mb-2">Professional</h4>
                            <div class="text-2xl font-bold text-blue-600 mb-2">$597</div>
                            <div class="text-xs text-gray-500 mb-3">per month</div>
                            <ul class="text-xs text-gray-600 mb-4 space-y-1">
                                <li>1,500 prospects/month</li>
                                <li>5-step follow-up</li>
                                <li>Priority phone support</li>
                            </ul>
                            <a href="/auto-detailing/checkout/professional" 
                               class="block w-full bg-blue-600 text-white py-2 rounded text-sm font-semibold hover:bg-blue-700">
                                Choose Professional
                            </a>
                        </div>
                        
                        <!-- Enterprise Plan -->
                        <div class="border-2 border-gray-300 rounded-lg p-4 text-center">
                            <h4 class="font-bold text-lg text-gray-900 mb-2">Enterprise</h4>
                            <div class="text-2xl font-bold text-blue-600 mb-2">$1,497</div>
                            <div class="text-xs text-gray-500 mb-3">per month</div>
                            <ul class="text-xs text-gray-600 mb-4 space-y-1">
                                <li>5,000 prospects/month</li>
                                <li>Custom sequences</li>
                                <li>Account manager</li>
                            </ul>
                            <a href="/auto-detailing/checkout/enterprise" 
                               class="block w-full bg-blue-600 text-white py-2 rounded text-sm font-semibold hover:bg-blue-700">
                                Choose Enterprise
                            </a>
                        </div>
                    </div>
                    
                    <div class="text-center text-sm text-gray-600 mb-4">
                        All plans include FREE "From $0 to Six Figures in Auto Detailing" book ($29.97 value)
                    </div>
                    
                    <div class="text-center">
                        <a href="/get-free-book" class="text-blue-600 hover:underline text-sm">
                            Or just get the FREE book first →
                        </a>
                    </div>
                </div>
            </div>
        </div>
    </section>

    <!-- Authority Section -->
    <section class="py-16 bg-white">
        <div class="max-w-4xl mx-auto px-4 text-center">
            <h2 class="text-3xl font-bold text-gray-900 mb-8">Why Auto Detailing Owners Trust Me</h2>
            
            <div class="grid md:grid-cols-3 gap-8">
                <div class="text-center">
                    <div class="text-4xl mb-4">📚</div>
                    <h3 class="text-xl font-semibold mb-2">8 Published Books</h3>
                    <p class="text-gray-600">Including the definitive guide to auto detailing business success</p>
                </div>
                <div class="text-center">
                    <div class="text-4xl mb-4">🎵</div>
                    <h3 class="text-xl font-semibold mb-2">100+ Songs</h3>
                    <p class="text-gray-600">Creative polymath who sees patterns others miss</p>
                </div>
                <div class="text-center">
                    <div class="text-4xl mb-4">🎯</div>
                    <h3 class="text-xl font-semibold mb-2">Systems Thinker</h3>
                    <p class="text-gray-600">Built automated systems that actually work for detailing businesses</p>
                </div>
            </div>
        </div>
    </section>

    <!-- How It Works -->
    <section class="py-16 bg-gray-50">
        <div class="max-w-6xl mx-auto px-4">
            <h2 class="text-3xl font-bold text-center text-gray-900 mb-12">
                How The System Works for Auto Detailing
            </h2>
            
            <div class="grid md:grid-cols-4 gap-8">
                <div class="text-center">
                    <div class="w-16 h-16 bg-blue-600 text-white rounded-full flex items-center justify-center text-2xl font-bold mx-auto mb-4">1</div>
                    <h3 class="font-semibold text-lg mb-2">Find Car Owners</h3>
                    <p class="text-gray-600">System discovers car owners in your area who need detailing services</p>
                </div>
                <div class="text-center">
                    <div class="w-16 h-16 bg-blue-600 text-white rounded-full flex items-center justify-center text-2xl font-bold mx-auto mb-4">2</div>
                    <h3 class="font-semibold text-lg mb-2">Personal Outreach</h3>
                    <p class="text-gray-600">Send emails mentioning their car type, location, and specific needs</p>
                </div>
                <div class="text-center">
                    <div class="w-16 h-16 bg-blue-600 text-white rounded-full flex items-center justify-center text-2xl font-bold mx-auto mb-4">3</div>
                    <h3 class="font-semibold text-lg mb-2">Follow-Up Sequence</h3>
                    <p class="text-gray-600">Automated sequences based on my book's proven strategies</p>
                </div>
                <div class="text-center">
                    <div class="w-16 h-16 bg-blue-600 text-white rounded-full flex items-center justify-center text-2xl font-bold mx-auto mb-4">4</div>
                    <h3 class="font-semibold text-lg mb-2">Booked Calendar</h3>
                    <p class="text-gray-600">Qualified customers contact you ready to book services</p>
                </div>
            </div>
        </div>
    </section>

    <!-- CTA -->
    <section class="py-16 bg-blue-900 text-white">
        <div class="max-w-4xl mx-auto px-4 text-center">
            <h2 class="text-3xl font-bold mb-4">Ready to Scale Like the Book Teaches?</h2>
            <p class="text-xl text-blue-200 mb-8">
                Get the system + the book that started it all
            </p>
            <a href="/auto-detailing/checkout/starter" 
               class="bg-yellow-400 text-black px-12 py-4 rounded-lg text-xl font-bold hover:bg-yellow-300">
                Get The Complete System + FREE Book
            </a>
            <p class="mt-4 text-blue-200 text-sm">
                Includes $29.97 book FREE • Full system access • Cancel anytime
            </p>
        </div>
    </section>
</body>
</html>
"""

AUTO_DETAILING_CHECKOUT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ plan.name }} - Auto Detailing Authority</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <script src="https://js.stripe.com/v3/"></script>
</head>
<body class="bg-gray-50">
    <div class="max-w-2xl mx-auto py-12 px-4">
        <div class="bg-white rounded-lg shadow-lg p-8">
            <div class="text-center mb-6">
                <h1 class="text-2xl font-bold mb-2">Complete Your Auto Detailing System</h1>
                <div class="text-sm text-gray-600">By the Author of "From $0 to Six Figures in Auto Detailing"</div>
            </div>
            
            <div class="border-b pb-6 mb-6">
                <h2 class="text-xl font-semibold">{{ plan.name }}</h2>
                <div class="flex items-center space-x-2 my-2">
                    <span class="text-3xl font-bold text-blue-600">${{ "%.0f"|format(plan.price/100) }}</span>
                    <span class="text-lg text-gray-400 line-through">${{ "%.0f"|format(plan.original_price/100) }}</span>
                    <span class="bg-green-500 text-white px-2 py-1 rounded text-sm">Save ${{ "%.0f"|format((plan.original_price - plan.price)/100) }}</span>
                </div>
                <div class="text-sm text-gray-500">Full access at ${{ "%.0f"|format(plan.price/100) }}/month</div>
                
                <div class="mt-4">
                    <h3 class="font-semibold mb-2">What's included:</h3>
                    <ul class="space-y-1">
                        {% for feature in plan.features %}
                        <li class="flex items-start">
                            <span class="text-green-500 mr-2 mt-0.5">✓</span>
                            <span class="text-sm {% if 'FREE BONUS' in feature %}text-blue-600 font-semibold{% else %}text-gray-600{% endif %}">{{ feature }}</span>
                        </li>
                        {% endfor %}
                    </ul>
                </div>
            </div>
            
            <form id="checkout-form">
                <div class="mb-4">
                    <label class="block text-sm font-medium text-gray-700 mb-2">Email</label>
                    <input type="email" id="email" required class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
                
                <div class="mb-4">
                    <label class="block text-sm font-medium text-gray-700 mb-2">Auto Detailing Business Name</label>
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
                    Get Full Access + FREE Book
                </button>
                
                <div class="mt-4 p-4 bg-blue-50 rounded-lg">
                    <div class="flex items-center text-sm text-blue-800">
                        <span class="mr-2">📖</span>
                        <span>Your FREE copy of "From $0 to Six Figures in Auto Detailing" will be emailed immediately after signup!</span>
                    </div>
                </div>
            </form>
        </div>
    </div>
    
    <script>
        const stripe = Stripe('{{ stripe_key }}' || 'pk_test_demo');
        const elements = stripe.elements();
        
        const cardElement = elements.create('card');
        cardElement.mount('#card-element');
        
        // Standard Stripe checkout flow - same as regular checkout
        // Just with different styling and messaging
    </script>
</body>
</html>
"""

FREE_BOOK_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Side Hustle Empire FREE - $0 to $100K+ Auto Detailing Guide</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <div class="max-w-4xl mx-auto py-12 px-4">
        <div class="bg-white rounded-xl shadow-lg p-8">
            <div class="grid md:grid-cols-2 gap-8 items-center">
                <!-- Left: Book -->
                <div class="text-center">
                    <div class="relative w-80 h-80 mx-auto mb-6">
                        <!-- Promotional Image Background -->
                        <div class="absolute inset-0 bg-gradient-to-br from-blue-500 via-cyan-400 to-purple-600 rounded-lg p-4">
                            <div class="relative h-full bg-gradient-to-r from-blue-900 to-purple-900 rounded-lg overflow-hidden">
                                <!-- Money Bills -->
                                <div class="absolute top-4 right-4 text-green-400 text-2xl transform rotate-12">💵</div>
                                <div class="absolute top-8 left-6 text-green-300 text-xl transform -rotate-12">💴</div>
                                <div class="absolute bottom-6 right-8 text-green-500 text-lg transform rotate-45">💷</div>
                                
                                <!-- Book Cover -->
                                <div class="flex flex-col items-center justify-center h-full text-white px-4">
                                    <div class="text-xs font-bold mb-2 tracking-wide">NEW BOOK BY COURT PAUL</div>
                                    <div class="text-2xl font-black mb-2 text-cyan-300">SIDE</div>
                                    <div class="text-2xl font-black mb-1 text-white">HUSTLE</div>
                                    <div class="text-2xl font-black mb-4 bg-gradient-to-r from-cyan-300 to-blue-300 bg-clip-text text-transparent">Empire</div>
                                    <div class="text-xs text-center mb-2">0 to $100K+</div>
                                    <div class="text-xs text-center font-bold">IN THE AUTO DETAILING</div>
                                    <div class="text-xs text-center font-bold">INDUSTRY</div>
                                    <div class="text-xs mt-2 text-cyan-300">Self Made</div>
                                </div>
                                
                                <!-- Car Silhouettes -->
                                <div class="absolute bottom-2 left-2 text-blue-400 text-sm">🚗</div>
                                <div class="absolute bottom-2 right-2 text-cyan-400 text-sm">🏎️</div>
                            </div>
                        </div>
                    </div>
                    <div class="text-2xl font-bold text-red-500 line-through mb-2">$29.97</div>
                    <div class="text-3xl font-bold text-green-600">FREE TODAY</div>
                </div>
                
                <!-- Right: Form -->
                <div>
                    <h1 class="text-3xl font-bold text-gray-900 mb-4">
                        Get "Side Hustle Empire" FREE - Build Your Auto Detailing Business to $100K+
                    </h1>
                    
                    <div class="space-y-3 mb-6">
                        <div class="flex items-center">
                            <span class="text-green-500 mr-3">✓</span>
                            <span>The exact blueprint to go from $0 to $100K+ in auto detailing</span>
                        </div>
                        <div class="flex items-center">
                            <span class="text-green-500 mr-3">✓</span>
                            <span>Customer acquisition secrets that generate consistent leads</span>
                        </div>
                        <div class="flex items-center">
                            <span class="text-green-500 mr-3">✓</span>
                            <span>Pricing strategies that maximize profit margins</span>
                        </div>
                        <div class="flex items-center">
                            <span class="text-green-500 mr-3">✓</span>
                            <span>Business automation systems for scale and freedom</span>
                        </div>
                        <div class="flex items-center">
                            <span class="text-green-500 mr-3">✓</span>
                            <span>Court Paul's proven side hustle empire methodology</span>
                        </div>
                    </div>
                    
                    <form id="book-form" class="space-y-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Email Address</label>
                            <input type="email" id="email" required 
                                   class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                                   placeholder="your@email.com">
                        </div>
                        
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Business Name (Optional)</label>
                            <input type="text" id="business_name"
                                   class="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                                   placeholder="Your Auto Detailing Business">
                        </div>
                        
                        <button type="submit" id="submit-btn"
                                class="w-full bg-blue-600 text-white py-4 rounded-lg text-lg font-bold hover:bg-blue-700">
                            Send Me The FREE Book
                        </button>
                    </form>
                    
                    <p class="text-xs text-gray-500 mt-4">
                        100% free. No spam. Unsubscribe anytime. Written by polymath author with 8 published books.
                    </p>
                </div>
            </div>
        </div>
        
        <!-- Author Credibility -->
        <div class="mt-8 text-center">
            <h3 class="text-xl font-semibold text-gray-900 mb-4">About the Author</h3>
            <p class="text-gray-600 max-w-2xl mx-auto">
                Polymath creator with 8 published books and 100+ songs. Spent years studying successful auto detailing businesses 
                and distilled the patterns into this comprehensive guide. Now helps business owners automate their customer acquisition.
            </p>
        </div>
    </div>

    <script>
        document.getElementById('book-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const submitBtn = document.getElementById('submit-btn');
            submitBtn.disabled = true;
            submitBtn.textContent = 'Sending...';
            
            const response = await fetch('/api/book-download', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    email: document.getElementById('email').value,
                    business_name: document.getElementById('business_name').value
                })
            });
            
            const result = await response.json();
            
            if (result.success) {
                window.location.href = '/book-download-success';
            } else {
                alert(result.error);
                submitBtn.disabled = false;
                submitBtn.textContent = 'Send Me The FREE Book';
            }
        });
    </script>
</body>
</html>
"""

BOOK_SUCCESS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>FREE Book Sent! - Auto Detailing Authority</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <div class="max-w-4xl mx-auto py-20 px-4 text-center">
        <div class="bg-white rounded-xl shadow-lg p-12">
            <div class="text-6xl mb-6">📧</div>
            <h1 class="text-3xl font-bold text-green-600 mb-4">Your FREE Book is On Its Way!</h1>
            <p class="text-lg text-gray-600 mb-8">
                Check your email in the next 2-3 minutes for your copy of 
                "From $0 to Six Figures in Auto Detailing"
            </p>
            
            <div class="bg-blue-50 p-6 rounded-lg mb-8 max-w-md mx-auto">
                <h3 class="font-semibold mb-4">While you wait...</h3>
                <p class="text-sm text-gray-700 mb-4">
                    Want to see this book's strategies automated for your business? 
                    The SINCOR system puts everything on autopilot.
                </p>
                <a href="/auto-detailing-authority" 
                   class="bg-blue-600 text-white px-6 py-2 rounded-lg text-sm font-semibold hover:bg-blue-700">
                    See The Automated System
                </a>
            </div>
            
            <p class="text-sm text-gray-500">
                Questions? Email the author directly at support@sincor.com
            </p>
        </div>
    </div>
</body>
</html>
"""