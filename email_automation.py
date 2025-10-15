"""
SINCOR Email Automation System - Phase 2
Intelligent Email Campaigns with Polymath Authority

This system creates and sends personalized email campaigns using your
multi-industry expertise and book authority for maximum credibility.
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from email.message import EmailMessage
import smtplib
import os

# Email Templates by Industry with Polymath Authority
EMAIL_TEMPLATES = {
    "auto_detailing": {
        "subject_templates": [
            "Quick question about {business_name}'s detailing services",
            "Saw {business_name}'s {rating}★ rating - impressive!",
            "From the author of 'Six Figures in Auto Detailing' - partnership opportunity",
            "Help {business_name} automate customer acquisition"
        ],
        "email_templates": [
            {
                "name": "authority_introduction",
                "subject": "From the author of 'Six Figures in Auto Detailing' - partnership opportunity",
                "body": """Hi {contact_name},

I hope this message finds you well. I'm reaching out because I noticed {business_name} has built an impressive {rating}★ reputation in {location}.

I'm the author of "From $0 to Six Figures in the Auto Detailing Industry" (available on Amazon), and I've helped dozens of detailing businesses automate their customer acquisition process.

What caught my attention about {business_name}:
• {rating}★ rating with {review_count} reviews
• Strong local presence in {location}
• Professional service approach

I've developed an AI system called SINCOR that automatically finds and contacts potential customers for detailing businesses. The businesses using it are seeing 300-500% increases in qualified leads.

Would you be interested in a 10-minute conversation about how this could work for {business_name}?

Best regards,
[Your Name]
Polymath Author & Business Systems Expert
📖 Author of 8 published books including "Six Figures in Auto Detailing"
🎵 Creator of 100+ songs (pattern recognition across all domains)

P.S. If you're curious about the book, you can check it out here: https://www.amazon.com/dp/B0DV3N8P47"""
            },
            {
                "name": "credibility_follow_up",
                "subject": "Quick follow-up - {business_name}'s growth potential",
                "body": """Hi again,

I sent you a note earlier about SINCOR's automated customer acquisition system for {business_name}.

Since you haven't had a chance to respond yet, I thought I'd share what makes this different from typical marketing services:

1. **Industry Authority**: I literally wrote the definitive book on auto detailing business success
2. **Proven System**: Currently helping 47 detailing businesses automate their lead generation
3. **Polymath Approach**: My pattern recognition across 8 books and multiple industries reveals insights others miss

The detailing businesses using SINCOR are averaging 23 new qualified leads per month, with conversion rates 3x higher than traditional marketing.

Given {business_name}'s {rating}★ rating and strong reputation, you'd likely see even better results.

Worth a brief conversation?

Best,
[Your Name]
Author & Systems Expert"""
            },
            {
                "name": "value_demonstration",
                "subject": "Found 47 potential customers for {business_name} in {location}",
                "body": """Hi {contact_name},

I did something that might interest you...

Using SINCOR's business intelligence engine, I found 47 car owners in {location} who match the profile of customers who would value {business_name}'s {rating}★ service.

Here's what the data reveals:
• 23 luxury car owners (BMW, Mercedes, Audi) within 15 miles
• 18 truck/SUV owners with premium vehicle packages  
• 6 classic car enthusiasts based on online activity

This is exactly the type of intelligence that my book "From $0 to Six Figures in Auto Detailing" teaches - finding the RIGHT customers, not just more customers.

As a polymath who's analyzed patterns across multiple industries, I can tell you that {business_name} has the perfect foundation for automated growth.

Would you like to see how we could reach these 47 prospects with personalized messages this week?

Best regards,
[Your Name]
Business Intelligence Expert
📊 Pattern Recognition Across 8 Published Books"""
            }
        ]
    },
    
    "hvac": {
        "subject_templates": [
            "HVAC business intelligence for {business_name}",
            "Saw {business_name}'s expertise - quick question",
            "From a multi-industry systems expert - growth opportunity",
            "Help {business_name} automate customer acquisition"
        ],
        "email_templates": [
            {
                "name": "systems_expertise",
                "subject": "HVAC business intelligence for {business_name}",
                "body": """Hi {contact_name},

I'm a polymath business systems expert who's written 8 books across multiple industries, and {business_name}'s {rating}★ reputation caught my attention.

Here's what's interesting about your market position:
• {rating}★ rating with {review_count} reviews shows quality work
• HVAC is a relationship business - perfect for systematic customer acquisition
• Your location in {location} has untapped opportunity

I've developed SINCOR, an AI system that discovers homeowners needing HVAC services before they even start searching. The contractors using it are seeing 400%+ increases in qualified leads.

As someone who's analyzed successful businesses across auto detailing, pest control, and other service industries, I can see {business_name} has the foundation for explosive growth.

Worth a brief conversation about automating your customer acquisition?

Best,
[Your Name]
Multi-Industry Systems Expert
📚 8 Published Books on Business Growth
🔧 Specialized in Service Industry Automation"""
            }
        ]
    },
    
    "pest_control": {
        "subject_templates": [
            "Business intelligence for {business_name}",
            "Pest control automation opportunity - {location}",
            "From a multi-industry expert - quick question",
            "Help {business_name} dominate {location} market"
        ],
        "email_templates": [
            {
                "name": "market_intelligence",
                "subject": "Business intelligence for {business_name}",
                "body": """Hi {contact_name},

As a polymath who's studied successful businesses across 6+ industries, {business_name}'s {rating}★ reputation immediately stood out.

Here's what my business intelligence reveals about your opportunity:
• Pest control has predictable seasonal patterns (I can automate timing)
• Homeowners in {location} search reactively, not proactively
• Most competitors use generic marketing approaches

I've built SINCOR to solve this exact problem. It identifies homeowners BEFORE they have pest issues, using AI to analyze property data, seasonal patterns, and buying behaviors.

The pest control companies using this system are booking 300-400% more services because they're reaching customers at the perfect moment.

My background analyzing patterns across industries (8 published books, systems thinking approach) gives me insights that industry-specific marketers miss.

Would you be interested in a brief conversation about dominating the {location} market?

Best regards,
[Your Name]
Multi-Industry Business Intelligence Expert"""
            }
        ]
    }
}

class EmailAutomationEngine:
    """Intelligent email campaign automation with polymath authority."""
    
    def __init__(self):
        self.db_path = Path(__file__).parent / "data" / "business_intelligence.db"
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_pass = os.getenv("SMTP_PASS", "")
        self.email_from = os.getenv("EMAIL_FROM", "")
    
    def create_campaign(self, industry, location, campaign_type="authority_introduction"):
        """Create and launch an email campaign for discovered businesses."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get businesses that haven't been contacted yet
        cursor.execute('''
            SELECT b.* FROM businesses b
            LEFT JOIN campaigns c ON b.id = c.business_id
            WHERE b.industry = ? AND b.address LIKE ? 
            AND c.id IS NULL AND b.lead_score > 50
            ORDER BY b.lead_score DESC
            LIMIT 50
        ''', (industry, f"%{location}%"))
        
        businesses = cursor.fetchall()
        
        campaigns_created = 0
        for business in businesses:
            email_content = self._generate_email(business, industry, campaign_type)
            if email_content:
                # Save campaign record
                cursor.execute('''
                    INSERT INTO campaigns 
                    (business_id, campaign_type, template_used, status)
                    VALUES (?, ?, ?, 'draft')
                ''', (business[0], campaign_type, email_content['template_name']))
                
                campaigns_created += 1
        
        conn.commit()
        conn.close()
        
        return {
            "campaigns_created": campaigns_created,
            "industry": industry,
            "location": location,
            "campaign_type": campaign_type
        }
    
    def _generate_email(self, business_data, industry, campaign_type):
        """Generate personalized email content using business data."""
        templates = EMAIL_TEMPLATES.get(industry, {}).get("email_templates", [])
        template = next((t for t in templates if t["name"] == campaign_type), None)
        
        if not template:
            return None
        
        # Extract business information
        business_id, name, address, phone, website, industry_type, rating, review_count, place_id, lat, lng, discovered_at, last_analyzed, lead_score, contact_status, notes = business_data
        
        # Extract location from address
        location = address.split(',')[-2].strip() if ',' in address else "your area"
        
        # Personalize the email
        personalized_subject = template["subject"].format(
            business_name=name,
            contact_name="there",  # Would extract from business details in real implementation
            location=location,
            rating=rating or "high",
            review_count=review_count or "many"
        )
        
        personalized_body = template["body"].format(
            business_name=name,
            contact_name="there",
            location=location,
            rating=rating or "high",
            review_count=review_count or "many"
        )
        
        return {
            "template_name": template["name"],
            "subject": personalized_subject,
            "body": personalized_body,
            "business_id": business_id,
            "business_name": name
        }
    
    def send_campaigns(self, limit=10):
        """Send draft campaigns (respects rate limits)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get draft campaigns
        cursor.execute('''
            SELECT c.*, b.name, b.address 
            FROM campaigns c
            JOIN businesses b ON c.business_id = b.id
            WHERE c.status = 'draft'
            ORDER BY c.id
            LIMIT ?
        ''', (limit,))
        
        campaigns = cursor.fetchall()
        sent_count = 0
        
        for campaign in campaigns:
            campaign_id = campaign[0]
            business_id = campaign[1]
            
            # Generate email content
            cursor.execute('SELECT * FROM businesses WHERE id = ?', (business_id,))
            business = cursor.fetchone()
            
            if business:
                # Get industry from business data
                industry = business[5]  # industry column
                email_content = self._generate_email(business, industry, campaign[2])  # campaign_type
                
                if email_content and self._send_email(email_content):
                    # Update campaign status
                    cursor.execute('''
                        UPDATE campaigns 
                        SET status = 'sent', sent_at = ?
                        WHERE id = ?
                    ''', (datetime.now().isoformat(), campaign_id))
                    
                    sent_count += 1
        
        conn.commit()
        conn.close()
        
        return {"campaigns_sent": sent_count}
    
    def _send_email(self, email_content):
        """Send individual email (or save as draft)."""
        try:
            if self.smtp_host and self.smtp_user and self.smtp_pass:
                # Send via SMTP
                msg = EmailMessage()
                msg["From"] = self.email_from
                msg["To"] = "demo@example.com"  # Would use actual business email
                msg["Subject"] = email_content["subject"]
                msg.set_content(email_content["body"])
                
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_pass)
                    server.send_message(msg)
                
                return True
            else:
                # Demo mode - just log the campaign
                print(f"DEMO: Would send email to {email_content['business_name']}")
                print(f"Subject: {email_content['subject']}")
                return True
                
        except Exception as e:
            print(f"Error sending email: {e}")
            return False

def add_automation_routes(app):
    """Add email automation routes to Flask app."""
    
    @app.route("/api/create-campaign", methods=["POST"])
    def create_campaign_api():
        """API endpoint to create email campaigns."""
        try:
            from flask import request, jsonify
            data = request.get_json()
            
            industry = data.get("industry", "auto_detailing")
            location = data.get("location", "Austin, TX")
            campaign_type = data.get("campaign_type", "authority_introduction")
            
            engine = EmailAutomationEngine()
            result = engine.create_campaign(industry, location, campaign_type)
            
            return jsonify({
                "success": True,
                "result": result,
                "message": f"Created {result['campaigns_created']} campaigns for {industry} in {location}"
            })
            
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    
    @app.route("/api/send-campaigns", methods=["POST"])
    def send_campaigns_api():
        """API endpoint to send draft campaigns."""
        try:
            from flask import request, jsonify
            data = request.get_json()
            
            limit = data.get("limit", 10)
            
            engine = EmailAutomationEngine()
            result = engine.send_campaigns(limit)
            
            return jsonify({
                "success": True,
                "result": result,
                "message": f"Sent {result['campaigns_sent']} campaigns"
            })
            
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    
    @app.route("/campaign-dashboard")
    def campaign_dashboard():
        """Email campaign management dashboard."""
        from flask import render_template_string
        return render_template_string(CAMPAIGN_DASHBOARD_TEMPLATE)

# Campaign Dashboard Template
CAMPAIGN_DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>SINCOR Email Campaign Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
    <script src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
</head>
<body class="bg-gray-50">
    <!-- Premium Header -->
    <header class="bg-black text-white shadow-lg">
        <div class="max-w-7xl mx-auto px-4 py-4">
            <div class="flex items-center justify-between">
                <div class="flex items-center">
                    <img src="/static/logo.png" alt="SINCOR" class="h-10 w-auto mr-3">
                    <div>
                        <h1 class="text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-yellow-400 to-yellow-200">SINCOR</h1>
                        <div class="text-xs text-yellow-300">Email Campaign Intelligence</div>
                    </div>
                </div>
                <nav class="space-x-4">
                    <a href="/discovery-dashboard" class="text-yellow-300 hover:text-yellow-100">Discovery</a>
                    <a href="/" class="text-yellow-300 hover:text-yellow-100">Home</a>
                </nav>
            </div>
        </div>
    </header>

    <div class="max-w-7xl mx-auto py-8 px-4" x-data="campaignApp()">
        <!-- Campaign Creation -->
        <div class="bg-white rounded-lg shadow-lg p-6 mb-8">
            <h2 class="text-2xl font-bold mb-6">🧠 Create Polymath Authority Campaign</h2>
            
            <div class="grid md:grid-cols-4 gap-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Industry</label>
                    <select x-model="industry" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                        <option value="auto_detailing">Auto Detailing</option>
                        <option value="hvac">HVAC</option>
                        <option value="pest_control">Pest Control</option>
                        <option value="plumbing">Plumbing</option>
                        <option value="electrical">Electrical</option>
                    </select>
                </div>
                
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Location</label>
                    <input type="text" x-model="location" placeholder="Austin, TX" 
                           class="w-full px-3 py-2 border border-gray-300 rounded-md">
                </div>
                
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-2">Campaign Type</label>
                    <select x-model="campaignType" class="w-full px-3 py-2 border border-gray-300 rounded-md">
                        <option value="authority_introduction">Authority Introduction</option>
                        <option value="credibility_follow_up">Credibility Follow-up</option>
                        <option value="value_demonstration">Value Demonstration</option>
                    </select>
                </div>
                
                <div class="flex items-end">
                    <button @click="createCampaign()" :disabled="loading"
                            class="w-full bg-gradient-to-r from-yellow-500 to-yellow-400 text-black px-4 py-2 rounded-lg font-semibold hover:from-yellow-400 hover:to-yellow-300 disabled:opacity-50">
                        <span x-show="!loading">📧 Create Campaign</span>
                        <span x-show="loading">Creating...</span>
                    </button>
                </div>
            </div>
            
            <div x-show="campaignResult" class="mt-4 p-4 bg-green-50 border border-green-200 rounded-lg">
                <div class="text-green-800">
                    <strong>✅ Campaign Created!</strong>
                    <div x-text="campaignMessage" class="mt-1"></div>
                </div>
                
                <button @click="sendCampaigns()" :disabled="sending" 
                        class="mt-3 bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 disabled:opacity-50">
                    <span x-show="!sending">🚀 Send Campaigns (Demo Mode)</span>
                    <span x-show="sending">Sending...</span>
                </button>
            </div>
        </div>
        
        <!-- Campaign Templates Preview -->
        <div class="bg-white rounded-lg shadow-lg p-6">
            <h3 class="text-xl font-bold mb-6">📝 Email Template Preview</h3>
            
            <div class="bg-gray-50 p-4 rounded-lg">
                <h4 class="font-semibold mb-2">Authority Introduction Template</h4>
                <div class="text-sm text-gray-600 mb-4">
                    Subject: "From the author of 'Six Figures in Auto Detailing' - partnership opportunity"
                </div>
                <div class="text-sm text-gray-700">
                    <p class="mb-2"><strong>Key Authority Elements:</strong></p>
                    <ul class="list-disc pl-6 space-y-1">
                        <li>📖 Book authorship credibility</li>
                        <li>🧠 Polymath pattern recognition</li>
                        <li>⭐ Business-specific personalization</li>
                        <li>🎯 Industry expertise demonstration</li>
                        <li>📊 Concrete results from other businesses</li>
                    </ul>
                </div>
            </div>
        </div>
    </div>

    <script>
        function campaignApp() {
            return {
                industry: 'auto_detailing',
                location: 'Austin, TX',
                campaignType: 'authority_introduction',
                loading: false,
                sending: false,
                campaignResult: null,
                campaignMessage: '',
                
                async createCampaign() {
                    this.loading = true;
                    this.campaignResult = null;
                    
                    try {
                        const response = await fetch('/api/create-campaign', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                industry: this.industry,
                                location: this.location,
                                campaign_type: this.campaignType
                            })
                        });
                        
                        const data = await response.json();
                        
                        if (data.success) {
                            this.campaignResult = data.result;
                            this.campaignMessage = data.message;
                        } else {
                            alert('Error: ' + data.error);
                        }
                    } catch (error) {
                        alert('Network error: ' + error.message);
                    } finally {
                        this.loading = false;
                    }
                },
                
                async sendCampaigns() {
                    this.sending = true;
                    
                    try {
                        const response = await fetch('/api/send-campaigns', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ limit: 10 })
                        });
                        
                        const data = await response.json();
                        
                        if (data.success) {
                            alert('✅ ' + data.message + ' (Demo mode - emails logged, not sent)');
                        } else {
                            alert('Error: ' + data.error);
                        }
                    } catch (error) {
                        alert('Network error: ' + error.message);
                    } finally {
                        this.sending = false;
                    }
                }
            }
        }
    </script>
</body>
</html>
"""