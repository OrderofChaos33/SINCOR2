"""
SINCOR Music Marketing Integration System
Revolutionary: First business automation platform with custom music integration

Features:
- Industry-specific jingles and songs in media packs
- Custom ringtones for business phones
- Hold music for customer service
- Background music for promotional videos
- Viral marketing songs for social media

This positions you as the ONLY polymath who combines business systems + music!
"""

from flask import render_template_string, request, jsonify, send_file
import os
from datetime import datetime

# Music Marketing Configuration  
INDUSTRY_MUSIC = {
    "auto_detailing": {
        "jingle": {
            "title": "Shine Bright Jingle",
            "duration": 15,  # seconds
            "description": "Catchy 15-second jingle for auto detailing businesses",
            "usage": ["radio ads", "phone hold music", "video intros"],
            "sample_lyrics": "🎵 Shine bright, drive right, [Business Name] makes it spotless every time! 🎵"
        },
        "theme_song": {
            "title": "The Detailing Dream",
            "duration": 180,  # 3 minutes
            "description": "Full inspirational song about auto detailing success",
            "usage": ["promotional videos", "social media content", "background music"],
            "sample_lyrics": "From dirty cars to shining stars, we make your ride brand new..."
        },
        "hold_music": {
            "title": "Smooth Detail Instrumentals", 
            "duration": 120,
            "description": "Professional instrumental for customer phone hold",
            "usage": ["phone systems", "waiting areas", "background ambiance"]
        }
    },
    
    "hvac": {
        "jingle": {
            "title": "Cool Comfort Jingle",
            "duration": 15,
            "description": "HVAC service jingle with temperature themes",
            "usage": ["radio ads", "phone hold music", "video intros"], 
            "sample_lyrics": "🎵 Hot or cold, young or old, [Business Name] keeps comfort in control! 🎵"
        },
        "theme_song": {
            "title": "Climate Masters",
            "duration": 180,
            "description": "Powerful anthem about HVAC expertise",
            "usage": ["promotional videos", "trade show presentations"],
            "sample_lyrics": "We control the climate, masters of the air..."
        }
    },
    
    "pest_control": {
        "jingle": {
            "title": "Bug-Free Zone Jingle", 
            "duration": 15,
            "description": "Catchy pest control jingle with action themes",
            "usage": ["radio ads", "van/truck audio", "phone systems"],
            "sample_lyrics": "🎵 Bugs beware, we're on the case, [Business Name] protects your space! 🎵"
        },
        "theme_song": {
            "title": "The Exterminators",
            "duration": 180, 
            "description": "Action-packed song about pest elimination",
            "usage": ["promotional videos", "social media campaigns"]
        }
    }
}

# Music Licensing & Usage Rights
MUSIC_LICENSING = {
    "commercial_use": True,
    "modification_allowed": True,
    "attribution_required": False,
    "resale_allowed": False,
    "licensing_fee": 0,  # Included in media pack price
    "territory": "worldwide",
    "duration": "lifetime"
}

def add_music_marketing_routes(app):
    """Add music marketing routes to SINCOR system."""
    
    @app.route("/music-marketing")
    def music_marketing_hub():
        """Main hub for music marketing features."""
        return render_template_string(MUSIC_HUB_TEMPLATE, 
                                    music_library=INDUSTRY_MUSIC,
                                    licensing=MUSIC_LICENSING)
    
    @app.route("/music-preview/<industry>/<track_type>")
    def music_preview(industry, track_type):
        """Preview page for specific industry music."""
        if industry not in INDUSTRY_MUSIC:
            return "Industry not found", 404
            
        if track_type not in INDUSTRY_MUSIC[industry]:
            return "Track type not found", 404
            
        track = INDUSTRY_MUSIC[industry][track_type]
        return render_template_string(MUSIC_PREVIEW_TEMPLATE,
                                    industry=industry,
                                    track_type=track_type,
                                    track=track)
    
    @app.route("/custom-music-request")
    def custom_music_request():
        """Form for requesting custom music creation."""
        return render_template_string(CUSTOM_MUSIC_TEMPLATE)
    
    @app.route("/api/music/request", methods=["POST"])
    def process_music_request():
        """Process custom music creation request."""
        try:
            data = request.get_json()
            
            # In production, this would:
            # 1. Save request to database
            # 2. Queue for music creation
            # 3. Send confirmation email
            # 4. Process payment if premium
            
            return jsonify({
                "success": True,
                "message": "Music request received! We'll contact you within 24 hours.",
                "estimated_delivery": "3-5 business days",
                "pricing": {
                    "jingle_15sec": 297,
                    "theme_song_3min": 997, 
                    "hold_music_loop": 197,
                    "custom_package": 1497
                }
            })
            
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

# Templates
MUSIC_HUB_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR Music Marketing - Revolutionary Business Music Integration</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <!-- Hero: Music Revolution -->
    <section class="bg-gradient-to-r from-purple-900 via-blue-900 to-indigo-900 text-white py-20">
        <div class="max-w-6xl mx-auto px-4 text-center">
            <div class="mb-8">
                <span class="bg-yellow-400 text-black px-6 py-3 rounded-full text-lg font-bold">
                    🎵 WORLD'S FIRST BUSINESS + MUSIC PLATFORM
                </span>
            </div>
            
            <h1 class="text-5xl md:text-6xl font-bold mb-6">
                The Only Marketing Platform
                <span class="text-yellow-300">With Custom Music</span>
            </h1>
            
            <p class="text-2xl mb-8 text-blue-100 max-w-4xl mx-auto">
                100+ Songs Created • Industry-Specific Jingles • Custom Compositions
            </p>
            
            <div class="bg-white bg-opacity-10 p-8 rounded-xl max-w-2xl mx-auto">
                <h3 class="text-2xl font-bold mb-4">Revolutionary Concept:</h3>
                <p class="text-xl text-blue-200">
                    "What if your business marketing came with custom music that made your brand 
                    instantly memorable and emotionally engaging?"
                </p>
            </div>
        </div>
    </section>

    <!-- Music Library Grid -->
    <section class="py-16">
        <div class="max-w-7xl mx-auto px-4">
            <div class="text-center mb-12">
                <h2 class="text-3xl font-bold text-gray-900 mb-4">Industry Music Library</h2>
                <p class="text-xl text-gray-600">Custom music for every business type</p>
            </div>
            
            <div class="grid md:grid-cols-2 lg:grid-cols-3 gap-8">
                {% for industry, tracks in music_library.items() %}
                <div class="bg-white rounded-xl shadow-lg p-6">
                    <div class="text-center mb-6">
                        <h3 class="text-2xl font-bold text-gray-900 capitalize mb-2">
                            {{ industry.replace('_', ' ') }}
                        </h3>
                        <div class="text-4xl mb-4">
                            {% if industry == 'auto_detailing' %}🚗🎵
                            {% elif industry == 'hvac' %}🏠🎵  
                            {% elif industry == 'pest_control' %}🐛🎵
                            {% endif %}
                        </div>
                    </div>
                    
                    <div class="space-y-4">
                        {% for track_type, track in tracks.items() %}
                        <div class="border rounded-lg p-4">
                            <div class="flex justify-between items-center mb-2">
                                <h4 class="font-semibold">{{ track.title }}</h4>
                                <span class="text-sm text-gray-500">{{ track.duration }}s</span>
                            </div>
                            <p class="text-sm text-gray-600 mb-3">{{ track.description }}</p>
                            
                            <div class="flex justify-between items-center">
                                <div class="text-xs text-blue-600">
                                    {{ track.usage|length }} use cases
                                </div>
                                <a href="/music-preview/{{ industry }}/{{ track_type }}" 
                                   class="bg-blue-600 text-white px-3 py-1 rounded text-sm hover:bg-blue-700">
                                    Preview
                                </a>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
    </section>

    <!-- Music Marketing Value -->
    <section class="py-16 bg-blue-900 text-white">
        <div class="max-w-6xl mx-auto px-4">
            <div class="text-center mb-12">
                <h2 class="text-3xl font-bold mb-6">Why Music Marketing Works</h2>
            </div>
            
            <div class="grid md:grid-cols-3 gap-8">
                <div class="text-center">
                    <div class="text-5xl mb-4">🧠</div>
                    <h3 class="text-xl font-bold mb-4">Memory Enhancement</h3>
                    <p class="text-blue-200">Music increases brand recall by 400% compared to text alone</p>
                </div>
                <div class="text-center">
                    <div class="text-5xl mb-4">❤️</div>
                    <h3 class="text-xl font-bold mb-4">Emotional Connection</h3>
                    <p class="text-blue-200">Custom music creates emotional bonds with your customers</p>
                </div>
                <div class="text-center">
                    <div class="text-5xl mb-4">🎯</div>
                    <h3 class="text-xl font-bold mb-4">Brand Differentiation</h3>
                    <p class="text-blue-200">Be the ONLY business in your industry with custom music</p>
                </div>
            </div>
        </div>
    </section>

    <!-- Custom Music CTA -->
    <section class="py-16">
        <div class="max-w-4xl mx-auto px-4 text-center">
            <h2 class="text-3xl font-bold text-gray-900 mb-6">Need Custom Music for Your Business?</h2>
            <p class="text-xl text-gray-600 mb-8">
                Get personally composed music from a polymath with 100+ songs created
            </p>
            
            <div class="grid md:grid-cols-2 gap-8 mb-8">
                <div class="bg-white p-6 rounded-xl shadow-lg">
                    <h3 class="text-xl font-bold mb-4">Standard Music Packs</h3>
                    <ul class="text-left space-y-2 text-gray-600">
                        <li>• Pre-made industry jingles</li>
                        <li>• Professional audio quality</li>
                        <li>• Multiple format downloads</li>
                        <li>• Commercial usage rights</li>
                    </ul>
                    <div class="text-2xl font-bold text-blue-600 mt-4">Included in Media Packs</div>
                </div>
                
                <div class="bg-gradient-to-br from-purple-600 to-blue-600 text-white p-6 rounded-xl">
                    <h3 class="text-xl font-bold mb-4">Custom Compositions</h3>
                    <ul class="text-left space-y-2">
                        <li>• Personally composed for your business</li>
                        <li>• Your business name in the lyrics</li>
                        <li>• Multiple versions (radio, full, instrumental)</li>
                        <li>• Exclusive ownership rights</li>
                    </ul>
                    <div class="text-2xl font-bold text-yellow-300 mt-4">Starting at $297</div>
                </div>
            </div>
            
            <a href="/custom-music-request" 
               class="bg-purple-600 text-white px-8 py-4 rounded-lg text-xl font-bold hover:bg-purple-700">
                Request Custom Music
            </a>
        </div>
    </section>
</body>
</html>
"""

MUSIC_PREVIEW_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ track.title }} - Music Preview</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <div class="max-w-4xl mx-auto py-12 px-4">
        <div class="bg-white rounded-xl shadow-lg p-8">
            <div class="text-center mb-8">
                <h1 class="text-3xl font-bold text-gray-900 mb-2">{{ track.title }}</h1>
                <p class="text-xl text-gray-600">{{ industry.replace('_', ' ').title() }} Industry</p>
            </div>
            
            <div class="grid md:grid-cols-2 gap-8">
                <div>
                    <h3 class="text-xl font-bold mb-4">Track Details</h3>
                    <div class="space-y-3">
                        <div>
                            <span class="font-semibold">Duration:</span> {{ track.duration }} seconds
                        </div>
                        <div>
                            <span class="font-semibold">Description:</span> {{ track.description }}
                        </div>
                        <div>
                            <span class="font-semibold">Usage:</span>
                            <ul class="mt-1 ml-4 list-disc">
                                {% for use in track.usage %}
                                <li>{{ use.title() }}</li>
                                {% endfor %}
                            </ul>
                        </div>
                    </div>
                </div>
                
                <div>
                    <h3 class="text-xl font-bold mb-4">Sample Lyrics</h3>
                    {% if track.get('sample_lyrics') %}
                    <div class="bg-blue-50 p-4 rounded-lg italic text-blue-800">
                        {{ track.sample_lyrics }}
                    </div>
                    {% else %}
                    <div class="bg-gray-50 p-4 rounded-lg text-gray-600">
                        Instrumental track - no lyrics
                    </div>
                    {% endif %}
                </div>
            </div>
            
            <div class="mt-8 text-center">
                <p class="text-gray-600 mb-4">
                    This track is included in the {{ industry.replace('_', ' ').title() }} Media Pack
                </p>
                <a href="/media-pack/{{ industry }}" 
                   class="bg-blue-600 text-white px-6 py-3 rounded-lg hover:bg-blue-700">
                    Get This Track + Full Media Pack
                </a>
            </div>
        </div>
    </div>
</body>
</html>
"""

CUSTOM_MUSIC_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Custom Music Request - SINCOR Music Marketing</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <div class="max-w-4xl mx-auto py-12 px-4">
        <div class="text-center mb-8">
            <h1 class="text-3xl font-bold text-gray-900 mb-4">Custom Music Creation Request</h1>
            <p class="text-xl text-gray-600">Get personally composed music from a polymath with 100+ songs</p>
        </div>
        
        <div class="bg-white rounded-xl shadow-lg p-8">
            <form id="music-request-form" class="space-y-6">
                <div class="grid md:grid-cols-2 gap-6">
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">Business Name</label>
                        <input type="text" required 
                               class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500">
                    </div>
                    
                    <div>
                        <label class="block text-sm font-medium text-gray-700 mb-2">Industry</label>
                        <select class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500">
                            <option>Auto Detailing</option>
                            <option>HVAC</option>
                            <option>Pest Control</option>
                            <option>Plumbing</option>
                            <option>Electrical</option>
                            <option>Other</option>
                        </select>
                    </div>
                </div>
                
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Music Type</label>
                    <div class="grid md:grid-cols-3 gap-4">
                        <label class="flex items-center">
                            <input type="checkbox" class="mr-2"> 15-Second Jingle ($297)
                        </label>
                        <label class="flex items-center">
                            <input type="checkbox" class="mr-2"> Full Theme Song ($997)
                        </label>
                        <label class="flex items-center">
                            <input type="checkbox" class="mr-2"> Hold Music Loop ($197)
                        </label>
                    </div>
                </div>
                
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Special Requirements</label>
                    <textarea rows="4" 
                              class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500"
                              placeholder="Describe your vision, key messages, style preferences, etc."></textarea>
                </div>
                
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Contact Email</label>
                    <input type="email" required
                           class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500">
                </div>
                
                <div class="text-center">
                    <button type="submit" 
                            class="bg-purple-600 text-white px-8 py-4 rounded-lg text-lg font-bold hover:bg-purple-700">
                        Request Custom Music
                    </button>
                    
                    <p class="mt-4 text-sm text-gray-500">
                        Response within 24 hours • 3-5 day delivery • 100% satisfaction guarantee
                    </p>
                </div>
            </form>
        </div>
    </div>
    
    <script>
        document.getElementById('music-request-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const response = await fetch('/api/music/request', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({/* form data */})
            });
            
            const result = await response.json();
            
            if (result.success) {
                alert('Music request received! We\'ll contact you within 24 hours.');
            } else {
                alert('Error: ' + result.error);
            }
        });
    </script>
</body>
</html>
"""