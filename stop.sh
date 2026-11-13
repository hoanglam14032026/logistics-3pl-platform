#!/usr/bin/env bash
# Dừng bản demo. KHÔNG dùng `down -v` nên dữ liệu (CSDL, file tải lên) được giữ lại.
set -euo pipefail
cd "$(dirname "$0")"
docker compose -f docker-compose.demo.yml --env-file .env.demo down
echo "Đã dừng, dữ liệu vẫn còn"
