# 90 – Hiện trạng mã nguồn v1.1.0 (trước khi chuyển đổi)

> Tài liệu **mô tả hiện trạng**, không phải đặc tả đích. Nội dung §5–§6 dưới đây trích nguyên văn từ README.md bản 2.1 (07/11/2026), đã đối chiếu với code và bộ test (18/18 đạt, chạy lại ngày 08/11/2026). Đặc tả đích v2.0 xem các file `00`–`07`. Ánh xạ chuyển đổi xem `07-cau-truc-ma-nguon-chuyen-doi-lo-trinh.md` §3.

## Đính chính so với README bản 2.1 (rà soát 08/11/2026)

| # | Nội dung README 2.1 | Thực tế code v1.1.0 | Xử lý trong đặc tả v2.0 |
|---|---|---|---|
| C3 | Không nêu ràng buộc NULL của `is_active`, `created_at`, `updated_at` | Ba cột cho phép NULL ở mức CSDL; chỉ API chặn | 06 §2.4: mọi cột trạng thái/thời gian `NOT NULL` |
| C4 | Danh sách "trường bắt buộc khi PUT" dùng chung (`title, category, content, record_type, is_active`) | `category` chỉ bắt buộc ở Business Guide; 3 nhóm còn lại cho phép `category: null` | Ràng buộc ghi theo từng thực thể |
| C5 | 422 + `detail` | Hai dạng body: Pydantic trả mảng; `apply_update` trả chuỗi `"Fields cannot be null: ..."` | 06 §2.2: một định dạng lỗi duy nhất |
| C6 | `?is_active=false` để xem bản ghi đã tắt | Không có cách lấy cả hai trạng thái; `?is_active=` → 422 | 06 §2.3: `status=active|inactive|all` |
| C7 | Lọc `category` so khớp chính xác | `?category=` (rỗng) bị bỏ qua | 06 §2.3: chuỗi rỗng → 422 |
| C8 | `file_path`, `video_path` | Trả đường dẫn tuyệt đối trên server; không có endpoint tải file | 06 §2.6: `file_id` + endpoint tải |
| C9 | Tên file UUID + phần mở rộng | File không đuôi lưu không đuôi; file 0 byte được chấp nhận (201) | 06 §2.6: kiểm tra MIME, dung lượng, từ chối file rỗng |
| C10 | "Không có cảnh báo deprecated" | Đúng trên Python 3.10/3.11; `datetime.utcnow` cảnh báo từ Python 3.12 | 06 §2.4: thời gian có múi giờ (UTC-aware) |

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

