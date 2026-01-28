#!/usr/bin/env python3
"""
Production Oversight Dashboard
Real-time accountability system for CAD leads and SINCOR revenue
"""

import sqlite3
import json
from datetime import datetime, timedelta
from flask import Flask, render_template_string
import os

app = Flask(__name__)

class ProductionOversight:
    def __init__(self):
        self.cad_db = "clinton_auto_detailing/instance/app.db"  # CAD SQLite
        self.sincor_results_db = "sincor_results.db"  # SINCOR tracking
        
    def get_cad_metrics(self):
        """Get real CAD lead metrics"""
        try:
            conn = sqlite3.connect(self.cad_db)
            cursor = conn.cursor()
            
            # Last 7 days leads
            week_ago = (datetime.now() - timedelta(days=7)).isoformat()
            
            cursor.execute("""
                SELECT COUNT(*) as total_leads,
                       COUNT(CASE WHEN created_at > ? THEN 1 END) as leads_7_days
                FROM lead
            """, (week_ago,))
            
            lead_data = cursor.fetchone()
            
            # Revenue tracking (if available)
            cursor.execute("""
                SELECT COUNT(*) as total_users
                FROM user 
            """)
            
            user_data = cursor.fetchone()
            conn.close()
            
            return {
                'total_leads': lead_data[0] if lead_data else 0,
                'leads_7_days': lead_data[1] if lead_data else 0,
                'conversion_rate': 0,  # Calculate based on bookings
                'revenue_7_days': 0,  # Track actual bookings
                'last_updated': datetime.now().isoformat()
            }
        except Exception as e:
            return {
                'total_leads': 0,
                'leads_7_days': 0,
                'conversion_rate': 0,
                'revenue_7_days': 0,
                'error': str(e),
                'last_updated': datetime.now().isoformat()
            }
    
    def get_sincor_metrics(self):
        """Get SINCOR business discovery and revenue metrics"""
        try:
            # Create SINCOR tracking DB if not exists
            conn = sqlite3.connect(self.sincor_results_db)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS businesses_discovered (
                    id INTEGER PRIMARY KEY,
                    business_name TEXT,
                    city TEXT,
                    state TEXT,
                    phone TEXT,
                    discovery_date TEXT,
                    contacted BOOLEAN DEFAULT 0,
                    proposal_sent BOOLEAN DEFAULT 0,
                    sale_amount REAL DEFAULT 0
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS media_pack_sales (
                    id INTEGER PRIMARY KEY,
                    business_name TEXT,
                    sale_amount REAL,
                    sale_date TEXT,
                    package_type TEXT
                )
            """)
            
            # Get metrics
            week_ago = (datetime.now() - timedelta(days=7)).isoformat()
            
            cursor.execute("""
                SELECT COUNT(*) as total_businesses,
                       COUNT(CASE WHEN discovery_date > ? THEN 1 END) as businesses_7_days,
                       COUNT(CASE WHEN contacted = 1 THEN 1 END) as contacted,
                       COUNT(CASE WHEN proposal_sent = 1 THEN 1 END) as proposals_sent
                FROM businesses_discovered
            """, (week_ago,))
            
            business_data = cursor.fetchone()
            
            cursor.execute("""
                SELECT COUNT(*) as total_sales,
                       SUM(sale_amount) as total_revenue,
                       COUNT(CASE WHEN sale_date > ? THEN 1 END) as sales_7_days,
                       SUM(CASE WHEN sale_date > ? THEN sale_amount ELSE 0 END) as revenue_7_days
                FROM media_pack_sales
            """, (week_ago, week_ago))
            
            sales_data = cursor.fetchone()
            conn.close()
            
            return {
                'businesses_discovered': business_data[0] if business_data else 0,
                'businesses_7_days': business_data[1] if business_data else 0,
                'contacted': business_data[2] if business_data else 0,
                'proposals_sent': business_data[3] if business_data else 0,
                'total_sales': sales_data[0] if sales_data else 0,
                'total_revenue': sales_data[1] if sales_data else 0,
                'sales_7_days': sales_data[2] if sales_data else 0,
                'revenue_7_days': sales_data[3] if sales_data else 0,
                'last_updated': datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                'businesses_discovered': 0,
                'businesses_7_days': 0,
                'contacted': 0,
                'proposals_sent': 0,
                'total_sales': 0,
                'total_revenue': 0,
                'sales_7_days': 0,
                'revenue_7_days': 0,
                'error': str(e),
                'last_updated': datetime.now().isoformat()
            }

oversight = ProductionOversight()

# Dashboard HTML Template
DASHBOARD_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Production Oversight - CAD + SINCOR</title>
    <meta http-equiv="refresh" content="30">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { text-align: center; color: #333; margin-bottom: 30px; }
        .metrics-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 30px; }
        .section { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .metric { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #eee; }
        .metric:last-child { border-bottom: none; }
        .metric-label { font-weight: bold; }
        .metric-value { color: #007bff; font-weight: bold; }
        .target { background: #fff3cd; border: 1px solid #ffeaa7; padding: 15px; border-radius: 5px; margin: 15px 0; }
        .status-good { color: #28a745; }
        .status-warning { color: #ffc107; }
        .status-danger { color: #dc3545; }
        .update-time { text-align: center; color: #666; margin-top: 20px; font-size: 0.9em; }
        .accountability { background: #e3f2fd; border: 2px solid #2196f3; padding: 20px; border-radius: 8px; margin: 20px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="header">PRODUCTION OVERSIGHT DASHBOARD</h1>
        <h2 class="header" style="color: #666;">7-Day Proof of Concept - Sales Before Scale</h2>
        
        <div class="accountability">
            <h3>ACCOUNTABILITY TARGETS - NEXT 7 DAYS:</h3>
            <p><strong>CAD:</strong> 5+ leads, 2+ appointments, $250+ revenue</p>
            <p><strong>SINCOR:</strong> 25+ businesses found, 3+ proposals, 1+ sale at $500+</p>
            <p><strong>SCALE TRIGGER:</strong> Hit all targets = Scale x1000</p>
        </div>
        
        <div class="metrics-grid">
            <div class="section">
                <h2>CAD (Clinton Auto Detailing)</h2>
                <div class="target">
                    <strong>TARGET:</strong> Real leads flowing to Clinton, IA business
                </div>
                
                <div class="metric">
                    <span class="metric-label">Total Leads:</span>
                    <span class="metric-value">{{ cad.total_leads }}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Leads (7 days):</span>
                    <span class="metric-value {{ 'status-good' if cad.leads_7_days >= 5 else 'status-warning' if cad.leads_7_days >= 2 else 'status-danger' }}">
                        {{ cad.leads_7_days }}/5 TARGET
                    </span>
                </div>
                <div class="metric">
                    <span class="metric-label">Revenue (7 days):</span>
                    <span class="metric-value {{ 'status-good' if cad.revenue_7_days >= 250 else 'status-warning' if cad.revenue_7_days >= 100 else 'status-danger' }}">
                        ${{ cad.revenue_7_days }}/$250 TARGET
                    </span>
                </div>
                <div class="metric">
                    <span class="metric-label">Conversion Rate:</span>
                    <span class="metric-value">{{ "%.1f"|format(cad.conversion_rate) }}%</span>
                </div>
                
                {% if cad.error %}
                <div style="color: red; margin-top: 10px;">Error: {{ cad.error }}</div>
                {% endif %}
            </div>
            
            <div class="section">
                <h2>SINCOR (Business Discovery)</h2>
                <div class="target">
                    <strong>TARGET:</strong> Find other detailing businesses nationwide, sell media packs
                </div>
                
                <div class="metric">
                    <span class="metric-label">Businesses Found:</span>
                    <span class="metric-value">{{ sincor.businesses_discovered }}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Found (7 days):</span>
                    <span class="metric-value {{ 'status-good' if sincor.businesses_7_days >= 25 else 'status-warning' if sincor.businesses_7_days >= 10 else 'status-danger' }}">
                        {{ sincor.businesses_7_days }}/25 TARGET
                    </span>
                </div>
                <div class="metric">
                    <span class="metric-label">Contacted:</span>
                    <span class="metric-value">{{ sincor.contacted }}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Proposals Sent:</span>
                    <span class="metric-value {{ 'status-good' if sincor.proposals_sent >= 3 else 'status-warning' if sincor.proposals_sent >= 1 else 'status-danger' }}">
                        {{ sincor.proposals_sent }}/3 TARGET
                    </span>
                </div>
                <div class="metric">
                    <span class="metric-label">Sales (7 days):</span>
                    <span class="metric-value {{ 'status-good' if sincor.sales_7_days >= 1 else 'status-danger' }}">
                        {{ sincor.sales_7_days }}/1 TARGET
                    </span>
                </div>
                <div class="metric">
                    <span class="metric-label">Revenue (7 days):</span>
                    <span class="metric-value {{ 'status-good' if sincor.revenue_7_days >= 500 else 'status-warning' if sincor.revenue_7_days >= 200 else 'status-danger' }}">
                        ${{ sincor.revenue_7_days }}/$500 TARGET
                    </span>
                </div>
                
                {% if sincor.error %}
                <div style="color: red; margin-top: 10px;">Error: {{ sincor.error }}</div>
                {% endif %}
            </div>
        </div>
        
        <div class="update-time">
            Last Updated: {{ update_time }}<br>
            Auto-refresh every 30 seconds
        </div>
        
        <div style="text-align: center; margin: 30px 0;">
            <h3>SCALE DECISION MATRIX:</h3>
            <p><strong>GREEN:</strong> All targets hit = SCALE x1000</p>
            <p><strong>YELLOW:</strong> Partial success = Optimize and retry</p>
            <p><strong>RED:</strong> Major issues = Fix fundamental problems</p>
        </div>
    </div>
</body>
</html>
'''

@app.route('/')
def dashboard():
    """Production oversight dashboard"""
    cad_metrics = oversight.get_cad_metrics()
    sincor_metrics = oversight.get_sincor_metrics()
    
    return render_template_string(
        DASHBOARD_TEMPLATE,
        cad=cad_metrics,
        sincor=sincor_metrics,
        update_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

@app.route('/api/cad')
def api_cad():
    """CAD metrics API"""
    return oversight.get_cad_metrics()

@app.route('/api/sincor') 
def api_sincor():
    """SINCOR metrics API"""
    return oversight.get_sincor_metrics()

if __name__ == "__main__":
    print("Production Oversight Dashboard starting...")
    print("7-Day Proof of Concept - Sales Before Scale x1000")
    print("Access dashboard: http://localhost:5002")
    app.run(host='0.0.0.0', port=5002, debug=True)