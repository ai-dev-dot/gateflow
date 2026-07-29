#!/usr/bin/env bash
# GateFlow 启动脚本
# 使用方式: bash start.sh

set -e

cd "$(dirname "$0")"

cleanup() {
    echo ""
    echo "正在停止服务..."
    kill $BACKEND_PID 2>/dev/null
    wait $BACKEND_PID 2>/dev/null
    echo "已停止。"
}
trap cleanup EXIT INT TERM

echo "=== 启动 GateFlow (port 8000) ==="
echo "运行数据库迁移 (alembic upgrade head)..."
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!

echo ""
echo "GateFlow 已启动: http://localhost:8000"
echo "按 Ctrl+C 停止服务"
echo ""

wait
