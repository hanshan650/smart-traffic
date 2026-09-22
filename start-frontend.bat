@echo off
REM ============================================================
REM  启动前端开发服务器（Vite）
REM  双击本文件即可运行；关闭窗口即停止服务。
REM  需先启动后端（start-backend.bat）。
REM ============================================================
chcp 65001 > nul
cd /d "%~dp0frontend"

if not exist "node_modules" (
    echo [提示] 未找到 node_modules，正在安装依赖...
    call npm install
)

echo ============================================================
echo   智能交通监测与事故预警平台 — 前端
echo   地址：http://localhost:5173
echo   代理：/api  -^>  http://127.0.0.1:8000
echo ============================================================
echo.

call npm run dev

echo.
echo [服务已停止]
pause
