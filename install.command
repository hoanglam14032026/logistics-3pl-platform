#!/usr/bin/env bash
# Bấm đúp để cài (Mac). Mở bằng Terminal, giữ cửa sổ để đọc kết quả.
cd "$(dirname "$0")" || exit 1
./install.sh
code=$?
echo ""
read -r -p "Nhấn Enter để đóng cửa sổ..." _
exit $code
