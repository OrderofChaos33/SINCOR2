@echo off
REM Quick Edit Helper - Unlocks files, allows editing, then re-locks
echo ============================================
echo SINCOR Quick Edit Helper
echo ============================================
echo.

if "%1"=="" (
    echo Usage: quick_edit.bat [unlock^|lock^|verify^|status]
    echo.
    echo   unlock  - Unlock all critical files for editing
    echo   lock    - Lock all critical files after editing
    echo   verify  - Check file integrity
    echo   status  - Show lock status
    exit /b 0
)

if "%1"=="unlock" (
    echo Unlocking files for editing...
    python security_lockdown.py unlock
    echo.
    echo [OK] Files unlocked. Make your changes, then run:
    echo      quick_edit.bat lock
)

if "%1"=="lock" (
    echo Updating baseline and locking files...
    python security_lockdown.py baseline
    python security_lockdown.py lock
    echo.
    echo [OK] Files locked and secured!
)

if "%1"=="verify" (
    python security_lockdown.py verify
)

if "%1"=="status" (
    echo Checking file permissions...
    python -c "import os; files=['app.py','agency_kernel.py','cortecs_core.py']; [print(f'[LOCKED]' if oct(os.stat(f).st_mode)[-3:]=='444' else f'[UNLOCKED]', f) for f in files if os.path.exists(f)]"
)
