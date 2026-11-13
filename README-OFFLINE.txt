BẢN DEMO LOGISTICS 3PL – CÀI ĐẶT KHI MÁY KHÔNG CÓ MẠNG
======================================================

Gói zip này đã chứa sẵn toàn bộ thành phần (ứng dụng, cơ sở dữ liệu, hàng đợi) trong file
logistics-3pl-demo-image.tar. Máy KHÔNG cần internet để cài ứng dụng.
Riêng Docker Desktop phải được cài sẵn (bộ cài tải từ https://www.docker.com/products/docker-desktop/ ở máy khác,
chép sang bằng USB nếu máy đích không có mạng).

Bản demo TẮT AI, nhập tay, không cần Ollama.

CÁC BƯỚC
1. Giải nén file logistics-3pl-demo.zip vào một thư mục (ví dụ Documents\logistics-demo hoặc ~/logistics-demo).
   Giữ nguyên tất cả file trong cùng một thư mục.

2. Cài Docker Desktop (nếu chưa có) rồi MỞ lên, đợi biểu tượng cá voi ổn định.

3. (Không bắt buộc) Nạp image thủ công. Bước này install tự làm giúp nếu thấy file .tar,
   chỉ cần làm tay khi muốn kiểm tra riêng:
     Mở Terminal / Command Prompt trong thư mục vừa giải nén, gõ:
       docker load -i logistics-3pl-demo-image.tar
   (mất vài phút; xong sẽ thấy "Loaded image ...")

4. Bấm đúp install:
   - Windows: install.bat
   - Mac:     install.command
   Xong sẽ thấy "CÀI ĐẶT XONG". Địa chỉ: http://localhost:8000/docs
   Tài khoản demo: admin / Demo@123

5. Những lần sau chỉ bấm đúp run.bat (Windows) hoặc run.command (Mac); dừng bằng stop.bat / stop.command.
   Dữ liệu vẫn còn sau khi dừng hoặc khởi động lại máy.

Chi tiết và cách xử lý lỗi: xem README-Windows.txt hoặc README-Mac.txt.
