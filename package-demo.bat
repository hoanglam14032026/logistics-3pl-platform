@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
rem Đóng gói bản demo thành logistics-3pl-demo.zip (cần Docker Desktop + PowerShell, có sẵn trên Windows 11).
set "ZIP=logistics-3pl-demo.zip"
set "TAR=logistics-3pl-demo-image.tar"

where docker >nul 2>nul
if errorlevel 1 ( echo Chưa có Docker. & exit /b 1 )

echo [1/4] Dựng image demo...
docker compose -f docker-compose.demo.yml --env-file .env.demo build
if errorlevel 1 exit /b 1

echo [2/4] Đảm bảo có image postgres, redis...
docker image inspect postgres:17 redis:7.4-alpine >nul 2>nul
if errorlevel 1 (
  docker compose -f docker-compose.demo.yml --env-file .env.demo pull db redis
  if errorlevel 1 exit /b 1
)

echo [3/4] Lưu image ra %TAR% (vài phút)...
if exist "%TAR%" del "%TAR%"
if exist "%ZIP%" del "%ZIP%"
docker save logistics-3pl-platform:demo postgres:17 redis:7.4-alpine -o "%TAR%"
if errorlevel 1 exit /b 1

echo [4/4] Nén %ZIP%...
powershell -NoProfile -Command "Compress-Archive -Force -DestinationPath '%ZIP%' -Path install.sh,install.command,install.bat,run.sh,run.command,run.bat,stop.sh,stop.command,stop.bat,README-Mac.txt,README-Windows.txt,README-OFFLINE.txt,docker-compose.demo.yml,.env.demo,'%TAR%'"
if errorlevel 1 exit /b 1

dir "%ZIP%" "%TAR%"
echo Xong. Gửi file %ZIP% cho người thử nghiệm.
pause
