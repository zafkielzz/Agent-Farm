#!/usr/bin/env bash
# ==============================================================================
# AGY Orchestrator - Stop Script
# Chỉ dừng tiến trình đang LẮNG NGHE trên cổng (không đụng tới trình duyệt đang kết nối vào cổng đó).
# Terminal chạy trong tmux vẫn sống; lần khởi động sau hub tự gắn lại.
# ==============================================================================
PORT=${1:-8888}

echo "🔍 Đang tìm tiến trình AGY Hub đang lắng nghe trên cổng $PORT..."
PIDS=$(lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
if [ -z "$PIDS" ]; then
    PIDS=$(ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u || true)
fi

if [ -z "$PIDS" ]; then
    echo "ℹ️ Không có tiến trình nào đang lắng nghe trên cổng $PORT."
    exit 0
fi

echo "🛑 Dừng AGY Hub (PID: $PIDS)..."
kill -TERM $PIDS 2>/dev/null || true
for i in $(seq 1 20); do
    sleep 0.5
    STILL=""
    for p in $PIDS; do kill -0 "$p" 2>/dev/null && STILL="$STILL $p"; done
    [ -z "$STILL" ] && { echo "✅ Đã tắt AGY Hub."; exit 0; }
done
echo "⚠️ Hub chưa thoát sau 10 giây, buộc dừng (PID:$STILL)..."
kill -KILL $STILL 2>/dev/null || true
sleep 1
echo "✅ Đã buộc tắt AGY Hub."
