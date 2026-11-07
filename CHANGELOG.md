# CHANGELOG

## [1.1.0] – 07/11/2026

Rà soát, sửa lỗi toàn bộ mã nguồn và hoàn thiện tài liệu. Bản trước khi sửa: commit `5f2b336` ("backup truoc khi hieu chinh dac ta").

### Sửa lỗi khiến ứng dụng không khởi động

| Lỗi | File | Cách sửa |
|---|---|---|
| Import vòng tròn `app.database` ↔ `app.models` (`ImportError: cannot import name 'Base'`) | `app/database/__init__.py` | Bỏ import `app.models` khỏi module database; model được import tại `app/main.py` trước `create_all()` |
| 4 model khai báo trùng (`Table 'business_guides' is already defined`) | `app/models/__init__.py` | Chỉ re-export từ các file model riêng |
| Thiếu `from typing import Optional` (`NameError`) | 4 file router | Bổ sung import |

### Sửa lỗi chức năng

| Lỗi | File | Cách sửa |
|---|---|---|
| Lặp prefix đường dẫn (`/api/v1/guide/guide`, `/api/v1/training/training/documents`, …) | `app/main.py`, 4 router | Prefix đầy đủ đặt ở `main.py`; route trong router chỉ còn `""` và `/{id}` |
| PUT bắt buộc gửi đủ trường kèm `id`; `id` trong body ghi đè khóa chính; có thể ghi đè `file_path`/`video_path` tùy ý | `app/schemas/*.py` | Schema `*Update` gồm toàn trường tùy chọn, không có `id` và đường dẫn file |
| PUT gửi `null` cho trường NOT NULL gây lỗi 500 | `app/core/crud.py` (mới) | `apply_update()` trả 422 |
| Tên file upload có hai dấu chấm (`..pdf`); dùng `hash()` không ổn định giữa các lần chạy; đưa tiêu đề vào tên file (path traversal); thumbnail trùng tiêu đề ghi đè nhau | `app/core/storage.py` (mới), router training/video | Tên file = UUID4 + phần mở rộng gốc |
| Xóa file trước khi commit CSDL | router training/video | Commit xóa bản ghi trước, sau đó mới xóa file |
| Schema không khớp CSDL: `data_content` bắt buộc dù cột cho phép null | `app/schemas/business_data_record.py` | Chuyển thành tùy chọn |
| Schema trùng lặp | `app/schemas/__init__.py` | Chỉ re-export |
| Cú pháp Pydantic v1 (`orm_mode`, `.dict()`) trên Pydantic 2.5 | `app/schemas/*.py`, router | `ConfigDict(from_attributes=True)`, `model_dump()` |
| Handler `async def` gọi SQLAlchemy đồng bộ (chặn event loop) | 4 router | Đổi sang `def`; chỉ giữ `async` cho POST có đọc file upload |
| Thư mục upload tạo khi import module | router training/video | Tạo khi lưu file |

### Thay đổi hành vi

- POST trả mã **201 Created** (trước đây 200).
- Danh sách sắp xếp theo `id` tăng dần.
- Bổ sung kiểm tra độ dài trường theo giới hạn cột CSDL; `duration` ≥ 0.
- Đọc `DATABASE_URL`, `UPLOAD_DIR` từ biến môi trường (mặc định như cũ).
- `version` của ứng dụng: `1.0.0` → `1.1.0`; endpoint `/` lấy version từ cấu hình app.

### Bổ sung

- `tests/` với 18 test pytest; `requirements-dev.txt`, `pytest.ini`.
- `docker-compose.yml`, `.dockerignore`; `Dockerfile` khai báo biến môi trường, volume dữ liệu, chỉ sao chép `app/`.
- `.gitignore` loại `*.db`, `uploads/`, `data/`, `.pytest_cache/`.
- Các file `__init__.py` cho package `app/api`, `app/api/v1` và các thư mục con.
- `README.md` thay bằng đặc tả đầy đủ bản 2.1 (tiếng Việt); `README-v2.md` là bản sao. README cũ xem tại commit `5f2b336`.

### Chưa xử lý (CẦN XÁC NHẬN)

- Xác thực/phân quyền; giới hạn dung lượng và định dạng file upload.
- Migration CSDL (Alembic): CSDL tạo từ bản cũ vẫn dùng được vì cấu trúc bảng không đổi.
- Thư mục `venv/` (Python 3.15, rỗng) chưa xóa.
- Docker chưa được chạy thử.
- Giấy phép (README cũ ghi MIT, không có file `LICENSE`).
