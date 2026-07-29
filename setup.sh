#!/usr/bin/env bash
# GateFlow 首次安装脚本（装依赖，不启动服务）
# 使用方式: bash setup.sh

set -e

cd "$(dirname "$0")"

echo "[1/1] 安装依赖..."
if [ ! -f "venv/bin/python" ]; then
    python3 -m venv venv
    echo "  ✓ 已创建 venv"
else
    echo "  ✓ venv 已存在"
fi
./venv/bin/pip install -q -r requirements.txt
echo "  ✓ 依赖装完"

echo ""
echo "安装完成。下一步："
echo "  1. 确认 .env 已配置（DATABASE_URL 等）"
echo "  2. 运行 bash start.sh 启动服务（首次启动自动建表/迁移）"
