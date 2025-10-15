#!/usr/bin/env python3
"""
Test that integration routes are working
"""

import requests
import json

def test_routes():
    """Test if the integration routes are accessible."""
    
    base_url = "https://getsincor.com"  # Railway URL
    
    routes_to_test = [
        "/connect-calendar",
        "/connect-payments", 
        "/connect-email",
        "/connect-sms",
        "/test-integrations"
    ]
    
    print("Testing integration routes on Railway...")
    
    for route in routes_to_test:
        try:
            url = f"{base_url}{route}"
            print(f"\nTesting: {url}")
            
            response = requests.post(url, 
                                   headers={'Content-Type': 'application/json'},
                                   timeout=10)
            
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    print(f"✅ Route works - Response: {data.get('success', 'unknown')}")
                except:
                    print(f"✅ Route works - HTML response")
            else:
                print(f"❌ Route failed - Status: {response.status_code}")
                
        except requests.exceptions.Timeout:
            print(f"⏰ Route timeout - {route}")
        except Exception as e:
            print(f"❌ Route error - {e}")
    
    # Test if the main dashboard loads
    try:
        print(f"\nTesting main dashboard: {base_url}/admin")
        response = requests.get(f"{base_url}/admin", timeout=10)
        print(f"Dashboard status: {response.status_code}")
        
        if "Connect Calendar" in response.text:
            print("✅ Dashboard contains integration buttons")
        else:
            print("❌ Dashboard missing integration buttons")
            
    except Exception as e:
        print(f"❌ Dashboard error - {e}")

if __name__ == "__main__":
    test_routes()