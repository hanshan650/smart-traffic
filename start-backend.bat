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

if not exist ".env" (
    echo [提示] 未找到 backend\.env，正在从模板创建...
    copy /y ".env.example" ".env" > nul
    echo        已创建。地图与视频源功能需填入自己的 Key 后重启，
    echo        详见 .env 内注释（.env 不会被提交到仓库）。
    echo.
)

for %%F in ("yolov8s.pt" "yolov8n.pt") do (
    if exist "%%~F" goto :model_ok
)
echo [提示] 未找到模型权重 yolov8s.pt / yolov8n.pt
echo        检测功能不可用，其余功能正常。下载地址：
echo        https://github.com/ultralytics/assets/releases
echo.

:model_ok

echo ============================================================
echo   智能交通监测与事故预警平台 — 后端
echo   地址：http://127.0.0.1:8000
echo   文档：http://127.0.0.1:8000/docs
echo ============================================================
echo.
echo [首次使用] 警务端需要登录，口令要先初始化一次。重复执行是安全的，
echo            只会给还没有口令的账号补上：
echo              .venv\Scripts\python.exe scripts\seed_credentials.py
echo            登录名是警号，初始口令见 .env 的 AUTH_DEFAULT_PASSWORD。
echo.

".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 %*

echo.
echo [服务已停止]
pause
