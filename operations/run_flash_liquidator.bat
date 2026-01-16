@echo off
cd ../external/sin-bonding-curve
echo Starting Base Flash Loan Arbitrage & Liquidator...
node scripts/bots/flash_liquidator_bot.js
pause
