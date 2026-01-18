@echo off
cd /d "%~dp0..\external\sin-bonding-curve"
echo ═══════════════════════════════════════════════════════════════
echo   FLASH LOAN LIQUIDATOR V3 - Multi-Protocol (Aave, Compound, Moonwell)
echo ═══════════════════════════════════════════════════════════════
echo.
echo Select version to run:
echo   1) V3 - Multi-Protocol (RECOMMENDED)
echo   2) V2 - Aave Only (Legacy)
echo   3) Sniper Mode - Target specific user
echo.
set /p choice="Enter choice (1-3): "

if "%choice%"=="1" (
    echo Starting Flash Liquidator V3...
    node scripts/bots/flash_liquidator_v3.js
) else if "%choice%"=="2" (
    echo Starting Flash Liquidator V2 (Legacy)...
    node scripts/bots/flash_liquidator_bot.js
) else if "%choice%"=="3" (
    echo Starting Sniper Mode...
    node scripts/liquidation-sniper.js
) else (
    echo Invalid choice. Starting V3 by default...
    node scripts/bots/flash_liquidator_v3.js
)
pause
