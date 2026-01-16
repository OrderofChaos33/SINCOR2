@echo off
cd /d %~dp0\..\external\sin-bonding-curve
echo Running Liquidation Sniper...
node scripts/liquidation-sniper.js > sniper_out.txt 2>&1
echo Done.
type sniper_out.txt
