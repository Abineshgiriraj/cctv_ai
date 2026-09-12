@echo off
cd /d "%~dp0"
echo Starting CCTV AI backend with MySQL analytics on port 5000...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" stream_server_mysql.py
) else (
  python stream_server_mysql.py
)
pause
