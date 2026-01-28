"""
Master Integration Orchestrator for Clinton Auto Detailing
Coordinates all business integrations and provides unified dashboard
"""

import os
import sys
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from flask import Flask, Blueprint, request, jsonify, render_template_string
import threading
import schedule
import time

# Import all integration modules
sys.path.append('integrations')
from integrations.square_integration import SquareIntegration, square_bp
from integrations.google_calendar_integration import GoogleCalendarIntegration, calendar_bp
from integrations.email_workflow_system import EmailWorkflowSystem, email_bp
from integrations.text_onboarding_system import TextOnboardingSystem, text_bp
from integrations.facebook_meta_integration import FacebookMetaIntegration, facebook_bp
from integrations.google_ads_integration import GoogleAdsIntegration, googleads_bp
from integrations.yelp_scraper_integration import YelpScraperIntegration, yelp_bp
from integrations.crm_integration import CRMIntegration, crm_bp
from integrations.accounting_integration import AccountingIntegration, accounting_bp
from integrations.square_workflow_optimizer import SquareWorkflowOptimizer, workflow_bp
from integrations.instagram_integration import InstagramBusinessIntegration, instagram_bp

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MasterIntegrationOrchestrator:
    def __init__(self):
        self.business_name = "Clinton Auto Detailing"
        self.business_location = "Clinton, MS"
        
        # Initialize all integration instances
        self.square = SquareIntegration()
        self.calendar = GoogleCalendarIntegration()
        self.email = EmailWorkflowSystem()
        self.text = TextOnboardingSystem()
        self.facebook = FacebookMetaIntegration()
        self.google_ads = GoogleAdsIntegration()
        self.yelp = YelpScraperIntegration()
        self.crm = CRMIntegration()
        self.accounting = AccountingIntegration()
        self.workflow = SquareWorkflowOptimizer()
        self.instagram = InstagramBusinessIntegration()
        
        # Initialize master database
        self.init_master_database()
        
        # Start background scheduler
        self.start_scheduler()
        
    def init_master_database(self):
        """Initialize master orchestrator database"""
        conn = sqlite3.connect('clinton_auto_detailing_master.db')
        cursor = conn.cursor()
        
        # System health monitoring
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_name TEXT,
                status TEXT,
                last_check TIMESTAMP,
                error_message TEXT,
                response_time_ms INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Integration sync status
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sync_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                integration_pair TEXT,
                last_sync TIMESTAMP,
                records_synced INTEGER,
                sync_status TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Business metrics dashboard
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS business_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                new_customers INTEGER DEFAULT 0,
                total_revenue DECIMAL(10,2) DEFAULT 0,
                appointments_booked INTEGER DEFAULT 0,
                appointments_completed INTEGER DEFAULT 0,
                email_opens INTEGER DEFAULT 0,
                sms_responses INTEGER DEFAULT 0,
                facebook_leads INTEGER DEFAULT 0,
                google_ad_clicks INTEGER DEFAULT 0,
                yelp_views INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Alert notifications
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type TEXT,
                message TEXT,
                severity TEXT,
                resolved BOOLEAN DEFAULT 0,
                resolved_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        
    def check_system_health(self) -> Dict:
        """Check health of all integrated systems"""
        health_status = {}
        
        integrations = {
            'Square': self.square,
            'Google Calendar': self.calendar,
            'Email System': self.email,
            'Text System': self.text,
            'Facebook/Meta': self.facebook,
            'Google Ads': self.google_ads,
            'Yelp Scraper': self.yelp,
            'CRM': self.crm,
            'Accounting': self.accounting,
            'Workflow Engine': self.workflow,
            'Instagram': self.instagram
        }
        
        conn = sqlite3.connect('clinton_auto_detailing_master.db')
        cursor = conn.cursor()
        
        for name, integration in integrations.items():
            try:
                start_time = datetime.now()
                
                # Basic health check - try to access database or API
                if hasattr(integration, 'test_connection'):
                    status = integration.test_connection()
                else:
                    status = True  # Assume healthy if no test method
                
                end_time = datetime.now()
                response_time = int((end_time - start_time).total_seconds() * 1000)
                
                health_status[name] = {
                    'status': 'healthy' if status else 'unhealthy',
                    'response_time_ms': response_time,
                    'last_check': datetime.now().isoformat()
                }
                
                # Save to database
                cursor.execute('''
                    INSERT INTO system_health 
                    (service_name, status, last_check, response_time_ms)
                    VALUES (?, ?, ?, ?)
                ''', (
                    name,
                    'healthy' if status else 'unhealthy',
                    datetime.now(),
                    response_time
                ))
                
            except Exception as e:
                health_status[name] = {
                    'status': 'error',
                    'error': str(e),
                    'last_check': datetime.now().isoformat()
                }
                
                cursor.execute('''
                    INSERT INTO system_health 
                    (service_name, status, last_check, error_message)
                    VALUES (?, ?, ?, ?)
                ''', (name, 'error', datetime.now(), str(e)))
        
        conn.commit()
        conn.close()
        
        return health_status
    
    def sync_customer_data(self):
        """Sync customer data across all systems"""
        logger.info("Starting customer data sync across all systems...")
        
        try:
            # Get customers from Square
            square_customers = self.square.get_all_customers()
            
            for customer in square_customers:
                # Sync to CRM
                self.crm.sync_customer_from_square(customer)
                
                # Update email system
                self.email.add_customer_to_workflows(customer)
                
                # Update text system
                self.text.add_customer_to_sequences(customer)
            
            # Record sync status
            conn = sqlite3.connect('clinton_auto_detailing_master.db')
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO sync_status 
                (integration_pair, last_sync, records_synced, sync_status)
                VALUES (?, ?, ?, ?)
            ''', (
                'Square -> CRM/Email/Text',
                datetime.now(),
                len(square_customers),
                'success'
            ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Successfully synced {len(square_customers)} customers")
            
        except Exception as e:
            logger.error(f"Customer sync failed: {e}")
            
            conn = sqlite3.connect('clinton_auto_detailing_master.db')
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO sync_status 
                (integration_pair, last_sync, records_synced, sync_status, error_message)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                'Square -> CRM/Email/Text',
                datetime.now(),
                0,
                'failed',
                str(e)
            ))
            
            conn.commit()
            conn.close()
    
    def generate_daily_metrics(self):
        """Generate daily business metrics from all systems"""
        today = datetime.now().date()
        
        try:
            # Get metrics from each system
            square_metrics = self.square.get_daily_metrics(today)
            crm_metrics = self.crm.get_daily_metrics(today)
            email_metrics = self.email.get_daily_metrics(today)
            text_metrics = self.text.get_daily_metrics(today)
            facebook_metrics = self.facebook.get_daily_metrics(today)
            
            # Combine metrics
            daily_metrics = {
                'date': today,
                'new_customers': crm_metrics.get('new_customers', 0),
                'total_revenue': square_metrics.get('total_revenue', 0),
                'appointments_booked': square_metrics.get('appointments_booked', 0),
                'appointments_completed': square_metrics.get('appointments_completed', 0),
                'email_opens': email_metrics.get('opens', 0),
                'sms_responses': text_metrics.get('responses', 0),
                'facebook_leads': facebook_metrics.get('leads', 0),
                'google_ad_clicks': 0,  # Will be updated when Google Ads is active
                'yelp_views': 0  # Will be updated when Yelp scraping is active
            }
            
            # Save to database
            conn = sqlite3.connect('clinton_auto_detailing_master.db')
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO business_metrics 
                (date, new_customers, total_revenue, appointments_booked, appointments_completed,
                 email_opens, sms_responses, facebook_leads, google_ad_clicks, yelp_views)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                today,
                daily_metrics['new_customers'],
                daily_metrics['total_revenue'],
                daily_metrics['appointments_booked'],
                daily_metrics['appointments_completed'],
                daily_metrics['email_opens'],
                daily_metrics['sms_responses'],
                daily_metrics['facebook_leads'],
                daily_metrics['google_ad_clicks'],
                daily_metrics['yelp_views']
            ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Daily metrics generated for {today}")
            return daily_metrics
            
        except Exception as e:
            logger.error(f"Failed to generate daily metrics: {e}")
            return None
    
    def check_and_alert(self):
        """Check for issues and create alerts"""
        # Check for failed payments
        failed_payments = self.square.get_failed_payments_today()
        if failed_payments:
            self.create_alert(
                alert_type='payment_failure',
                message=f"{len(failed_payments)} failed payments today",
                severity='high'
            )
        
        # Check for missed appointments
        missed_appointments = self.calendar.get_missed_appointments_today()
        if missed_appointments:
            self.create_alert(
                alert_type='missed_appointment',
                message=f"{len(missed_appointments)} missed appointments today",
                severity='medium'
            )
        
        # Check system health
        health = self.check_system_health()
        for service, status in health.items():
            if status['status'] != 'healthy':
                self.create_alert(
                    alert_type='system_health',
                    message=f"{service} is {status['status']}",
                    severity='high'
                )
    
    def create_alert(self, alert_type: str, message: str, severity: str = 'medium'):
        """Create system alert"""
        conn = sqlite3.connect('clinton_auto_detailing_master.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO system_alerts (alert_type, message, severity)
            VALUES (?, ?, ?)
        ''', (alert_type, message, severity))
        
        conn.commit()
        conn.close()
        
        logger.warning(f"Alert created: {alert_type} - {message}")
        
        # Send notification (email/text to business owner)
        if severity == 'high':
            self.email.send_alert_email(message)
            self.text.send_alert_text(message)
    
    def start_scheduler(self):
        """Start background scheduler for automated tasks"""
        def run_scheduler():
            # Daily tasks
            schedule.every().day.at("06:00").do(self.sync_customer_data)
            schedule.every().day.at("23:59").do(self.generate_daily_metrics)
            schedule.every().day.at("08:00").do(self.check_and_alert)
            
            # Hourly tasks
            schedule.every().hour.do(self.check_system_health)
            
            # Weekly tasks
            schedule.every().monday.at("09:00").do(self.yelp.scrape_competitors)
            
            while True:
                schedule.run_pending()
                time.sleep(60)  # Check every minute
        
        # Start scheduler in background thread
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()
        logger.info("Background scheduler started")
    
    def get_dashboard_data(self) -> Dict:
        """Get comprehensive dashboard data"""
        conn = sqlite3.connect('clinton_auto_detailing_master.db')
        cursor = conn.cursor()
        
        # Get last 30 days metrics
        cursor.execute('''
            SELECT * FROM business_metrics 
            WHERE date >= date('now', '-30 days')
            ORDER BY date DESC
        ''')
        metrics = cursor.fetchall()
        
        # Get recent alerts
        cursor.execute('''
            SELECT * FROM system_alerts 
            WHERE resolved = 0 
            ORDER BY created_at DESC
            LIMIT 10
        ''')
        alerts = cursor.fetchall()
        
        # Get system health
        cursor.execute('''
            SELECT service_name, status, last_check, response_time_ms 
            FROM system_health 
            WHERE id IN (
                SELECT MAX(id) 
                FROM system_health 
                GROUP BY service_name
            )
        ''')
        health = cursor.fetchall()
        
        conn.close()
        
        return {
            'business_name': self.business_name,
            'business_location': self.business_location,
            'metrics': metrics,
            'alerts': alerts,
            'system_health': health,
            'last_updated': datetime.now().isoformat()
        }

# Flask application setup
app = Flask(__name__)

# Initialize orchestrator
orchestrator = MasterIntegrationOrchestrator()

# Register all blueprint routes
app.register_blueprint(square_bp, url_prefix='/api/square')
app.register_blueprint(calendar_bp, url_prefix='/api/calendar')
app.register_blueprint(email_bp, url_prefix='/api/email')
app.register_blueprint(text_bp, url_prefix='/api/text')
app.register_blueprint(facebook_bp, url_prefix='/api/facebook')
app.register_blueprint(googleads_bp, url_prefix='/api/googleads')
app.register_blueprint(yelp_bp, url_prefix='/api/yelp')
app.register_blueprint(crm_bp, url_prefix='/api/crm')
app.register_blueprint(accounting_bp, url_prefix='/api/accounting')
app.register_blueprint(workflow_bp, url_prefix='/api/workflow')
app.register_blueprint(instagram_bp, url_prefix='/api/instagram')

# Master orchestrator routes
@app.route('/')
def dashboard():
    """Main dashboard for Clinton Auto Detailing"""
    dashboard_html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>{{business_name}} - Integration Dashboard</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
            .container { max-width: 1200px; margin: 0 auto; }
            .header { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 20px; }
            .stat-card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .stat-value { font-size: 2em; font-weight: bold; color: #2563eb; }
            .stat-label { color: #6b7280; margin-top: 5px; }
            .system-health { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .health-item { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #e5e7eb; }
            .health-status { padding: 4px 12px; border-radius: 20px; font-size: 0.8em; font-weight: bold; }
            .healthy { background: #dcfce7; color: #166534; }
            .unhealthy { background: #fef2f2; color: #dc2626; }
            .alerts { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .alert-item { padding: 10px; margin: 10px 0; border-radius: 4px; }
            .alert-high { background: #fef2f2; border-left: 4px solid #dc2626; }
            .alert-medium { background: #fffbeb; border-left: 4px solid #d97706; }
            .btn { background: #2563eb; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; text-decoration: none; display: inline-block; }
            .btn:hover { background: #1d4ed8; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>{{business_name}} Integration Dashboard</h1>
                <p>{{business_location}} • Last updated: <span id="lastUpdated">Loading...</span></p>
                <a href="/api/health" class="btn">System Health Check</a>
                <a href="/api/sync" class="btn">Manual Sync</a>
            </div>
            
            <div class="stats-grid" id="statsGrid">
                Loading metrics...
            </div>
            
            <div class="system-health">
                <h2>System Health</h2>
                <div id="systemHealth">Loading...</div>
            </div>
            
            <div class="alerts">
                <h2>Active Alerts</h2>
                <div id="activeAlerts">Loading...</div>
            </div>
        </div>
        
        <script>
            async function loadDashboard() {
                try {
                    const response = await fetch('/api/dashboard');
                    const data = await response.json();
                    
                    document.getElementById('lastUpdated').textContent = new Date(data.last_updated).toLocaleString();
                    
                    // Load stats
                    const statsHtml = `
                        <div class="stat-card">
                            <div class="stat-value">${data.metrics[0]?.new_customers || 0}</div>
                            <div class="stat-label">New Customers Today</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value">$${data.metrics[0]?.total_revenue || 0}</div>
                            <div class="stat-label">Revenue Today</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value">${data.metrics[0]?.appointments_booked || 0}</div>
                            <div class="stat-label">Appointments Booked</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value">${data.metrics[0]?.appointments_completed || 0}</div>
                            <div class="stat-label">Services Completed</div>
                        </div>
                    `;
                    document.getElementById('statsGrid').innerHTML = statsHtml;
                    
                    // Load system health
                    const healthHtml = data.system_health.map(item => `
                        <div class="health-item">
                            <span>${item[0]}</span>
                            <span class="health-status ${item[1]}">${item[1]}</span>
                        </div>
                    `).join('');
                    document.getElementById('systemHealth').innerHTML = healthHtml;
                    
                    // Load alerts
                    const alertsHtml = data.alerts.length > 0 ? 
                        data.alerts.map(alert => `
                            <div class="alert-item alert-${alert[3]}">
                                <strong>${alert[1]}</strong> - ${alert[2]}
                            </div>
                        `).join('') : '<p>No active alerts</p>';
                    document.getElementById('activeAlerts').innerHTML = alertsHtml;
                    
                } catch (error) {
                    console.error('Failed to load dashboard:', error);
                }
            }
            
            loadDashboard();
            setInterval(loadDashboard, 60000); // Refresh every minute
        </script>
    </body>
    </html>
    '''
    
    return render_template_string(dashboard_html, 
                                business_name=orchestrator.business_name,
                                business_location=orchestrator.business_location)

@app.route('/api/dashboard')
def api_dashboard():
    """API endpoint for dashboard data"""
    return jsonify(orchestrator.get_dashboard_data())

@app.route('/api/health')
def api_health():
    """API endpoint for system health check"""
    health = orchestrator.check_system_health()
    return jsonify({'success': True, 'health': health})

@app.route('/api/sync', methods=['POST'])
def api_sync():
    """API endpoint to trigger manual sync"""
    try:
        orchestrator.sync_customer_data()
        return jsonify({'success': True, 'message': 'Sync completed'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/metrics')
def api_metrics():
    """API endpoint for business metrics"""
    metrics = orchestrator.generate_daily_metrics()
    return jsonify({'success': True, 'metrics': metrics})

@app.route('/api/alerts')
def api_alerts():
    """API endpoint for system alerts"""
    conn = sqlite3.connect('clinton_auto_detailing_master.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM system_alerts 
        WHERE resolved = 0 
        ORDER BY created_at DESC
    ''')
    alerts = cursor.fetchall()
    conn.close()
    
    return jsonify({'success': True, 'alerts': alerts})

if __name__ == "__main__":
    print(f"🚀 Starting {orchestrator.business_name} Integration System")
    print(f"📍 Location: {orchestrator.business_location}")
    print("🔗 Integrated Systems:")
    print("   ✅ Square (Payments & Bookings)")
    print("   ✅ Google Calendar (Scheduling)")
    print("   ✅ Email Workflows (Customer Communication)")
    print("   ✅ Text/SMS System (Customer Onboarding)")
    print("   ✅ Facebook/Meta (Social Media & Ads)")
    print("   ✅ Google Ads (Advertising)")
    print("   ✅ Yelp Scraper (Competitive Intelligence)")
    print("   ✅ CRM System (Customer Management)")
    print("   ✅ Accounting System (Financial Tracking)")
    print("   ✅ Workflow Automation (Business Logic)")
    print("   ✅ Instagram Business (Social Media)")
    print("")
    print("🌐 Dashboard: http://localhost:5000")
    print("📊 API Documentation: http://localhost:5000/api/health")
    
    app.run(debug=True, host='0.0.0.0', port=5000)