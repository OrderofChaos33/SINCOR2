@echo off
REM SINCOR Agent Monitor Launcher

echo ==========================================
echo SINCOR Agent Monitoring System
echo ==========================================
echo.

cd /d "%~dp0"

echo Checking Python environment...
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python not found. Please install Python 3.8+ first.
    pause
    exit /b 1
)

echo.
echo Choose monitoring option:
echo.
echo 1) Quick Status Check (command line)
echo 2) Web Dashboard Monitor (browser)
echo 3) Both
echo.

set /p choice="Enter choice (1-3): "

if "%choice%"=="1" (
    echo.
    echo Running quick agent status check...
    python check_agents.py
    echo.
    pause
) else if "%choice%"=="2" (
    echo.
    echo Starting web dashboard...
    echo Open http://localhost:5001 in your browser
    echo Press Ctrl+C to stop
    python agent_monitor.py
) else if "%choice%"=="3" (
    echo.
    echo Running quick status check first...
    python check_agents.py
    echo.
    echo Press any key to start web dashboard...
    pause >nul
    echo.
    echo Starting web dashboard...
    echo Open http://localhost:5001 in your browser
    echo Press Ctrl+C to stop
    python agent_monitor.py
) else (
    echo Invalid choice. Running quick status check...
    python check_agents.py
    pause
)