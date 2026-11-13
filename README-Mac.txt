BẢN DEMO LOGISTICS 3PL – HƯỚNG DẪN CHO MAC
==========================================

Yêu cầu: Mac chip Apple Silicon (M1/M2/M3...), macOS 14 trở lên, còn trống khoảng 5 GB.

LƯU Ý QUAN TRỌNG
- Bản demo TẮT AI: mọi chức năng AI/đọc chứng từ tự động đều báo "Bản demo tắt AI, vui lòng nhập tay".
  Chứng từ được NHẬP TAY.
- KHÔNG cần Ollama.
- Chỉ cần internet ở lần cài đầu tiên (để tải Docker và các thành phần). Sau đó dùng offline được.

CÁC BƯỚC
1. Cài Docker Desktop
   - Tải tại: https://www.docker.com/products/docker-desktop/ (chọn "Apple Silicon").
   - Kéo Docker vào thư mục Applications, mở lên, chấp nhận điều khoản, đợi biểu tượng cá voi ở thanh menu
     ngừng nhấp nháy.

2. Bấm đúp "install.command"
   - Lần đầu macOS có thể báo "không thể mở vì không xác định được nhà phát triển":
     bấm chuột phải vào file > Mở > Mở. (Nếu báo không có quyền chạy: mở Terminal, gõ
     chmod +x  rồi kéo thả file install.command vào cửa sổ, nhấn Enter.)
   - Đợi vài phút đến khi thấy "CÀI ĐẶT XONG".

3. Bấm đúp "run.command"
   - Trình duyệt tự mở trang http://localhost:8000/docs (địa chỉ gốc: http://localhost:8000).

4. Đăng nhập demo
   - Trong trang /docs, tìm mục "Auth" > POST /api/v2/auth/login > "Try it out", nhập:
       username: admin
       password: Demo@123
   - Các tài khoản demo (mật khẩu chung Demo@123): admin, ops_staff, learner, customer.
   - Chủ đề học liệu T01..T07 và 1 hợp đồng mẫu đã có sẵn.

DỪNG: bấm đúp "stop.command". Dữ liệu vẫn còn, lần sau bấm run.command để chạy lại.

LỖI THƯỜNG GẶP
- "Docker chưa chạy" / "Cannot connect to the Docker daemon":
  mở ứng dụng Docker Desktop, đợi vài chục giây rồi chạy lại.
- Cổng 8000 bị chiếm ("port is already allocated"):
  tắt ứng dụng đang dùng cổng 8000, hoặc chạy ./stop.sh rồi ./run.sh. Xem ai đang dùng:
  mở Terminal gõ:  lsof -i :8000
- Quên mật khẩu demo / tài khoản bị khóa / mất dữ liệu demo:
  mở Terminal trong thư mục này, chạy:
    docker compose -f docker-compose.demo.yml --env-file .env.demo exec api python -m app.scripts.seed_demo
  Lệnh này chỉ tạo lại phần còn thiếu; mật khẩu demo luôn là Demo@123 cho tài khoản mới tạo.
  (Tài khoản đã tồn tại thì giữ nguyên mật khẩu hiện tại.)
- Xem nhật ký lỗi:  docker logs logistics-3pl-demo-api
- XÓA SẠCH dữ liệu demo (không thể khôi phục):
    docker compose -f docker-compose.demo.yml --env-file .env.demo down -v
