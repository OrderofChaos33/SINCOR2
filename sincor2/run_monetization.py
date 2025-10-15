#!/usr/bin/env python3
"""
Execute SINCOR Monetization Strategy with Real PayPal Payment Processing
Loads .env credentials and runs the monetization engine
"""

import os
import asyncio
import json
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables from .env file
load_dotenv()

# Verify PayPal credentials are loaded
paypal_id = os.getenv('PAYPAL_REST_API_ID')
paypal_secret = os.getenv('PAYPAL_REST_API_SECRET')

print("=" * 80)
print("SINCOR MONETIZATION ENGINE - LIVE EXECUTION")
print("=" * 80)
print(f"\nStarting: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"\nPayPal Credentials Status:")
print(f"  Client ID: {'[OK]' if paypal_id else '[MISSING]'}")
print(f"  Secret: {'[OK]' if paypal_secret else '[MISSING]'}")
print(f"  Mode: {os.getenv('PAYPAL_MODE', 'sandbox')}")
print()

if not paypal_id or not paypal_secret:
    print("ERROR: PayPal credentials not found in .env file")
    exit(1)

# Import monetization engine
from monetization_engine import MonetizationEngine

async def main():
    """Execute monetization strategy"""

    print("Initializing Monetization Engine...")
    engine = MonetizationEngine()

    print("\nExecuting Strategy: AGGRESSIVE_GROWTH")
    print("Max Concurrent Opportunities: 50")
    print("\nIdentifying and executing revenue opportunities...\n")

    # Execute the strategy with scaled-up concurrent execution
    result = await engine.execute_monetization_strategy(max_concurrent_opportunities=50)

    # Display results
    print("\n" + "=" * 80)
    print("EXECUTION RESULTS")
    print("=" * 80)
    print(json.dumps(result, indent=2))
    print("\n" + "=" * 80)
    print(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    return result

if __name__ == "__main__":
    asyncio.run(main())
