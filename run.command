#!/usr/bin/env bash
# Bấm đúp để chạy bản demo (Mac).
cd "$(dirname "$0")" || exit 1
./run.sh
code=$?
echo ""
read -r -p "Nhấn Enter để đóng cửa sổ (ứng dụng vẫn chạy nền)..." _
exit $code
