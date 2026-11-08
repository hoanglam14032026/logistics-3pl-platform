# 02 – Mô hình dữ liệu (Models & Schemas)

> Mô tả **code thực tế** trong `app/models/` và `app/schemas/`. Format bất đồng: `Nội dung cũ (README) | Nội dung mới (code) | Lý do sửa`.

---

## 1. Base & cấu hình CSDL

- `app/database.py`: `engine` (SQLite, `check_same_thread=False`, `connect_args={"timeout":15}`), `SessionLocal` (autocommit/autoflush=False), `Base = declarative_base()`, `get_db()` (tạo session, `close` ở finally, **không rollback**).
- Đường dẫn DB & upload: hard-code trong `app/core/config.py` (`data/business_guide.db`, `data/uploads`).

## 2. Bốn model

### 2.1 `BusinessGuide` (`app/models/business_guide.py`, bảng `business_guides`)
| Trường | Kiểu | Cột DB | Ghi chú |
|---|---|---|---|
| `id` | int | PK, autoincrement | – |
| `title` | str | NOT NULL | – |
| `category` | str | NOT NULL | – |
| `content` | str | NOT NULL | – |
| `is_active` | bool | NOT NULL, default `True` | – |
| `created_at` | datetime | NOT NULL, `datetime.utcnow` | server quản lý |
| `updated_at` | datetime | NOT NULL, `onupdate=datetime.utcnow` | server quản lý |

Schema: `BusinessGuideBase / BusinessGuideCreate / BusinessGuideUpdate / BusinessGuideSchema`.
- Create: `title` (min 1, max 200), `category` (min 1, max 100), `content` (min 1, max 10000). `is_active` mặc định `True`.
- Update: tất cả `Optional`, `exclude_unset`.

### 2.2 `TrainingDocument` (`app/models/training_document.py`, bảng `training_documents`)
| Trường | Kiểu | Cột DB | Ghi chú |
|---|---|---|---|
| `id` | int | PK | – |
| `title` | str | NOT NULL | – |
| `description` | str | **NULL** | `None` mặc định |
| `file_path` | str | NOT NULL | đường dẫn trên đĩa |
| `file_type` | str | **NULL** | `None` mặc định |
| `category` | str | **NULL** | `None` mặc định |
| `is_active` | bool | NOT NULL, default `True` | – |
| `created_at`/`updated_at` | datetime | NOT NULL | server quản lý |

Schema: `TrainingDocumentBase / TrainingDocumentCreate / TrainingDocumentUpdate / TrainingDocumentSchema`.
- Create: `title` (min 1, max 200); `description/file_type/category` optional; `is_active` mặc định `True`. **`file_path`/`file_type` không ở trong schema** – được router đặt bằng `save_upload`/`file_type`.
- `file_path` NOT NULL trong DB nhưng không xuất hiện trong schema → luôn do router cung cấp.

### 2.3 `VideoResource` (`app/models/video_resource.py`, bảng `video_resources`)
| Trường | Kiểu | Cột DB | Ghi chú |
|---|---|---|---|
| `id` | int | PK | – |
| `title` | str | NOT NULL | – |
| `description` | str | NOT NULL, default `""` | – |
| `file_path` | str | NOT NULL | – |
| `thumbnail_path` | str | **NULL** | `None` nếu không upload |
| `category` | str | **NULL** | – |
| `duration` | int | **NULL** | giây |
| `is_active` | bool | NOT NULL, default `True` | – |
| `created_at`/`updated_at` | datetime | NOT NULL | – |

Schema: `VideoResourceBase / VideoResourceCreate / VideoResourceUpdate / VideoResourceSchema`.
- `duration` Optional (`Optional[int]`), `thumbnail_path` Optional.

### 2.4 `BusinessDataRecord` (`app/models/business_data_record.py`, bảng `business_data_records`)
| Trường | Kiểu | Cột DB | Ghi chú |
|---|---|---|---|
| `id` | int | PK | – |
| `record_type` | str | NOT NULL | free string |
| `title` | str | NOT NULL | – |
| `content` | str | NOT NULL, default `""` | – |
| `is_active` | bool | NOT NULL, default `True` | – |
| `created_at`/`updated_at` | datetime | NOT NULL | – |

Schema: `BusinessDataRecordBase / ...Create / ...Update / ...Schema`.
- `record_type`: README liệt kê `customer, partner, contract, form, business_result` nhưng **code KHÔNG ràng buộc** (chuỗi tự do, không enum, không kiểm tra). → **CẦN XÁC NHẬN** có nên enum không.

## 3. Quy ước chung

- 4 model đều có `id`, `is_active` (default `True`), `created_at`, `updated_at`.
- `updated_at` dùng `onupdate=datetime.utcnow` (tự cập nhật khi PUT).
- Schema response dùng `from_attributes=True` (còn `orm_mode = True` giữ cho tương thích, nhưng Pydantic v2 đã deprecate – **CẦN XÁC NHẬN** có dọn `orm_mode` không).

## 4. Bất đồng với README

| # | Nội dung cũ (README) | Nội dung mới (code) | Lý do sửa |
|---|---|---|---|
| D4 | §4.5 "`record_type` ... (không ràng buộc)" | Đúng, nhưng README liệt kê 5 giá trị ví dụ khiến dễ hiểu nhầm là có enum | Code không có bất kỳ ràng buộc giá trị nào |
| D5 | §1.2 mô tả "Business Data" generic | `content` của `BusinessDataRecord` default `""` (không `None`), còn `TrainingDocument.description` default `None` | Khác nhau giữa 2 model (đều hợp lệ nhưng cần ghi rõ) |

## 5. Điểm cần xác nhận (CẦN XÁC NHẬN)

- [ ] `record_type` có nên dùng `Enum`/`Literal` để ràng buộc 5 giá trị không?
- [ ] `orm_mode = True` trong schema có nên bỏ (deprecate trong Pydantic v2) không?
- [ ] `datetime.utcnow` (deprecate) có nên đổi sang timezone-aware (`datetime.now(timezone.utc)`) không?
- [ ] `TrainingDocument` cho phép `description/file_type/category = null` nhưng README mô tả như "bắt buộc" ở một số chỗ – thống nhất ngữ nghĩa.
