@echo off
chcp 65001 >nul 2>&1
setlocal

set "ROOT=%~dp0"

echo [1/2] 检查 Python 环境...
cd /d "%ROOT%"
if not exist "venv\Scripts\python.exe" (
    python -m venv venv
    if errorlevel 1 goto :error
    echo   ^✓ 已创建 venv
) else (
    echo   ^✓ venv 已存在
)

echo [2/2] 安装依赖...
call venv\Scripts\activate.bat
pip install -q -r requirements.txt
if errorlevel 1 goto :error
echo   ^✓ 依赖装完

echo.
echo 安装完成。下一步：
echo   1. 确认 .env 已配置（DATABASE_URL 等）
echo   2. 双击 start.bat 启动服务（首次启动自动建表/迁移）
exit /b 0

:error
echo.
echo 安装失败，请检查 Python / 网络是否正常。
exit /b 1
