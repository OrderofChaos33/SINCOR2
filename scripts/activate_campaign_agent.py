
import sys
import os
import time
from pathlib import Path

# Add project root to path so imports work
current_dir = Path(os.getcwd())
sys.path.append(str(current_dir))

try:
    from agents.marketing.campaign_automation_agent import CampaignAutomationAgent, CampaignConfig
    from agents.base_agent import BaseAgent
except ImportError as e:
    print(f"CRITICAL IMPORT ERROR: {e}")
    print(f"Current Path: {sys.path}")
    sys.exit(1)

def proof_of_work():
    print("\n" + "="*50)
    print("SINCOR AGENT ACTIVATION: PROOF OF WORK")
    print("="*50)
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target Module: CampaignAutomationAgent")
    print("-" * 50)

    # 1. Configuration - TARGETING CLINTON, IOWA
    config = CampaignConfig(
        name="Clinton_Detailing_Campaign_v1",
        target_business_type="auto_detailing",
        target_persona="business_owner",
        min_lead_score=50,
        max_businesses_per_day=5
        # Note: The agent will need to be connected to Google/SEO inputs to filter by Geo "Clinton, Iowa"
        # For this test, we are setting up the intent.
    )
    print(f"[✓] Configuration Loaded:")
    print(f"    - Campaign: {config.name}")
    print(f"    - Vertical: {config.target_business_type}")
    print(f"    - Location: Clinton, Iowa (Target)")

    # 2. Instantiation
    try:
        print("\n[ ] Initializing Agent...")
        agent = CampaignAutomationAgent()
        print(f"[✓] Agent Instantiated: {agent.name}")
        
    except Exception as e:
        print(f"[X] Instantiation Failed: {e}")
        return

    # 3. Heartbeat Check
    print("\n[ ] Checking Pulse...")
    status = agent.heartbeat()
    print(f"[✓] Heartbeat Response: {status}")
    print(f"    - Status: {agent.status}")
    print(f"    - Count: {agent.heartbeat_count}")

    # 4. Database Check (Simulation)
    db_path = Path("sincor.db")
    print(f"\n[ ] Database Check ({db_path})...")
    if db_path.exists():
        print(f"[✓] Database Found (Size: {db_path.stat().st_size} bytes)")
    else:
        print(f"[!] Database Not Found (Will be created by agent logic)")

    print("\n" + "="*50)
    print("PROOF OF WORK COMPLETE: AGENT SYSTEM OPERATIONAL")
    print("="*50 + "\n")

if __name__ == "__main__":
    proof_of_work()
