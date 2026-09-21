@echo off
cd /d "%~dp0"
if not exist "models\helmet.pt" goto setupmodels
if not exist "models\road_damage.pt" goto setupmodels
goto modelsready

:setupmodels
echo Required helmet/road AI weights are missing.
echo Downloading verified pretrained models...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" setup_ai_models.py
) else (
  python setup_ai_models.py
)

:modelsready
echo.
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
