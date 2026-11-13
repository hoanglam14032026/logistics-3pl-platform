@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "COMPOSE=docker compose -f docker-compose.demo.yml --env-file .env.demo"

echo === CÀI ĐẶT BẢN DEMO LOGISTICS 3PL ===

where docker >nul 2>nul
if errorlevel 1 (
  echo Chưa cài Docker Desktop. Đang mở trang tải về...
  echo Cài xong, mở Docker Desktop rồi bấm đúp install.bat lại.
  start "" "https://www.docker.com/products/docker-desktop/"
  pause
  exit /b 1
)
docker --version

docker info >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop chưa chạy. Hãy mở Docker Desktop, đợi biểu tượng cá voi ổn định rồi chạy lại.
  pause
  exit /b 1
)

if not exist .env.demo (
  echo Thiếu file .env.demo ^(cấu hình demo^). Hãy giải nén đủ thư mục rồi chạy lại.
  pause
  exit /b 1
)

echo [1/4] Tải các image có sẵn (postgres, redis)...
%COMPOSE% pull db redis
if errorlevel 1 goto :fail

echo [2/4] Dựng và khởi động ứng dụng (lần đầu có thể mất vài phút)...
%COMPOSE% up -d --build
if errorlevel 1 goto :fail

echo [3/4] Đợi ứng dụng sẵn sàng...
set /a TRIES=0
:wait
for /f "delims=" %%S in ('docker inspect -f "{{.State.Health.Status}}" logistics-3pl-demo-api 2^>nul') do set "STATUS=%%S"
if "%STATUS%"=="healthy" goto :seed
set /a TRIES+=1
if %TRIES% GEQ 60 (
  echo Ứng dụng chưa sẵn sàng sau 5 phút. Xem lỗi bằng lệnh: docker logs logistics-3pl-demo-api
  pause
  exit /b 1
)
timeout /t 5 /nobreak >nul
goto :wait

:seed
echo [4/4] Nạp dữ liệu demo (tài khoản, hợp đồng mẫu)...
%COMPOSE% exec -T api python -m app.scripts.seed_demo
if errorlevel 1 goto :fail

echo.
echo === CÀI ĐẶT XONG ===
echo Địa chỉ: http://localhost:8000
echo Tài khoản demo: admin / Demo@123
echo Lần sau chỉ cần bấm đúp run.bat để mở.
pause
exit /b 0

:fail
echo.
echo Có lỗi xảy ra ở bước trên. Hãy chụp màn hình cửa sổ này gửi cho người hỗ trợ.
pause
exit /b 1
