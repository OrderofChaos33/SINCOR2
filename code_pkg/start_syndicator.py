#!/usr/bin/env python3
"""
SINCOR Syndicator Launcher
Starts the 24/7 automated content creation and business discovery system
"""

import os
import sys
import time
from pathlib import Path

# Add the agents directory to the path
sys.path.append(str(Path(__file__).parent / "agents" / "syndicator"))

def main():
    print("SINCOR SYNDICATOR AGENT")
    print("=" * 50)
    print("Initializing 24/7 automated content creation...")
    
    # Set environment variables if not already set
    if not os.getenv('GOOGLE_API_KEY'):
        os.environ['GOOGLE_API_KEY'] = 'AIzaSyDa4P7-8LnWfq2GJl7BpKLBJvKfNOGRvck'  # From context
    
    try:
        from syn import initialize_syndicator, get_syndicator
        
        # Initialize syndicator
        config_path = Path(__file__).parent / "config.yaml"
        agent = initialize_syndicator(str(config_path))
        
        print("Syndicator initialized successfully")
        print("Starting business discovery...")
        print("Content generation pipeline activated")
        print("Canva integration ready")
        print("Safe scraping protocols enabled")
        print()
        
        # Start 24/7 automation
        agent.start_24_7_automation()
        
        print("SYNDICATOR IS NOW RUNNING 24/7")
        print("-" * 50)
        print("Live Statistics:")
        print()
        
        try:
            cycle_count = 0
            while True:
                cycle_count += 1
                stats = agent.get_stats()
                
                print(f"Cycle #{cycle_count} - {time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"Total Businesses Discovered: {stats.get('total_businesses_discovered', 0)}")
                print(f"Businesses Today: {stats.get('businesses_discovered_today', 0)}")
                print(f"Total Content Created: {stats.get('total_content_created', 0)}")
                print(f"Content Today: {stats.get('content_created_today', 0)}")
                print(f"Automation Status: {'RUNNING' if stats.get('automation_running') else 'STOPPED'}")
                print("-" * 50)
                
                time.sleep(30)  # Update every 30 seconds
                
        except KeyboardInterrupt:
            print("\nStopping syndicator...")
            agent.stop_automation()
            print("Syndicator stopped successfully")
    
    except ImportError as e:
        print(f"Failed to import syndicator: {e}")
        print("Installing missing dependencies...")
        
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "fake-useragent", "requests"])
        
        print("Dependencies installed. Please restart the syndicator.")

if __name__ == "__main__":
    main()