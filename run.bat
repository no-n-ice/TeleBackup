@echo off
title TeleBackup
cd /d "%~dp0"

if not exist "config.ini" (
    if exist "config.ini.example" (
        echo [TeleBackup] Creating config.ini from config.ini.example...
        copy config.ini.example config.ini >nul
    )
)

echo [TeleBackup] Starting server on http://127.0.0.1:8000 ...
python app.py
if errorlevel 1 (
    echo.
    echo [TeleBackup] Application terminated with error.
    pause
)

