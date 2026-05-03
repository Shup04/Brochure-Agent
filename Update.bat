@echo off
setlocal
cd /d "%~dp0"

echo Updating Brochure Agent...

git pull --ff-only
if errorlevel 1 (
    echo.
    echo Git update failed. Make sure Git is installed and this folder is a git clone.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Virtual environment not found. Running setup first...
    call Setup.bat
    exit /b %errorlevel%
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo Update complete.
pause
