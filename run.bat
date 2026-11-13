@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

docker info >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop chưa chạy. Hãy mở Docker Desktop, đợi vài chục giây rồi chạy lại.
  pause
  exit /b 1
)

docker compose -f docker-compose.demo.yml --env-file .env.demo up -d
if errorlevel 1 (
  echo Không khởi động được. Xem phần "Lỗi thường gặp" trong README-Windows.txt
  pause
  exit /b 1
)

echo Đợi ứng dụng sẵn sàng...
set /a TRIES=0
:wait
for /f "delims=" %%S in ('docker inspect -f "{{.State.Health.Status}}" logistics-3pl-demo-api 2^>nul') do set "STATUS=%%S"
if "%STATUS%"=="healthy" goto :open
set /a TRIES+=1
if %TRIES% GEQ 36 goto :open
timeout /t 5 /nobreak >nul
goto :wait

:open
echo Mở trình duyệt: http://localhost:8000/docs ^(trang thử API; địa chỉ gốc http://localhost:8000^)
start "" "http://localhost:8000/docs"
echo.
echo Tài khoản demo ^(mật khẩu chung Demo@123^):
echo   admin / ops_staff / learner / customer
echo Dừng bản demo: bấm đúp stop.bat
pause
