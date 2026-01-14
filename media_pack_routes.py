"""
SINCOR Media Pack Sales System
Ready-to-use marketing content for service businesses

Industries: Auto Detailing, Pest Control, HVAC, Chiropractors, Electrical, Plumbing, Dog Grooming
"""

from flask import render_template_string, request, jsonify, redirect
import stripe
import os
from datetime import datetime
import json

# Media Pack Configuration
MEDIA_PACKS = {
    "auto_detailing": {
        "name": "Auto Detailing Marketing Pack",
        "price": 19700,  # $197
        "description": "Complete marketing system for auto detailing businesses",
        "image": "/static/images/auto-detailing-pack.jpg",
        "includes": [
            "20 Email Templates (follow-up sequences)",
            "15 Social Media Posts (before/after showcases)", 
            "10 Google/Facebook Ad Templates",
            "5 Landing Page Templates",
            "Customer Review Request Templates",
            "Pricing Calculator Template",
            "Seasonal Campaign Templates"
        ],
        "fields": ["business_name", "location", "phone", "website", "specialties", "price_range"]
    },
    
    "pest_control": {
        "name": "Pest Control Marketing Pack", 
        "price": 19700,  # $197
        "description": "Comprehensive marketing toolkit for pest control companies",
        "image": "/static/images/pest-control-pack.jpg", 
        "includes": [
            "25 Email Templates (seasonal pest alerts)",
            "20 Social Media Posts (pest education)",
            "12 Google/Facebook Ad Templates", 
            "6 Landing Page Templates",
            "Emergency Service Templates",
            "Maintenance Plan Templates",
            "Before/After Photo Templates"
        ],
        "fields": ["business_name", "location", "phone", "website", "service_areas", "emergency_number"]
    },
    
    "hvac": {
        "name": "HVAC Marketing Pack",
        "price": 24700,  # $247 (premium industry)
        "description": "Professional marketing system for HVAC contractors",
        "image": "/static/images/hvac-pack.jpg",
        "includes": [
            "30 Email Templates (seasonal maintenance)",
            "25 Social Media Posts (energy saving tips)",
            "15 Google/Facebook Ad Templates",
            "8 Landing Page Templates", 
            "Emergency Service Templates",
            "Financing Option Templates",
            "Warranty/Guarantee Templates"
        ],
        "fields": ["business_name", "location", "phone", "website", "service_areas", "certifications", "financing_options"]
    },
    
    "chiropractor": {
        "name": "Chiropractic Marketing Pack",
        "price": 22700,  # $227
        "description": "Complete marketing solution for chiropractic practices", 
        "image": "/static/images/chiro-pack.jpg",
        "includes": [
            "25 Email Templates (patient education)",
            "20 Social Media Posts (wellness tips)",
            "12 Google/Facebook Ad Templates",
            "6 Landing Page Templates",
            "New Patient Welcome Series",
            "Appointment Reminder Templates", 
            "Testimonial Collection Templates"
        ],
        "fields": ["practice_name", "doctor_name", "location", "phone", "website", "specialties", "insurance_accepted"]
    },
    
    "electrical": {
        "name": "Electrical Contractor Pack",
        "price": 21700,  # $217
        "description": "Marketing toolkit for electrical contractors",
        "image": "/static/images/electrical-pack.jpg", 
        "includes": [
            "22 Email Templates (safety tips)",
            "18 Social Media Posts (electrical education)",
            "12 Google/Facebook Ad Templates",
            "6 Landing Page Templates",
            "Emergency Service Templates",
            "Commercial/Residential Templates",
            "Safety Inspection Templates"
        ],
        "fields": ["business_name", "location", "phone", "website", "license_number", "service_areas", "specialties"]
    },
    
    "plumbing": {
        "name": "Plumbing Marketing Pack",
        "price": 19700,  # $197
        "description": "Complete marketing system for plumbing businesses",
        "image": "/static/images/plumbing-pack.jpg",
        "includes": [
            "25 Email Templates (maintenance tips)",
            "20 Social Media Posts (DIY vs Pro)",
            "15 Google/Facebook Ad Templates", 
            "7 Landing Page Templates",
            "Emergency Service Templates",
            "Seasonal Campaign Templates",
            "Water Heater/Drain Cleaning Templates"
        ],
        "fields": ["business_name", "location", "phone", "website", "service_areas", "emergency_number", "specialties"]
    },
    
    "dog_grooming": {
        "name": "Dog Grooming Marketing Pack",
        "price": 17700,  # $177
        "description": "Marketing toolkit for dog grooming businesses",
        "image": "/static/images/dog-grooming-pack.jpg",
        "includes": [
            "20 Email Templates (pet care tips)",
            "25 Social Media Posts (cute before/afters)",
            "12 Google/Facebook Ad Templates",
            "5 Landing Page Templates", 
            "Appointment Reminder Templates",
            "Seasonal Grooming Templates",
            "New Pet Owner Templates"
        ],
        "fields": ["business_name", "location", "phone", "website", "services_offered", "breed_specialties"]
    }
}

def add_media_pack_routes(app):
    """Add media pack routes to Flask app."""
    
    @app.route("/media-packs")
    def media_packs_landing():
        """Main media packs landing page."""
        return render_template_string(MEDIA_PACKS_TEMPLATE, packs=MEDIA_PACKS)
    
    @app.route("/media-pack/<pack_id>")
    def media_pack_detail(pack_id):
        """Individual media pack detail page."""
        if pack_id not in MEDIA_PACKS:
            return "Pack not found", 404
        
        pack = MEDIA_PACKS[pack_id]
        return render_template_string(MEDIA_PACK_DETAIL_TEMPLATE, pack=pack, pack_id=pack_id)
    
    @app.route("/media-pack/<pack_id>/customize")
    def customize_media_pack(pack_id):
        """Customization form for media pack."""
        if pack_id not in MEDIA_PACKS:
            return "Pack not found", 404
        
        pack = MEDIA_PACKS[pack_id] 
        return render_template_string(CUSTOMIZE_TEMPLATE, pack=pack, pack_id=pack_id)
    
    @app.route("/api/media-pack/purchase", methods=["POST"])
    def purchase_media_pack():
        """Process media pack purchase."""
        try:
            data = request.get_json()
            pack_id = data.get('pack_id')
            customization = data.get('customization', {})
            
            if pack_id not in MEDIA_PACKS:
                return jsonify({"success": False, "error": "Invalid pack"})
            
            pack = MEDIA_PACKS[pack_id]
            
            # Create Stripe payment intent
            stripe.api_key = os.environ.get("stripe", "")
            
            intent = stripe.PaymentIntent.create(
                amount=pack['price'],
                currency='usd',
                metadata={
                    'pack_id': pack_id,
                    'pack_name': pack['name'],
                    'customization': json.dumps(customization)
                }
            )
            
            return jsonify({
                "success": True,
                "client_secret": intent.client_secret,
                "pack_name": pack['name'],
                "amount": pack['price']
            })
            
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    
    @app.route("/media-pack/success")
    def media_pack_success():
        """Media pack purchase success page."""
        return render_template_string(SUCCESS_TEMPLATE)

# Templates
MEDIA_PACKS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR Media Packs - Ready-to-Use Marketing Content</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <!-- Header -->
    <header class="bg-white shadow-sm">
        <div class="max-w-7xl mx-auto px-4 py-4">
            <div class="flex justify-between items-center">
                <div class="flex items-center">
                    <a href="/" class="text-2xl font-bold text-blue-600">SINCOR</a>
                    <span class="ml-2 text-sm text-gray-500">Media Packs</span>
                </div>
                <a href="/" class="text-blue-600 hover:text-blue-700">← Back to SINCOR</a>
            </div>
        </div>
    </header>

    <!-- Hero -->
    <section class="bg-gradient-to-r from-purple-600 to-blue-600 text-white py-16">
        <div class="max-w-4xl mx-auto px-4 text-center">
            <h1 class="text-4xl md:text-5xl font-bold mb-6">
                Ready-to-Use Marketing Content
            </h1>
            <p class="text-xl mb-8">
                Professional marketing materials customized for your business. 
                No design skills needed - just add your details and start marketing!
            </p>
            <div class="bg-white bg-opacity-20 p-4 rounded-lg inline-block">
                <p class="text-lg font-semibold">⚡ Instant Download • 🎯 Industry-Specific • 💰 Money-Back Guarantee</p>
            </div>
        </div>
    </section>

    <!-- Media Packs Grid -->
    <section class="py-16">
        <div class="max-w-7xl mx-auto px-4">
            <div class="grid md:grid-cols-2 lg:grid-cols-3 gap-8">
                {% for pack_id, pack in packs.items() %}
                <div class="bg-white rounded-xl shadow-lg overflow-hidden hover:shadow-xl transition-shadow">
                    <div class="h-48 bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center">
                        <div class="text-white text-center">
                            <div class="text-4xl mb-2">
                                {% if pack_id == 'auto_detailing' %}🚗
                                {% elif pack_id == 'pest_control' %}🐛  
                                {% elif pack_id == 'hvac' %}🏠
                                {% elif pack_id == 'chiropractor' %}💆
                                {% elif pack_id == 'electrical' %}⚡
                                {% elif pack_id == 'plumbing' %}🔧
                                {% elif pack_id == 'dog_grooming' %}🐕
                                {% endif %}
                            </div>
                            <h3 class="text-xl font-bold">{{ pack.name }}</h3>
                        </div>
                    </div>
                    
                    <div class="p-6">
                        <p class="text-gray-600 mb-4">{{ pack.description }}</p>
                        
                        <div class="mb-4">
                            <div class="text-2xl font-bold text-blue-600">${{ "%.0f"|format(pack.price/100) }}</div>
                            <div class="text-sm text-gray-500">One-time purchase</div>
                        </div>
                        
                        <div class="mb-6">
                            <h4 class="font-semibold mb-2">Includes:</h4>
                            <ul class="text-sm text-gray-600 space-y-1">
                                {% for item in pack.includes[:3] %}
                                <li class="flex items-center">
                                    <span class="text-green-500 mr-2">✓</span>{{ item }}
                                </li>
                                {% endfor %}
                                <li class="text-blue-600">+ {{ pack.includes|length - 3 }} more items...</li>
                            </ul>
                        </div>
                        
                        <a href="/media-pack/{{ pack_id }}" 
                           class="block w-full bg-blue-600 text-white text-center py-3 rounded-lg hover:bg-blue-700 font-semibold">
                            View Details & Buy
                        </a>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
    </section>

    <!-- CTA Section -->
    <section class="py-16 bg-gray-900 text-white">
        <div class="max-w-4xl mx-auto px-4 text-center">
            <h2 class="text-3xl font-bold mb-4">Need Automated Lead Generation Too?</h2>
            <p class="text-xl text-gray-300 mb-8">
                Combine these media packs with SINCOR's automated lead generation system
            </p>
            <a href="/#pricing" class="bg-blue-600 text-white px-8 py-3 rounded-lg text-lg font-semibold hover:bg-blue-700">
                View SINCOR Plans
            </a>
        </div>
    </section>
</body>
</html>
"""

MEDIA_PACK_DETAIL_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ pack.name }} - SINCOR Media Packs</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <!-- Header -->
    <header class="bg-white shadow-sm">
        <div class="max-w-7xl mx-auto px-4 py-4">
            <div class="flex justify-between items-center">
                <div class="flex items-center">
                    <a href="/" class="text-2xl font-bold text-blue-600">SINCOR</a>
                    <span class="ml-2 text-sm text-gray-500">Media Packs</span>
                </div>
                <a href="/media-packs" class="text-blue-600 hover:text-blue-700">← Back to Media Packs</a>
            </div>
        </div>
    </header>

    <div class="max-w-6xl mx-auto py-12 px-4">
        <div class="grid lg:grid-cols-2 gap-12">
            <!-- Pack Details -->
            <div>
                <div class="bg-gradient-to-br from-blue-500 to-purple-600 text-white p-12 rounded-xl text-center mb-8">
                    <div class="text-6xl mb-4">
                        {% if pack_id == 'auto_detailing' %}🚗
                        {% elif pack_id == 'pest_control' %}🐛  
                        {% elif pack_id == 'hvac' %}🏠
                        {% elif pack_id == 'chiropractor' %}💆
                        {% elif pack_id == 'electrical' %}⚡
                        {% elif pack_id == 'plumbing' %}🔧
                        {% elif pack_id == 'dog_grooming' %}🐕
                        {% endif %}
                    </div>
                    <h1 class="text-3xl font-bold">{{ pack.name }}</h1>
                </div>
                
                <div class="bg-white p-8 rounded-xl shadow-lg">
                    <h2 class="text-2xl font-bold mb-4">What's Included:</h2>
                    <ul class="space-y-3">
                        {% for item in pack.includes %}
                        <li class="flex items-start">
                            <span class="text-green-500 mr-3 mt-1">✓</span>
                            <span>{{ item }}</span>
                        </li>
                        {% endfor %}
                    </ul>
                </div>
            </div>
            
            <!-- Purchase Section -->
            <div>
                <div class="bg-white p-8 rounded-xl shadow-lg sticky top-8">
                    <div class="text-center mb-6">
                        <div class="text-4xl font-bold text-blue-600 mb-2">${{ "%.0f"|format(pack.price/100) }}</div>
                        <div class="text-gray-600">One-time purchase • Instant download</div>
                        <div class="text-sm text-green-600 font-semibold">30-day money-back guarantee</div>
                    </div>
                    
                    <div class="space-y-4 mb-6">
                        <div class="flex items-center text-sm text-gray-600">
                            <span class="text-green-500 mr-2">✓</span>
                            Customized with your business details
                        </div>
                        <div class="flex items-center text-sm text-gray-600">
                            <span class="text-green-500 mr-2">✓</span>
                            Editable templates (Word, Canva, etc.)
                        </div>
                        <div class="flex items-center text-sm text-gray-600">
                            <span class="text-green-500 mr-2">✓</span>
                            Commercial use license included
                        </div>
                        <div class="flex items-center text-sm text-gray-600">
                            <span class="text-green-500 mr-2">✓</span>
                            Instant email delivery
                        </div>
                    </div>
                    
                    <a href="/media-pack/{{ pack_id }}/customize" 
                       class="block w-full bg-blue-600 text-white text-center py-4 rounded-lg text-lg font-bold hover:bg-blue-700 mb-4">
                        Customize & Purchase
                    </a>
                    
                    <p class="text-xs text-gray-500 text-center">
                        Secure checkout powered by Stripe • SSL encrypted
                    </p>
                </div>
                
                <!-- Testimonial -->
                <div class="bg-blue-50 p-6 rounded-xl mt-8">
                    <div class="flex items-start">
                        <div class="text-2xl mr-4">💬</div>
                        <div>
                            <p class="text-gray-700 italic mb-2">
                                "These templates saved me weeks of work and thousands in design costs. 
                                My {{ pack_id.replace('_', ' ').title() }} business is booked solid!"
                            </p>
                            <p class="text-sm text-gray-600">- Sarah M., Business Owner</p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

CUSTOMIZE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Customize {{ pack.name }} - SINCOR</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <script src="https://js.stripe.com/v3/"></script>
</head>
<body class="bg-gray-50">
    <!-- Header -->
    <header class="bg-white shadow-sm">
        <div class="max-w-7xl mx-auto px-4 py-4">
            <div class="flex justify-between items-center">
                <div class="flex items-center">
                    <a href="/" class="text-2xl font-bold text-blue-600">SINCOR</a>
                    <span class="ml-2 text-sm text-gray-500">Customize Pack</span>
                </div>
                <a href="/media-pack/{{ pack_id }}" class="text-blue-600 hover:text-blue-700">← Back</a>
            </div>
        </div>
    </header>

    <div class="max-w-4xl mx-auto py-12 px-4">
        <div class="text-center mb-8">
            <h1 class="text-3xl font-bold text-gray-900 mb-2">Customize Your {{ pack.name }}</h1>
            <p class="text-gray-600">Enter your business details to personalize all templates</p>
        </div>

        <div class="bg-white rounded-xl shadow-lg p-8">
            <form id="customize-form">
                <div class="grid md:grid-cols-2 gap-6">
                    {% for field in pack.fields %}
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2 capitalize">
                            {{ field.replace('_', ' ') }}
                        </label>
                        <input type="text" 
                               name="{{ field }}" 
                               required
                               class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                               {% if field == 'business_name' %}placeholder="Your Business Name"
                               {% elif field == 'location' %}placeholder="City, State"
                               {% elif field == 'phone' %}placeholder="(555) 123-4567"
                               {% elif field == 'website' %}placeholder="www.yourbusiness.com"
                               {% elif field == 'email' %}placeholder="info@yourbusiness.com"
                               {% endif %}>
                    </div>
                    {% endfor %}
                </div>
                
                <!-- Payment Section -->
                <div class="mt-8 pt-8 border-t">
                    <h3 class="text-xl font-semibold mb-4">Payment Information</h3>
                    
                    <div class="mb-4">
                        <label class="block text-sm font-medium text-gray-700 mb-2">Email for Receipt & Download</label>
                        <input type="email" id="email" required 
                               class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500">
                    </div>
                    
                    <div class="mb-6">
                        <label class="block text-sm font-medium text-gray-700 mb-2">Card Information</label>
                        <div id="card-element" class="p-3 border border-gray-300 rounded-lg bg-white">
                            <!-- Stripe Elements will create form elements here -->
                        </div>
                        <div id="card-errors" role="alert" class="text-red-600 text-sm mt-2"></div>
                    </div>
                    
                    <div class="flex items-center justify-between">
                        <div>
                            <div class="text-2xl font-bold text-blue-600">${{ "%.0f"|format(pack.price/100) }}</div>
                            <div class="text-sm text-gray-600">One-time purchase</div>
                        </div>
                        
                        <button type="submit" id="submit-button" 
                                class="bg-blue-600 text-white px-8 py-3 rounded-lg font-bold hover:bg-blue-700 disabled:opacity-50">
                            Complete Purchase
                        </button>
                    </div>
                </div>
            </form>
        </div>
        
        <!-- Security -->
        <div class="text-center mt-8">
            <div class="flex items-center justify-center space-x-4 text-sm text-gray-500">
                <span>🔒 SSL Encrypted</span>
                <span>💳 Stripe Secure</span>
                <span>💰 30-Day Guarantee</span>
            </div>
        </div>
    </div>

    <script>
        const stripe = Stripe('{{ stripe_key }}' || 'pk_test_demo');
        const elements = stripe.elements();
        
        const cardElement = elements.create('card');
        cardElement.mount('#card-element');
        
        const form = document.getElementById('customize-form');
        const submitButton = document.getElementById('submit-button');
        
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            submitButton.disabled = true;
            submitButton.textContent = 'Processing...';
            
            // Collect customization data
            const formData = new FormData(form);
            const customization = {};
            for (let [key, value] of formData.entries()) {
                customization[key] = value;
            }
            
            // Create payment intent
            const response = await fetch('/api/media-pack/purchase', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    pack_id: '{{ pack_id }}',
                    customization: customization
                })
            });
            
            const result = await response.json();
            
            if (result.success) {
                // Confirm payment
                const {error} = await stripe.confirmCardPayment(result.client_secret, {
                    payment_method: {
                        card: cardElement,
                        billing_details: {
                            email: document.getElementById('email').value,
                        },
                    }
                });
                
                if (error) {
                    document.getElementById('card-errors').textContent = error.message;
                    submitButton.disabled = false;
                    submitButton.textContent = 'Complete Purchase';
                } else {
                    window.location.href = '/media-pack/success?pack={{ pack_id }}';
                }
            } else {
                document.getElementById('card-errors').textContent = result.error;
                submitButton.disabled = false;
                submitButton.textContent = 'Complete Purchase';
            }
        });
        
        cardElement.on('change', ({error}) => {
            const displayError = document.getElementById('card-errors');
            if (error) {
                displayError.textContent = error.message;
            } else {
                displayError.textContent = '';
            }
        });
    </script>
</body>
</html>
"""

SUCCESS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Purchase Complete - SINCOR Media Packs</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <div class="max-w-4xl mx-auto py-20 px-4 text-center">
        <div class="bg-white rounded-xl shadow-lg p-12">
            <div class="text-6xl mb-6">🎉</div>
            <h1 class="text-3xl font-bold text-green-600 mb-4">Purchase Complete!</h1>
            <p class="text-lg text-gray-600 mb-8">
                Your customized media pack has been sent to your email. 
                Check your inbox for download links and instructions.
            </p>
            
            <div class="bg-blue-50 p-6 rounded-lg mb-8">
                <h2 class="font-semibold mb-4">What happens next?</h2>
                <div class="text-left space-y-2 max-w-md mx-auto">
                    <div class="flex items-center">
                        <span class="text-blue-600 mr-3">1.</span>
                        Check your email for download links (within 5 minutes)
                    </div>
                    <div class="flex items-center">
                        <span class="text-blue-600 mr-3">2.</span>
                        Download your customized templates
                    </div>
                    <div class="flex items-center">
                        <span class="text-blue-600 mr-3">3.</span>
                        Start marketing your business with professional content!
                    </div>
                </div>
            </div>
            
            <div class="space-x-4">
                <a href="/media-packs" class="bg-blue-600 text-white px-6 py-3 rounded-lg hover:bg-blue-700">
                    Browse More Packs
                </a>
                <a href="/#pricing" class="border border-blue-600 text-blue-600 px-6 py-3 rounded-lg hover:bg-blue-50">
                    Try SINCOR Lead Gen
                </a>
            </div>
            
            <p class="text-sm text-gray-500 mt-8">
                Questions? Email support@sincor.com
            </p>
        </div>
    </div>
</body>
</html>
"""