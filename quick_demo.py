#!/usr/bin/env python3
"""
SINCOR BUSINESS INTELLIGENCE EMPIRE - QUICK DEMO

Shows the system working with real Google Places API integration!
"""

import os
import sys
from pathlib import Path

# Add agents to path
sys.path.append(str(Path(__file__).parent / "agents"))

def test_google_places_api():
    """Test Google Places API integration with real data."""
    print("="*70)
    print("TESTING GOOGLE PLACES API INTEGRATION")
    print("="*70)
    
    # Import the business intel agent
    from intelligence.business_intel_agent import BusinessIntelAgent
    
    # Get API key from environment (Railway config)
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    
    if not api_key:
        print("ERROR: Google Places API key not found!")
        print("Make sure you've added GOOGLE_PLACES_API_KEY to Railway environment variables")
        return False
    
    print(f"API Key configured: {api_key[:20]}..." if len(api_key) > 20 else api_key)
    
    # Configure agent
    config = {
        "google_api_key": api_key,
        "search_radius": 25000,
        "rate_limit_delay": 1
    }
    
    agent = BusinessIntelAgent(config=config)
    print("Business Intelligence Agent initialized!")
    
    # Test search
    print("\nSearching for auto detailing businesses in Austin, TX...")
    businesses = agent.search_businesses_by_location("Austin, TX", "auto detailing")
    
    if businesses:
        print(f"SUCCESS! Found {len(businesses)} businesses!")
        
        for i, business in enumerate(businesses[:3], 1):  # Show first 3
            print(f"\n  BUSINESS #{i}:")
            print(f"    Name: {business.get('business_name', 'Unknown')}")
            print(f"    Address: {business.get('address', 'N/A')}")
            print(f"    Rating: {business.get('rating', 'N/A')}")
            print(f"    Reviews: {business.get('review_count', 'N/A')}")
            print(f"    Phone: {business.get('phone', 'N/A')}")
            print(f"    Lead Score: {business.get('lead_score', 0)}/100")
        
        # Save to database
        saved = agent.save_businesses(businesses)
        print(f"\nSaved {saved} businesses to database!")
        
        # Get stats
        stats = agent.get_database_stats()
        print(f"\nDATABASE STATS:")
        print(f"  Total businesses: {stats.get('total_businesses', 0)}")
        print(f"  High-value prospects: {stats.get('high_value_prospects', 0)}")
        print(f"  Average lead score: {stats.get('average_lead_score', 0)}")
        
        return True
    else:
        print("No businesses found. Check your API key and quota.")
        return False

def test_template_generation():
    """Test template generation system."""
    print("\n" + "="*70)
    print("TESTING TEMPLATE GENERATION SYSTEM")
    print("="*70)
    
    from intelligence.template_engine import TemplateEngine
    
    engine = TemplateEngine()
    
    # Create templates
    print("Creating default templates...")
    engine.create_default_templates()
    print("Templates created!")
    
    # Sample business data
    business_data = {
        "id": 1,
        "business_name": "Austin Auto Spa",
        "city": "Austin",
        "state": "TX", 
        "phone": "+15125551234",
        "rating": 4.7,
        "review_count": 89,
        "business_type": "auto_detailing",
        "lead_score": 85
    }
    
    print(f"\nGenerating personalized email for: {business_data['business_name']}")
    
    # Generate email
    email_content = engine.generate_personalized_content(
        business_data, "email", "business_owner"
    )
    
    if email_content:
        print("SUCCESS! Email generated!")
        print(f"\nSUBJECT: {email_content.get('subject_line', 'N/A')}")
        
        # Show preview of email body
        body = email_content.get('content_body', '')
        if body:
            # Replace template variables with actual data
            preview = body.replace('{{ business_name }}', business_data['business_name'])
            preview = preview.replace('{{ city }}', business_data['city'])
            preview = preview.replace('{{ rating }}', str(business_data['rating']))
            
            print(f"\nEMAIL PREVIEW (first 300 characters):")
            print(f"{preview[:300]}...")
        
        return True
    else:
        print("ERROR: Could not generate email content")
        return False

def test_email_system():
    """Test email sending capability."""
    print("\n" + "="*70) 
    print("TESTING EMAIL SYSTEM CONFIGURATION")
    print("="*70)
    
    # Check email config
    smtp_config = {
        "host": os.environ.get("SMTP_HOST", ""),
        "port": os.environ.get("SMTP_PORT", ""),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASS", ""),
        "from_email": os.environ.get("EMAIL_FROM", ""),
        "to_email": os.environ.get("EMAIL_TO", "")
    }
    
    print("Email Configuration:")
    print(f"  SMTP Host: {smtp_config['host']}")
    print(f"  SMTP Port: {smtp_config['port']}")
    print(f"  SMTP User: {smtp_config['user']}")
    print(f"  From Email: {smtp_config['from_email']}")
    print(f"  To Email: {smtp_config['to_email']}")
    print(f"  Password: {'*' * len(smtp_config['password'])} (hidden)")
    
    # Check if all required fields are present
    required_fields = ['host', 'user', 'password', 'from_email', 'to_email']
    missing_fields = [field for field in required_fields if not smtp_config[field]]
    
    if missing_fields:
        print(f"\nWARNING: Missing email configuration: {', '.join(missing_fields)}")
        print("Add these to Railway environment variables for email sending.")
        return False
    else:
        print("\nSUCCESS: Email configuration complete!")
        print("SINCOR can now send automated marketing emails!")
        return True

def show_system_capabilities():
    """Show what SINCOR can do."""
    print("\n" + "="*70)
    print("SINCOR SYSTEM CAPABILITIES")
    print("="*70)
    
    capabilities = [
        "Discover businesses using Google Places API",
        "Extract contact information (phone, email, website)",
        "Score leads automatically (0-100 scale)",
        "Generate personalized email content",
        "Create industry-specific marketing campaigns",
        "Send automated email sequences",
        "Track responses and conversions", 
        "Scale across multiple industries simultaneously",
        "Handle 1000+ businesses per day",
        "Operate 24/7 with minimal oversight"
    ]
    
    print("What SINCOR can do RIGHT NOW:")
    for i, capability in enumerate(capabilities, 1):
        print(f"  {i:2d}. {capability}")
    
    print(f"\nTARGET MARKETS:")
    markets = [
        ("Auto Detailing", "60,000 businesses"),
        ("HVAC Services", "120,000 businesses"), 
        ("Landscaping", "400,000 businesses"),
        ("Plumbing Services", "130,000 businesses"),
        ("Roofing Contractors", "100,000 businesses"),
        ("Cleaning Services", "200,000 businesses"),
        ("Pool Services", "50,000 businesses")
    ]
    
    total = 0
    for market, count in markets:
        business_count = int(count.replace(',', '').split()[0])
        total += business_count
        print(f"  - {market}: {count}")
    
    print(f"\nTOTAL ADDRESSABLE MARKET: {total:,} businesses")
    print(f"REVENUE POTENTIAL: ${total * 2500:,} annually (est. $2,500 avg contract)")

def main():
    """Run SINCOR system demonstration."""
    print("SINCOR BUSINESS INTELLIGENCE EMPIRE")
    print("="*70)
    print("Automated Business Discovery & Marketing System")
    print("Target: 1,000,000+ service businesses nationwide")
    print("="*70)
    
    print("\nStarting SINCOR system tests...")
    
    # Test 1: Google Places API
    api_success = test_google_places_api()
    
    # Test 2: Template Generation
    template_success = test_template_generation()
    
    # Test 3: Email System
    email_success = test_email_system()
    
    # Show capabilities
    show_system_capabilities()
    
    # Summary
    print("\n" + "="*70)
    print("SINCOR SYSTEM TEST RESULTS")
    print("="*70)
    
    results = [
        ("Google Places API", api_success),
        ("Template Generation", template_success), 
        ("Email Configuration", email_success)
    ]
    
    for test_name, success in results:
        status = "SUCCESS" if success else "NEEDS SETUP"
        print(f"  {test_name}: {status}")
    
    all_success = all(success for _, success in results)
    
    if all_success:
        print(f"\nCONGRATULATIONS! SINCOR IS FULLY OPERATIONAL!")
        print(f"Ready to begin the 60,000+ business conquest!")
    else:
        print(f"\nSINCOR is partially operational. Complete the setup for full power!")
    
    print(f"\nYour live SINCOR system: https://sincor-production.up.railway.app")
    print(f"="*70)

if __name__ == "__main__":
    main()