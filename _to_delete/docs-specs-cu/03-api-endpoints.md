# 03 – API Endpoints

> Danh sách **tất cả endpoint có trong code** (`app/api/v1/*` + `app/main.py`). Format bất đồng: `Nội dung cũ (README) | Nội dung mới (code) | Lý do sửa`.

---

## 0. Bức tranh tổng

| Method | Path | Router / file |
|---|---|---|
| GET | `/` | `main.py` |
| GET | `/health` | `main.py` |
| GET | `/api/v1/guide` | `guide.py` |
| POST | `/api/v1/guide` | `guide.py` |
| GET | `/api/v1/guide/{guide_id}` | `guide.py` |
| PUT | `/api/v1/guide/{guide_id}` | `guide.py` |
| DELETE | `/api/v1/guide/{guide_id}` | `guide.py` |
| POST | `/api/v1/training/documents` | `training.py` (multipart) |
| GET | `/api/v1/training/documents` | `training.py` |
| GET | `/api/v1/training/documents/{doc_id}` | `training.py` |
| PUT | `/api/v1/training/documents/{doc_id}` | `training.py` |
| DELETE | `/api/v1/training/documents/{doc_id}` | `training.py` |
| GET | `/api/v1/business-data/records` | `business_data.py` |
| POST | `/api/v1/business-data/records` | `business_data.py` |
| GET | `/api/v1/business-data/records/{record_id}` | `business_data.py` |
| PUT | `/api/v1/business-data/records/{record_id}` | `business_data.py` |
| DELETE | `/api/v1/business-data/records/{record_id}` | `business_data.py` |
| POST | `/api/v1/video/resources` | `video.py` (multipart) |
| GET | `/api/v1/video/resources` | `video.py` |
| GET | `/api/v1/video/resources/{res_id}` | `video.py` |
| PUT | `/api/v1/video/resources/{res_id}` | `video.py` |
| DELETE | `/api/v1/video/resources/{res_id}` | `video.py` |

**Không có** PATCH, không có xác thực cho bất kỳ endpoint nào.

---

## 1. Root & Health

- `GET /` → 200: `{"app":"3PL Logistics API","version":"1.1.0","message":"Welcome to 3PL Logistics Business Guide Application","docs":"/docs","health":"/health"}`.
- `GET /health` → 200: `{"status":"healthy"}`.

---

## 2. Business Guides – `/api/v1/guide`

| Method | Path | 2xx | Lỗi | Ghi chú |
|---|---|---|---|---|
| GET | `/api/v1/guide` | 200 list | – | Query `is_active: bool = Query(True)` (**mặc định chỉ active**) |
| POST | `/api/v1/guide` | **201** | 422 | Body `BusinessGuideCreate` |
| GET | `/api/v1/guide/{guide_id}` | 200 | **404** `Guide not found` | – |
| PUT | `/api/v1/guide/{guide_id}` | 200 | **404**, **422** | `apply_update(NON_NULLABLE=("title","category","content","is_active"))` |
| DELETE | `/api/v1/guide/{guide_id}` | **200** `{"success":true,...}` | **404** | – |

- POST: `title`/`content`/`category` min_length 1 → 422 khi vi phạm.

---

## 3. Training Documents – `/api/v1/training`

| Method | Path | 2xx | Lỗi | Ghi chú |

---

## 4. Business Data Records – `/api/v1/business-data`

| Method | Path | 2xx | Lỗi | Ghi chú |
|---|---|---|---|---|
| GET | `/api/v1/business-data/records` | 200 list | – | Query `record_type: str = "general"`, `status: str = "active"`, `priority: str = "normal"`, `is_active: bool = True`, `include_archived: bool = False` (mọi filter mặc định có giá trị) |
| POST | `/api/v1/business-data/records` | **201** | 422 | Body `BusinessDataRecordCreate` |
| GET | `/api/v1/business-data/records/{record_id}` | 200 | **404** `Business data record not found` | – |
| PUT | `/api/v1/business-data/records/{record_id}` | 200 | **404**, **422** | `NON_NULLABLE=("record_type","title","content","status","priority","is_active")` |
| DELETE | `/api/v1/business-data/records/{record_id}` | 200 | **404** | – |

---

## 5. Video Resources – `/api/v1/video`

| Method | Path | 2xx | Lỗi | Ghi chú |
|---|---|---|---|---|
| POST | `/api/v1/video/resources` | **201** | 422 | Multipart: `title` (form), `file` (UploadFile, required), `description`/`category`/`tags`/`duration`/`file_type` (form, optional) |
| GET | `/api/v1/video/resources` | 200 list | – | Query `is_active: bool = True`, `category: Optional[str] = None`, `tags: Optional[str] = None`, `min_duration: int = 0`, `max_duration: int = 3600` |
| GET | `/api/v1/video/resources/{res_id}` | 200 | **404** `Video resource not found` | – |
| PUT | `/api/v1/video/resources/{res_id}` | 200 | **404**, **422** | `NON_NULLABLE=("title","is_active")`; `file_path` giữ nguyên |
| DELETE | `/api/v1/video/resources/{res_id}` | 200 | **404** | Xóa file trên đĩa sau khi xóa DB |

- Video list filter logic:
  - `is_active=True` ⇒ chỉ trả về active.
  - `is_active=False` ⇒ trả về cả (active + inactive).
  - `category` (chuỗi, so khớp exact, case-insensitive qua `ilike`).
  - `tags` ⇒ LIKE `%tags%` (case-insensitive).
  - `min_duration`/`max_duration` ⇒ filter theo trường `duration`.

---

## 6. Lỗi chuẩn

| HTTP | Khi nào | Detail mẫu |
|---|---|---|
| 404 | Resource không tồn tại | `"Guide not found"` / `"Training document not found"` / `"Business data record not found"` / `"Video resource not found"` |
| 422 | Validation Schema fail (FastAPI tự phát, pydantic v2) | JSON validation errors |
| 400 | Chưa có endpoint nào trả về 400 hiện tại | – |
| 403 / 401 | **Không** (không có xác thực) | – |
| 409 | Chưa có | – |

---

## 7. Các điểm bất đồng với README

- `Nội dung cũ (README không đề cập) | PUT/DELETE trên mỗi resource đều có | Lý do: code tồn tại ở mọi router`.
- `Nội dung cũ (không nói rõ status code POST) | 201 Created (mọi POST tạo mới trả 201) | Lý do: `HTTPStatus` hard-code`.
- `Nội dung cũ (đọc danh sách mặc định trả tất cả) | Mặc định `is_active=True` ⇒ chỉ active | Lý do: `Query(True)` / default trong signature`.
- `Nội dung cũ (chưa rõ filter business-data) | 6 query params có default | Lý do: code định nghĩa rõ`.
- `Nội dung cũ (không rõ filter video) | `tags` LIKE, `min/max_duration`, `category` ilike | Lý do: code thực thi trong `list_video_resources`.`.

---

## 8. **CẦN XÁC NHẬN**

- [ ] Không có endpoint `/api/v1/business-data/records/archive` hay unarchive mặc dù có flag `include_archived`.
- [ ] PUT trên training/video giữ `file_path` cũ; có endpoint riêng để thay file mới không?
- [ ] DELETE training/video xóa file trên đĩa – xử lý khi file đã bị xóa trước (FileNotFoundError)?
- [ ] Không có endpoint "duplicate guide", "publish/unpublish" riêng; chỉ dùng PUT.
- [ ] 400 chưa có endpoint nào dùng – có cần cho trường hợp file upload fail?
- [ ] Không có versioning header, không có `Accept-Language`, không có i18n.
- [ ] Không có rate limiting / timeout upload – file quá lớn xử lý thế nào?
- [ ] `tags` trên video là chuỗi; có cần endpoint tìm theo tag đơn lẻ?

|---|---|---|---|---|
| POST | `/api/v1/training/documents` | **201** | 422 | Multipart: `title` (form), `file` (UploadFile, **required**), `description`/`file_type`/`category` (form, optional) |
| GET | `/api/v1/training/documents` | 200 list | – | Query `is_active: bool = Query(True)`, `category: Optional[str] = Query(None)` |
| GET | `/api/v1/training/documents/{doc_id}` | 200 | **404** `Training document not found` | – |
| PUT | `/api/v1/training/documents/{doc_id}` | 200 | **404**, **422** | `NON_NULLABLE=("title","is_active")`; `file_path` giữ nguyên, **không upload file mới ở PUT** |
| DELETE | `/api/v1/training/documents/{doc_id}` | 200 | **404** | Xóa file trên đĩa (`delete_file`) sau khi xóa DB |

> `file_type` ở POST là Form **bắt buộc** (không default).
