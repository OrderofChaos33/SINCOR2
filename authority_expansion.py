"""
SINCOR Authority Expansion System - Phase 1
Multi-Industry Book Series & Authority Positioning

This system manages the polymath author brand across multiple industries,
allowing for industry-specific authority positioning and content delivery.
"""

from flask import render_template_string, request, jsonify
from datetime import datetime
import json

# Multi-Industry Authority Configuration
AUTHORITY_BOOKS = {
    "auto_detailing": {
        "title": "From $0 to Six Figures in the Auto Detailing Industry",
        "subtitle": "The Complete Guide to Building a Profitable Auto Detailing Business", 
        "amazon_link": "https://www.amazon.com/dp/B0DV3N8P47",
        "status": "published",
        "price": 2997,  # $29.97
        "target_audience": "auto detailing entrepreneurs and existing businesses",
        "authority_hook": "The polymath who literally wrote THE definitive book on auto detailing success",
        "credibility_proof": "Amazon bestseller with proven strategies from real six-figure detailing businesses",
        "lead_magnet_copy": "Get the same blueprint that helped dozens of detailers break six figures",
        "book_description": "This isn't just another business book. It's the complete systematic approach to building a six-figure auto detailing business, from a polymath who's cracked the code across multiple industries."
    },
    
    "hvac": {
        "title": "From $0 to Six Figures in HVAC Business",
        "subtitle": "The Complete Contractor's Guide to Systematic Growth",
        "amazon_link": "https://www.amazon.com/future-hvac-book",  # Future
        "status": "planned",
        "price": 3497,  # $34.97 (premium industry)
        "target_audience": "HVAC contractors and companies",
        "authority_hook": "The polymath who cracked the HVAC business code",
        "credibility_proof": "From the author of 8 books including the definitive auto detailing guide"
    },
    
    "pest_control": {
        "title": "From $0 to Six Figures in Pest Control", 
        "subtitle": "Building a Systematic Extermination Empire",
        "amazon_link": "https://www.amazon.com/future-pest-book",  # Future
        "status": "planned",
        "price": 2997,  # $29.97
        "target_audience": "pest control companies",
        "authority_hook": "The systems expert who conquered pest control marketing",
        "credibility_proof": "Multi-industry business author with proven track record"
    },
    
    "plumbing": {
        "title": "From $0 to Six Figures in Plumbing Business",
        "subtitle": "The Master Plumber's Guide to Business Success", 
        "amazon_link": "https://www.amazon.com/future-plumbing-book",  # Future
        "status": "planned", 
        "price": 2997,  # $29.97
        "target_audience": "plumbing contractors",
        "authority_hook": "The business architect who systematized plumbing success",
        "credibility_proof": "Proven system designer with multiple industry expertise"
    },
    
    "electrical": {
        "title": "From $0 to Six Figures in Electrical Contracting",
        "subtitle": "Wiring Your Business for Maximum Profit",
        "amazon_link": "https://www.amazon.com/future-electrical-book",  # Future
        "status": "planned",
        "price": 3197,  # $31.97 
        "target_audience": "electrical contractors",
        "authority_hook": "The polymath who electrified business growth strategies",
        "credibility_proof": "Systems thinker with cross-industry pattern recognition"
    }
}

# Polymath Brand Configuration
POLYMATH_CREDENTIALS = {
    "books_published": 8,
    "songs_created": "100+", 
    "industries_studied": ["auto detailing", "hvac", "pest control", "plumbing", "electrical", "business systems"],
    "core_expertise": "Pattern recognition across industries and systematic business growth",
    "unique_value": "Connects dots others miss through polymath thinking",
    "proof_points": [
        "8 published books across multiple domains",
        "100+ songs demonstrating creative range",
        "Multi-industry business analysis and systematization",
        "Proven ability to distill complex systems into actionable frameworks"
    ]
}

def add_authority_expansion_routes(app):
    """Add authority expansion routes for multi-industry positioning."""
    
    @app.route("/authority-hub")
    def authority_hub():
        """Central hub showcasing polymath authority across industries."""
        return render_template_string(AUTHORITY_HUB_TEMPLATE, 
                                    books=AUTHORITY_BOOKS,
                                    credentials=POLYMATH_CREDENTIALS)
    
    @app.route("/industry/<industry_id>/authority")
    def industry_authority(industry_id):
        """Industry-specific authority page."""
        if industry_id not in AUTHORITY_BOOKS:
            return "Industry not found", 404
            
        book = AUTHORITY_BOOKS[industry_id]
        return render_template_string(INDUSTRY_AUTHORITY_TEMPLATE,
                                    book=book, 
                                    industry=industry_id,
                                    credentials=POLYMATH_CREDENTIALS)
    
    @app.route("/polymath-story")
    def polymath_story():
        """The complete polymath entrepreneur story."""
        return render_template_string(POLYMATH_STORY_TEMPLATE,
                                    credentials=POLYMATH_CREDENTIALS)
    
    @app.route("/api/authority/book-series")
    def book_series_api():
        """API endpoint for book series data."""
        return jsonify({
            "books": AUTHORITY_BOOKS,
            "polymath_credentials": POLYMATH_CREDENTIALS,
            "total_industries": len(AUTHORITY_BOOKS),
            "published_books": len([b for b in AUTHORITY_BOOKS.values() if b["status"] == "published"]),
            "planned_books": len([b for b in AUTHORITY_BOOKS.values() if b["status"] == "planned"])
        })

# Templates
AUTHORITY_HUB_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>The Polymath Authority - Multi-Industry Business Expert</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <!-- Premium Header -->
    <header class="bg-black text-white py-4">
        <div class="max-w-7xl mx-auto px-4">
            <div class="flex items-center justify-center">
                <img src="/static/logo.png" alt="SINCOR" class="h-12 w-auto mr-4">
                <div class="text-center">
                    <h1 class="text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-yellow-400 to-yellow-200">SINCOR</h1>
                    <div class="text-yellow-300 text-sm">Authority & Business Intelligence</div>
                </div>
            </div>
        </div>
    </header>
    
    <!-- Hero: Polymath Positioning -->
    <section class="bg-gradient-to-r from-gray-900 via-yellow-900 to-black text-white py-20">
        <div class="max-w-6xl mx-auto px-4 text-center">
            <div class="mb-8">
                <span class="bg-gradient-to-r from-yellow-500 to-yellow-300 text-black px-6 py-3 rounded-full text-lg font-bold shadow-lg">
                    🧠 POLYMATH ENTREPRENEUR
                </span>
            </div>
            
            <h1 class="text-5xl md:text-6xl font-bold mb-6">
                The Business Pattern
                <span class="text-yellow-300">Recognition</span> Expert
            </h1>
            
            <p class="text-2xl mb-8 text-blue-100 max-w-4xl mx-auto">
                {{ credentials.books_published }} Books • {{ credentials.songs_created }} Songs • 
                Multiple Industries Systematized
            </p>
            
            <div class="grid md:grid-cols-3 gap-6 max-w-4xl mx-auto mb-12">
                <div class="bg-white bg-opacity-10 p-6 rounded-lg">
                    <div class="text-3xl font-bold text-yellow-300">{{ credentials.books_published }}</div>
                    <div class="text-blue-200">Published Books</div>
                </div>
                <div class="bg-white bg-opacity-10 p-6 rounded-lg">
                    <div class="text-3xl font-bold text-yellow-300">{{ credentials.songs_created }}</div>
                    <div class="text-blue-200">Songs Created</div>
                </div>
                <div class="bg-white bg-opacity-10 p-6 rounded-lg">
                    <div class="text-3xl font-bold text-yellow-300">{{ books|length }}</div>
                    <div class="text-blue-200">Industries Analyzed</div>
                </div>
            </div>
            
            <p class="text-xl text-blue-200 mb-8">
                "Most people see trees. Polymaths see the forest, the ecosystem, and the patterns that connect everything."
            </p>
        </div>
    </section>

    <!-- Industry Authority Grid -->
    <section class="py-16">
        <div class="max-w-7xl mx-auto px-4">
            <div class="text-center mb-12">
                <h2 class="text-3xl font-bold text-gray-900 mb-4">
                    Industry Expertise & Authority
                </h2>
                <p class="text-xl text-gray-600">
                    Systematic business growth across multiple industries
                </p>
            </div>
            
            <div class="grid md:grid-cols-2 lg:grid-cols-3 gap-8">
                {% for industry, book in books.items() %}
                <div class="bg-white rounded-xl shadow-lg p-6 hover:shadow-xl transition-shadow">
                    <div class="flex items-center justify-between mb-4">
                        <h3 class="text-xl font-bold text-gray-900 capitalize">{{ industry.replace('_', ' ') }}</h3>
                        {% if book.status == 'published' %}
                            <span class="bg-green-500 text-white px-3 py-1 rounded-full text-sm">PUBLISHED</span>
                        {% else %}
                            <span class="bg-blue-500 text-white px-3 py-1 rounded-full text-sm">COMING SOON</span>
                        {% endif %}
                    </div>
                    
                    <div class="h-40 bg-gradient-to-br from-blue-500 to-purple-600 rounded-lg mb-4 flex items-center justify-center">
                        <div class="text-white text-center">
                            <div class="text-3xl mb-2">📖</div>
                            <div class="text-sm font-bold">{{ book.title[:20] }}...</div>
                        </div>
                    </div>
                    
                    <p class="text-gray-600 mb-4 text-sm">{{ book.authority_hook }}</p>
                    
                    <div class="flex justify-between items-center">
                        {% if book.status == 'published' %}
                            <a href="{{ book.amazon_link }}" target="_blank" 
                               class="text-blue-600 hover:underline text-sm font-semibold">
                                View on Amazon →
                            </a>
                        {% else %}
                            <span class="text-gray-500 text-sm">In Development</span>
                        {% endif %}
                        
                        <a href="/industry/{{ industry }}/authority" 
                           class="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700">
                            Learn More
                        </a>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
    </section>

    <!-- Polymath Value Proposition -->
    <section class="py-16 bg-blue-900 text-white">
        <div class="max-w-4xl mx-auto px-4 text-center">
            <h2 class="text-3xl font-bold mb-8">The Polymath Advantage</h2>
            
            <div class="grid md:grid-cols-2 gap-8 mb-12">
                <div class="text-left">
                    <h3 class="text-xl font-semibold mb-4">What Others See:</h3>
                    <ul class="space-y-2 text-blue-200">
                        <li>• Individual industry problems</li>
                        <li>• Isolated solutions</li>
                        <li>• One-size-fits-all approaches</li>
                        <li>• Surface-level tactics</li>
                    </ul>
                </div>
                <div class="text-left">
                    <h3 class="text-xl font-semibold mb-4">What I See:</h3>
                    <ul class="space-y-2 text-yellow-300">
                        <li>• Cross-industry patterns</li>
                        <li>• Systematic frameworks</li>
                        <li>• Adaptable methodologies</li>
                        <li>• Deep structural insights</li>
                    </ul>
                </div>
            </div>
            
            <a href="/polymath-story" 
               class="bg-yellow-400 text-black px-8 py-4 rounded-lg text-lg font-bold hover:bg-yellow-300">
                Read My Full Story
            </a>
        </div>
    </section>
</body>
</html>
"""

INDUSTRY_AUTHORITY_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ book.title }} - Industry Authority</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <div class="max-w-4xl mx-auto py-12 px-4">
        <div class="text-center mb-8">
            <h1 class="text-4xl font-bold text-gray-900 mb-4">{{ book.title }}</h1>
            <p class="text-xl text-gray-600">{{ book.subtitle }}</p>
            
            {% if book.status == 'published' %}
                <div class="mt-6">
                    <a href="{{ book.amazon_link }}" target="_blank"
                       class="bg-orange-500 text-white px-8 py-3 rounded-lg font-bold hover:bg-orange-600">
                        Available on Amazon
                    </a>
                </div>
            {% else %}
                <div class="mt-6 bg-blue-100 p-4 rounded-lg">
                    <h3 class="font-semibold text-blue-900">Coming Soon</h3>
                    <p class="text-blue-700">This industry analysis is in development</p>
                </div>
            {% endif %}
        </div>
        
        <div class="bg-white rounded-xl shadow-lg p-8">
            <h2 class="text-2xl font-bold mb-6">Authority Positioning</h2>
            <p class="text-lg text-gray-700 mb-6">{{ book.authority_hook }}</p>
            
            <div class="border-t pt-6">
                <h3 class="text-xl font-semibold mb-4">Credibility Foundation</h3>
                <p class="text-gray-600">{{ book.credibility_proof }}</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

POLYMATH_STORY_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>The Polymath Story - Multi-Domain Expertise</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <div class="max-w-4xl mx-auto py-12 px-4">
        <div class="bg-white rounded-xl shadow-lg p-8">
            <h1 class="text-3xl font-bold text-center mb-8">The Polymath Journey</h1>
            
            <div class="prose max-w-none">
                <p class="text-lg text-gray-700 mb-6">
                    Most people specialize. Polymaths synthesize.
                </p>
                
                <h2 class="text-2xl font-bold mt-8 mb-4">The Pattern Recognition Advantage</h2>
                <p class="text-gray-700 mb-6">
                    With {{ credentials.books_published }} published books and {{ credentials.songs_created }} songs,
                    I've developed a unique ability to see patterns across seemingly unrelated domains.
                </p>
                
                <h2 class="text-2xl font-bold mt-8 mb-4">Core Expertise</h2>
                <p class="text-gray-700 mb-4">{{ credentials.core_expertise }}</p>
                
                <h2 class="text-2xl font-bold mt-8 mb-4">Unique Value</h2>
                <p class="text-gray-700 mb-4">{{ credentials.unique_value }}</p>
                
                <h2 class="text-2xl font-bold mt-8 mb-4">Proof Points</h2>
                <ul class="list-disc pl-6 mb-6">
                    {% for point in credentials.proof_points %}
                    <li class="text-gray-700 mb-2">{{ point }}</li>
                    {% endfor %}
                </ul>
            </div>
        </div>
    </div>
</body>
</html>
"""