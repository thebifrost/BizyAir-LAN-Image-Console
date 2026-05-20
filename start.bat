@echo off
setlocal
cd /d "%~dp0"
if not exist .env (
  echo Missing .env. Copy .env.example to .env and fill BIZYAIR_API_KEY and ADMIN_TOKEN.
  pause
  exit /b 1
)
python upload_server.py
pause
