# Checklist test trên Windows 11 sạch (chưa cài Python/Docker)

Mục tiêu: xác nhận người không biết code cài và chạy được bản demo chỉ bằng cách bấm đúp.
Máy test: Windows 11 64-bit, chưa cài Python, Docker, WSL. Ghi kết quả vào cột cuối; lỗi nào cũng chụp màn hình.

**Chuẩn bị:** file `logistics-3pl-demo.zip` (~280 MB, chứa cả ảnh Docker) + bộ cài Docker Desktop (nếu thử offline).

| # | Bước | Kỳ vọng | Đạt? / Ghi chú |
|---|---|---|---|
| 1 | Xác nhận máy sạch: mở Command Prompt, gõ `docker --version` và `python --version` | Cả hai báo "không nhận ra lệnh" | |
| 2 | Cài Docker Desktop (https://www.docker.com/products/docker-desktop/), giữ lựa chọn "Use WSL 2", khởi động lại nếu được yêu cầu | Cài xong, mở Docker Desktop, biểu tượng cá voi ổn định (không báo lỗi WSL/ảo hóa) | |
| 3 | Giải nén `logistics-3pl-demo.zip` vào thư mục ngắn, không dấu, ví dụ `C:\logistics-demo` | Có đủ 15 file (14 file + `logistics-3pl-demo-image.tar`) | |
| 4 | Bấm đúp `install.bat` (nếu Windows chặn: "More info" > "Run anyway") | Thấy lần lượt [1/4]..[4/4], tự nạp image từ `.tar`, cuối cùng "CÀI ĐẶT XONG" và danh sách tài khoản demo. Không cần mạng ở bước này | |
| 5 | Bấm đúp `run.bat` | Trình duyệt mở `http://localhost:8000/docs` | |
| 6 | `http://localhost:8000/health` | `{"status":"healthy"}` | |
| 7 | Trong `/docs`: POST `/api/v2/auth/login` với `admin` / `Demo@123` | HTTP 200, có `access_token` | |
| 8 | Bấm "Authorize" bằng token, GET `/api/v2/learning/topics` | Có đủ T01..T07 | |
| 9 | GET `/api/v2/doc-jobs` | HTTP 503, "Bản demo tắt AI, vui lòng nhập tay" | |
| 10 | Đăng nhập thử `ops_staff`, `learner`, `customer` (mật khẩu `Demo@123`) | Cả 3 đăng nhập được | |
| 11 | Bấm đúp `stop.bat` | In "Đã dừng, dữ liệu vẫn còn" | |
| 12 | **Tắt mạng** (rút dây / bật chế độ máy bay), bấm đúp `run.bat` | Vẫn lên `http://localhost:8000/docs`, đăng nhập `admin` được | |
| 13 | **Khởi động lại máy**, mở Docker Desktop, bấm đúp `run.bat` | Đăng nhập `admin` được, hợp đồng mẫu `HD-2026-0001` và T01..T07 vẫn còn (dữ liệu không mất) | |
| 14 | Chạy `install.bat` lần 2 | Không lỗi; seed báo "đã có" cho mọi tài khoản (không nhân đôi) | |
| 15 | Cổng bận: chạy một chương trình khác chiếm cổng 8000 rồi `run.bat` | Báo lỗi cổng 8000; làm theo README-Windows.txt xử lý được | |

## Tiêu chí đạt
- Bước 1–14 đều đạt, người test KHÔNG phải gõ lệnh nào ngoài bước 1.
- Mọi thông báo hiển thị tiếng Việt đúng dấu trong cửa sổ cmd (nếu vỡ chữ: ghi lại phông đang dùng).

## Rủi ro cần để ý
- Windows 11 Home cần WSL 2; máy tắt ảo hóa trong BIOS thì Docker không chạy.
- Docker Desktop có điều khoản giấy phép cho doanh nghiệp lớn.
- Chưa có file `stop`/`install` kiểm thử thực tế trên Windows: ghi lại mọi lỗi cú pháp `.bat` nếu có.
