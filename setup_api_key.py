#!/usr/bin/env python3
"""
SINCOR Google API Key Setup

Simple script to configure your Google API key from Railway locally.
"""

import os
from pathlib import Path

def setup_google_api_key():
    print("=== SINCOR Google API Key Setup ===")
    print()
    print("STEP 1: Get your API key from Railway:")
    print("1. Go to https://railway.app/dashboard")  
    print("2. Click your SINCOR project")
    print("3. Click 'Variables' tab")
    print("4. Look for GOOGLE_PLACES_API_KEY or GOOGLE_API_KEY")
    print("5. Copy the value (starts with 'AIza...')")
    print()
    
    # Simple manual input method
    print("STEP 2: Enter your API key below:")
    print("(Paste the key and press Enter)")
    print()
    
    try:
        api_key = input("Google API Key: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled.")
        return False
    
    if not api_key:
        print("No key provided. Please run the script again with your API key.")
        return False
    
    if not api_key.startswith('AIza'):
        print("Warning: Google API keys usually start with 'AIza' - double check this is correct")
        response = input("Continue anyway? (y/n): ").lower()
        if response != 'y':
            return False
    
    # Update .env file
    config_dir = Path(__file__).parent / "config"
    env_file = config_dir / ".env"
    
    print(f"\nUpdating {env_file}...")
    
    # Read existing content
    if env_file.exists():
        with open(env_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    else:
        lines = []
    
    # Remove existing Google API key lines
    filtered_lines = []
    for line in lines:
        if not line.strip().startswith('GOOGLE_API_KEY') and not line.strip().startswith('GOOGLE_PLACES_API_KEY'):
            filtered_lines.append(line)
    
    # Add new API key
    if not filtered_lines or not filtered_lines[-1].endswith('\n'):
        filtered_lines.append('\n')
    
    filtered_lines.append(f"# Google API Keys - Added {os.getenv('DATE', 'manually')}\n")
    filtered_lines.append(f"GOOGLE_API_KEY={api_key}\n")
    filtered_lines.append(f"GOOGLE_PLACES_API_KEY={api_key}\n")
    
    # Write back to file
    try:
        with open(env_file, 'w', encoding='utf-8') as f:
            f.writelines(filtered_lines)
        
        print("✅ API key successfully saved!")
        print(f"✅ Updated: {env_file}")
        print()
        
        # Test the configuration
        print("Testing configuration...")
        
        # Reload environment
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
            
            test_key = os.getenv('GOOGLE_API_KEY', '')
            if test_key:
                print("✅ API key is now available in environment")
                print(f"   Key starts with: {test_key[:10]}...")
            else:
                print("⚠️  API key not found in environment - restart may be needed")
        except ImportError:
            print("⚠️  python-dotenv not available - restart app to load new key")
        
        print()
        print("🎉 Setup Complete!")
        print()
        print("Next steps:")
        print("1. Restart your SINCOR application")
        print("2. Run: python check_agents.py")
        print("3. Look for: [ACTIVE] Business Intelligence: CONFIGURED")
        print()
        
        return True
        
    except Exception as e:
        print(f"❌ Error saving configuration: {e}")
        return False

if __name__ == "__main__":
    setup_google_api_key()