BẢN DEMO LOGISTICS 3PL – HƯỚNG DẪN CHO WINDOWS
==============================================

Yêu cầu: Windows 11 (64-bit), còn trống khoảng 5 GB, máy bật ảo hóa (thường đã bật sẵn).

LƯU Ý QUAN TRỌNG
- Bản demo TẮT AI: mọi chức năng AI/đọc chứng từ tự động đều báo "Bản demo tắt AI, vui lòng nhập tay".
  Chứng từ được NHẬP TAY.
- KHÔNG cần Ollama, KHÔNG cần cài Python.
- Chỉ cần internet ở lần cài đầu tiên (để tải Docker và các thành phần). Sau đó dùng offline được.

CÁC BƯỚC
1. Cài Docker Desktop
   - Tải tại: https://www.docker.com/products/docker-desktop/ (chọn "Windows").
   - Cài đặt, để nguyên lựa chọn "Use WSL 2". Khởi động lại máy nếu được yêu cầu.
   - Mở Docker Desktop, chấp nhận điều khoản, đợi tới khi biểu tượng cá voi ở khay hệ thống ổn định.

2. Bấm đúp "install.bat"
   - Nếu Windows báo "Windows protected your PC": bấm "More info" > "Run anyway".
   - Đợi vài phút đến khi thấy "CÀI ĐẶT XONG".

3. Bấm đúp "run.bat"
   - Trình duyệt tự mở trang http://localhost:8000/docs (địa chỉ gốc: http://localhost:8000).

4. Đăng nhập demo
   - Trong trang /docs, tìm mục "Auth" > POST /api/v2/auth/login > "Try it out", nhập:
       username: admin
       password: Demo@123
   - Các tài khoản demo (mật khẩu chung Demo@123): admin, ops_staff, learner, customer.
   - Chủ đề học liệu T01..T07 và 1 hợp đồng mẫu đã có sẵn.

DỪNG: bấm đúp "stop.bat". Dữ liệu vẫn còn, lần sau bấm run.bat để chạy lại.

LỖI THƯỜNG GẶP
- "Docker chưa chạy" / "error during connect":
  mở Docker Desktop, đợi vài chục giây rồi chạy lại.
- "WSL 2 installation is incomplete" hoặc Docker không khởi động:
  mở PowerShell bằng quyền Administrator, gõ  wsl --install  rồi khởi động lại máy.
- Cổng 8000 bị chiếm ("port is already allocated"):
  tắt ứng dụng đang dùng cổng 8000, hoặc bấm đúp stop.bat rồi run.bat. Xem ai đang dùng:
  mở Command Prompt gõ:  netstat -ano | findstr :8000
- Quên mật khẩu demo / mất dữ liệu demo:
  mở Command Prompt trong thư mục này, chạy:
    docker compose -f docker-compose.demo.yml --env-file .env.demo exec api python -m app.scripts.seed_demo
  Lệnh này chỉ tạo lại phần còn thiếu; mật khẩu demo luôn là Demo@123 cho tài khoản mới tạo.
  (Tài khoản đã tồn tại thì giữ nguyên mật khẩu hiện tại.)
- Xem nhật ký lỗi:  docker logs logistics-3pl-demo-api
- XÓA SẠCH dữ liệu demo (không thể khôi phục):
    docker compose -f docker-compose.demo.yml --env-file .env.demo down -v
