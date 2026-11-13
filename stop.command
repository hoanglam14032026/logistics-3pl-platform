#!/usr/bin/env bash
# Bấm đúp để dừng bản demo (Mac).
cd "$(dirname "$0")" || exit 1
./stop.sh
code=$?
read -r -p "Nhấn Enter để đóng cửa sổ..." _
exit $code
