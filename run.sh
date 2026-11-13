#!/usr/bin/env bash
# Chạy bản demo (Mac): khởi động rồi mở trình duyệt.
set -euo pipefail
cd "$(dirname "$0")"

if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop chưa chạy. Hãy mở Docker Desktop, đợi vài chục giây rồi chạy lại."
  exit 1
fi

docker compose -f docker-compose.demo.yml --env-file .env.demo up -d

echo "Đợi ứng dụng sẵn sàng..."
for i in $(seq 1 36); do
  status=$(docker inspect -f '{{.State.Health.Status}}' logistics-3pl-demo-api 2>/dev/null || echo "starting")
  [ "$status" = "healthy" ] && break
  sleep 5
done

echo "Mở trình duyệt: http://localhost:8000/docs (trang thử API; địa chỉ gốc http://localhost:8000)"
open "http://localhost:8000/docs" 2>/dev/null || true
echo ""
echo "Tài khoản demo (mật khẩu chung Demo@123):"
echo "  admin / ops_staff / learner / customer"
echo "Dừng bản demo: bấm đúp stop.command hoặc chạy ./stop.sh"
