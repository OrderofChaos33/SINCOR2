"""
Real-time SINCOR Dashboard Routes
Connects to the SINCOR Engine to display live business intelligence data
"""

from flask import jsonify
from datetime import datetime
import json

def add_dashboard_routes(app):
    """Add real-time dashboard routes to Flask app."""
    
    @app.route("/api/dashboard/data")
    def dashboard_api():
        """API endpoint for dashboard data."""
        try:
            from sincor_engine import SINCOREngine
            engine = SINCOREngine()
            data = engine.get_dashboard_data()
            return jsonify(data)
        except Exception as e:
            # Return demo data if engine unavailable
            return jsonify({
                "businesses_discovered": 247,
                "emails_sent": 43, 
                "responses_received": 12,
                "estimated_pipeline": 33600,
                "conversion_rate": 27.9,
                "recent_activity": [
                    {
                        "type": "campaign_started",
                        "business": "Austin Auto Detailing Campaign", 
                        "details": "25 businesses targeted",
                        "timestamp": datetime.now().isoformat()
                    }
                ],
                "last_updated": datetime.now().isoformat(),
                "status": "demo_mode",
                "error": str(e)
            })
    
    @app.route("/api/campaign/run")
    def run_campaign():
        """API endpoint to trigger a new campaign."""
        try:
            from sincor_engine import SINCOREngine, CampaignConfig
            
            engine = SINCOREngine()
            config = CampaignConfig(
                target_industry="auto detailing",
                locations=["Austin, TX", "Dallas, TX"], 
                max_businesses_per_day=25
            )
            
            # Run campaign in background
            results = engine.run_automated_campaign(config)
            
            return jsonify({
                "success": True,
                "message": "Campaign started successfully",
                "campaign_id": results.get("campaign_id"),
                "businesses_targeted": results.get("businesses_discovered", 0),
                "emails_sent": results.get("emails_sent", 0)
            })
            
        except Exception as e:
            return jsonify({
                "success": False, 
                "error": str(e),
                "message": "Campaign failed to start"
            })
    
    @app.route("/dashboard/live")
    def live_dashboard():
        """Live dashboard with real-time data updates."""
        return """
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR Live Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
</head>
<body class="bg-gray-50" x-data="dashboard()" x-init="loadDashboard()">
    <div class="max-w-7xl mx-auto py-8 px-4">
        <div class="flex justify-between items-center mb-8">
            <h1 class="text-3xl font-bold text-gray-900">SINCOR Live Dashboard</h1>
            <div class="flex items-center space-x-4">
                <span class="text-sm text-gray-500" x-text="'Last updated: ' + lastUpdated"></span>
                <div class="w-3 h-3 bg-green-500 rounded-full animate-pulse"></div>
                <span class="text-sm text-green-600 font-semibold">LIVE</span>
                <button @click="refreshData()" class="bg-blue-600 text-white px-3 py-1 rounded text-sm hover:bg-blue-700">
                    Refresh
                </button>
            </div>
        </div>
        
        <!-- Metrics Cards -->
        <div class="grid md:grid-cols-4 gap-6 mb-8">
            <div class="bg-white p-6 rounded-lg shadow">
                <div class="text-2xl font-bold text-blue-600" x-text="data.businesses_discovered || 0"></div>
                <div class="text-gray-600">Businesses Discovered</div>
                <div class="text-xs text-blue-500 mt-1">Today's discoveries</div>
            </div>
            <div class="bg-white p-6 rounded-lg shadow">
                <div class="text-2xl font-bold text-green-600" x-text="data.emails_sent || 0"></div>
                <div class="text-gray-600">Emails Sent</div>
                <div class="text-xs text-green-500 mt-1">Personalized outreach</div>
            </div>
            <div class="bg-white p-6 rounded-lg shadow">
                <div class="text-2xl font-bold text-purple-600" x-text="data.responses_received || 0"></div>
                <div class="text-gray-600">Responses Received</div>
                <div class="text-xs text-purple-500 mt-1" x-text="(data.conversion_rate || 0) + '% response rate'"></div>
            </div>
            <div class="bg-white p-6 rounded-lg shadow">
                <div class="text-2xl font-bold text-yellow-600" x-text="'$' + (data.estimated_pipeline || 0).toLocaleString()"></div>
                <div class="text-gray-600">Estimated Pipeline</div>
                <div class="text-xs text-yellow-500 mt-1">Avg $2,800 per lead</div>
            </div>
        </div>
        
        <div class="grid md:grid-cols-2 gap-8">
            <!-- Recent Activity -->
            <div class="bg-white rounded-lg shadow p-6">
                <h2 class="text-xl font-semibold mb-4">Recent Activity</h2>
                <div class="space-y-4">
                    <template x-for="activity in (data.recent_activity || [])" :key="activity.timestamp">
                        <div class="border-l-4 border-blue-500 pl-4">
                            <div class="font-semibold" x-text="activity.business"></div>
                            <div class="text-sm text-gray-600" x-text="activity.details + ' • ' + formatTimeAgo(activity.timestamp)"></div>
                        </div>
                    </template>
                    <div x-show="!data.recent_activity || data.recent_activity.length === 0" class="text-gray-500">
                        No recent activity
                    </div>
                </div>
            </div>
            
            <!-- Campaign Controls -->
            <div class="bg-white rounded-lg shadow p-6">
                <h2 class="text-xl font-semibold mb-4">Campaign Management</h2>
                
                <div class="space-y-4 mb-6">
                    <div class="flex justify-between items-center">
                        <span class="text-gray-600">Discovery Rate</span>
                        <span class="font-semibold" x-text="(data.businesses_discovered || 0) + ' businesses/day'"></span>
                    </div>
                    <div class="flex justify-between items-center">
                        <span class="text-gray-600">Email Open Rate</span>
                        <span class="font-semibold">24.3%</span>
                    </div>
                    <div class="flex justify-between items-center">
                        <span class="text-gray-600">Response Rate</span>
                        <span class="font-semibold text-green-600" x-text="(data.conversion_rate || 0) + '%'"></span>
                    </div>
                    <div class="flex justify-between items-center">
                        <span class="text-gray-600">Pipeline Conversion</span>
                        <span class="font-semibold text-purple-600">18.7%</span>
                    </div>
                </div>
                
                <div class="space-y-3">
                    <button @click="runCampaign()" 
                            :disabled="campaignRunning"
                            :class="campaignRunning ? 'bg-gray-400' : 'bg-blue-600 hover:bg-blue-700'"
                            class="w-full text-white py-2 px-4 rounded-lg">
                        <span x-show="!campaignRunning">🚀 Run New Campaign</span>
                        <span x-show="campaignRunning">⏳ Campaign Running...</span>
                    </button>
                    
                    <div x-show="campaignResult" class="text-sm p-3 rounded" 
                         :class="campaignResult?.success ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'">
                        <span x-text="campaignResult?.message"></span>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Engine Status -->
        <div class="mt-8 bg-gradient-to-r from-blue-600 to-purple-600 text-white p-6 rounded-lg">
            <h3 class="text-lg font-semibold mb-2">🎯 SINCOR Engine Status</h3>
            <p class="text-blue-100 mb-4">Automatically discovering prospects, sending personalized emails, and tracking responses 24/7</p>
            <div class="flex space-x-6 text-sm">
                <div class="flex items-center space-x-2">
                    <div class="w-2 h-2 bg-green-400 rounded-full"></div>
                    <span>Business Discovery</span>
                </div>
                <div class="flex items-center space-x-2">
                    <div class="w-2 h-2 bg-green-400 rounded-full"></div>
                    <span>Email Campaigns</span>
                </div>
                <div class="flex items-center space-x-2">
                    <div class="w-2 h-2 bg-green-400 rounded-full"></div>
                    <span>Lead Scoring</span>
                </div>
                <div class="flex items-center space-x-2">
                    <div class="w-2 h-2 bg-green-400 rounded-full"></div>
                    <span>Analytics</span>
                </div>
            </div>
            
            <div x-show="data.status === 'demo_mode'" class="mt-4 p-3 bg-yellow-500 bg-opacity-20 rounded-lg">
                <div class="text-yellow-100">⚠️ Running in Demo Mode - Add Google API key for live data</div>
            </div>
        </div>
    </div>

    <script>
        function dashboard() {
            return {
                data: {},
                lastUpdated: '',
                campaignRunning: false,
                campaignResult: null,
                
                async loadDashboard() {
                    try {
                        const response = await fetch('/api/dashboard/data');
                        this.data = await response.json();
                        this.lastUpdated = new Date(this.data.last_updated).toLocaleTimeString();
                    } catch (error) {
                        console.error('Error loading dashboard:', error);
                    }
                },
                
                async refreshData() {
                    await this.loadDashboard();
                },
                
                async runCampaign() {
                    this.campaignRunning = true;
                    this.campaignResult = null;
                    
                    try {
                        const response = await fetch('/api/campaign/run');
                        const result = await response.json();
                        this.campaignResult = result;
                        
                        if (result.success) {
                            // Refresh dashboard after 3 seconds
                            setTimeout(() => this.loadDashboard(), 3000);
                        }
                    } catch (error) {
                        this.campaignResult = {
                            success: false,
                            message: 'Network error: ' + error.message
                        };
                    }
                    
                    this.campaignRunning = false;
                },
                
                formatTimeAgo(timestamp) {
                    if (!timestamp) return 'Unknown';
                    
                    const now = new Date();
                    const then = new Date(timestamp);
                    const diffHours = Math.floor((now - then) / (1000 * 60 * 60));
                    
                    if (diffHours < 1) return 'Just now';
                    if (diffHours === 1) return '1 hour ago';
                    return `${diffHours} hours ago`;
                }
            }
        }
        
        // Auto-refresh every 30 seconds
        setInterval(() => {
            if (typeof dashboard !== 'undefined') {
                // Refresh data without disrupting user interactions
                fetch('/api/dashboard/data')
                    .then(response => response.json())
                    .then(data => {
                        // Update Alpine.js data
                        window.dispatchEvent(new CustomEvent('dashboard-update', { detail: data }));
                    });
            }
        }, 30000);
    </script>
</body>
</html>"""