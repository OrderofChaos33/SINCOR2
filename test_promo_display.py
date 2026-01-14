#!/usr/bin/env python3
"""
Test what the promo page actually looks like
"""

import requests

def test_promo_page():
    """Test the actual promo page output."""
    print("Testing FRIENDSTEST promo page...")
    
    try:
        response = requests.get("https://getsincor.com/free-trial/FRIENDSTEST", timeout=10)
        
        if response.status_code == 200:
            print("✅ Page loads successfully (200 OK)")
            print(f"Content length: {len(response.text)} characters")
            
            # Check for template issues
            if "{promo_code}" in response.text:
                print("❌ FOUND UNPOPULATED TEMPLATE VARIABLES!")
                print("The template variables are not being replaced.")
            elif "FRIENDSTEST" in response.text:
                print("✅ Template variables are populated correctly")
            
            # Check for mobile viewport
            if 'viewport' in response.text:
                print("✅ Mobile viewport tag found")
            else:
                print("❌ Missing mobile viewport tag")
            
            # Save a snippet to see what it looks like
            print("\nFirst 500 characters of response:")
            print("-" * 50)
            print(response.text[:500])
            print("-" * 50)
            
        else:
            print(f"❌ Page failed to load: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Error testing page: {e}")

if __name__ == "__main__":
    test_promo_page()