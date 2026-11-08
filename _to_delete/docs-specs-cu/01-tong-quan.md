# 01 – Tổng quan (Overview)

> Tài liệu mô tả **code thực tế** của ứng dụng (thư mục `app/`). Mọi bất đồng với `README.md` được đánh dấu theo format: `Nội dung cũ (README) | Nội dung mới (code) | Lý do sửa`. Các điểm chưa rõ đánh dấu **CẦN XÁC NHẬN**.

---

## 1. Mục đích ứng dụng

Ứng dụng quản lý **hướng dẫn nghiệp vụ 3PL logistics**: lưu trữ hướng dẫn (guide), tài liệu đào tạo (có upload file), video hướng dẫn (có upload file), và dữ liệu nghiệp vụ tổng quát (customer, partner, contract, ...).

## 2. Công nghệ (theo code & `requirements.txt`)

| Thành phần | Thư viện / phiên bản | Ghi chú |
|---|---|---|
| Web framework | `fastapi==0.104.1` | – |
| Server ASGI | `uvicorn==0.24.0` | – |
| ORM | `sqlalchemy==2.0.23` (SQLAlchemy 2.x) | dùng `declarative_base()` |
| Schema/validation | `pydantic==2.5.0` (Pydantic v2, `model_config`) | – |
| Upload file | `python-multipart==0.0.6` | – |
| CSDL | SQLite (file `business_guide.db`) | qua SQLAlchemy |

> **Cần lưu ý:** `app/core/config.py` hard-code đường dẫn SQLite = `<dự án>/data/business_guide.db` và thư mục upload = `<dự án>/data/uploads`. Không có biến môi trường nào điều khiển (không đọc `DATABASE_URL`/`UPLOAD_DIR`). **CẦN XÁC NHẬN** liệu có nên cấu hình qua environment khi triển khai production hay không.

## 3. Điểm vào (entrypoint)

- File: `app/main.py`, đối tượng `app = FastAPI(...)`.
- Chạy dev: `uvicorn app.main:app --reload` (theo README; `uv`/`python -m` tương đương).
- `lifespan` khởi tạo bảng CSDL khi bắt đầu (`Base.metadata.create_all`).

## 4. Đăng ký route

- `app.include_router(guide.router)` – prefix `/api/v1/guide`
- `app.include_router(training.router)` – prefix `/api/v1/training`
- `app.include_router(business_data.router)` – prefix `/api/v1/business-data`
- `app.include_router(video.router)` – prefix `/api/v1/video`
- 2 route root/health khai báo trực tiếp trong `main.py`.

## 5. Phiên bản & metadata

| Mục | Giá trị trong code | Giá trị trong README | So khớp |
|---|---|---|---|
| `app.title` | `"3PL Logistics API"` | `3PL Logistics API` | ✅ |
| `app.version` | `"1.1.0"` | `1.1.0` | ✅ |
| Root message | `Welcome to 3PL Logistics Business Guide Application` | giống | ✅ |

## 6. Mô hình request/response & xử lý lỗi

- Mọi route trả JSON (kể cả upload trả metadata JSON, **không** stream file).
- Schema response kế thừa `orm_mode = True` (Pydantic v2 dùng `from_attributes=True`).
- Lỗi: raise `HTTPException(status, detail)`; FastAPI trả format `{"detail": ...}`.
- **`apply_update` (helper CRUD dùng chung)** raise **`422`** (không phải 400) khi client gửi `null` cho trường không được phép null.

### ⚠️ Bất đồng với README

| # | Nội dung cũ (README) | Nội dung mới (code) | Lý do sửa |
|---|---|---|---|
| D1 | §5.2 "Trường non-nullable = null → **400** Bad Request" | Code raise **`HTTP_422_UNPROCESSABLE_ENTITY`** | `app/core/crud.py` dùng `status.HTTP_422_UNPROCESSABLE_ENTITY` |
| D2 | §5.2 "Đặt trường null: **Không có** (không xóa được)" | Có thể set `null` cho các trường **nullable** (`description`, `file_type`, `category`, `duration`, ...) | Những trường này không nằm trong `NON_NULLABLE`, nên `apply_update` ghi đè `None` bình thường |
| D3 | §12.2 & §12.4 "PUT ... **không báo 400**" | PUT training/documents & video/resources **có thể báo 422** khi `title`/`is_active` = null | Router gọi `apply_update(..., NON_NULLABLE=("title","is_active"))` |

## 7. Chu kỳ phiên CSDL (session lifecycle)

- `get_db()` (FastAPI dependency): tạo session mới mỗi request, đóng ở `finally`.
- `main.py` tự tạo session (không dùng dependency) ở các route root/health.
- `get_db` **không rollback tự động** khi lỗi (chỉ `close`). **CẦN XÁC NHẬN** liệu cần `rollback()` trước `close()` khi phát sinh exception.

## 8. Xử lý file upload (`app/core/storage.py`)

- `save_upload(file, subdir)`: tên file = `uuid4().hex + suffix` (an toàn, chống path-traversal), ghi vào `data/uploads/<subdir>/`.
- `delete_file(path)`: xóa file nếu tồn tại.
- Upload được đọc toàn bộ vào RAM (`await file.read()`), **không giới hạn dung lượng**, **không kiểm tra loại file**.
- **CẦN XÁC NHẬN:** có nên thêm giới hạn kích thước (ví dụ max MB) và whitelist loại file không.

## 9. Điều gì KHÔNG tồn tại trong code (so với README §10)

✅ Đúng với README: không có xác thực/authorization, không migration, không pagination, không rate limit, không logging cấu hình.

⚠️ README §7 (cấu trúc thư mục) **bỏ sót** các file/thư mục thực tế có trong repo:
`Dockerfile`, `docker-compose.yml`, `requirements-dev.txt`, `pytest.ini`, thư mục `tests/`.
→ Chi tiết trong `04-cau-truc-thu-muc.md`.

## 10. Chạy thử nhanh

```bash
uv run uvicorn app.main:app --reload   # hoặc: python -m uvicorn app.main:app --reload
# API docs: http://127.0.0.1:8000/docs
# health:   http://127.0.0.1:8000/health
```
Kèm theo: tạo thư mục `data/uploads/` (code tự tạo khi upload lần đầu).

## 11. Checklist xác nhận (CẦN XÁC NHẬN)

- [ ] Có cần cấu hình CSDL/upload qua biến môi trường không (hiện hard-code)?
- [ ] Có cần giới hạn dung lượng & whitelist loại file upload không?
- [ ] Có cần thêm `rollback()` trong `get_db` không?
- [ ] Có cần pagination cho các endpoint list không?
- [ ] `record_type` của `business_data_record` hiện là chuỗi tự do (không enum) – có cần ràng buộc giá trị không?
- [ ] Có nên thêm authentication cho hệ thống nghiệp vụ 3PL không?
