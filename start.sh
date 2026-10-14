#!/usr/bin/env bash
# ==============================================================================
# AGY Orchestrator - Localhost Startup Script
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=${1:-8888}
HOST=${2:-"127.0.0.1"}

echo "========================================================================"
echo "🚀 Khởi động Antigravity Multi-Account Hub (AGY Flow)"
echo "📁 Thư mục: $DIR"
echo "🌐 Địa chỉ: http://$HOST:$PORT"
echo "========================================================================"

cd "$DIR"

# Kiểm tra python
if ! command -v python3 &> /dev/null; then
    echo "❌ Không tìm thấy python3!"
    exit 1
fi

# Chạy server
python3 server.py --host "$HOST" --port "$PORT"
