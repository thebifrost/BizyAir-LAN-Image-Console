@echo off
setlocal
cd /d "%~dp0"
if not exist .env (
  echo Missing .env. Copy .env.example to .env and fill BIZYAIR_API_KEY and ADMIN_TOKEN.
  pause
  exit /b 1
)
:run
python upload_server.py
set EXIT_CODE=%ERRORLEVEL%
echo.
echo Service exited with code %EXIT_CODE%.
echo Restarting in 5 seconds. Press Ctrl+C to stop.
timeout /t 5 /nobreak >nul
goto run
