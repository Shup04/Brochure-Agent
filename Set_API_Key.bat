@echo off
setlocal

echo This stores the OpenAI API key in this Windows user's environment variables.
echo The key will not be saved in this project folder or pushed to GitHub.
echo.
set /p OPENAI_KEY=Paste OpenAI API key: 

if "%OPENAI_KEY%"=="" (
    echo No key entered.
    pause
    exit /b 1
)

setx OPENAI_API_KEY "%OPENAI_KEY%"

echo.
echo API key saved.
echo Close and reopen the brochure app before generating a brochure.
pause
