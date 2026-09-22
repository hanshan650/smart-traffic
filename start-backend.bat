@echo off
REM ============================================================
REM  启动后端服务（FastAPI + Uvicorn）
REM  双击本文件即可运行；关闭窗口即停止服务。
REM ============================================================
chcp 65001 > nul
cd /d "%~dp0backend"

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境 backend\.venv
    echo        请先在 backend 目录执行：python -m venv .venv
    pause
    exit /b 1
)

echo ============================================================
echo   智能交通监测与事故预警平台 — 后端
echo   地址：http://127.0.0.1:8000
echo   文档：http://127.0.0.1:8000/docs
echo ============================================================
echo.

".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 %*

echo.
echo [服务已停止]
pause
