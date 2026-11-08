# 04 – Cấu trúc thư mục

> Cây thư mục **thực tế** của dự án và vai trò từng file. Format bất đồng: `Nội dung cũ (README) | Nội dung mới (code) | Lý do sửa`.

---

## 1. Cây thư mục thực tế

```
fastapi-todo/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app, root + /health, include_router x4, lifespan create_all
│   ├── database.py              # engine (SQLite), SessionLocal, Base, get_db()
│   ├── models/
│   │   ├── __init__.py
│   │   ├── business_guide.py        # Model + 4 Schema (Guide)
│   │   ├── training_document.py     # Model + 4 Schema (Training Document)
│   │   ├── video_resource.py        # Model + 4 Schema (Video Resource)
│   │   └── business_data_record.py  # Model + 4 Schema (Business Data Record)
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── guide.py             # router /api/v1/guide
│   │       ├── training.py          # router /api/v1/training
│   │       ├── business_data.py     # router /api/v1/business-data
│   │       └── video.py             # router /api/v1/video
│   └── core/
│       ├── __init__.py
│       ├── config.py                # APP_NAME, APP_VERSION, DATA_DIR, UPLOAD_DIR, DB_PATH
│       ├── crud.py                  # apply_update() – helper PUT dùng chung (raise 422)
│       └── storage.py               # save_upload(), delete_file()
├── README.md
├── requirements.txt           # fastapi, uvicorn, sqlalchemy, pydantic, python-multipart
├── requirements-dev.txt       # pytest, httpx (dev/test)         ← README bỏ sót
├── pytest.ini                                    ← README bỏ sót
├── tests/                                        ← README bỏ sót
├── Dockerfile                                  ← README bỏ sót
├── docker-compose.yml                          ← README bỏ sót
├── .dockerignore
├── .gitignore
├── .python-version
├── data/                                        # tạo runtime (DB + uploads)
│   ├── business_guide.db
│   └── uploads/
│       ├── training/
│       ├── video/
│       └── thumbnails/
└── docs/
    └── specs/   (thư mục spec này)
```

## 2. Vai trò từng module `app/`

| File | Vai trò |
|---|---|
| `app/main.py` | Tạo `FastAPI(title, version)`, `lifespan` (create_all), route `GET /`, `GET /health`, `include_router` cho 4 router. |
| `app/database.py` | `engine` (SQLite file, `check_same_thread=False`, timeout 15s), `SessionLocal`, `Base`, `get_db()` (create/close, không rollback). |
| `app/models/*` | 4 cặp Model + Schema (Base/Create/Update/Schema). Dùng `declarative_base()`. |
| `app/api/v1/*.py` | 4 router (guide, training, business_data, video). |
| `app/core/config.py` | Hằng số + đường dẫn `data/business_guide.db`, `data/uploads`. `os.makedirs(..., exist_ok=True)`. |
| `app/core/crud.py` | `apply_update(obj, data: dict, non_nullable)` – dùng `exclude_unset`, raise **422** nếu null vào `non_nullable`. |
| `app/core/storage.py` | `save_upload` (uuid + suffix, ghi đĩa), `delete_file` (xóa nếu tồn tại). |

## 3. File triển khai & cấu hình

| File | Mục đích |
|---|---|
| `Dockerfile` | Build image Python cho app. **(README §7 không liệt kê)** |
| `docker-compose.yml` | Chạy app + service phụ (nếu có). **(README §7 không liệt kê)** |
| `requirements.txt` | Runtime deps (5 thư viện). |
| `requirements-dev.txt` | Dev/test deps (pytest, httpx). **(README §7 không liệt kê)** |
| `pytest.ini` | Cấu hình pytest. **(README §7 không liệt kê)** |
| `tests/` | Thư mục test. **(README §7 không liệt kê)** |
| `.dockerignore` | Bỏ qua khi build image. |
| `.gitignore` | Bỏ qua `data/`, `__pycache__`, v.v. |
| `.python-version` | Bản Python (uv). |

## 4. Runtime data

- `data/business_guide.db`: SQLite (tự tạo khi khởi động).
- `data/uploads/{training,video,thumbnails}/`: thư mục upload (tự tạo khi upload lần đầu qua `save_upload`).
- `.gitignore` bỏ qua `data/` → DB & file không vào git.

## 5. Điều gì KHÔNG tồn tại (khớp README §10, xác minh lại)

✅ Không có: authentication, authorization, RBAC, migration tool (alembic), pagination, rate limiting, logging framework, CI/CD, monitoring.

⚠️ **Có thật nhưng README §7 "cấu trúc thư mục" không liệt kê:**
`Dockerfile`, `docker-compose.yml`, `requirements-dev.txt`, `pytest.ini`, `tests/`.

## 6. Bất đồng với README

| # | Nội dung cũ (README) | Nội dung mới (code/repo) | Lý do sửa |
|---|---|---|---|
| D6 | §7 chỉ liệt kê `app/`, `data/`, `README.md`, `requirements.txt`, `.gitignore` | Repo có thêm `Dockerfile`, `docker-compose.yml`, `requirements-dev.txt`, `pytest.ini`, `tests/` | README chưa cập nhật cấu trúc mới |
| – | README §10 liệt kê ".dockerignore (file)" | Đúng, nhưng không nhắc `Dockerfile`/`docker-compose.yml` tuy có trong repo | Bổ sung cho đầy đủ |

## 7. Gợi ý (không thay đổi code)

- Thêm mục "Deploy" (Docker/compose) và "Testing" (pytest) vào README để khớp file thực tế.
- Đánh dấu rõ `requirements-dev.txt` là dev-only.

## 8. Điểm cần xác nhận (CẦN XÁC NHẬN)

- [ ] `Dockerfile`/`docker-compose.yml`/`tests/` đã được review & dùng trong pipeline chưa? (hiện không có CI/CD)
- [ ] `data/` trong `.gitignore` – có nên commit mẫu schema hay không?
- [ ] Có cần thêm `conftest.py`/fixture cho pytest không?
