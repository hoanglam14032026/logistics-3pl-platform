# Ứng dụng Hướng dẫn nghiệp vụ Logistics 3PL (3PL Logistics Business Guide)

> **Tài liệu đặc tả – phiên bản 2.1** (cập nhật 07/11/2026, ứng với mã nguồn phiên bản `1.1.0`).
> Nội dung được đối chiếu trực tiếp với code trong `app/` và đã kiểm chứng bằng bộ test tự động (18/18 test đạt) cùng chạy thử server thật bằng `curl`. Những điểm chưa có căn cứ trong code được đánh dấu **CẦN XÁC NHẬN**. Lịch sử sửa lỗi code xem tại [`CHANGELOG.md`](CHANGELOG.md).

---

## 1. Tổng quan dự án

Ứng dụng web API (FastAPI) cung cấp kho tri thức nghiệp vụ cho nhân viên doanh nghiệp logistics bên thứ ba (3PL). Hệ thống gồm 4 nhóm chức năng, mỗi nhóm có đủ thao tác CRUD (tạo – đọc – cập nhật – xóa):

| Nhóm chức năng | Tag trên Swagger | Bảng dữ liệu | Mô tả |
|---|---|---|---|
| Hướng dẫn nghiệp vụ | `Business Guide` | `business_guides` | Quy trình, hướng dẫn nghiệp vụ dạng văn bản, phân theo danh mục |
| Tài liệu đào tạo | `Training Documents` | `training_documents` | Tài liệu đào tạo dạng file upload (metadata lưu CSDL, file lưu đĩa) |
| Dữ liệu kinh doanh | `Business Data Records` | `business_data_records` | Bản ghi dữ liệu kinh doanh: khách hàng, đối tác, hợp đồng, biểu mẫu, kết quả kinh doanh |
| Tài nguyên video | `Video Resources` | `video_resources` | Video đào tạo/hướng dẫn, kèm ảnh thumbnail tùy chọn |

Ngoài ra có nhóm `System`: trang chào (`/`) và kiểm tra sức khỏe dịch vụ (`/health`).

**Phạm vi chưa có trong code** (không phải chức năng của hệ thống hiện tại): xác thực/phân quyền người dùng, phân trang, tìm kiếm toàn văn, endpoint tải/xem file đã upload, migration CSDL.

---

## 2. Công nghệ sử dụng

| Thành phần | Phiên bản | Vai trò |
|---|---|---|
| Python | 3.11 (image Docker `python:3.11-slim`) | Ngôn ngữ. Bộ test đã chạy đạt trên Python 3.10. **CẦN XÁC NHẬN**: thư mục `venv/` có sẵn trong project được tạo bằng Python 3.15 và chưa cài thư viện; chưa kiểm chứng `pydantic 2.5.0` trên Python ≥ 3.13 |
| FastAPI | 0.104.1 | Web framework, tự sinh tài liệu OpenAPI |
| Uvicorn | 0.24.0 | ASGI server |
| SQLAlchemy | 2.0.23 | ORM |
| Pydantic | 2.5.0 | Kiểm tra dữ liệu vào/ra (cú pháp v2: `ConfigDict`, `model_dump`) |
| python-multipart | 0.0.6 | Nhận upload file (`multipart/form-data`) |
| SQLite | đi kèm Python | CSDL mặc định |
| pytest / httpx | 8.3.3 / 0.25.2 | Kiểm thử (chỉ trong `requirements-dev.txt`) |
| Docker, Docker Compose | — | Đóng gói và triển khai |

---

## 3. Cấu trúc thư mục

```
fastapi-todo/
├── app/
│   ├── main.py                     # Khởi tạo FastAPI, tạo bảng, gắn 4 router, endpoint / và /health
│   ├── core/
│   │   ├── config.py               # Đọc biến môi trường DATABASE_URL, UPLOAD_DIR
│   │   ├── storage.py              # Lưu/xóa file upload (tên file UUID)
│   │   └── crud.py                 # apply_update(): cập nhật từng phần, chặn null trường bắt buộc
│   ├── database/__init__.py        # engine, SessionLocal, Base, dependency get_db()
│   ├── models/                     # 4 model SQLAlchemy (mỗi model 1 file; __init__.py chỉ re-export)
│   │   ├── business_guide.py
│   │   ├── training_document.py
│   │   ├── business_data_record.py
│   │   └── video_resource.py
│   ├── schemas/                    # Schema Pydantic tương ứng (__init__.py chỉ re-export)
│   └── api/v1/
│       ├── business_guide.py       # /api/v1/guide
│       ├── training/documents.py   # /api/v1/training/documents
│       ├── business_data/records.py# /api/v1/business-data/records
│       └── video/resources.py      # /api/v1/video/resources
├── tests/                          # 18 test pytest (5 file + conftest.py)
├── Dockerfile
├── docker-compose.yml
├── .dockerignore / .gitignore
├── requirements.txt                # Thư viện chạy ứng dụng
├── requirements-dev.txt            # + pytest, httpx
├── pytest.ini
├── README.md                       # Tài liệu đặc tả chính (bản 2.1)
├── README-v2.md                    # Bản sao của README.md (giữ để đối chiếu)
└── CHANGELOG.md                    # Lịch sử sửa lỗi code
```

Dữ liệu sinh ra khi chạy (đã loại khỏi git qua `.gitignore`): file CSDL `business_guide.db`, thư mục `uploads/training_documents/`, `uploads/video_resources/`, và `data/` khi chạy bằng Docker Compose.

---

## 4. Cấu hình

| Biến môi trường | Mặc định | Ý nghĩa |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./business_guide.db` | Chuỗi kết nối SQLAlchemy. Đường dẫn tương đối tính theo thư mục chạy lệnh |
| `UPLOAD_DIR` | `uploads` | Thư mục gốc lưu file upload |

**CẦN XÁC NHẬN**: code chỉ được kiểm thử với SQLite. Chuỗi kết nối tới CSDL khác (PostgreSQL, MySQL…) đòi hỏi cài thêm driver chưa có trong `requirements.txt`.

---

## 5. Mô hình dữ liệu

Các bảng được tạo tự động khi khởi động (`Base.metadata.create_all`); **không có migration** – nếu đổi cấu trúc bảng phải tự xử lý CSDL cũ. Các bảng độc lập, không có khóa ngoại.

Trường chung của mọi bảng:

| Trường | Kiểu | Ràng buộc / mặc định |
|---|---|---|
| `id` | Integer | Khóa chính, có index; không sửa được qua API |
| `is_active` | Boolean | Mặc định `true` |
| `created_at` | DateTime | Tự gán khi tạo, giờ **UTC**, không kèm múi giờ |
| `updated_at` | DateTime | Tự cập nhật khi sửa, giờ UTC |

Cột "Khi tạo" cho biết trường bắt buộc (B) hay tùy chọn (T) trong request tạo mới; "Server" là trường do hệ thống sinh.

### 5.1. `business_guides` – Hướng dẫn nghiệp vụ

| Trường | Kiểu CSDL | Null | Khi tạo | Kiểm tra |
|---|---|---|---|---|
| `title` | String(200) | Không | B | 1–200 ký tự |
| `category` | String(100) | Không | B | 1–100 ký tự |
| `content` | Text | Không | B | ≥ 1 ký tự |
| `is_active` | Boolean | — | T (mặc định `true`) | |

### 5.2. `training_documents` – Tài liệu đào tạo

| Trường | Kiểu CSDL | Null | Khi tạo | Kiểm tra |
|---|---|---|---|---|
| `title` | String(200) | Không | B | 1–200 ký tự |
| `description` | Text | Có | T | |
| `file_path` | String(500) | Không | Server | Sinh khi upload, không sửa được qua API |
| `file_type` | String(50) | Có | B | 1–50 ký tự; do người dùng khai (vd. `pdf`, `docx`), server không kiểm tra với file thật |
| `category` | String(100) | Có | T | ≤ 100 ký tự |
| `is_active` | Boolean | — | Không nhận (mặc định `true`, đổi qua PUT) | |

### 5.3. `business_data_records` – Dữ liệu kinh doanh

| Trường | Kiểu CSDL | Null | Khi tạo | Kiểm tra |
|---|---|---|---|---|
| `record_type` | String(100) | Không | B | 1–100 ký tự. Giá trị gợi ý trong code: `customer`, `partner`, `contract`, `form`, `business_result`; **không ràng buộc** danh sách |
| `title` | String(200) | Không | B | 1–200 ký tự |
| `description` | Text | Có | T | |
| `data_content` | Text | Có | T | Lưu dạng chuỗi (JSON hoặc văn bản), không kiểm tra cú pháp JSON |
| `category` | String(100) | Có | T | ≤ 100 ký tự |
| `is_active` | Boolean | — | T (mặc định `true`) | |

### 5.4. `video_resources` – Tài nguyên video

| Trường | Kiểu CSDL | Null | Khi tạo | Kiểm tra |
|---|---|---|---|---|
| `title` | String(200) | Không | B | 1–200 ký tự |
| `description` | Text | Có | T | |
| `video_path` | String(500) | Không | Server | Sinh khi upload video |
| `thumbnail_path` | String(500) | Có | Server | Sinh nếu có upload thumbnail |
| `duration` | Integer | Có | T | ≥ 0, đơn vị **giây** |
| `category` | String(100) | Có | T | ≤ 100 ký tự |
| `is_active` | Boolean | — | Không nhận (mặc định `true`, đổi qua PUT) | |

---

## 6. API endpoints

Tài liệu tương tác: `/docs` (Swagger UI), `/redoc` (ReDoc), `/openapi.json`.

### 6.1. System

| Phương thức | Đường dẫn | Phản hồi |
|---|---|---|
| GET | `/` | `{"message": "Welcome to 3PL Logistics Business Guide Application", "version": "1.1.0", "documentation": "/docs"}` |
| GET | `/health` | `{"status": "healthy"}` |

### 6.2. Business Guide – `/api/v1/guide`

| Phương thức | Đường dẫn | Mô tả | Đầu vào | Thành công |
|---|---|---|---|---|
| GET | `/api/v1/guide` | Danh sách | Query: `category`, `is_active` (mặc định `true`) | 200, mảng |
| GET | `/api/v1/guide/{guide_id}` | Chi tiết | Path `guide_id` | 200 |
| POST | `/api/v1/guide` | Tạo mới | JSON: `title`, `category`, `content`, `is_active` | **201** |
| PUT | `/api/v1/guide/{guide_id}` | Cập nhật từng phần | JSON: một hoặc nhiều trường `title`, `category`, `content`, `is_active` | 200 |
| DELETE | `/api/v1/guide/{guide_id}` | Xóa | — | 200, `{"message": "Business guide deleted successfully"}` |

### 6.3. Training Documents – `/api/v1/training/documents`

| Phương thức | Đường dẫn | Mô tả | Đầu vào | Thành công |
|---|---|---|---|---|
| GET | `/api/v1/training/documents` | Danh sách | Query: `category`, `is_active` | 200 |
| GET | `/api/v1/training/documents/{document_id}` | Chi tiết | — | 200 |
| POST | `/api/v1/training/documents` | Upload và tạo | **`multipart/form-data`**: `title` (B), `file_type` (B), `file` (B), `description`, `category` | **201** |
| PUT | `/api/v1/training/documents/{document_id}` | Cập nhật metadata từng phần | JSON: `title`, `description`, `file_type`, `category`, `is_active` | 200 |
| DELETE | `/api/v1/training/documents/{document_id}` | Xóa bản ghi **và file trên đĩa** | — | 200 |

### 6.4. Business Data Records – `/api/v1/business-data/records`

| Phương thức | Đường dẫn | Mô tả | Đầu vào | Thành công |
|---|---|---|---|---|
| GET | `/api/v1/business-data/records` | Danh sách | Query: `record_type`, `category`, `is_active` | 200 |
| GET | `/api/v1/business-data/records/{record_id}` | Chi tiết | — | 200 |
| POST | `/api/v1/business-data/records` | Tạo mới | JSON: `record_type` (B), `title` (B), `description`, `data_content`, `category`, `is_active` | **201** |
| PUT | `/api/v1/business-data/records/{record_id}` | Cập nhật từng phần | JSON: bất kỳ trường nào ở trên | 200 |
| DELETE | `/api/v1/business-data/records/{record_id}` | Xóa | — | 200 |

### 6.5. Video Resources – `/api/v1/video/resources`

| Phương thức | Đường dẫn | Mô tả | Đầu vào | Thành công |
|---|---|---|---|---|
| GET | `/api/v1/video/resources` | Danh sách | Query: `category`, `is_active` | 200 |
| GET | `/api/v1/video/resources/{resource_id}` | Chi tiết | — | 200 |
| POST | `/api/v1/video/resources` | Upload và tạo | **`multipart/form-data`**: `title` (B), `video_file` (B), `thumbnail_file`, `description`, `category`, `duration` | **201** |
| PUT | `/api/v1/video/resources/{resource_id}` | Cập nhật metadata từng phần | JSON: `title`, `description`, `duration`, `category`, `is_active` | 200 |
| DELETE | `/api/v1/video/resources/{resource_id}` | Xóa bản ghi, **file video và thumbnail** | — | 200 |

### 6.6. Quy tắc chung

| Nội dung | Hành vi |
|---|---|
| Lọc mặc định | `GET` danh sách mặc định `is_active=true` → bản ghi đã tắt bị ẩn; dùng `?is_active=false` để xem bản ghi đã tắt |
| Lọc chuỗi | `category`, `record_type` so khớp chính xác |
| Thứ tự | Sắp xếp tăng dần theo `id` |
| Phân trang | Không có – trả toàn bộ kết quả |
| Cập nhật (PUT) | Từng phần: chỉ trường có trong body bị thay đổi. Trường không thuộc schema (vd. `id`, `file_path`, `video_path`) bị bỏ qua. Gửi `null` cho trường bắt buộc (`title`, `category`, `content`, `record_type`, `is_active`) → **422** |
| Xóa | Xóa cứng khỏi CSDL. Muốn "ẩn" thay vì xóa: PUT `{"is_active": false}` |
| Mã lỗi | `404` + `detail` (`"Business guide not found"`, `"Training document not found"`, `"Business data record not found"`, `"Video resource not found"`); `422` khi dữ liệu không hợp lệ |
| Lưu file upload | `UPLOAD_DIR/training_documents/` hoặc `UPLOAD_DIR/video_resources/`, tên file = UUID4 + phần mở rộng gốc viết thường (vd. `27845c7c….pdf`). Tiêu đề không dùng để đặt tên file |
| Xác thực | Không có – mọi endpoint công khai. **CẦN XÁC NHẬN** trước khi triển khai ngoài mạng nội bộ |
| Giới hạn upload | Không giới hạn dung lượng hay định dạng. **CẦN XÁC NHẬN** yêu cầu nghiệp vụ |

---

## 7. Cách chạy

### 7.1. Chạy local

```bash
cd fastapi-todo
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

uvicorn app.main:app --reload      # http://127.0.0.1:8000 , tài liệu: /docs
```

- Chạy lệnh từ thư mục gốc project: `business_guide.db` và `uploads/` được tạo tại đây.
- Đổi vị trí dữ liệu: `DATABASE_URL=sqlite:////duong/dan/bg.db UPLOAD_DIR=/duong/dan/uploads uvicorn app.main:app`.
- Thư mục `venv/` cũ (Python 3.15, rỗng) không dùng được; nên xóa và tạo lại như trên.

### 7.2. Chạy bằng Docker Compose (khuyến nghị)

```bash
docker compose up -d --build       # http://127.0.0.1:8000
docker compose logs -f api
docker compose down
```

Dữ liệu được giữ trên máy chủ qua volume: `./data/business_guide.db` và `./uploads/`.

### 7.3. Chạy bằng Docker

```bash
docker build -t fastapi-todo .
docker run -d --name fastapi-todo -p 8000:8000 \
  -v "$(pwd)/data:/app/data" -v "$(pwd)/uploads:/app/uploads" \
  fastapi-todo
```

Trong image: `DATABASE_URL=sqlite:////app/data/business_guide.db`, `UPLOAD_DIR=/app/uploads`; chỉ thư mục `app/` được sao chép vào image (xem `.dockerignore`).

**CẦN XÁC NHẬN**: phần Docker chưa được chạy thử trong đợt rà soát này (môi trường kiểm tra không có Docker).

---

## 8. Cách test

### 8.1. Test tự động

```bash
pip install -r requirements-dev.txt
pytest
```

`tests/conftest.py` đặt `DATABASE_URL` và `UPLOAD_DIR` vào thư mục tạm, nên test **không ảnh hưởng** dữ liệu thật; mỗi test dùng CSDL trống.

| File | Số test | Nội dung kiểm tra |
|---|---|---|
| `test_system.py` | 3 | `/`, `/health`, OpenAPI có đủ 8 nhóm đường dẫn |
| `test_business_guide.py` | 4 | CRUD, cập nhật từng phần, lọc `category`/`is_active`, 404, 422, không đổi được `id` |
| `test_business_data_records.py` | 3 | CRUD, `data_content` tùy chọn, lọc `record_type` |
| `test_training_documents.py` | 4 | Upload, tên file an toàn (không `..`, không path traversal), xóa file khi DELETE, thiếu file → 422, PUT không sửa `file_path` |
| `test_video_resources.py` | 4 | Upload có/không thumbnail, xóa file, hai video trùng tiêu đề không ghi đè nhau, `duration` âm → 422 |

Kết quả đợt rà soát 07/11/2026: **18 passed**, không có cảnh báo deprecated.

### 8.2. Kiểm thử thủ công bằng `curl`

```bash
B=http://127.0.0.1:8000

curl $B/health

curl -X POST $B/api/v1/guide -H "Content-Type: application/json" \
     -d '{"title": "Quy trình nhập kho", "category": "warehouse", "content": "Kiểm đếm, dán nhãn, cất hàng"}'

curl "$B/api/v1/guide?category=warehouse"

curl -X PUT $B/api/v1/guide/1 -H "Content-Type: application/json" -d '{"is_active": false}'

curl -X POST $B/api/v1/business-data/records -H "Content-Type: application/json" \
     -d '{"record_type": "contract", "title": "Hợp đồng kho bãi KH A", "data_content": "{\"so_hd\": \"HD-01\"}"}'

curl -X POST $B/api/v1/training/documents \
     -F "title=SOP xuất kho" -F "file_type=pdf" -F "file=@./sop_xuat_kho.pdf"

curl -X POST $B/api/v1/video/resources \
     -F "title=Hướng dẫn xe nâng" -F "duration=300" \
     -F "video_file=@./xe_nang.mp4" -F "thumbnail_file=@./xe_nang.jpg"
```

Các lệnh trên đã chạy thử với server thật, cho đúng mã trạng thái nêu ở mục 6.

---

## 9. Giấy phép

README cũ ghi "MIT License" nhưng project không có file `LICENSE`. **CẦN XÁC NHẬN** giấy phép áp dụng.

---

## 10. Bảng thay đổi so với README.md cũ

README.md cũ (tiếng Anh) có thể xem lại tại commit `5f2b336`: `git show 5f2b336:README.md`.

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Viết bằng tiếng Anh | Viết bằng tiếng Việt | Theo yêu cầu |
| Features: chỉ quản lý business guide | 4 nhóm chức năng: Business Guide, Training Documents, Business Data Records, Video Resources; nêu rõ phạm vi chưa có | `main.py` gắn 4 router; README cũ bỏ sót 3 nhóm |
| Không có mục công nghệ | Bảng công nghệ kèm phiên bản, thêm pytest/httpx | Bổ sung theo yêu cầu đặc tả |
| Không có cấu trúc thư mục | Cây thư mục đầy đủ, gồm `app/core/`, `tests/`, file Docker | Bổ sung; phản ánh cấu trúc sau khi sửa |
| Không có mục cấu hình | Biến môi trường `DATABASE_URL`, `UPLOAD_DIR` | Code mới đọc cấu hình từ biến môi trường (trước đây ghi cứng) |
| "single table `business_guides`" | 4 bảng, mô tả từng trường: kiểu, null, bắt buộc khi tạo, quy tắc kiểm tra | `app/models/` có 4 model |
| Không nêu múi giờ của `created_at`/`updated_at` | Ghi rõ giờ UTC, không kèm múi giờ | Theo `datetime.utcnow` trong model |
| `GET/POST /api/v1/guide`, `GET/PUT/DELETE /api/v1/guide/{guide_id}` | Giữ nguyên các đường dẫn này | Code đã sửa lỗi lặp prefix (trước đó thực tế là `/api/v1/guide/guide/...`), nay khớp README cũ |
| Không có endpoint training/business-data/video | Bổ sung 15 endpoint với đường dẫn chuẩn `/api/v1/training/documents`, `/api/v1/business-data/records`, `/api/v1/video/resources` | Có trong code nhưng README cũ bỏ sót; đường dẫn đã sửa hết lặp prefix |
| "Category-based filtering" | Lọc `category`, `record_type`, `is_active` (mặc định `true`), sắp xếp theo `id` | Mô tả đúng tham số thật |
| "Active/inactive status management" | Xóa là xóa cứng; ẩn bằng PUT `is_active=false` | Làm rõ hành vi |
| Không nêu hành vi PUT | PUT cập nhật từng phần, bỏ qua `id`/đường dẫn file, chặn null trường bắt buộc | Code mới; trước đây PUT bắt buộc gửi đủ trường kèm `id` và có thể đổi khóa chính |
| Không nêu mã trạng thái | POST trả 201; 404/422 kèm thông điệp | Code mới trả 201 (trước là 200) |
| Không nhắc upload file | Mô tả `multipart/form-data`, nơi lưu, quy tắc đặt tên UUID, xóa file khi DELETE | Có trong code; quy tắc đặt tên mới thay cho cách cũ bị lỗi |
| `git clone <repository-url>` | Bắt đầu từ `cd fastapi-todo` | Không có URL repo thật |
| `python -m venv venv` | `python3.11 -m venv .venv`; ghi chú `venv/` cũ không dùng được | Khớp Python 3.11 trong Dockerfile; `venv/` hiện có là Python 3.15 rỗng |
| Không có hướng dẫn Docker | Docker Compose và Docker, có volume giữ dữ liệu | Bổ sung `docker-compose.yml`, sửa `Dockerfile`, thêm `.dockerignore` |
| Usage Examples: 3 lệnh `curl` cho guide | Ví dụ `curl` cho cả 4 nhóm, đã chạy thử | Bổ sung, kiểm chứng |
| "Development: To run tests..." (không có test) | Mục "Cách test": 18 test pytest, cách chạy, bảng phạm vi kiểm thử | Bổ sung bộ test tự động |
| "MIT License" | Ghi không có file `LICENSE`, CẦN XÁC NHẬN | Không có căn cứ trong project |
| Không có lịch sử thay đổi | Tham chiếu `CHANGELOG.md` | Ghi lại toàn bộ sửa lỗi code |
