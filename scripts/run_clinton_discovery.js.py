
import sys
import os
import json
import sqlite3
from pathlib import Path
from business_discovery import BusinessDiscoveryEngine

def run_clinton_discovery():
    print("\n" + "="*50)
    print("SINCOR BUSINESS DISCOVERY: CLINTON, IOWA MODE")
    print("="*50)
    
    # Instantiate Engine
    engine = BusinessDiscoveryEngine()
    
    location = "Clinton, Iowa"
    industry = "Auto Detailing"
    
    print(f"[ ] Searching for: '{industry}' in '{location}'...")
    
    # Run Discovery
    # This will use the _get_demo_businesses fallback if no Google API key is present
    # which effectively acts as a simulation/demo of the logic.
    businesses = engine.discover_businesses(industry, location)
    
    print(f"\n[✓] Discovery Complete. Found {len(businesses)} prospects:")
    print("-" * 50)
    
    for idx, biz in enumerate(businesses[:5], 1): # Show top 5
        print(f"{idx}. {biz['name']}")
        print(f"   Score: {biz.get('lead_score', 'N/A')}")
        print(f"   Status: {biz.get('contact_status', 'N/A')}")
        print("   ---")
        
    print(f"...and {max(0, len(businesses)-5)} more.")
    print("="*50)
    print("CAMPAIGN READY FOR ACTIVATION")

if __name__ == "__main__":
    run_clinton_discovery()
