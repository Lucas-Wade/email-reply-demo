@echo off
chcp 65001 >nul
title 询盘回复系统

:loop
echo [%date% %time%] 启动服务...
cd /d "%~dp0"
venv\Scripts\python -m uvicorn main:app --port 8000
echo.
echo [%date% %time%] 服务已退出（退出码: %errorlevel%），5 秒后自动重启...
echo 如需彻底停止，请在此窗口按 Ctrl+C
timeout /t 5 /nobreak >nul
goto loop
