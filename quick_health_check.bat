@echo off
echo Testing SINCOR Railway App Health...
echo.

echo Starting Railway app locally...
start /b python sincor_app_railway.py

echo Waiting for app to start...
timeout /t 3 /nobreak >nul

echo Testing routes...
echo.

echo Testing homepage (/)...
curl -s -o nul -w "Status: %%{http_code}" http://127.0.0.1:5000/
echo.

echo Testing executive dashboard (/admin/executive)...
curl -s -o nul -w "Status: %%{http_code}" http://127.0.0.1:5000/admin/executive
echo.

echo Testing demo page (/demo)...
curl -s -o nul -w "Status: %%{http_code}" http://127.0.0.1:5000/demo
echo.

echo Testing login page (/login)...
curl -s -o nul -w "Status: %%{http_code}" http://127.0.0.1:5000/login
echo.

echo.
echo Health check complete!
echo If all routes show "Status: 200", the app is working properly.
echo.
echo If Railway is still showing 500 errors, the issue is likely:
echo 1. Railway is not using the updated sincor_app_railway.py file
echo 2. Railway environment variables are missing
echo 3. Railway needs to be redeployed with the new configuration
echo.

echo Stopping test server...
taskkill /f /im python.exe 2>nul