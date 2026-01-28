#!/usr/bin/env python3
"""
RUN THIS - Autonomous sales bot that makes money
"""

import time
from customer_acquisition_bot import CustomerAcquisitionBot

bot = CustomerAcquisitionBot()

print("\n🚀 SINCOR SALES BOT RUNNING")
print("Finding customers and making sales...\n")

while True:
    bot.run_acquisition_cycle()
    print("\n⏳ Waiting 1 hour before next cycle...")
    time.sleep(3600)  # Run every hour
