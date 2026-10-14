#!/usr/bin/env bash
# ==============================================================================
# agy-hub: Google Account Profile Login Script
# ==============================================================================
set -e

PROFILE_ID=${1:-1}

# Đảm bảo đường dẫn PROFILE_HOME không bị lồng nhau nếu HOME đã được set
if [[ "$HOME" == *".agy_profiles"* ]]; then
    PROFILE_HOME="$HOME"
else
    PROFILE_HOME="$HOME/.agy_profiles/acc$PROFILE_ID"
fi

echo "================================================================================"
echo "🔑 KHỞI TẠO ĐĂNG NHẬP GOOGLE ACCOUNT CHO: Profile acc$PROFILE_ID"
echo "📁 Thư mục Profile: $PROFILE_HOME"
echo "================================================================================"

mkdir -p "$PROFILE_HOME/.local/share/keyrings"
# Auth state was already moved to ~/.agy_profiles/_backup/ by the hub (reset_profile_auth).
export HOME="$PROFILE_HOME"

echo ""
echo "👉 HƯỚNG DẪN BIND TÀI KHOẢN GOOGLE PRO:"
echo "1. Hãy copy đường link OAuth hiển thị dưới đây và mở trên trình duyệt (Tab ẩn danh nếu cần)."
echo "2. Đăng nhập tài khoản Google bạn muốn gán cho acc$PROFILE_ID."
echo "3. Sau khi Google cấp Authorization Code (chuỗi mã dài), HÃY CLICK VÀO MÀN HÌNH TERMINAL NÀY, DÁN MÃ VÀO VÀ BẤM ENTER!"
echo "================================================================================"
echo ""

if [ -n "$AGY_ISOLATED_DBUS" ]; then
    agy -p 'ping' --model gemini-3.8-flash-low
else
    echo "Đang mở phiên D-Bus Keyring cô lập để đăng nhập..."
    dbus-run-session -- bash -c "
      eval \$(echo '' | gnome-keyring-daemon --unlock --components=secrets 2>/dev/null)
      agy -p 'ping' --model gemini-3.8-flash-low
    "
fi

echo ""
echo "================================================================================"
echo "🎉 Đăng nhập hoàn tất cho acc$PROFILE_ID!"
echo "👉 Bạn có thể chuyển sang tab 'Tài Khoản Google' để thấy email đã được bind."
echo "================================================================================"
