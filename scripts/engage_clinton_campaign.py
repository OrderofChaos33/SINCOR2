
import sys
import os
import time
from pathlib import Path
from business_discovery import BusinessDiscoveryEngine

# Ensure paths are correct
current_dir = Path(os.getcwd())
sys.path.append(str(current_dir))

def engage_campaign():
    print("\n" + "="*50)
    print("SINCOR CAMPAIGN AGENT: ENGAGEMENT MODE")
    print("="*50)
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Targeting: Auto Detailing @ Clinton, Iowa")
    print("-" * 50)

    # 1. Fetch Integration Targets
    engine = BusinessDiscoveryEngine()
    targets = engine._get_clinton_territory_hunt("auto_detailing")
    
    print(f"[✓] Targets Acquired: {len(targets)}")
    
    # 2. Generate Outreach Plan
    print("\n[ ] Generating Outreach Plan...")
    for i, biz in enumerate(targets, 1):
        print(f"\n   TARGET #{i}: {biz['name']}")
        print(f"   Address: {biz.get('address', 'N/A')}")
        
        # Determine strategy based on data
        if "Mobile" in biz['name']:
            strategy = "Partner - Referral Network"
            angle = "We send you overflow work"
        elif biz['rating'] > 4.5:
            strategy = "Acquisition / High-End Partner"
            angle = "Join the premier network"
        else:
            strategy = "Lead Generation Service"
            angle = "Get more 5-star reviews"
            
        print(f"   ► Strategy: {strategy}")
        print(f"   ► Angle: {angle}")
        print(f"   ► Status: QUEUED FOR CONTACT")

    print("\n" + "="*50)
    print("CAMPAIGN READY FOR EXECUTION")
    print("Awaiting confirmation to send emails/messages.")
    print("="*50 + "\n")

if __name__ == "__main__":
    engage_campaign()
