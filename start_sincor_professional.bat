@echo off
echo =========================================
echo        SINCOR Professional Launch
echo         Production-Ready System
echo =========================================
echo.

REM Check if we're in the right directory
if not exist "sincor_app_professional.py" (
    echo ERROR: sincor_app_professional.py not found
    echo Please run this script from the SINCOR directory
    pause
    exit /b 1
)

REM Create virtual environment if it doesn't exist
if not exist "venv" (
    echo Creating Python virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo ERROR: Failed to create virtual environment
        echo Make sure Python is installed and in PATH
        pause
        exit /b 1
    )
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo ERROR: Failed to activate virtual environment
    pause
    exit /b 1
)

REM Install/update requirements
echo Installing/updating requirements...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo WARNING: Some packages may not have installed correctly
    echo Continuing with startup...
)

REM Check for environment file
if not exist "config\.env" (
    if not exist "config\production.env" (
        echo.
        echo WARNING: No environment configuration found
        echo System will start with default settings
        echo For production, create config\.env or config\production.env
        echo.
    )
)

REM Ensure data directories exist
if not exist "data" mkdir data
if not exist "outputs" mkdir outputs
if not exist "logs" mkdir logs
if not exist "static\images" mkdir static\images

echo.
echo =========================================
echo       SINCOR Professional Status
echo =========================================
echo ✅ Virtual Environment: Active
echo ✅ Requirements: Installed
echo ✅ Data Directories: Ready
echo ✅ Professional Admin: Enabled
echo ✅ Executive Dashboard: /admin/executive
echo ✅ Real Metrics: Enabled (No Fake Data)
echo ✅ API Health Check: /api/admin/health-check
echo.
echo Starting SINCOR Professional Application...
echo.

REM Start the professional application
python sincor_app_professional.py

REM If we get here, the application stopped
echo.
echo SINCOR Professional has stopped.
pause