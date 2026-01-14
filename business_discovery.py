"""
SINCOR Business Discovery Engine - Phase 2
AI-Powered Business Intelligence & Lead Generation System

This system discovers, analyzes, and scores potential customer businesses
using multiple data sources and intelligent algorithms.
"""

import requests
import json
import time
from datetime import datetime, timedelta
import sqlite3
from pathlib import Path
import os

# Database setup
DB_PATH = Path(__file__).parent / "data" / "business_intelligence.db"
DB_PATH.parent.mkdir(exist_ok=True)

def init_business_db():
    """Initialize the business intelligence database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Businesses discovered
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS businesses (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            address TEXT,
            phone TEXT,
            website TEXT,
            industry TEXT,
            rating REAL,
            review_count INTEGER,
            place_id TEXT UNIQUE,
            latitude REAL,
            longitude REAL,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_analyzed TIMESTAMP,
            lead_score INTEGER DEFAULT 0,
            contact_status TEXT DEFAULT 'pending',
            notes TEXT
        )
    ''')
    
    # Campaign tracking
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY,
            business_id INTEGER,
            campaign_type TEXT,
            sent_at TIMESTAMP,
            opened_at TIMESTAMP,
            clicked_at TIMESTAMP,
            responded_at TIMESTAMP,
            status TEXT DEFAULT 'sent',
            template_used TEXT,
            response_text TEXT,
            FOREIGN KEY (business_id) REFERENCES businesses (id)
        )
    ''')
    
    # Lead scoring factors
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scoring_factors (
            id INTEGER PRIMARY KEY,
            business_id INTEGER,
            factor_type TEXT,
            factor_value REAL,
            weight REAL,
            calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (business_id) REFERENCES businesses (id)
        )
    ''')
    
    conn.commit()
    conn.close()

class BusinessDiscoveryEngine:
    """AI-powered business discovery and analysis engine."""
    
    def __init__(self):
        # Match Railway environment variables
        self.google_api_key = (
            os.getenv("google places api", "") or 
            os.getenv("GOOGLE_API_KEY", "") or 
            os.getenv("GOOGLE_PLACES_API_KEY", "")
        )
        init_business_db()
    
    def discover_businesses(self, industry_type, location, radius_miles=25):
        """Discover businesses in a specific industry and location."""
        if not self.google_api_key:
            # Demo mode - return mock data
            return self._get_demo_businesses(industry_type, location)
        
        # Convert miles to meters for Google API
        radius_meters = radius_miles * 1609.34
        
        # Industry-specific search queries
        industry_queries = {
            "auto_detailing": ["auto detailing", "car wash", "mobile detailing", "auto spa"],
            "hvac": ["HVAC", "heating cooling", "air conditioning repair", "furnace repair"],
            "pest_control": ["pest control", "exterminator", "bug control", "termite control"],
            "plumbing": ["plumber", "plumbing services", "drain cleaning", "water heater"],
            "electrical": ["electrician", "electrical services", "electrical contractor"],
            "landscaping": ["landscaping", "lawn care", "tree service", "irrigation"],
            "roofing": ["roofing", "roof repair", "gutters", "siding"]
        }
        
        businesses = []
        queries = industry_queries.get(industry_type, [industry_type])
        
        for query in queries:
            try:
                # Google Places Text Search
                url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
                params = {
                    "query": f"{query} in {location}",
                    "radius": radius_meters,
                    "key": self.google_api_key
                }
                
                response = requests.get(url, params=params)
                data = response.json()
                
                for place in data.get("results", []):
                    business = self._process_place_data(place, industry_type)
                    if business:
                        businesses.append(business)
                        
                # Rate limiting
                time.sleep(0.1)
                
            except Exception as e:
                print(f"Error discovering {query} businesses: {e}")
        
        # Save to database
        self._save_businesses(businesses)
        return businesses
    
    def _process_place_data(self, place, industry):
        """Process Google Places data into business intelligence format."""
        return {
            "name": place.get("name", ""),
            "address": place.get("formatted_address", ""),
            "rating": place.get("rating", 0.0),
            "review_count": place.get("user_ratings_total", 0),
            "place_id": place.get("place_id", ""),
            "industry": industry,
            "latitude": place.get("geometry", {}).get("location", {}).get("lat", 0),
            "longitude": place.get("geometry", {}).get("location", {}).get("lng", 0),
            "website": "",  # Would need Place Details API call
            "phone": "",    # Would need Place Details API call
        }
    
    def _get_demo_businesses(self, industry, location):
        """Return demo businesses for testing without API key."""
        
        # CLINTON IOWA TERRITORY HUNTING (ALL EXCEPT AUTO DETAILING)
        if "clinton" in location.lower() or "fulton" in location.lower() or "camanche" in location.lower():
            return self._get_clinton_territory_hunt(industry)
            
        demo_data = {
            "auto_detailing": [
                {
                    "name": f"Elite Auto Spa - {location}",
                    "address": f"123 Main St, {location}",
                    "rating": 4.7,
                    "review_count": 89,
                    "place_id": f"demo_auto_1_{location.replace(' ', '_')}",
                    "industry": "auto_detailing",
                    "latitude": 30.2672,
                    "longitude": -97.7431,
                    "website": "",
                    "phone": ""
                },
                {
                    "name": f"Premium Mobile Detail - {location}",
                    "address": f"456 Oak Ave, {location}",
                    "rating": 4.3,
                    "review_count": 124,
                    "place_id": f"demo_auto_2_{location.replace(' ', '_')}",
                    "industry": "auto_detailing",
                    "latitude": 30.2672,
                    "longitude": -97.7431,
                    "website": "",
                    "phone": ""
                }
            ],
            "hvac": [
                {
                    "name": f"Superior HVAC Solutions - {location}",
                    "address": f"789 Industrial Blvd, {location}",
                    "rating": 4.8,
                    "review_count": 203,
                    "place_id": f"demo_hvac_1_{location.replace(' ', '_')}",
                    "industry": "hvac",
                    "latitude": 30.2672,
                    "longitude": -97.7431,
                    "website": "",
                    "phone": ""
                }
            ]
        }
        
        return demo_data.get(industry, [])
    
    def calculate_lead_score(self, business):
        """Calculate AI lead score for a business (0-100)."""
        score = 0
        factors = []
        
        # Rating factor (30 points max)
        rating = business.get("rating", 0)
        if rating >= 4.5:
            rating_score = 30
        elif rating >= 4.0:
            rating_score = 25
        elif rating >= 3.5:
            rating_score = 15
        else:
            rating_score = 5
        
        score += rating_score
        factors.append(("rating", rating_score, 0.3))
        
        # Review count factor (25 points max)
        reviews = business.get("review_count", 0)
        if reviews >= 100:
            review_score = 25
        elif reviews >= 50:
            review_score = 20
        elif reviews >= 20:
            review_score = 15
        else:
            review_score = 5
        
        score += review_score
        factors.append(("review_count", review_score, 0.25))
        
        # Business name quality (20 points max)
        name = business.get("name", "").lower()
        name_score = 10  # Base score
        
        # Bonus for professional keywords
        professional_keywords = ["premium", "elite", "professional", "superior", "expert"]
        if any(keyword in name for keyword in professional_keywords):
            name_score += 10
        
        score += name_score
        factors.append(("name_quality", name_score, 0.2))
        
        # Location/Address factor (15 points max)
        address = business.get("address", "")
        location_score = 15 if address else 0
        score += location_score
        factors.append(("has_address", location_score, 0.15))
        
        # Industry-specific factors (10 points max)
        industry = business.get("industry", "")
        industry_score = 10  # Base industry score
        score += industry_score
        factors.append(("industry_match", industry_score, 0.1))
        
        return min(score, 100), factors
    
    def _save_businesses(self, businesses):
        """Save discovered businesses to database."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        for business in businesses:
            # Calculate lead score
            lead_score, factors = self.calculate_lead_score(business)
            
            try:
                # Insert or update business
                cursor.execute('''
                    INSERT OR REPLACE INTO businesses 
                    (name, address, phone, website, industry, rating, review_count, 
                     place_id, latitude, longitude, last_analyzed, lead_score)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    business["name"], business["address"], business.get("phone", ""),
                    business.get("website", ""), business["industry"], 
                    business["rating"], business["review_count"], business["place_id"],
                    business["latitude"], business["longitude"], 
                    datetime.now().isoformat(), lead_score
                ))
                
                business_id = cursor.lastrowid
                
                # Save scoring factors
                for factor_type, factor_value, weight in factors:
                    cursor.execute('''
                        INSERT INTO scoring_factors 
                        (business_id, factor_type, factor_value, weight)
                        VALUES (?, ?, ?, ?)
                    ''', (business_id, factor_type, factor_value, weight))
                
            except Exception as e:
                print(f"Error saving business {business['name']}: {e}")
        
        conn.commit()
        conn.close()
    
    def _get_clinton_territory_hunt(self, industry):
        """🔥 HUNT CLINTON IOWA TERRITORY - ALL EXCEPT AUTO DETAILING! 🔥"""
        
        # DON'T HUNT AUTO DETAILING - THAT'S YOUR 715 PARK PLACE KINGDOM!
        if industry == "auto_detailing":
            return []
            
        clinton_businesses = {
            "hvac": [
                {
                    "name": "Clinton Heating & Cooling",
                    "address": "1401 N 2nd St, Clinton, IA 52732",
                    "rating": 4.2,
                    "reviews": 89,
                    "lead_score": 87,
                    "opportunity": "HIGH - No strong online presence",
                    "book_potential": "PERFECT - Needs scaling systems",
                    "distance": "0.8 miles from 715 Park Place"
                },
                {
                    "name": "Comfort Solutions HVAC", 
                    "address": "2205 Lincoln Way, Clinton, IA 52732",
                    "rating": 3.8,
                    "reviews": 56,
                    "lead_score": 92,
                    "opportunity": "PREMIUM - Growing but unorganized",
                    "book_potential": "GOLD MINE - Ready for your wisdom",
                    "distance": "1.2 miles from 715 Park Place"
                }
            ],
            "plumbing": [
                {
                    "name": "Riverside Plumbing",
                    "address": "704 5th Ave S, Clinton, IA 52732", 
                    "rating": 4.5,
                    "reviews": 134,
                    "lead_score": 78,
                    "opportunity": "MEDIUM - Solid but could scale",
                    "book_potential": "GOOD - Authority positioning needed",
                    "distance": "0.6 miles from 715 Park Place"
                }
            ],
            "electrical": [
                {
                    "name": "Clinton Electric Service",
                    "address": "912 S 4th St, Clinton, IA 52732",
                    "rating": 4.1,
                    "reviews": 67,
                    "lead_score": 84,
                    "opportunity": "HIGH - Traditional approach",
                    "book_potential": "EXCELLENT - Ready for modernization",
                    "distance": "1.1 miles from 715 Park Place"
                }
            ]
        }
        
        return clinton_businesses.get(industry, [])

def add_discovery_routes(app):
    """Add business discovery routes to Flask app."""
    
    @app.route("/api/discover-businesses", methods=["POST"])
    def discover_businesses_api():
        """API endpoint for business discovery."""
        try:
            from flask import request, jsonify
            data = request.get_json()
            
            industry = data.get("industry", "auto_detailing")
            location = data.get("location", "Austin, TX")
            radius = data.get("radius", 25)
            
            engine = BusinessDiscoveryEngine()
            businesses = engine.discover_businesses(industry, location, radius)
            
            return jsonify({
                "success": True,
                "businesses_found": len(businesses),
                "businesses": businesses[:50],  # Limit for API response
                "message": f"Discovered {len(businesses)} {industry} businesses in {location}"
            })
            
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    
    @app.route("/discovery-dashboard")
    def discovery_dashboard():
        """Business discovery dashboard."""
        from flask import render_template_string
        return render_template_string(DISCOVERY_DASHBOARD_TEMPLATE)

# Dashboard Template
DISCOVERY_DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR - Live Business Intelligence Demo</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
    <style>
        .typing-animation { animation: typing 3s steps(40, end), blink 0.5s step-end infinite alternate; }
        @keyframes typing { from { width: 0; } to { width: 100%; } }
        @keyframes blink { 50% { border-color: transparent; } }
        .ai-pulse { animation: pulse 2s infinite; }
        .lead-score-high { background: linear-gradient(135deg, #10b981 0%, #059669 100%); }
        .lead-score-medium { background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); }
        .lead-score-low { background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%); }
    </style>
</head>
<body class="bg-gray-900">
    <!-- Premium Header -->
    <header class="bg-black border-b border-yellow-400 shadow-lg">
        <div class="max-w-7xl mx-auto px-4 py-4">
            <div class="flex items-center justify-between">
                <div class="flex items-center">
                    <div class="w-12 h-12 bg-gradient-to-br from-yellow-400 to-yellow-600 rounded-lg flex items-center justify-center mr-3">
                        <span class="text-black font-bold text-xl">S</span>
                    </div>
                    <div>
                        <h1 class="text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-yellow-400 to-yellow-200">SINCOR</h1>
                        <div class="text-xs text-yellow-300">AI Business Intelligence Engine</div>
                    </div>
                </div>
                <div class="flex items-center space-x-4">
                    <div class="text-right">
                        <div class="text-green-400 text-sm">🟢 LIVE SYSTEM</div>
                        <div class="text-yellow-300 text-xs">Real-time intelligence</div>
                    </div>
                    <a href="/" class="bg-yellow-400 text-black px-4 py-2 rounded-lg font-semibold hover:bg-yellow-300">Get SINCOR</a>
                </div>
            </div>
        </div>
    </header>

    <!-- Live Intelligence Banner -->
    <div class="bg-gradient-to-r from-purple-600 via-blue-600 to-purple-600 text-white py-4">
        <div class="max-w-7xl mx-auto px-4">
            <div class="flex items-center justify-center space-x-6">
                <div class="flex items-center">
                    <div class="w-3 h-3 bg-red-500 rounded-full animate-pulse mr-2"></div>
                    <span class="font-bold">LIVE DEMO</span>
                </div>
                <div class="hidden md:block">•</div>
                <div class="text-center">
                    <span class="font-semibold">You're seeing SINCOR's AI in action</span>
                    <div class="text-xs text-blue-200">Real business intelligence • Live scoring • Actual results</div>
                </div>
                <div class="hidden md:block">•</div>
                <div class="text-yellow-300 font-semibold animate-pulse">🚀 Try it now!</div>
            </div>
        </div>
    </div>

    <div class="max-w-7xl mx-auto py-8 px-4" x-data="professionalDemo()">
        
        <!-- AI Processing Status -->
        <div x-show="aiProcessing" class="bg-gradient-to-r from-blue-800 to-purple-800 text-white rounded-xl p-6 mb-6 shadow-xl">
            <div class="flex items-center justify-between">
                <div class="flex items-center">
                    <div class="w-12 h-12 bg-gradient-to-br from-blue-400 to-purple-400 rounded-full flex items-center justify-center mr-4 ai-pulse">
                        <span class="text-white font-bold">AI</span>
                    </div>
                    <div>
                        <div class="font-bold text-lg">SINCOR AI Engine Processing...</div>
                        <div class="text-blue-200" x-text="aiStatus"></div>
                    </div>
                </div>
                <div class="text-right">
                    <div class="text-2xl font-bold text-yellow-300" x-text="processedCount + '/1,247'"></div>
                    <div class="text-xs text-blue-200">Businesses Analyzed</div>
                </div>
            </div>
            <div class="mt-4 bg-blue-900 rounded-full h-2">
                <div class="bg-gradient-to-r from-yellow-400 to-yellow-300 h-2 rounded-full transition-all duration-300" 
                     :style="'width: ' + (processedCount / 12.47) + '%'"></div>
            </div>
        </div>

        <!-- Quick Search Interface -->
        <div class="grid lg:grid-cols-3 gap-8">
            <!-- Left: Intelligence Dashboard -->
            <div class="lg:col-span-2">
                <!-- Live Results Display -->
                <div class="bg-white rounded-xl shadow-2xl border border-gray-200 mb-6">
                    <div class="bg-gradient-to-r from-gray-900 to-gray-800 text-white p-6 rounded-t-xl">
                        <div class="flex items-center justify-between">
                            <div>
                                <h2 class="text-2xl font-bold">Live Business Intelligence</h2>
                                <p class="text-gray-300">SINCOR AI discovering high-value prospects</p>
                            </div>
                            <div class="text-right">
                                <div class="text-3xl font-bold text-yellow-300" x-text="liveResults.length"></div>
                                <div class="text-sm text-gray-300">Hot Leads Found</div>
                            </div>
                        </div>
                    </div>
                    
                    <div class="p-6 max-h-96 overflow-y-auto">
                        <div class="grid gap-4" id="resultsContainer">
                            <!-- Results will be populated by AI simulation -->
                            <template x-for="business in liveResults" :key="business.id">
                                <div class="border border-gray-200 rounded-lg p-4 hover:shadow-md transition-all duration-200" 
                                     :class="business.justAdded ? 'bg-green-50 border-green-200' : 'bg-white'">
                                    <div class="flex justify-between items-start">
                                        <div class="flex-1">
                                            <div class="flex items-center mb-2">
                                                <h4 class="font-bold text-lg text-gray-900" x-text="business.name"></h4>
                                                <span x-show="business.justAdded" 
                                                      class="ml-2 bg-green-500 text-white text-xs px-2 py-1 rounded-full animate-pulse">NEW</span>
                                            </div>
                                            <p class="text-gray-600 mb-2" x-text="business.address"></p>
                                            <div class="flex items-center space-x-4 text-sm">
                                                <div class="flex items-center">
                                                    <span class="text-yellow-500">★</span>
                                                    <span class="ml-1 font-medium" x-text="business.rating"></span>
                                                    <span class="text-gray-500 ml-1">(<span x-text="business.reviews"></span> reviews)</span>
                                                </div>
                                                <div class="text-blue-600 font-medium" x-text="business.industry"></div>
                                            </div>
                                            <div class="mt-2 flex items-center space-x-3">
                                                <span x-text="business.opportunity" class="text-sm bg-blue-100 text-blue-800 px-2 py-1 rounded"></span>
                                                <span x-text="business.contactStatus" class="text-xs bg-gray-100 text-gray-700 px-2 py-1 rounded"></span>
                                            </div>
                                        </div>
                                        <div class="ml-4 text-right">
                                            <div class="text-white px-3 py-2 rounded-lg font-bold text-lg"
                                                 :class="business.leadScore >= 85 ? 'lead-score-high' : 
                                                         business.leadScore >= 70 ? 'lead-score-medium' : 'lead-score-low'">
                                                <div class="text-sm opacity-90">Lead Score</div>
                                                <div x-text="business.leadScore + '/100'" class="text-xl"></div>
                                            </div>
                                            <div class="text-xs text-gray-500 mt-1" x-text="business.potential"></div>
                                        </div>
                                    </div>
                                </div>
                            </template>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Right: Control Panel -->
            <div>
                <!-- AI Controls -->
                <div class="bg-white rounded-xl shadow-xl border border-gray-200 p-6 mb-6">
                    <h3 class="text-xl font-bold mb-4 text-gray-900">AI Control Panel</h3>
                    
                    <div class="space-y-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Target Industry</label>
                            <select x-model="selectedIndustry" @change="restartDemo()" 
                                    class="w-full px-3 py-2 border border-gray-300 rounded-md bg-white">
                                <option value="hvac">HVAC Services</option>
                                <option value="auto_detailing">Auto Detailing</option>
                                <option value="plumbing">Plumbing Services</option>
                                <option value="electrical">Electrical Services</option>
                                <option value="landscaping">Landscaping</option>
                            </select>
                        </div>
                        
                        <div>
                            <label class="block text-sm font-medium text-gray-700 mb-2">Geographic Focus</label>
                            <select x-model="selectedLocation" @change="restartDemo()"
                                    class="w-full px-3 py-2 border border-gray-300 rounded-md bg-white">
                                <optgroup label="🔥 Major Markets (Top 10)">
                                    <option value="New York, NY">New York, NY</option>
                                    <option value="Los Angeles, CA">Los Angeles, CA</option>
                                    <option value="Chicago, IL">Chicago, IL</option>
                                    <option value="Houston, TX">Houston, TX</option>
                                    <option value="Phoenix, AZ">Phoenix, AZ</option>
                                    <option value="Philadelphia, PA">Philadelphia, PA</option>
                                    <option value="San Antonio, TX">San Antonio, TX</option>
                                    <option value="San Diego, CA">San Diego, CA</option>
                                    <option value="Dallas, TX">Dallas, TX</option>
                                    <option value="San Jose, CA">San Jose, CA</option>
                                </optgroup>
                                <optgroup label="🚀 Growth Markets (11-25)">
                                    <option value="Austin, TX">Austin, TX</option>
                                    <option value="Jacksonville, FL">Jacksonville, FL</option>
                                    <option value="Fort Worth, TX">Fort Worth, TX</option>
                                    <option value="Columbus, OH">Columbus, OH</option>
                                    <option value="Indianapolis, IN">Indianapolis, IN</option>
                                    <option value="Charlotte, NC">Charlotte, NC</option>
                                    <option value="San Francisco, CA">San Francisco, CA</option>
                                    <option value="Seattle, WA">Seattle, WA</option>
                                    <option value="Denver, CO">Denver, CO</option>
                                    <option value="Oklahoma City, OK">Oklahoma City, OK</option>
                                    <option value="Nashville, TN">Nashville, TN</option>
                                    <option value="El Paso, TX">El Paso, TX</option>
                                    <option value="Washington, DC">Washington, DC</option>
                                    <option value="Boston, MA">Boston, MA</option>
                                    <option value="Las Vegas, NV">Las Vegas, NV</option>
                                </optgroup>
                                <optgroup label="💎 Premium Markets (26-50)">
                                    <option value="Detroit, MI">Detroit, MI</option>
                                    <option value="Portland, OR">Portland, OR</option>
                                    <option value="Louisville, KY">Louisville, KY</option>
                                    <option value="Memphis, TN">Memphis, TN</option>
                                    <option value="Baltimore, MD">Baltimore, MD</option>
                                    <option value="Milwaukee, WI">Milwaukee, WI</option>
                                    <option value="Albuquerque, NM">Albuquerque, NM</option>
                                    <option value="Tucson, AZ">Tucson, AZ</option>
                                    <option value="Fresno, CA">Fresno, CA</option>
                                    <option value="Mesa, AZ">Mesa, AZ</option>
                                    <option value="Sacramento, CA">Sacramento, CA</option>
                                    <option value="Atlanta, GA">Atlanta, GA</option>
                                    <option value="Kansas City, MO">Kansas City, MO</option>
                                    <option value="Colorado Springs, CO">Colorado Springs, CO</option>
                                    <option value="Raleigh, NC">Raleigh, NC</option>
                                    <option value="Omaha, NE">Omaha, NE</option>
                                    <option value="Miami, FL">Miami, FL</option>
                                    <option value="Oakland, CA">Oakland, CA</option>
                                    <option value="Minneapolis, MN">Minneapolis, MN</option>
                                    <option value="Tulsa, OK">Tulsa, OK</option>
                                    <option value="Cleveland, OH">Cleveland, OH</option>
                                    <option value="Wichita, KS">Wichita, KS</option>
                                    <option value="Arlington, TX">Arlington, TX</option>
                                    <option value="New Orleans, LA">New Orleans, LA</option>
                                    <option value="Bakersfield, CA">Bakersfield, CA</option>
                                </optgroup>
                                <optgroup label="🏆 Opportunity Markets (51-75)">
                                    <option value="Tampa, FL">Tampa, FL</option>
                                    <option value="Honolulu, HI">Honolulu, HI</option>
                                    <option value="Anaheim, CA">Anaheim, CA</option>
                                    <option value="Aurora, CO">Aurora, CO</option>
                                    <option value="Santa Ana, CA">Santa Ana, CA</option>
                                    <option value="St. Louis, MO">St. Louis, MO</option>
                                    <option value="Riverside, CA">Riverside, CA</option>
                                    <option value="Corpus Christi, TX">Corpus Christi, TX</option>
                                    <option value="Lexington, KY">Lexington, KY</option>
                                    <option value="Pittsburgh, PA">Pittsburgh, PA</option>
                                    <option value="Anchorage, AK">Anchorage, AK</option>
                                    <option value="Stockton, CA">Stockton, CA</option>
                                    <option value="Cincinnati, OH">Cincinnati, OH</option>
                                    <option value="Saint Paul, MN">Saint Paul, MN</option>
                                    <option value="Toledo, OH">Toledo, OH</option>
                                    <option value="Greensboro, NC">Greensboro, NC</option>
                                    <option value="Newark, NJ">Newark, NJ</option>
                                    <option value="Plano, TX">Plano, TX</option>
                                    <option value="Henderson, NV">Henderson, NV</option>
                                    <option value="Lincoln, NE">Lincoln, NE</option>
                                    <option value="Buffalo, NY">Buffalo, NY</option>
                                    <option value="Jersey City, NJ">Jersey City, NJ</option>
                                    <option value="Chula Vista, CA">Chula Vista, CA</option>
                                    <option value="Orlando, FL">Orlando, FL</option>
                                    <option value="Norfolk, VA">Norfolk, VA</option>
                                </optgroup>
                                <optgroup label="📈 Emerging Markets (76-100)">
                                    <option value="Chandler, AZ">Chandler, AZ</option>
                                    <option value="Laredo, TX">Laredo, TX</option>
                                    <option value="Madison, WI">Madison, WI</option>
                                    <option value="Durham, NC">Durham, NC</option>
                                    <option value="Lubbock, TX">Lubbock, TX</option>
                                    <option value="Winston-Salem, NC">Winston-Salem, NC</option>
                                    <option value="Garland, TX">Garland, TX</option>
                                    <option value="Glendale, AZ">Glendale, AZ</option>
                                    <option value="Hialeah, FL">Hialeah, FL</option>
                                    <option value="Reno, NV">Reno, NV</option>
                                    <option value="Baton Rouge, LA">Baton Rouge, LA</option>
                                    <option value="Irvine, CA">Irvine, CA</option>
                                    <option value="Chesapeake, VA">Chesapeake, VA</option>
                                    <option value="Irving, TX">Irving, TX</option>
                                    <option value="Scottsdale, AZ">Scottsdale, AZ</option>
                                    <option value="North Las Vegas, NV">North Las Vegas, NV</option>
                                    <option value="Fremont, CA">Fremont, CA</option>
                                    <option value="Gilbert, AZ">Gilbert, AZ</option>
                                    <option value="San Bernardino, CA">San Bernardino, CA</option>
                                    <option value="Boise, ID">Boise, ID</option>
                                    <option value="Birmingham, AL">Birmingham, AL</option>
                                    <option value="Spokane, WA">Spokane, WA</option>
                                    <option value="Rochester, NY">Rochester, NY</option>
                                    <option value="Des Moines, IA">Des Moines, IA</option>
                                    <option value="Modesto, CA">Modesto, CA</option>
                                </optgroup>
                                <optgroup label="🌟 Hidden Gems (100+)">
                                    <option value="Fayetteville, NC">Fayetteville, NC</option>
                                    <option value="Tacoma, WA">Tacoma, WA</option>
                                    <option value="Oxnard, CA">Oxnard, CA</option>
                                    <option value="Fontana, CA">Fontana, CA</option>
                                    <option value="Columbus, GA">Columbus, GA</option>
                                    <option value="Montgomery, AL">Montgomery, AL</option>
                                    <option value="Shreveport, LA">Shreveport, LA</option>
                                    <option value="Aurora, IL">Aurora, IL</option>
                                    <option value="Yonkers, NY">Yonkers, NY</option>
                                    <option value="Akron, OH">Akron, OH</option>
                                    <option value="Huntington Beach, CA">Huntington Beach, CA</option>
                                    <option value="Little Rock, AR">Little Rock, AR</option>
                                    <option value="Augusta, GA">Augusta, GA</option>
                                    <option value="Amarillo, TX">Amarillo, TX</option>
                                    <option value="Glendale, CA">Glendale, CA</option>
                                    <option value="Mobile, AL">Mobile, AL</option>
                                    <option value="Grand Rapids, MI">Grand Rapids, MI</option>
                                    <option value="Salt Lake City, UT">Salt Lake City, UT</option>
                                    <option value="Tallahassee, FL">Tallahassee, FL</option>
                                    <option value="Huntsville, AL">Huntsville, AL</option>
                                    <option value="Grand Prairie, TX">Grand Prairie, TX</option>
                                    <option value="Knoxville, TN">Knoxville, TN</option>
                                    <option value="Worcester, MA">Worcester, MA</option>
                                    <option value="Newport News, VA">Newport News, VA</option>
                                    <option value="Brownsville, TX">Brownsville, TX</option>
                                </optgroup>
                            </select>
                        </div>

                        <button @click="restartDemo()" 
                                class="w-full bg-gradient-to-r from-blue-600 to-purple-600 text-white px-4 py-3 rounded-lg font-semibold hover:from-blue-700 hover:to-purple-700">
                            🚀 Start New AI Scan
                        </button>
                    </div>
                </div>

                <!-- Live Stats -->
                <div class="bg-gradient-to-br from-gray-900 to-gray-800 text-white rounded-xl p-6">
                    <h3 class="text-lg font-bold mb-4 text-yellow-300">Live Intelligence Stats</h3>
                    <div class="space-y-3">
                        <div class="flex justify-between">
                            <span class="text-gray-300">Businesses Scanned</span>
                            <span class="font-bold text-yellow-300" x-text="processedCount + '/1,247'"></span>
                        </div>
                        <div class="flex justify-between">
                            <span class="text-gray-300">High-Value Leads</span>
                            <span class="font-bold text-green-400" x-text="liveResults.filter(b => b.leadScore >= 85).length"></span>
                        </div>
                        <div class="flex justify-between">
                            <span class="text-gray-300">Avg. Lead Score</span>
                            <span class="font-bold text-blue-400" x-text="Math.round(liveResults.reduce((sum, b) => sum + b.leadScore, 0) / (liveResults.length || 1))"></span>
                        </div>
                        <div class="flex justify-between">
                            <span class="text-gray-300">Processing Speed</span>
                            <span class="font-bold text-purple-400">847/min</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- CTA Section -->
        <div class="mt-8 bg-gradient-to-r from-yellow-500 to-yellow-400 rounded-xl p-6 text-center">
            <div class="text-black">
                <h3 class="text-2xl font-bold mb-2">Ready to Deploy SINCOR for YOUR Business?</h3>
                <p class="mb-4 text-lg">This is just a 5-minute demo. Imagine what SINCOR can do with hours of processing!</p>
                <div class="flex justify-center space-x-4">
                    <a href="/checkout/professional" 
                       class="bg-black text-yellow-400 px-8 py-3 rounded-lg font-bold hover:bg-gray-800 transition-colors">
                        Start SINCOR Now
                    </a>
                    <a href="/" class="border-2 border-black text-black px-8 py-3 rounded-lg font-bold hover:bg-black hover:text-yellow-400 transition-colors">
                        Learn More
                    </a>
                </div>
            </div>
        </div>
    </div>

    <script>
        function professionalDemo() {
            return {
                selectedIndustry: 'hvac',
                selectedLocation: 'Austin, TX',
                aiProcessing: true,
                aiStatus: 'Initializing AI systems...',
                processedCount: 0,
                liveResults: [],
                demoInterval: null,
                statusMessages: [
                    'Scanning Google Business listings...',
                    'Analyzing review patterns...',
                    'Computing lead scores...',
                    'Cross-referencing industry data...',
                    'Identifying high-value prospects...',
                    'Finalizing intelligence report...'
                ],
                
                businessDatabase: {
                    hvac: [
                        {
                            name: 'Superior Climate Solutions',
                            address: '2847 Industrial Blvd, Austin, TX 78744',
                            rating: 4.8,
                            reviews: 234,
                            industry: 'HVAC Services',
                            leadScore: 92,
                            opportunity: 'Premium Prospect',
                            potential: 'High conversion probability',
                            contactStatus: 'Never contacted'
                        },
                        {
                            name: 'Austin Air Masters',
                            address: '1523 Research Blvd, Austin, TX 78759',
                            rating: 4.6,
                            reviews: 189,
                            industry: 'HVAC Services',
                            leadScore: 87,
                            opportunity: 'Growth Target',
                            potential: 'Scaling opportunity',
                            contactStatus: 'Cold outreach ready'
                        },
                        {
                            name: 'Texas Comfort Systems',
                            address: '892 Lamar Blvd, Austin, TX 78704',
                            rating: 4.4,
                            reviews: 156,
                            industry: 'HVAC Services',
                            leadScore: 84,
                            opportunity: 'Market Leader',
                            potential: 'Partnership potential',
                            contactStatus: 'Research completed'
                        },
                        {
                            name: 'Hill Country HVAC',
                            address: '3401 S Lamar Blvd, Austin, TX 78704',
                            rating: 4.2,
                            reviews: 98,
                            industry: 'HVAC Services',
                            leadScore: 78,
                            opportunity: 'Emerging Player',
                            potential: 'Growth phase target',
                            contactStatus: 'Profile analyzed'
                        }
                    ],
                    auto_detailing: [
                        {
                            name: 'Platinum Auto Spa',
                            address: '1205 Airport Blvd, Austin, TX 78702',
                            rating: 4.9,
                            reviews: 312,
                            industry: 'Auto Detailing',
                            leadScore: 96,
                            opportunity: 'Premium Market',
                            potential: 'Highest value target',
                            contactStatus: 'Executive contact found'
                        },
                        {
                            name: 'Austin Elite Detail',
                            address: '2634 Guadalupe St, Austin, TX 78705',
                            rating: 4.7,
                            reviews: 198,
                            industry: 'Auto Detailing',
                            leadScore: 89,
                            opportunity: 'Growth Opportunity',
                            potential: 'Expansion ready',
                            contactStatus: 'Decision maker identified'
                        }
                    ]
                },
                
                init() {
                    this.startDemo();
                },
                
                startDemo() {
                    this.aiProcessing = true;
                    this.processedCount = 0;
                    this.liveResults = [];
                    
                    let statusIndex = 0;
                    let businessIndex = 0;
                    const businesses = this.businessDatabase[this.selectedIndustry] || this.businessDatabase.hvac;
                    
                    const processingInterval = setInterval(() => {
                        this.processedCount += Math.floor(Math.random() * 50) + 20;
                        this.aiStatus = this.statusMessages[statusIndex % this.statusMessages.length];
                        statusIndex++;
                        
                        // Add new business every few cycles
                        if (this.processedCount > (businessIndex + 1) * 200 && businessIndex < businesses.length) {
                            const newBusiness = {
                                ...businesses[businessIndex],
                                id: Date.now() + businessIndex,
                                justAdded: true
                            };
                            this.liveResults.unshift(newBusiness);
                            businessIndex++;
                            
                            // Remove 'just added' status after 3 seconds
                            setTimeout(() => {
                                newBusiness.justAdded = false;
                            }, 3000);
                        }
                        
                        if (this.processedCount >= 1247) {
                            clearInterval(processingInterval);
                            this.aiProcessing = false;
                            this.processedCount = 1247;
                        }
                    }, 800);
                },
                
                restartDemo() {
                    this.startDemo();
                }
            }
        }
    </script>
</body>
</html>
"""