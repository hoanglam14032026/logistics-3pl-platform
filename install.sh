#!/usr/bin/env bash
# Cài bản demo (Mac): kiểm tra Docker → tải/dựng image → khởi động → nạp dữ liệu demo.
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE=(docker compose -f docker-compose.demo.yml --env-file .env.demo)

echo "=== CÀI ĐẶT BẢN DEMO LOGISTICS 3PL ==="

if ! command -v docker >/dev/null 2>&1; then
  echo "Chưa cài Docker Desktop."
  echo "Hãy tải và cài tại: https://www.docker.com/products/docker-desktop/"
  echo "Cài xong, mở Docker Desktop rồi bấm đúp install.command lại."
  exit 1
fi
echo "Đã có $(docker --version)"

if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop chưa chạy. Hãy mở ứng dụng Docker Desktop, đợi biểu tượng cá voi ổn định rồi chạy lại."
  exit 1
fi

if [ ! -f .env.demo ]; then
  echo "Thiếu file .env.demo (cấu hình demo). Hãy giải nén đủ thư mục rồi chạy lại."
  exit 1
fi
# Ghi chú: cấu hình demo luôn đọc từ .env.demo (--env-file), nên không ghi đè .env của máy dev nếu có.

IMAGE=logistics-3pl-platform:demo
TAR=logistics-3pl-demo-image.tar

echo "[1/4] Chuẩn bị các thành phần..."
# Gói offline: nếu máy chưa có image mà thư mục có file .tar thì nạp từ file (không cần mạng).
if ! docker image inspect "$IMAGE" >/dev/null 2>&1 && [ -f "$TAR" ]; then
  echo "Đang nạp image từ $TAR (vài phút)..."
  docker load -i "$TAR"
fi
# Chỉ tải postgres/redis khi máy chưa có (máy offline đã nạp từ .tar thì bỏ qua).
if ! docker image inspect postgres:17 redis:7.4-alpine >/dev/null 2>&1; then
  "${COMPOSE[@]}" pull db redis
fi

echo "[2/4] Khởi động ứng dụng (lần đầu có thể mất vài phút)..."
if [ -f Dockerfile ]; then
  "${COMPOSE[@]}" up -d --build   # có mã nguồn: dựng image
elif docker image inspect "$IMAGE" >/dev/null 2>&1; then
  "${COMPOSE[@]}" up -d --no-build  # gói offline: dùng image đã nạp
else
  echo "Không có image $IMAGE và không có file $TAR. Hãy giải nén đủ gói rồi chạy lại."
  exit 1
fi

echo "[3/4] Đợi ứng dụng sẵn sàng..."
for i in $(seq 1 60); do
  status=$(docker inspect -f '{{.State.Health.Status}}' logistics-3pl-demo-api 2>/dev/null || echo "starting")
  if [ "$status" = "healthy" ]; then
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "Ứng dụng chưa sẵn sàng sau 5 phút. Xem lỗi bằng lệnh: docker logs logistics-3pl-demo-api"
    exit 1
  fi
  sleep 5
done

echo "[4/4] Nạp dữ liệu demo (tài khoản, hợp đồng mẫu)..."
"${COMPOSE[@]}" exec -T api python -m app.scripts.seed_demo

echo ""
echo "=== CÀI ĐẶT XONG ==="
echo "Địa chỉ: http://localhost:8000"
echo "Tài khoản demo: admin / Demo@123"
echo "Lần sau chỉ cần bấm đúp run.command để mở."
