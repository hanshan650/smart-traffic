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
echo   警务端：http://localhost:5173/dashboard
echo   民众端：http://localhost:5173/citizen
echo   代理：/api  -^>  http://127.0.0.1:8000
echo ============================================================
echo.
echo [手机预览] 需先启动后端与后端所在电脑的局域网可达，
echo            手机与电脑同一 Wi-Fi 后访问 http://^<电脑IP^>:5173/citizen
echo            注意：此方式下浏览器禁用定位功能，属正常限制。
echo            要完整验证定位与 PWA，用 usb 转发：
echo              adb reverse tcp:5173 tcp:5173
echo            然后手机访问 http://127.0.0.1:5173/citizen
echo.

call npm run dev

echo.
echo [服务已停止]
pause
