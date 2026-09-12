@echo off
cd /d "%~dp0"
echo Starting CCTV MJPEG backend on port 5000...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" stream_server.py
) else (
  python stream_server.py
)
pause
