#!/usr/bin/env python3
"""
Test SINCOR's monetization system deployment
"""

import requests
import json

def test_sincor_monetization():
    """Test the live SINCOR monetization system."""
    
    base_url = "https://sincor-production.up.railway.app"
    
    print("="*60)
    print("TESTING SINCOR MONETIZATION SYSTEM")
    print("="*60)
    
    # Test 1: Health check
    print("1. Testing system health...")
    try:
        response = requests.get(f"{base_url}/health", timeout=10)
        if response.status_code == 200:
            print("   SUCCESS: System is ONLINE!")
        else:
            print(f"   ERROR: Health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"   ERROR: Could not connect: {e}")
        return False
    
    # Test 2: Marketing website
    print("\n2. Testing marketing website...")
    try:
        response = requests.get(f"{base_url}/", timeout=10)
        if response.status_code == 200:
            if "SINCOR" in response.text and ("Starter" in response.text or "Professional" in response.text):
                print("   SUCCESS: Professional marketing website is live!")
            else:
                print("   WARNING: Fallback website showing (templates not deployed)")
        else:
            print(f"   ERROR: Website failed: {response.status_code}")
    except Exception as e:
        print(f"   ERROR: Website error: {e}")
    
    # Test 3: Checkout pages
    print("\n3. Testing checkout system...")
    plans = ["starter", "professional", "enterprise"]
    
    for plan in plans:
        try:
            response = requests.get(f"{base_url}/checkout/{plan}", timeout=10)
            if response.status_code == 200:
                print(f"   SUCCESS: {plan.capitalize()} checkout page working!")
            elif response.status_code == 404:
                print(f"   ERROR: {plan.capitalize()} checkout not found (checkout.py not imported)")
            else:
                print(f"   WARNING: {plan.capitalize()} checkout issue: {response.status_code}")
        except Exception as e:
            print(f"   ERROR: {plan.capitalize()} error: {e}")
    
    # Test 4: Success page
    print("\n4. Testing success page...")
    try:
        response = requests.get(f"{base_url}/success", timeout=10)
        if response.status_code == 200:
            print("   SUCCESS: Success page working!")
        else:
            print(f"   ERROR: Success page failed: {response.status_code}")
    except Exception as e:
        print(f"   ERROR: Success page error: {e}")
    
    print(f"\n{'='*60}")
    print("SINCOR MONETIZATION STATUS")
    print(f"{'='*60}")
    print(f"Website: {base_url}")
    print(f"Checkout: {base_url}/checkout/starter")
    print(f"Revenue Model: $297-$1,497/month subscriptions")
    print(f"Target: Service businesses nationwide")
    
    print(f"\nNEXT STEPS:")
    print(f"1. Add Stripe API keys to Railway environment")
    print(f"2. Force redeploy if checkout routes not working")
    print(f"3. Test end-to-end payment flow")
    print(f"4. Launch marketing campaigns")

if __name__ == "__main__":
    test_sincor_monetization()