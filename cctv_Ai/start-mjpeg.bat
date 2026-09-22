@echo off
cd /d "%~dp0"
echo Starting CCTV AI backend from %CD%
echo Keep this window open. Stop the previous backend with Ctrl+C first.
echo.
echo Model downloads and recorder-title sync are separate setup steps.
echo If Helmet Violations reports a missing model, run setup-ai-models.bat.
echo To refresh recorder titles, run python nvr_channel_sync.py separately.
echo.
echo Starting CCTV AI backend with MySQL analytics on port 5000...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" stream_server_mysql.py
) else (
  python stream_server_mysql.py
)
pause
