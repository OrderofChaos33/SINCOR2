@echo off
REM ================================================
REM IntentHook Deployment Script for Windows
REM Run this from your SINCOR2 project root
REM ================================================

echo.
echo === SINC IntentHook Deployment (Windows) ===
echo.

REM === CONFIGURE THESE VALUES ===
set RPC_URL=https://mainnet.base.org
set TREASURY=0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac
set FEE_BPS=25

REM === SET YOUR PRIVATE KEY HERE (or use .env) ===
REM WARNING: Never commit this file with your real key
set PRIVATE_KEY=YOUR_PRIVATE_KEY_HERE

if "%PRIVATE_KEY%"=="YOUR_PRIVATE_KEY_HERE" (
    echo ERROR: Please edit this file and replace YOUR_PRIVATE_KEY_HERE with your actual private key.
    pause
    exit /b 1
)

echo Deploying IntentHook...
echo Treasury: %TREASURY%
echo Fee: %FEE_BPS% bps
echo.

forge script script/DeployIntentHook.s.sol:DeployIntentHook ^
    --rpc-url %RPC_URL% ^
    --broadcast ^
    --private-key %PRIVATE_KEY% ^
    -vvvv

echo.
echo Deployment finished.
echo Check the output above for the deployed contract address.
echo.
pause