@echo off
cd /d "%~dp0"
echo Installing CivicVision helmet and road-damage AI models...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" setup_ai_models.py
) else (
  python setup_ai_models.py
)
pause
