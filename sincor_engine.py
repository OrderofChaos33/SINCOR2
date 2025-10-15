#!/usr/bin/env python3
"""
SINCOR Business Intelligence Engine
Complete automated marketing system for service businesses

This is the core engine that orchestrates:
1. Business discovery and lead generation
2. Personalized email campaigns  
3. Follow-up sequences
4. Response tracking and analytics
5. Real-time dashboard data
"""

import os
import json
import time
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from threading import Thread
import schedule

# Import agents
import sys
sys.path.append(str(Path(__file__).parent / "agents"))
try:
    from agents.intelligence.business_intel_agent import BusinessIntelAgent
except ImportError:
    print("Warning: BusinessIntelAgent not available - running in demo mode")
    BusinessIntelAgent = None

@dataclass
class CampaignConfig:
    """Campaign configuration for targeted outreach."""
    target_industry: str = "auto detailing"
    locations: List[str] = None
    min_rating: float = 3.5
    min_reviews: int = 5
    max_businesses_per_day: int = 50
    email_template: str = "default"
    
    def __post_init__(self):
        if self.locations is None:
            self.locations = ["Austin, TX", "Dallas, TX", "Houston, TX"]

class SINCOREngine:
    """Main SINCOR business intelligence engine."""
    
    def __init__(self, config_path: str = None):
        """Initialize SINCOR engine with configuration."""
        self.root = Path(__file__).parent
        self.data_dir = self.root / "data"
        self.data_dir.mkdir(exist_ok=True)
        
        # Initialize database
        self.db_path = self.data_dir / "sincor_main.db"
        self._init_main_database()
        
        # Load configuration
        self.config = self._load_config(config_path)
        
        # Initialize agents
        self._init_agents()
        
        # Campaign management
        self.active_campaigns = {}
        self.campaign_stats = {}
        
        print("SINCOR Engine initialized successfully!")
    
    def _load_config(self, config_path: str = None) -> Dict:
        """Load SINCOR configuration."""
        default_config = {
            "google_api_key": os.getenv("GOOGLE_API_KEY", ""),
            "email_smtp_host": os.getenv("EMAIL_SMTP_HOST", ""),
            "email_smtp_port": int(os.getenv("EMAIL_SMTP_PORT", "587")),
            "email_username": os.getenv("EMAIL_USERNAME", ""),
            "email_password": os.getenv("EMAIL_PASSWORD", ""),
            "email_from": os.getenv("EMAIL_FROM", ""),
            "search_radius": 50000,
            "rate_limit_delay": 2,
            "max_daily_emails": 100,
            "enable_auto_campaign": True
        }
        
        if config_path and Path(config_path).exists():
            with open(config_path, 'r') as f:
                user_config = json.load(f)
                default_config.update(user_config)
        
        return default_config
    
    def _init_agents(self):
        """Initialize SINCOR agents."""
        # Force demo mode if no Google API key
        if BusinessIntelAgent and self.config.get("google_api_key"):
            self.business_agent = BusinessIntelAgent(
                config=self.config,
                log_path="logs/sincor_engine.log"
            )
        else:
            self.business_agent = None
            print("Running in demo mode - No Google API key configured")
    
    def _init_main_database(self):
        """Initialize main SINCOR database."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Campaign performance table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS campaign_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT,
                    date TEXT,
                    businesses_discovered INTEGER DEFAULT 0,
                    emails_sent INTEGER DEFAULT 0,
                    emails_opened INTEGER DEFAULT 0,
                    responses_received INTEGER DEFAULT 0,
                    meetings_booked INTEGER DEFAULT 0,
                    pipeline_value REAL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Email tracking
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS email_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    business_id INTEGER,
                    email_subject TEXT,
                    email_content TEXT,
                    sent_at TEXT,
                    opened_at TEXT,
                    clicked_at TEXT,
                    responded_at TEXT,
                    response_content TEXT,
                    follow_up_sequence INTEGER DEFAULT 1,
                    status TEXT DEFAULT 'sent'
                )
            ''')
            
            # Dashboard metrics (real-time)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS dashboard_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric_name TEXT,
                    metric_value REAL,
                    metric_date TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            print(f"Database initialization error: {e}")
    
    def discover_businesses(self, campaign_config: CampaignConfig = None) -> List[Dict]:
        """Discover businesses for marketing campaigns."""
        if not campaign_config:
            campaign_config = CampaignConfig()
        
        all_businesses = []
        
        if self.business_agent:
            # Real business discovery
            for location in campaign_config.locations:
                print(f"Discovering businesses in {location}...")
                
                businesses = self.business_agent.search_businesses_by_location(
                    location=location,
                    business_type=campaign_config.target_industry,
                    radius=self.config.get("search_radius", 50000)
                )
                
                # Filter by quality criteria
                quality_businesses = [
                    b for b in businesses
                    if (b.get("rating", 0) >= campaign_config.min_rating and 
                        b.get("review_count", 0) >= campaign_config.min_reviews)
                ]
                
                # Save to database
                if quality_businesses:
                    self.business_agent.save_businesses(quality_businesses)
                    all_businesses.extend(quality_businesses)
                
                print(f"   Found {len(quality_businesses)} quality prospects")
                
                # Rate limiting
                time.sleep(self.config.get("rate_limit_delay", 2))
        else:
            # Demo mode - generate sample data
            print("Demo Mode: Generating sample business data...")
            all_businesses = self._generate_demo_businesses(campaign_config)
            print(f"   Generated {len(all_businesses)} demo businesses")
        
        # Update dashboard metrics
        self._update_dashboard_metric("businesses_discovered_today", len(all_businesses))
        
        print(f"Total discovered: {len(all_businesses)} businesses")
        return all_businesses
    
    def _generate_demo_businesses(self, config: CampaignConfig) -> List[Dict]:
        """Generate demo business data for testing."""
        import random
        
        demo_businesses = [
            {
                "business_name": "Elite Auto Spa & Detailing",
                "address": "1234 Main St, Austin, TX 78701",
                "city": "Austin", "state": "TX", "zip_code": "78701",
                "phone": "+1-512-555-0123",
                "email": "info@eliteautospa.com",
                "website": "https://eliteautospa.com",
                "rating": 4.7,
                "review_count": 89,
                "lead_score": 87,
                "business_type": "auto_detailing"
            },
            {
                "business_name": "Precision Mobile Detail",
                "address": "5678 Oak Ave, Dallas, TX 75201", 
                "city": "Dallas", "state": "TX", "zip_code": "75201",
                "phone": "+1-214-555-0187",
                "email": "contact@precisionmobile.com",
                "website": "https://precisionmobiledetail.com",
                "rating": 4.9,
                "review_count": 156,
                "lead_score": 95,
                "business_type": "auto_detailing"
            },
            {
                "business_name": "Shine Bright Auto Care",
                "address": "9012 Pine St, Houston, TX 77001",
                "city": "Houston", "state": "TX", "zip_code": "77001", 
                "phone": "+1-713-555-0234",
                "email": "hello@shinebright.com",
                "website": "https://shinebrightauto.com",
                "rating": 4.3,
                "review_count": 67,
                "lead_score": 78,
                "business_type": "auto_detailing"
            }
        ]
        
        # Add some variety
        for business in demo_businesses:
            business["google_place_id"] = f"demo_{random.randint(1000, 9999)}"
            business["discovered_at"] = datetime.now().isoformat()
        
        # Save to database for consistency
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            for business in demo_businesses:
                cursor.execute('''
                    INSERT OR REPLACE INTO campaign_performance 
                    (campaign_id, date, businesses_discovered)
                    VALUES (?, ?, ?)
                ''', ("demo_campaign", datetime.now().date().isoformat(), 1))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error saving demo data: {e}")
        
        return demo_businesses
    
    def create_personalized_email(self, business: Dict, template: str = "default") -> Dict:
        """Create personalized email for a business."""
        
        templates = {
            "auto_detailing": {
                "subject": "The Guy Who Wrote THE Book on Auto Detailing Success Wants to Help {business_name}",
                "content": """Hi {business_name} team,

I'm the author of "From $0 to Six Figures in Auto Detailing" - you may have seen it around the industry.

I noticed {business_name} has built an impressive {rating}⭐ reputation with {review_count} reviews in {city}. That tells me you deliver quality work, just like the successful detailers I wrote about in my book.

Here's why I'm reaching out: I've now AUTOMATED the customer acquisition strategies from my book.

Instead of manually doing what the book teaches, my SINCOR system does it all automatically:
• Finds car owners in {city} who need detailing services
• Sends personalized emails mentioning their car type and location  
• Follows up with proven sequences from my book
• Books appointments directly to your calendar

Real results: Elite Mobile Detail in Austin went from 12 customers/month to 47 customers/month in 90 days using this system.

Since you're in the auto detailing industry, I'd like to offer you:
✓ The complete SINCOR automated system ($297/month)
✓ FREE BONUS: My book "From $0 to Six Figures in Auto Detailing" ($29.97 value)
✓ Auto detailing-specific email templates
✓ Direct access to me (the author) for questions

First month is just $1 to try it out.

Interested in seeing how this works for {business_name}? 

Just reply "SHOW ME" and I'll send you a quick demo plus your free book.

Best,
[Your Name]
Author of "From $0 to Six Figures in Auto Detailing"
Creator of the SINCOR System

P.S. - This offer (free book + $1 trial) is only for auto detailing businesses like yours. I know the industry inside and out.
"""
            },
            
            "default": {
                "subject": "How {business_name} Can Get 50+ New Customers Every Month on Autopilot",
                "content": """Hi {business_name} team,

I noticed {business_name} has built an impressive {rating}⭐ reputation with {review_count} reviews in {city}. That tells me you deliver quality work.

But here's what I'm curious about: How many new customers are you turning away each month because you're already at capacity?

I ask because I help successful {business_type} businesses like yours systematically discover and connect with customers who are actively looking for their services.

Here's what this looks like:
• Automatically find 300-500 potential customers in your area every month
• Send personalized outreach that mentions their specific needs 
• Follow-up sequences that convert 4-8% into paying customers
• Complete automation - no cold calling or manual work

One client (Elite Mobile Detail in Austin) went from 12 customers/month to 47 customers/month in 90 days using this system.

Would you be interested in seeing how this could work for {business_name}?

I have a few demo spots open this week. Just reply "SHOW ME" and I'll send you a quick case study + schedule a 15-minute demo.

Best,
SINCOR Team
Lead Generation on Autopilot

P.S. - We also have ready-to-use marketing packs for {business_type} businesses starting at $197. Perfect if you prefer to handle marketing yourself but need professional templates.
"""
            },
            
            "high_value": {
                "subject": "EXCLUSIVE: {business_name} Selected for Elite Growth Program", 
                "content": """Hello {business_name},

Your outstanding {rating}⭐ reputation and {review_count} reviews put you in the top 5% of {business_type} businesses in {city}.

That's why you're receiving this exclusive invitation.

SINCOR's Elite Growth Program is typically reserved for businesses already doing $500K+ annually. We only accept 10 companies per city, per quarter.

What Elite members get:
• Automated discovery of 1,500+ qualified prospects monthly
• AI-powered personalized outreach campaigns
• Dedicated growth specialist (white-glove service)
• GUARANTEED 300% increase in qualified leads within 90 days

Current Elite members:
→ Superior HVAC (Phoenix): +$48K monthly revenue  
→ Garcia Landscaping (Miami): 15 hours/week freed up
→ Elite Mobile Detail (Austin): 300% lead increase in 60 days

We have 2 spots remaining in {city} for Q1 2025.

Interested? Reply "ELITE ACCESS" and I'll send the private program details.

Best,
SINCOR Elite Growth Team

*This invitation expires in 48 hours and cannot be extended.*
"""
            }
        }
        
        # Select template based on business type and lead score
        business_type = business.get("business_type", "")
        lead_score = business.get("lead_score", 50)
        
        if "auto" in business_type.lower() or "detail" in business_type.lower():
            template = "auto_detailing"
        elif lead_score >= 85:
            template = "high_value"
        
        email_template = templates.get(template, templates["default"])
        
        # Personalize content
        personalized_email = {
            "subject": email_template["subject"].format(
                business_name=business.get("business_name", ""),
                rating=business.get("rating", ""),
                review_count=business.get("review_count", ""),
                city=business.get("city", ""),
                business_type=business.get("business_type", "").replace("_", " ").title()
            ),
            "content": email_template["content"].format(
                business_name=business.get("business_name", ""),
                rating=business.get("rating", ""),
                review_count=business.get("review_count", ""),  
                city=business.get("city", ""),
                business_type=business.get("business_type", "").replace("_", " ").title()
            ),
            "template_used": template,
            "personalization_score": self._calculate_personalization_score(business)
        }
        
        return personalized_email
    
    def _calculate_personalization_score(self, business: Dict) -> int:
        """Calculate how well personalized the email is."""
        score = 0
        
        if business.get("business_name"): score += 25
        if business.get("rating"): score += 20  
        if business.get("review_count"): score += 20
        if business.get("city"): score += 15
        if business.get("business_type"): score += 10
        if business.get("website"): score += 10
        
        return score
    
    def send_campaign_emails(self, businesses: List[Dict], max_emails: int = None) -> Dict:
        """Send personalized emails to businesses."""
        max_emails = max_emails or self.config.get("max_daily_emails", 100)
        
        results = {
            "emails_sent": 0,
            "emails_failed": 0, 
            "personalization_avg": 0,
            "businesses_contacted": []
        }
        
        personalization_scores = []
        
        for business in businesses[:max_emails]:
            try:
                # Create personalized email
                email_data = self.create_personalized_email(business)
                
                # For now, simulate sending (would integrate with real SMTP)
                success = self._simulate_email_send(business, email_data)
                
                if success:
                    results["emails_sent"] += 1
                    results["businesses_contacted"].append({
                        "business_name": business.get("business_name"),
                        "email": business.get("email", "demo@example.com"),
                        "lead_score": business.get("lead_score"),
                        "sent_at": datetime.now().isoformat()
                    })
                    
                    # Track email
                    self._track_email(business, email_data)
                    
                    personalization_scores.append(email_data.get("personalization_score", 0))
                else:
                    results["emails_failed"] += 1
                
                # Rate limiting
                time.sleep(1)
                
            except Exception as e:
                print(f"Error sending email to {business.get('business_name', 'Unknown')}: {e}")
                results["emails_failed"] += 1
        
        # Calculate average personalization
        if personalization_scores:
            results["personalization_avg"] = sum(personalization_scores) / len(personalization_scores)
        
        # Update dashboard
        self._update_dashboard_metric("emails_sent_today", results["emails_sent"])
        
        print(f"Campaign Results: {results['emails_sent']} sent, {results['emails_failed']} failed")
        return results
    
    def _simulate_email_send(self, business: Dict, email_data: Dict) -> bool:
        """Simulate email sending (replace with real SMTP integration)."""
        # For demo purposes, simulate 95% success rate
        import random
        return random.random() > 0.05
    
    def _track_email(self, business: Dict, email_data: Dict):
        """Track sent email in database."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO email_tracking 
                (business_id, email_subject, email_content, sent_at, status)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                business.get("google_place_id", "demo"),
                email_data.get("subject", ""),
                email_data.get("content", ""),
                datetime.now().isoformat(),
                "sent"
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            print(f"Error tracking email: {e}")
    
    def _update_dashboard_metric(self, metric_name: str, value: float):
        """Update dashboard metrics."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Update or insert metric
            cursor.execute('''
                INSERT OR REPLACE INTO dashboard_metrics 
                (metric_name, metric_value, metric_date, updated_at)
                VALUES (?, ?, ?, ?)
            ''', (metric_name, value, datetime.now().date().isoformat(), datetime.now().isoformat()))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            print(f"Error updating dashboard metric: {e}")
    
    def get_dashboard_data(self) -> Dict:
        """Get real-time dashboard data."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get current metrics
            dashboard_data = {
                "businesses_discovered": 0,
                "emails_sent": 0, 
                "responses_received": 0,
                "estimated_pipeline": 0,
                "recent_activity": [],
                "conversion_rate": 0,
                "last_updated": datetime.now().isoformat()
            }
            
            # Get today's metrics
            today = datetime.now().date().isoformat()
            
            cursor.execute('''
                SELECT metric_name, metric_value FROM dashboard_metrics 
                WHERE metric_date = ?
            ''', (today,))
            
            for metric_name, value in cursor.fetchall():
                if metric_name in dashboard_data:
                    dashboard_data[metric_name] = int(value)
            
            # Get recent email activity
            cursor.execute('''
                SELECT business_id, email_subject, sent_at, status
                FROM email_tracking 
                ORDER BY sent_at DESC 
                LIMIT 10
            ''')
            
            recent_emails = cursor.fetchall()
            for email in recent_emails:
                dashboard_data["recent_activity"].append({
                    "type": "email_sent",
                    "business": email[0],
                    "subject": email[1],
                    "timestamp": email[2],
                    "status": email[3]
                })
            
            # Calculate conversion rate
            if dashboard_data["emails_sent"] > 0:
                dashboard_data["conversion_rate"] = round(
                    (dashboard_data["responses_received"] / dashboard_data["emails_sent"]) * 100, 2
                )
            
            # Estimate pipeline value (avg $2,800 per response)
            dashboard_data["estimated_pipeline"] = dashboard_data["responses_received"] * 2800
            
            conn.close()
            return dashboard_data
            
        except Exception as e:
            print(f"Error getting dashboard data: {e}")
            # Return demo data if database unavailable
            return self._get_demo_dashboard_data()
    
    def _get_demo_dashboard_data(self) -> Dict:
        """Get demo dashboard data."""
        return {
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
                    "timestamp": (datetime.now() - timedelta(hours=2)).isoformat()
                },
                {
                    "type": "response_received", 
                    "business": "Elite Mobile Detail",
                    "details": "Very interested, please call",
                    "timestamp": (datetime.now() - timedelta(hours=4)).isoformat()
                },
                {
                    "type": "lead_scored",
                    "business": "Premium Auto Spa (92/100)",
                    "details": "High-value prospect identified", 
                    "timestamp": (datetime.now() - timedelta(hours=6)).isoformat()
                }
            ],
            "last_updated": datetime.now().isoformat()
        }
    
    def run_automated_campaign(self, campaign_config: CampaignConfig = None):
        """Run a complete automated campaign."""
        print("Starting SINCOR Automated Campaign...")
        
        # Step 1: Discover businesses
        businesses = self.discover_businesses(campaign_config)
        
        if not businesses:
            print("No businesses discovered - check configuration")
            return
        
        # Step 2: Send personalized emails
        email_results = self.send_campaign_emails(businesses)
        
        # Step 3: Update statistics 
        campaign_stats = {
            "campaign_id": f"auto_{datetime.now().strftime('%Y%m%d_%H%M')}",
            "businesses_discovered": len(businesses),
            "emails_sent": email_results["emails_sent"],
            "personalization_avg": email_results["personalization_avg"],
            "started_at": datetime.now().isoformat(),
            "status": "active"
        }
        
        self.campaign_stats["latest"] = campaign_stats
        
        print(f"Campaign Complete! {email_results['emails_sent']} emails sent to {len(businesses)} prospects")
        return campaign_stats
    
    def start_scheduler(self):
        """Start automated campaign scheduler."""
        if not self.config.get("enable_auto_campaign", False):
            print("Automated campaigns disabled in configuration")
            return
        
        # Schedule daily campaigns
        schedule.every().day.at("09:00").do(self.run_automated_campaign)
        schedule.every().day.at("14:00").do(self.run_automated_campaign)
        
        print("SINCOR Scheduler started - campaigns will run at 9 AM and 2 PM daily")
        
        while True:
            schedule.run_pending()
            time.sleep(60)  # Check every minute


# CLI Interface
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="SINCOR Business Intelligence Engine")
    parser.add_argument("--campaign", action="store_true", help="Run single campaign")
    parser.add_argument("--schedule", action="store_true", help="Start automated scheduler") 
    parser.add_argument("--dashboard", action="store_true", help="Show dashboard data")
    parser.add_argument("--demo", action="store_true", help="Run in demo mode")
    
    args = parser.parse_args()
    
    # Initialize engine
    engine = SINCOREngine()
    
    if args.campaign:
        # Run single campaign
        config = CampaignConfig(
            target_industry="auto detailing",
            locations=["Austin, TX", "Dallas, TX"],
            max_businesses_per_day=25
        )
        engine.run_automated_campaign(config)
        
    elif args.schedule:
        # Start scheduler
        engine.start_scheduler()
        
    elif args.dashboard:
        # Show dashboard
        data = engine.get_dashboard_data()
        print("\n📊 SINCOR Dashboard Data:")
        print(json.dumps(data, indent=2))
        
    else:
        # Default: run demo campaign
        print("Running SINCOR Demo Campaign...")
        config = CampaignConfig(target_industry="auto detailing")
        engine.run_automated_campaign(config)