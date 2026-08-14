@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Safety Law DB Update
python update_db.py
echo.
pause
