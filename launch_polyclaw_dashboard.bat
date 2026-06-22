@echo off
cd /d "%~dp0"
echo Starting Polyclaw Observer Dashboard...
python verticals/trading/polyclaw/dashboard.py
if errorlevel 1 (
    echo.
    echo Dashboard failed to start.
    echo Make sure you have customtkinter installed:
    echo     pip install customtkinter
    pause
) else (
    echo Dashboard closed.
)
pause
