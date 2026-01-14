#!/usr/bin/env python3
"""
SINCOR Campaign Runner - Simple CLI tool to run marketing campaigns

Usage:
    python run_campaign.py --demo           # Run demo campaign
    python run_campaign.py --live           # Run live campaign (requires Google API)
    python run_campaign.py --dashboard      # Show dashboard data
    python run_campaign.py --setup          # Setup configuration
"""

import argparse
import json
import os
from pathlib import Path
from datetime import datetime

def run_demo_campaign():
    """Run a demo campaign with sample data."""
    print("Starting SINCOR Demo Campaign...")
    print("=" * 50)
    
    try:
        from sincor_engine import SINCOREngine, CampaignConfig
        
        # Initialize engine
        engine = SINCOREngine()
        
        # Configure campaign
        config = CampaignConfig(
            target_industry="auto detailing",
            locations=["Austin, TX", "Dallas, TX", "Houston, TX"],
            max_businesses_per_day=25,
            min_rating=4.0,
            min_reviews=10
        )
        
        print(f"Target Industry: {config.target_industry}")
        print(f"Locations: {', '.join(config.locations)}")
        print(f"Min Rating: {config.min_rating}")
        print(f"Min Reviews: {config.min_reviews}")
        print()
        
        # Run campaign
        results = engine.run_automated_campaign(config)
        
        # Display results
        print("\n✅ Campaign Complete!")
        print("=" * 50)
        print(f"📈 Businesses Discovered: {results.get('businesses_discovered', 0)}")
        print(f"📧 Emails Sent: {results.get('emails_sent', 0)}")
        print(f"🎯 Personalization Avg: {results.get('personalization_avg', 0):.1f}%")
        print(f"🆔 Campaign ID: {results.get('campaign_id', 'N/A')}")
        
        # Show sample emails
        print(f"\n📝 Sample Personalized Email:")
        print("-" * 30)
        sample_business = {
            "business_name": "Elite Auto Detailing",
            "rating": 4.7,
            "review_count": 89,
            "city": "Austin",
            "business_type": "auto_detailing",
            "lead_score": 87
        }
        
        email = engine.create_personalized_email(sample_business)
        print(f"Subject: {email['subject']}")
        print(f"Content Preview: {email['content'][:200]}...")
        
        return results
        
    except Exception as e:
        print(f"❌ Error running demo campaign: {e}")
        return None

def run_live_campaign():
    """Run a live campaign with real Google API data."""
    print("🚀 Starting SINCOR Live Campaign...")
    print("=" * 50)
    
    # Check for Google API key
    google_key = os.getenv("GOOGLE_API_KEY")
    if not google_key:
        print("❌ Google API Key Required!")
        print("Please set GOOGLE_API_KEY environment variable")
        print("Get your key from: https://console.cloud.google.com/")
        return None
    
    try:
        from sincor_engine import SINCOREngine, CampaignConfig
        
        # Initialize engine with real API
        config_data = {
            "google_api_key": google_key,
            "search_radius": 50000,
            "rate_limit_delay": 2,
            "max_daily_emails": 50
        }
        
        engine = SINCOREngine()
        engine.config.update(config_data)
        
        # Configure campaign
        config = CampaignConfig(
            target_industry="auto detailing",
            locations=["Austin, TX"],  # Start with one city
            max_businesses_per_day=20,
            min_rating=3.5,
            min_reviews=5
        )
        
        print(f"🔑 Google API: Configured")
        print(f"🎯 Target: {config.target_industry}")
        print(f"📍 Location: {config.locations[0]}")
        print(f"🏢 Max Businesses: {config.max_businesses_per_day}")
        print()
        
        # Run live campaign
        results = engine.run_automated_campaign(config)
        
        # Display results
        print("\n✅ Live Campaign Complete!")
        print("=" * 50)
        print(f"🏢 Real Businesses Found: {results.get('businesses_discovered', 0)}")
        print(f"📧 Personalized Emails Sent: {results.get('emails_sent', 0)}")
        print(f"🎯 Avg Personalization: {results.get('personalization_avg', 0):.1f}%")
        
        return results
        
    except Exception as e:
        print(f"❌ Error running live campaign: {e}")
        return None

def show_dashboard():
    """Display dashboard data."""
    print("📊 SINCOR Dashboard Data")
    print("=" * 50)
    
    try:
        from sincor_engine import SINCOREngine
        
        engine = SINCOREngine()
        data = engine.get_dashboard_data()
        
        print(f"🏢 Businesses Discovered: {data.get('businesses_discovered', 0)}")
        print(f"📧 Emails Sent: {data.get('emails_sent', 0)}")
        print(f"💬 Responses Received: {data.get('responses_received', 0)}")
        print(f"💰 Estimated Pipeline: ${data.get('estimated_pipeline', 0):,}")
        print(f"📈 Conversion Rate: {data.get('conversion_rate', 0):.2f}%")
        print(f"🕐 Last Updated: {data.get('last_updated', 'Unknown')}")
        
        # Recent activity
        activities = data.get('recent_activity', [])
        if activities:
            print(f"\n📋 Recent Activity:")
            print("-" * 30)
            for activity in activities[:5]:
                print(f"• {activity.get('business', 'Unknown')}: {activity.get('details', 'N/A')}")
        
        return data
        
    except Exception as e:
        print(f"❌ Error loading dashboard: {e}")
        return None

def setup_configuration():
    """Setup SINCOR configuration."""
    print("⚙️  SINCOR Configuration Setup")
    print("=" * 50)
    
    config_path = Path("config") / "sincor_config.json"
    config_path.parent.mkdir(exist_ok=True)
    
    config = {}
    
    print("Enter your configuration (press Enter to skip):")
    print()
    
    # Google API Key
    google_key = input("Google API Key (for live business discovery): ").strip()
    if google_key:
        config["google_api_key"] = google_key
    
    # Email configuration
    print("\nEmail Configuration (for sending campaigns):")
    smtp_host = input("SMTP Host (e.g., smtp.gmail.com): ").strip()
    if smtp_host:
        config["email_smtp_host"] = smtp_host
        config["email_smtp_port"] = int(input("SMTP Port (587): ") or "587")
        config["email_username"] = input("Email Username: ").strip()
        config["email_password"] = input("Email Password: ").strip()
        config["email_from"] = input("From Email Address: ").strip()
    
    # Campaign settings
    print("\nCampaign Settings:")
    config["max_daily_emails"] = int(input("Max Daily Emails (100): ") or "100")
    config["search_radius"] = int(input("Search Radius in meters (50000): ") or "50000")
    config["rate_limit_delay"] = int(input("Rate Limit Delay in seconds (2): ") or "2")
    
    # Save configuration
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    
    print(f"\n✅ Configuration saved to: {config_path}")
    print("You can now run live campaigns with your settings!")
    
    return config

def main():
    """Main CLI interface."""
    parser = argparse.ArgumentParser(
        description="SINCOR Business Intelligence Campaign Runner",
        epilog="Example: python run_campaign.py --demo"
    )
    
    parser.add_argument("--demo", action="store_true", 
                       help="Run demo campaign with sample data")
    parser.add_argument("--live", action="store_true",
                       help="Run live campaign with Google API")
    parser.add_argument("--dashboard", action="store_true",
                       help="Show dashboard data")
    parser.add_argument("--setup", action="store_true",
                       help="Setup configuration")
    
    args = parser.parse_args()
    
    # Header
    print()
    print("SINCOR Business Intelligence Engine")
    print("Automated Marketing for Service Businesses")
    print()
    
    if args.demo:
        run_demo_campaign()
    elif args.live:
        run_live_campaign()
    elif args.dashboard:
        show_dashboard()
    elif args.setup:
        setup_configuration()
    else:
        # Show help and run demo by default
        parser.print_help()
        print("\n" + "="*50)
        print("Running demo campaign by default...")
        print()
        run_demo_campaign()

if __name__ == "__main__":
    main()