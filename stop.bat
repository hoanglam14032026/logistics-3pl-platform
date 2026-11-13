@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
rem Không dùng "down -v" nên dữ liệu được giữ lại.
docker compose -f docker-compose.demo.yml --env-file .env.demo down
if errorlevel 1 (
  echo Không dừng được. Docker Desktop có đang chạy không?
  pause
  exit /b 1
)
echo Đã dừng, dữ liệu vẫn còn
pause
