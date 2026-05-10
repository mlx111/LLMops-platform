@echo off
chcp 65001 >nul
setlocal

echo ====================================
echo Stopping LLMOps Platform
echo ====================================
echo.

echo [1/3] Closing Celery window...
taskkill /FI "WINDOWTITLE eq LLMOps Celery*" /T /F >nul 2>&1
if errorlevel 1 (
    echo [INFO] Celery window not found or already stopped.
) else (
    echo [OK] Celery stopped.
)

echo [2/3] Closing backend window...
taskkill /FI "WINDOWTITLE eq LLMOps Backend*" /T /F >nul 2>&1
if errorlevel 1 (
    echo [INFO] Backend window not found or already stopped.
) else (
    echo [OK] Backend stopped.
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do (
    taskkill /PID %%p /T /F >nul 2>&1
)

echo [3/3] Closing frontend window...
taskkill /FI "WINDOWTITLE eq LLMOps Frontend*" /T /F >nul 2>&1
if errorlevel 1 (
    echo [INFO] Frontend window not found or already stopped.
) else (
    echo [OK] Frontend stopped.
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":5173 .*LISTENING"') do (
    taskkill /PID %%p /T /F >nul 2>&1
)

echo.
echo Done.
exit /b 0
