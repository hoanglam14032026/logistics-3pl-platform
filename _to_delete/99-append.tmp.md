
### 10.6. Tái cấu trúc đặc tả v2.0 theo 4 luồng độc lập (08/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `docs/specs/01-tong-quan.md`, `02-mo-hinh-du-lieu.md`, `03-api-endpoints.md`, `04-cau-truc-thu-muc.md` | Chuyển sang `_to_delete/docs-specs-cu/` (bản gốc: commit `38301d2`) | Sai lệch với code: cấu hình, tên file, trường dữ liệu, tham số lọc, thông điệp lỗi, phản hồi API; dẫn chiếu các mục README không tồn tại; mục 3 của file `03` bị vỡ bảng |
| `docs/specs/REVIEW-02-logic.md` (chưa commit) | Chuyển sang `_to_delete/docs-specs-cu/` | Mô tả một dự án khác (settings.py, pagination.py, auth, services, 16 test, oracle tiếng Trung) – không có trong repo này |
| README bản 2.1: đặc tả 4 nhóm CRUD độc lập (guide, training documents, business data, video) | README v2.0 ngắn gọn + bộ đặc tả `00`–`07` theo 4 luồng: Vận hành; Quản lý – kiểm soát; Học liệu; Đánh giá KPI | Yêu cầu chuẩn hóa chương trình theo các luồng xử lý độc lập; mô hình CRUD rời rạc không thể hiện quy trình hợp đồng – chứng từ – thực hiện – kiểm soát – đánh giá |
| README §5–§6 (mô hình dữ liệu, API v1.1.0) | Chuyển nguyên văn sang `90-hien-trang-v1.1.md`, bổ sung bảng đính chính C3–C10 | Giữ mô tả hiện trạng làm căn cứ chuyển đổi |
| README §10 (bảng chỉnh sửa) | Chuyển nguyên văn sang `99-lich-su-chinh-sua.md` | Tách lịch sử khỏi README |
| `business_data_records` (bảng tự do, `record_type` chuỗi tự do) | Tách thành `md_parties`, `ops_service_contracts`, học liệu `TEMPLATE` (`07` §3.1) | Dữ liệu khách hàng, hợp đồng, biểu mẫu cần cấu trúc riêng để thực hiện và kiểm soát quy trình |
| Định dạng lỗi 422 hai kiểu; không lọc được "tất cả"; trả đường dẫn file trên server; nhận file 0 byte | Quy ước thống nhất `06` §2 | Khắc phục C3–C10 |
