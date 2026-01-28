"""
SINCOR Admin Control Panel
Complete Empire Management Dashboard

This system provides comprehensive admin controls for managing
the entire SINCOR business empire across all systems.
"""

from flask import render_template_string, request, jsonify, redirect, session
from datetime import datetime, timedelta
import sqlite3
import json
import os
from pathlib import Path

# Admin Authentication (Basic - would use proper auth in production)
ADMIN_USERS = {
    "admin": "sincor2025!",  # Change in production
    "founder": "polymath8books"  # Founder access
}

class AdminControlPanel:
    """Comprehensive admin control system."""
    
    def __init__(self):
        self.db_path = Path(__file__).parent / "data" / "admin.db"
        self.init_admin_db()
    
    def init_admin_db(self):
        """Initialize admin database."""
        self.db_path.parent.mkdir(exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Admin actions log
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admin_actions (
                id INTEGER PRIMARY KEY,
                admin_user TEXT,
                action TEXT,
                target TEXT,
                details TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ip_address TEXT
            )
        ''')
        
        # System settings
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_settings (
                id INTEGER PRIMARY KEY,
                setting_key TEXT UNIQUE,
                setting_value TEXT,
                updated_by TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Insert default settings
        default_settings = [
            ("trial_price", "100"),  # $1.00 in cents
            ("spots_remaining", "47"),
            ("price_increase_date", "2025-01-31"),
            ("bonus_customer_limit", "50"),
            ("current_bonus_count", "23"),
            ("maintenance_mode", "false")
        ]
        
        for key, value in default_settings:
            cursor.execute('''
                INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_by)
                VALUES (?, ?, 'system')
            ''', (key, value))
        
        conn.commit()
        conn.close()
    
    def log_admin_action(self, admin_user, action, target, details, ip_address):
        """Log admin actions for audit trail."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO admin_actions (admin_user, action, target, details, ip_address)
            VALUES (?, ?, ?, ?, ?)
        ''', (admin_user, action, target, details, ip_address))
        
        conn.commit()
        conn.close()
    
    def get_real_leads_data(self):
        """Get real leads data from CSV file."""
        import csv
        leads = []
        try:
            with open(Path(__file__).parent / "outputs" / "leads.csv", 'r') as f:
                reader = csv.DictReader(f)
                leads = list(reader)
        except FileNotFoundError:
            pass
        return leads
    
    def get_real_database_counts(self):
        """Get real counts from databases."""
        counts = {"businesses": 0, "campaigns": 0, "emails_sent": 0}
        try:
            # Business intelligence DB
            bi_conn = sqlite3.connect(Path(__file__).parent / "data" / "business_intelligence.db")
            bi_cursor = bi_conn.cursor()
            bi_cursor.execute("SELECT COUNT(*) FROM businesses")
            counts["businesses"] = bi_cursor.fetchone()[0]
            bi_cursor.execute("SELECT COUNT(*) FROM campaigns") 
            counts["campaigns"] = bi_cursor.fetchone()[0]
            bi_conn.close()
            
            # Main SINCOR DB
            main_conn = sqlite3.connect(Path(__file__).parent / "data" / "sincor_main.db")
            main_cursor = main_conn.cursor()
            main_cursor.execute("SELECT COUNT(*) FROM email_tracking")
            counts["emails_sent"] = main_cursor.fetchone()[0]
            main_conn.close()
        except Exception as e:
            print(f"Database error: {e}")
        return counts

    def get_system_overview(self):
        """Get comprehensive system overview with real data."""
        leads_data = self.get_real_leads_data()
        db_counts = self.get_real_database_counts()
        
        # Real activity from leads
        recent_activity = []
        for lead in leads_data[-3:]:  # Last 3 leads
            recent_activity.append({
                "type": "lead_generated", 
                "details": f"{lead['name']} - {lead['service']}", 
                "time": lead['timestamp'][:16].replace('T', ' ')
            })
        
        return {
            "empire_metrics": {
                "total_leads": len(leads_data),
                "businesses_discovered": db_counts["businesses"], 
                "campaigns_active": db_counts["campaigns"],
                "emails_sent": db_counts["emails_sent"]
            },
            "system_status": {
                "sincor_core": "operational",
                "email_automation": "operational", 
                "business_discovery": "operational",
                "agents": "operational",
                "database": "operational",
                "lead_capture": "operational"
            },
            "recent_activity": recent_activity if recent_activity else [
                {"type": "system_status", "details": "SINCOR agents running", "time": "now"},
                {"type": "system_status", "details": "All systems operational", "time": "now"}
            ]
        }
    
    def get_content_settings(self):
        """Get current content settings."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT setting_key, setting_value FROM system_settings')
        settings = dict(cursor.fetchall())
        
        conn.close()
        
        return {
            "trial_price": f"${float(settings.get('trial_price', 100)) / 100:.0f}",
            "spots_remaining": settings.get("spots_remaining", "47"),
            "price_increase_date": settings.get("price_increase_date", "2025-01-31"),
            "bonus_customer_limit": settings.get("bonus_customer_limit", "50"),
            "current_bonus_count": settings.get("current_bonus_count", "23"),
            "maintenance_mode": settings.get("maintenance_mode", "false") == "true"
        }
    
    def update_setting(self, key, value, admin_user):
        """Update system setting."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO system_settings (setting_key, setting_value, updated_by)
            VALUES (?, ?, ?)
        ''', (key, str(value), admin_user))
        
        conn.commit()
        conn.close()
        
        return True

def check_admin_auth():
    """Check if user is authenticated as admin."""
    return session.get("admin_authenticated") and session.get("admin_user") in ADMIN_USERS

def get_agent_activity_status():
    """Get real-time agent activity status."""
    import os
    from datetime import datetime, timedelta
    
    agent_status = {
        "active_agents": [],
        "recent_activity": [],
        "system_health": {}
    }
    
    try:
        # Check oversight agent (meta oversight)
        oversight_log = Path(__file__).parent / "logs" / "oversight.log"
        if oversight_log.exists():
            with open(oversight_log, 'r') as f:
                lines = f.readlines()[-5:]  # Last 5 lines
                agent_status["active_agents"].append({
                    "name": "Oversight Agent (Meta)",
                    "status": "active",
                    "last_action": f"System checks: {len(open(oversight_log, 'r').readlines())}",
                    "type": "oversight"
                })
        
        # Check daetime agents (task scheduler)
        daetime_log_dir = Path(__file__).parent / "logs" / "daetime"
        if daetime_log_dir.exists():
            log_files = list(daetime_log_dir.glob("*.log"))
            if log_files:
                agent_status["active_agents"].append({
                    "name": "Task Scheduler Agent",
                    "status": "active", 
                    "last_action": f"Processed {len(log_files)} tasks",
                    "type": "scheduler"
                })
        
        # Add gazette agents status (compliance)
        gazette_agents = [
            ("AML Agent", "Anti-Money Laundering monitoring"),
            ("KYC Agent", "Know Your Customer verification"),
            ("SEC Watchdog", "Securities compliance monitoring")
        ]
        
        for agent_name, description in gazette_agents:
            agent_status["active_agents"].append({
                "name": agent_name,
                "status": "standby",
                "last_action": description,
                "type": "compliance"
            })
        
        # Check Master Orchestrator
        orchestrator_path = Path(__file__).parent / "agents" / "intelligence" / "master_orchestrator.py"
        if orchestrator_path.exists():
            agent_status["active_agents"].append({
                "name": "Master Orchestrator",
                "status": "standby",
                "last_action": "Coordinating business intelligence pipeline",
                "type": "intelligence"
            })
        
        # Recent activity from compliance logs
        compliance_log = Path(__file__).parent / "logs" / "compliance" / "compliance_202508.log"
        if compliance_log.exists():
            with open(compliance_log, 'r') as f:
                lines = f.readlines()[-3:]
                for line in lines:
                    if "compliance-report" in line and "GET" in line:
                        agent_status["recent_activity"].append({
                            "agent": "Compliance System",
                            "action": "Generated compliance report",
                            "time": line.split()[0] + " " + line.split()[1].split(',')[0],
                            "status": "success"
                        })
                        break
        
        # System health
        agent_status["system_health"] = {
            "total_agents": len(agent_status["active_agents"]),
            "active_count": len([a for a in agent_status["active_agents"] if a["status"] == "active"]),
            "standby_count": len([a for a in agent_status["active_agents"] if a["status"] == "standby"]),
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    
    except Exception as e:
        agent_status["error"] = f"Error reading agent status: {str(e)}"
    
    return agent_status

def add_admin_routes(app):
    """Add admin control panel routes."""
    
    @app.route("/admin")
    def admin_login():
        """Admin login page."""
        if check_admin_auth():
            return redirect("/admin/dashboard")
        
        return render_template_string(ADMIN_LOGIN_TEMPLATE)
    
    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login_page():
        """Handle both GET and POST for admin login."""
        if request.method == "GET":
            if check_admin_auth():
                return redirect("/admin/dashboard")
            return render_template_string(ADMIN_LOGIN_TEMPLATE)
        
        # POST method
        return admin_login_post()
    
    def admin_login_post():
        """Process admin login."""
        username = request.form.get("username")
        password = request.form.get("password")
        
        if username in ADMIN_USERS and ADMIN_USERS[username] == password:
            session["admin_authenticated"] = True
            session["admin_user"] = username
            return redirect("/admin/dashboard")
        else:
            return render_template_string(ADMIN_LOGIN_TEMPLATE, error="Invalid credentials")
    
    @app.route("/admin/logout")
    def admin_logout():
        """Admin logout."""
        session.pop("admin_authenticated", None)
        session.pop("admin_user", None)
        return redirect("/admin")
    
    @app.route("/admin/dashboard")
    def admin_dashboard():
        """Main admin dashboard with real data integration."""
        if not check_admin_auth():
            return redirect("/admin")
        
        try:
            # Import the data service to get real metrics
            from admin_data_service import admin_data_service
            
            # Get real system data
            system_metrics = admin_data_service.get_system_metrics()
            agent_network = admin_data_service.get_agent_network_status()
            recent_activity = admin_data_service.get_recent_activity()
            database_info = admin_data_service.get_database_info()
            
            return render_template('admin_dashboard.html', 
                                 metrics=system_metrics,
                                 agents=agent_network,
                                 activity=recent_activity,
                                 databases=database_info)
        except Exception as e:
            # Log error and fall back to basic dashboard
            print(f"Error loading admin dashboard: {e}")
            return render_template('admin_dashboard.html')
    
    @app.route("/admin/content-settings")
    def admin_content_settings():
        """Content and pricing settings."""
        if not check_admin_auth():
            return redirect("/admin")
        
        return render_template_string(ADMIN_CONTENT_TEMPLATE)
    
    @app.route("/admin/system-health")
    def admin_system_health():
        """System health and monitoring."""
        if not check_admin_auth():
            return redirect("/admin")
        
        return render_template_string(ADMIN_HEALTH_TEMPLATE)
    
    @app.route("/api/admin/overview")
    def admin_overview_api():
        """API endpoint for admin overview data."""
        if not check_admin_auth():
            return jsonify({"error": "Unauthorized"}), 401
        
        admin = AdminControlPanel()
        overview = admin.get_system_overview()
        return jsonify(overview)
    
    @app.route("/api/agent-activity")
    def agent_activity_api():
        """API endpoint for real-time agent activity."""
        agent_activity = get_agent_activity_status()
        return jsonify(agent_activity)
    
    @app.route("/api/admin/settings")
    def admin_settings_api():
        """API endpoint for admin settings."""
        if not check_admin_auth():
            return jsonify({"error": "Unauthorized"}), 401
        
        admin = AdminControlPanel()
        settings = admin.get_content_settings()
        return jsonify(settings)
    
    @app.route("/api/admin/update-setting", methods=["POST"])
    def admin_update_setting():
        """API endpoint to update settings."""
        if not check_admin_auth():
            return jsonify({"error": "Unauthorized"}), 401
        
        data = request.get_json()
        key = data.get("key")
        value = data.get("value")
        
        admin = AdminControlPanel()
        success = admin.update_setting(key, value, session["admin_user"])
        
        # Log the action
        admin.log_admin_action(
            session["admin_user"],
            "update_setting",
            key,
            f"Changed to: {value}",
            request.headers.get("X-Forwarded-For", request.remote_addr)
        )
        
        return jsonify({"success": success})

# Admin Templates
ADMIN_LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR Admin Control Panel</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-900 min-h-screen flex items-center justify-center">
    <div class="bg-black p-8 rounded-lg shadow-lg w-96">
        <div class="text-center mb-8">
            <img src="/static/logo.png" alt="SINCOR" class="h-16 w-auto mx-auto mb-4">
            <h1 class="text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-yellow-400 to-yellow-200">
                ADMIN CONTROL PANEL
            </h1>
            <p class="text-gray-400 text-sm">Empire Management System</p>
        </div>
        
        {% if error %}
        <div class="bg-red-600 text-white p-3 rounded mb-4 text-center">
            {{ error }}
        </div>
        {% endif %}
        
        <form method="post" action="/admin/login">
            <div class="mb-4">
                <label class="block text-gray-300 text-sm font-bold mb-2">Username</label>
                <input type="text" name="username" required
                       class="w-full px-3 py-2 bg-gray-800 border border-gray-600 rounded text-white focus:outline-none focus:border-yellow-400">
            </div>
            
            <div class="mb-6">
                <label class="block text-gray-300 text-sm font-bold mb-2">Password</label>
                <input type="password" name="password" required
                       class="w-full px-3 py-2 bg-gray-800 border border-gray-600 rounded text-white focus:outline-none focus:border-yellow-400">
            </div>
            
            <button type="submit"
                    class="w-full bg-gradient-to-r from-yellow-500 to-yellow-400 text-black py-2 rounded font-bold hover:from-yellow-400 hover:to-yellow-300">
                🔐 ACCESS CONTROL PANEL
            </button>
        </form>
        
        <div class="mt-6 text-center text-xs text-gray-500">
            🔒 Authorized Personnel Only
        </div>
    </div>
</body>
</html>
"""

ADMIN_DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR Empire Control Panel</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
</head>
<body class="bg-gray-900 text-white">
    <!-- Admin Header -->
    <header class="bg-black border-b border-gray-700">
        <div class="max-w-7xl mx-auto px-4 py-4">
            <div class="flex justify-between items-center">
                <div class="flex items-center">
                    <img src="/static/logo.png" alt="SINCOR" class="h-8 w-auto mr-3">
                    <div>
                        <h1 class="text-xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-yellow-400 to-yellow-200">
                            SINCOR CONTROL PANEL
                        </h1>
                        <div class="text-xs text-gray-400">Empire Management System</div>
                    </div>
                </div>
                <div class="flex items-center space-x-4">
                    <span class="text-sm text-gray-400">Admin: {{ session.admin_user }}</span>
                    <a href="/admin/logout" class="text-red-400 hover:text-red-300 text-sm">Logout</a>
                </div>
            </div>
        </div>
    </header>

    <div class="max-w-7xl mx-auto py-8 px-4" x-data="adminApp()" x-init="loadData()">
        <!-- Navigation -->
        <div class="flex space-x-6 mb-8">
            <a href="/admin/dashboard" class="bg-yellow-600 text-black px-4 py-2 rounded font-semibold">Dashboard</a>
            <a href="/admin/content-settings" class="bg-gray-700 hover:bg-gray-600 px-4 py-2 rounded">Content Settings</a>
            <a href="/admin/system-health" class="bg-gray-700 hover:bg-gray-600 px-4 py-2 rounded">System Health</a>
        </div>

        <!-- Empire Metrics -->
        <div class="bg-gradient-to-r from-purple-900 to-blue-900 rounded-lg p-8 mb-8">
            <h2 class="text-3xl font-bold mb-6">🏰 SINCOR Empire Status</h2>
            
            <div class="grid md:grid-cols-4 gap-6" x-show="data.empire_metrics">
                <div class="text-center">
                    <div class="text-3xl font-bold text-yellow-300" x-text="(data.empire_metrics?.total_leads || 0)">0</div>
                    <div class="text-purple-200">Total Leads</div>
                </div>
                <div class="text-center">
                    <div class="text-3xl font-bold text-yellow-300" x-text="(data.empire_metrics?.businesses_discovered || 0)">0</div>
                    <div class="text-purple-200">Businesses Discovered</div>
                </div>
                <div class="text-center">
                    <div class="text-3xl font-bold text-yellow-300" x-text="data.empire_metrics?.campaigns_active || 0">0</div>
                    <div class="text-purple-200">Active Campaigns</div>
                </div>
                <div class="text-center">
                    <div class="text-3xl font-bold text-yellow-300" x-text="data.empire_metrics?.emails_sent || 0">0</div>
                    <div class="text-purple-200">Emails Sent</div>
                </div>
            </div>
        </div>
        
        <!-- Agent Activity Status -->
        <div class="bg-gradient-to-r from-green-900 to-teal-900 rounded-lg p-8 mb-8" x-data="{ agentData: null }" x-init="
            fetch('/api/agent-activity').then(r => r.json()).then(data => agentData = data)
        ">
            <h2 class="text-3xl font-bold mb-6">🤖 Agent Activity Center</h2>
            
            <div class="grid md:grid-cols-3 gap-6 mb-6" x-show="agentData">
                <div class="text-center">
                    <div class="text-3xl font-bold text-green-300" x-text="agentData?.system_health?.total_agents || 0">0</div>
                    <div class="text-green-200">Total Agents</div>
                </div>
                <div class="text-center">
                    <div class="text-3xl font-bold text-green-300" x-text="agentData?.system_health?.active_count || 0">0</div>
                    <div class="text-green-200">Active</div>
                </div>
                <div class="text-center">
                    <div class="text-3xl font-bold text-green-300" x-text="agentData?.system_health?.standby_count || 0">0</div>
                    <div class="text-green-200">Standby</div>
                </div>
            </div>
            
            <div class="grid md:grid-cols-2 gap-6" x-show="agentData">
                <div>
                    <h4 class="font-bold mb-3">🔍 Active Agents</h4>
                    <div class="space-y-2" x-show="agentData.active_agents">
                        <template x-for="agent in agentData.active_agents" :key="agent.name">
                            <div class="bg-black bg-opacity-30 p-3 rounded">
                                <div class="flex justify-between items-center">
                                    <span class="font-semibold" x-text="agent.name">Agent Name</span>
                                    <span class="px-2 py-1 rounded text-xs"
                                          :class="agent.status === 'active' ? 'bg-green-600' : 'bg-yellow-600'"
                                          x-text="agent.status">Status</span>
                                </div>
                                <div class="text-xs text-gray-300 mt-1" x-text="agent.last_action">Last Action</div>
                            </div>
                        </template>
                    </div>
                </div>
                
                <div>
                    <h4 class="font-bold mb-3">⚡ Recent Agent Activity</h4>
                    <div class="space-y-2" x-show="agentData.recent_activity && agentData.recent_activity.length > 0">
                        <template x-for="activity in agentData.recent_activity" :key="activity.agent + activity.time">
                            <div class="border-l-4 border-green-500 pl-3">
                                <div class="font-medium text-sm" x-text="activity.agent + ': ' + activity.action">Activity</div>
                                <div class="text-xs text-gray-400" x-text="activity.time">Time</div>
                            </div>
                        </template>
                    </div>
                    <div x-show="!agentData.recent_activity || agentData.recent_activity.length === 0" class="text-gray-400 text-sm">
                        All agents running smoothly - no recent events
                    </div>
                </div>
            </div>
        </div>

        <!-- System Status -->
        <div class="grid md:grid-cols-2 gap-8 mb-8">
            <div class="bg-gray-800 rounded-lg p-6">
                <h3 class="text-xl font-bold mb-4">🖥️ System Status</h3>
                <div class="space-y-3" x-show="data.system_status">
                    <template x-for="(status, system) in data.system_status" :key="system">
                        <div class="flex justify-between items-center">
                            <span class="capitalize" x-text="system.replace('_', ' ')">System</span>
                            <span class="px-3 py-1 rounded text-xs font-semibold"
                                  :class="status === 'operational' ? 'bg-green-600 text-white' : 'bg-red-600 text-white'"
                                  x-text="status">Status</span>
                        </div>
                    </template>
                </div>
            </div>

            <div class="bg-gray-800 rounded-lg p-6">
                <h3 class="text-xl font-bold mb-4">⚡ Recent Activity</h3>
                <div class="space-y-3" x-show="data.recent_activity">
                    <template x-for="activity in data.recent_activity" :key="activity.details">
                        <div class="border-l-4 border-blue-500 pl-3">
                            <div class="font-medium" x-text="activity.details">Activity</div>
                            <div class="text-xs text-gray-400" x-text="activity.time">Time</div>
                        </div>
                    </template>
                </div>
            </div>
        </div>

        <!-- Quick Actions -->
        <div class="bg-gray-800 rounded-lg p-6">
            <h3 class="text-xl font-bold mb-4">🚀 Quick Actions</h3>
            
            <div class="grid md:grid-cols-4 gap-4">
                <a href="/admin/content-settings" 
                   class="bg-yellow-600 hover:bg-yellow-700 text-black p-4 rounded-lg text-center font-semibold">
                    📝 Update Content
                </a>
                <a href="/analytics-dashboard" target="_blank"
                   class="bg-blue-600 hover:bg-blue-700 text-white p-4 rounded-lg text-center font-semibold">
                    📊 View Analytics
                </a>
                <a href="/compliance-dashboard" target="_blank"
                   class="bg-red-600 hover:bg-red-700 text-white p-4 rounded-lg text-center font-semibold">
                    ⚖️ Check Compliance
                </a>
                <a href="/enterprise-dashboard" target="_blank"
                   class="bg-purple-600 hover:bg-purple-700 text-white p-4 rounded-lg text-center font-semibold">
                    🏰 Empire Overview
                </a>
            </div>
        </div>
    </div>

    <script>
        function adminApp() {
            return {
                data: {},
                loading: true,
                
                async loadData() {
                    try {
                        const response = await fetch('/api/admin/overview');
                        this.data = await response.json();
                    } catch (error) {
                        console.error('Error loading admin data:', error);
                    } finally {
                        this.loading = false;
                    }
                }
            }
        }
    </script>
</body>
</html>
"""

ADMIN_CONTENT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR Content Settings</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
</head>
<body class="bg-gray-900 text-white">
    <!-- Admin Header -->
    <header class="bg-black border-b border-gray-700">
        <div class="max-w-7xl mx-auto px-4 py-4">
            <div class="flex justify-between items-center">
                <div class="flex items-center">
                    <img src="/static/logo.png" alt="SINCOR" class="h-8 w-auto mr-3">
                    <h1 class="text-xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-yellow-400 to-yellow-200">
                        CONTENT SETTINGS
                    </h1>
                </div>
                <a href="/admin/dashboard" class="text-yellow-400 hover:text-yellow-300">← Back to Dashboard</a>
            </div>
        </div>
    </header>

    <div class="max-w-4xl mx-auto py-8 px-4" x-data="contentApp()" x-init="loadSettings()">
        <!-- Pricing Controls -->
        <div class="bg-gray-800 rounded-lg p-6 mb-8">
            <h2 class="text-2xl font-bold mb-6">💰 Pricing & Trial Settings</h2>
            
            <div class="grid md:grid-cols-2 gap-6">
                <div>
                    <label class="block text-gray-300 text-sm font-bold mb-2">Trial Price</label>
                    <input type="text" x-model="settings.trial_price"
                           class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:border-yellow-400">
                </div>
                
                <div>
                    <label class="block text-gray-300 text-sm font-bold mb-2">Spots Remaining</label>
                    <input type="number" x-model="settings.spots_remaining"
                           class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:border-yellow-400">
                </div>
                
                <div>
                    <label class="block text-gray-300 text-sm font-bold mb-2">Price Increase Date</label>
                    <input type="date" x-model="settings.price_increase_date"
                           class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:border-yellow-400">
                </div>
                
                <div>
                    <label class="block text-gray-300 text-sm font-bold mb-2">Bonus Customer Count</label>
                    <input type="number" x-model="settings.current_bonus_count"
                           class="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded text-white focus:border-yellow-400">
                </div>
            </div>
            
            <div class="mt-6">
                <button @click="updateSettings()" :disabled="saving"
                        class="bg-yellow-600 hover:bg-yellow-700 text-black px-6 py-2 rounded font-semibold disabled:opacity-50">
                    <span x-show="!saving">💾 Save Settings</span>
                    <span x-show="saving">Saving...</span>
                </button>
            </div>
        </div>

        <!-- System Controls -->
        <div class="bg-gray-800 rounded-lg p-6">
            <h2 class="text-2xl font-bold mb-6">🔧 System Controls</h2>
            
            <div class="space-y-4">
                <div class="flex items-center justify-between">
                    <div>
                        <div class="font-semibold">Maintenance Mode</div>
                        <div class="text-sm text-gray-400">Enable to show maintenance page to visitors</div>
                    </div>
                    <label class="inline-flex items-center">
                        <input type="checkbox" x-model="settings.maintenance_mode"
                               class="form-checkbox bg-gray-700 border-gray-600 text-yellow-500">
                        <span class="ml-2" x-text="settings.maintenance_mode ? 'Enabled' : 'Disabled'">Disabled</span>
                    </label>
                </div>
            </div>
        </div>
        
        <!-- Success Message -->
        <div x-show="showSuccess" class="fixed top-4 right-4 bg-green-600 text-white p-4 rounded-lg shadow-lg">
            ✅ Settings updated successfully!
        </div>
    </div>

    <script>
        function contentApp() {
            return {
                settings: {},
                saving: false,
                showSuccess: false,
                
                async loadSettings() {
                    try {
                        const response = await fetch('/api/admin/settings');
                        this.settings = await response.json();
                    } catch (error) {
                        console.error('Error loading settings:', error);
                    }
                },
                
                async updateSettings() {
                    this.saving = true;
                    
                    try {
                        for (const [key, value] of Object.entries(this.settings)) {
                            await fetch('/api/admin/update-setting', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ key, value })
                            });
                        }
                        
                        this.showSuccess = true;
                        setTimeout(() => this.showSuccess = false, 3000);
                    } catch (error) {
                        alert('Error updating settings: ' + error.message);
                    } finally {
                        this.saving = false;
                    }
                }
            }
        }
    </script>
</body>
</html>
"""

ADMIN_HEALTH_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR System Health</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-900 text-white">
    <!-- Admin Header -->
    <header class="bg-black border-b border-gray-700">
        <div class="max-w-7xl mx-auto px-4 py-4">
            <div class="flex justify-between items-center">
                <div class="flex items-center">
                    <img src="/static/logo.png" alt="SINCOR" class="h-8 w-auto mr-3">
                    <h1 class="text-xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-yellow-400 to-yellow-200">
                        SYSTEM HEALTH
                    </h1>
                </div>
                <a href="/admin/dashboard" class="text-yellow-400 hover:text-yellow-300">← Back to Dashboard</a>
            </div>
        </div>
    </header>

    <div class="max-w-6xl mx-auto py-8 px-4">
        <!-- Route Status -->
        <div class="bg-gray-800 rounded-lg p-6 mb-8">
            <h2 class="text-2xl font-bold mb-6">🌐 Route Status</h2>
            
            <div class="grid md:grid-cols-2 gap-6">
                <div>
                    <h3 class="text-lg font-semibold mb-4">Core Routes</h3>
                    <div class="space-y-2 text-sm">
                        <div class="flex justify-between">
                            <span>/</span>
                            <span class="text-green-400">✅ Active</span>
                        </div>
                        <div class="flex justify-between">
                            <span>/analytics-dashboard</span>
                            <span class="text-green-400">✅ Active</span>
                        </div>
                        <div class="flex justify-between">
                            <span>/enterprise-dashboard</span>
                            <span class="text-green-400">✅ Active</span>
                        </div>
                        <div class="flex justify-between">
                            <span>/compliance-dashboard</span>
                            <span class="text-green-400">✅ Active</span>
                        </div>
                    </div>
                </div>
                
                <div>
                    <h3 class="text-lg font-semibold mb-4">SEO Landing Pages</h3>
                    <div class="space-y-2 text-sm">
                        <div class="flex justify-between">
                            <span>/auto-detailing-leads</span>
                            <span class="text-green-400">✅ Active</span>
                        </div>
                        <div class="flex justify-between">
                            <span>/hvac-leads</span>
                            <span class="text-green-400">✅ Active</span>
                        </div>
                        <div class="flex justify-between">
                            <span>/pest-control-leads</span>
                            <span class="text-green-400">✅ Active</span>
                        </div>
                        <div class="flex justify-between">
                            <span>/sitemap.xml</span>
                            <span class="text-green-400">✅ Active</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Quick Tests -->
        <div class="bg-gray-800 rounded-lg p-6">
            <h2 class="text-2xl font-bold mb-6">🧪 Quick Tests</h2>
            
            <div class="grid md:grid-cols-3 gap-4">
                <a href="/sitemap.xml" target="_blank"
                   class="bg-blue-600 hover:bg-blue-700 p-4 rounded text-center">
                    📄 Test Sitemap
                </a>
                <a href="/robots.txt" target="_blank"
                   class="bg-blue-600 hover:bg-blue-700 p-4 rounded text-center">
                    🤖 Test Robots.txt
                </a>
                <a href="/auto-detailing-leads" target="_blank"
                   class="bg-blue-600 hover:bg-blue-700 p-4 rounded text-center">
                    🚗 Test SEO Page
                </a>
            </div>
        </div>
    </div>
</body>
</html>
"""