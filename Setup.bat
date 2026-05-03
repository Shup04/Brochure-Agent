@echo off
setlocal
cd /d "%~dp0"

echo Setting up Brochure Agent...

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Could not create the Python virtual environment.
    echo Install Python from https://www.python.org/downloads/windows/ and try again.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo Setup complete.
echo If this is the first install, run Set_API_Key.bat next.
pause
