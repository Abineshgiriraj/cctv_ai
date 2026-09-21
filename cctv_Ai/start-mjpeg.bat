@echo off
cd /d "%~dp0"
echo Syncing NVR channel titles...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" nvr_channel_sync.py
) else (
  python nvr_channel_sync.py
)
echo.
echo Starting CCTV AI backend with MySQL analytics on port 5000...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" stream_server_mysql.py
) else (
  python stream_server_mysql.py
)
pause
