#!/usr/bin/env bash
# Đóng gói bản demo thành 1 file zip để bàn giao (chạy được cả khi máy đích không có mạng).
# Kết quả: logistics-3pl-demo-image.tar (image) + logistics-3pl-demo.zip (toàn bộ gói).
set -euo pipefail
cd "$(dirname "$0")"

ZIP=logistics-3pl-demo.zip
TAR=logistics-3pl-demo-image.tar
FILES=(install.sh install.command install.bat run.sh run.command run.bat stop.sh stop.command stop.bat
       README-Mac.txt README-Windows.txt README-OFFLINE.txt docker-compose.demo.yml .env.demo "$TAR")

command -v docker >/dev/null || { echo "Chưa có Docker."; exit 1; }
command -v zip >/dev/null || { echo "Chưa có lệnh zip."; exit 1; }

echo "[1/4] Dựng image demo..."
docker compose -f docker-compose.demo.yml --env-file .env.demo build

# Máy đích offline cần cả postgres/redis: đảm bảo máy này đã có đủ rồi lưu chung vào .tar.
echo "[2/4] Đảm bảo có image postgres, redis..."
docker image inspect postgres:17 redis:7.4-alpine >/dev/null 2>&1 \
  || docker compose -f docker-compose.demo.yml --env-file .env.demo pull db redis

echo "[3/4] Lưu image ra $TAR (vài phút)..."
rm -f "$TAR" "$ZIP"
docker save logistics-3pl-platform:demo postgres:17 redis:7.4-alpine -o "$TAR"

echo "[4/4] Nén $ZIP..."
for f in "${FILES[@]}"; do [ -e "$f" ] || { echo "Thiếu file $f"; exit 1; }; done
zip -r "$ZIP" "${FILES[@]}"

echo ""
ls -lh "$ZIP" "$TAR"
echo "Xong. Gửi file $ZIP cho người thử nghiệm."
