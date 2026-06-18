@echo off
chcp 65001 >nul
cd /d "%~dp0backend"

echo Starting backend...
echo Checking Python...
python --version
echo.
echo Starting uvicorn...
python -m uvicorn main:app --host 0.0.0.0 --port 8767
pause
