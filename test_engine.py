#!/usr/bin/env python3
"""
Simple test of SINCOR engine functionality
"""

def test_sincor_engine():
    """Test the SINCOR engine with demo data."""
    print("SINCOR Engine Test")
    print("=" * 40)
    
    try:
        # Import engine
        from sincor_engine import SINCOREngine, CampaignConfig
        
        # Initialize engine
        print("1. Initializing SINCOR Engine...")
        engine = SINCOREngine()
        print("   SUCCESS: Engine initialized")
        
        # Test business discovery
        print("\n2. Testing business discovery...")
        config = CampaignConfig(
            target_industry="auto detailing",
            locations=["Austin, TX"],
            max_businesses_per_day=5
        )
        
        businesses = engine.discover_businesses(config)
        print(f"   SUCCESS: Found {len(businesses)} businesses")
        
        if businesses:
            sample = businesses[0]
            print(f"   Sample: {sample.get('business_name')} ({sample.get('rating')} stars)")
        
        # Test email generation
        print("\n3. Testing email personalization...")
        if businesses:
            email = engine.create_personalized_email(businesses[0])
            print(f"   SUCCESS: Generated personalized email")
            print(f"   Subject: {email['subject'][:50]}...")
            print(f"   Personalization Score: {email['personalization_score']}/100")
        
        # Test dashboard data
        print("\n4. Testing dashboard data...")
        dashboard_data = engine.get_dashboard_data()
        print(f"   SUCCESS: Dashboard loaded")
        print(f"   Businesses: {dashboard_data.get('businesses_discovered', 0)}")
        print(f"   Emails: {dashboard_data.get('emails_sent', 0)}")
        print(f"   Responses: {dashboard_data.get('responses_received', 0)}")
        
        print("\n" + "=" * 40)
        print("ALL TESTS PASSED - SINCOR ENGINE IS READY!")
        return True
        
    except Exception as e:
        print(f"   ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_sincor_engine()