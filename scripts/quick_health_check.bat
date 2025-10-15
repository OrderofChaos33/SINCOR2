@echo off
REM Quick Health Check for SINCOR
REM Run this script for a fast system health assessment

echo ==========================================
echo SINCOR Quick Health Check
echo ==========================================
echo.

cd /d "%~dp0"

echo [1/5] Checking Python environment...
python --version
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python not found or not in PATH
    pause
    exit /b 1
)

echo.
echo [2/5] Checking critical files...
if not exist "sincor_app.py" (
    echo ERROR: sincor_app.py not found
    pause
    exit /b 1
)
if not exist "requirements.txt" (
    echo ERROR: requirements.txt not found
    pause
    exit /b 1
)
echo Critical files found

echo.
echo [3/5] Testing Python syntax...
python -m py_compile sincor_app.py
if %ERRORLEVEL% neq 0 (
    echo ERROR: Syntax errors in sincor_app.py
    pause
    exit /b 1
)
echo Syntax check passed

echo.
echo [4/5] Checking dependencies...
python -c "import flask, requests, sqlite3, datetime, pathlib, json, threading, time, os, sys, re, csv; print('Core dependencies available')"
if %ERRORLEVEL% neq 0 (
    echo ERROR: Missing critical dependencies
    echo Try: pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo [5/5] Checking directory structure...
if not exist "logs" mkdir logs
if not exist "data" mkdir data
if not exist "outputs" mkdir outputs
echo Directory structure verified

echo.
echo ==========================================
echo Quick Health Check COMPLETED
echo ==========================================
echo.
echo System appears healthy. For comprehensive testing run:
echo   python test_crash_diagnostics.py
echo   python test_e2e_validation.py
echo   python run_final_checks.py
echo.
pause