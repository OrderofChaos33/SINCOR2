@echo off
REM SINCOR Environment Setup - Windows Batch Script

echo ==========================================
echo SINCOR Environment Configuration Setup
echo ==========================================
echo.

cd /d "%~dp0"

echo Checking Python environment...
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python not found. Please install Python 3.8+ first.
    echo Download from: https://python.org/downloads/
    pause
    exit /b 1
)

echo.
echo Starting interactive environment setup...
echo.

python setup_environment.py

echo.
echo Setup completed! Check the output above for next steps.
echo.
pause