@echo off
title Audit Platform
cd /d "%~dp0"

echo ========================================
echo   Audit Platform
echo ========================================
echo.

echo [START] Backend (8767)...
start "Backend" cmd /k "cd /d D:\Demo\audit-platform\backend & python -m uvicorn main:app --host 0.0.0.0 --port 8767"

echo Waiting for backend...
timeout /t 3 /nobreak >nul

echo [START] Frontend (5173)...
start "Frontend" cmd /k "cd /d D:\Demo\audit-platform\frontend & call npm run dev"

echo.
echo ========================================
echo   Frontend: http://localhost:5173
echo   Backend:  http://localhost:8767
echo ========================================
echo.
echo Close this window or press any key to exit...
pause >nul
