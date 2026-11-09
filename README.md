# Nền tảng vận hành và đào tạo nghiệp vụ Logistics 3PL

> **Đặc tả v2.3** (08/11/2026) – thư mục [`docs/specs/`](docs/specs/00-muc-luc-va-quy-uoc.md).
> **Mã nguồn hiện tại: v1.1.0** – chưa theo đặc tả v2.x. Hiện trạng: [`docs/specs/90-hien-trang-v1.1.md`](docs/specs/90-hien-trang-v1.1.md). Lộ trình chuyển đổi: [`docs/specs/07`](docs/specs/07-cau-truc-ma-nguon-chuyen-doi-lo-trinh.md).

## 1. Mục tiêu

Một hệ thống dùng chung cho **vận hành thật** của doanh nghiệp 3PL và **đào tạo** nghiệp vụ: người học thực hành chính các quy trình vận hành trên dữ liệu tình huống, được cung cấp học liệu theo chủ đề và được đánh giá bằng chính các KPI vận hành.

## 2. Bốn luồng xử lý độc lập

| Luồng | API | Nội dung |
|---|---|---|
| 1. Vận hành | `/api/v2/ops`, `/api/v2/portal` | Hợp đồng dịch vụ, phụ lục → yêu cầu dịch vụ → lô hàng → bộ chứng từ khách hàng (S/C, C/I, P/L, C/O, C/Q, B/L…) → kiểm tra chéo theo bộ quy tắc cấu hình được → thực hiện quy trình nghiệp vụ mẫu → giao hàng → thanh lý, quyết toán hợp đồng (ký tay – gửi scan – xác nhận); **sổ thu – chi theo hợp đồng** (kế toán thống kê, tạm ứng – hoàn ứng, kết quả hợp đồng; không tích hợp phần mềm kế toán, hóa đơn điện tử); **cổng khách hàng** gửi yêu cầu, chứng từ, đề nghị sửa hợp đồng, xác nhận quyết toán |
| 2. Quản lý – kiểm soát | `/api/v2/control` | Kế hoạch triển khai hợp đồng, phân công, **phê duyệt 2 cấp** (người phê duyệt thứ hai: người quản lý hệ thống kinh doanh), điểm kiểm soát 4 mắt, **giám sát, điều phối tiến trình dịch vụ** (người quản lý với hợp đồng thật; người hướng dẫn với hợp đồng thực hành – theo đúng quy trình hợp đồng thật), cảnh báo, sự cố – CAPA |
| 3. Học liệu | `/api/v2/learning` | Tài liệu học thuật, hướng dẫn nghiệp vụ, video, mẫu chứng từ, văn bản pháp quy theo chủ đề và theo bước quy trình; **kho phỏng vấn video/audio** (phỏng vấn online qua phòng họp WebRTC tự triển khai, hoặc người hướng dẫn tải lên; bản ghi lời tự động bằng nhận dạng tiếng nói đặt tại máy chủ đơn vị); khóa, lớp, tiến độ học |
| 4. Đánh giá – KPI | `/api/v2/assessment` | Danh mục và tính toán KPI; tình huống thực hành có đáp án; chấm điểm, quy đổi KPI → điểm, kết quả học tập |

Các luồng không truy cập dữ liệu của nhau, chỉ trao đổi qua sự kiện và giao diện đọc công bố. Nguyên tắc chi tiết: [`01-kien-truc-tong-the.md`](docs/specs/01-kien-truc-tong-the.md) §3.

## 3. Bộ tài liệu đặc tả

| File | Nội dung |
|---|---|
| [`00`](docs/specs/00-muc-luc-va-quy-uoc.md) | Mục lục, quy ước, thuật ngữ, vai trò |
| [`01`](docs/specs/01-kien-truc-tong-the.md) | Kiến trúc 4 luồng, nguyên tắc độc lập, danh mục sự kiện |
| [`02`](docs/specs/02-luong-1-van-hanh.md) | Luồng 1 – Vận hành |
| [`03`](docs/specs/03-luong-2-quan-ly-kiem-soat.md) | Luồng 2 – Quản lý, kiểm soát |
| [`04`](docs/specs/04-luong-3-hoc-lieu.md) | Luồng 3 – Học liệu |
| [`05`](docs/specs/05-luong-4-danh-gia-kpi.md) | Luồng 4 – Đánh giá KPI |
| [`06`](docs/specs/06-quy-uoc-api-bao-mat.md) | Quy ước API, file, bảo mật, phân quyền |
| [`07`](docs/specs/07-cau-truc-ma-nguon-chuyen-doi-lo-trinh.md) | Cấu trúc mã nguồn, chuyển đổi, kiểm thử, lộ trình |
| [`90`](docs/specs/90-hien-trang-v1.1.md) | Hiện trạng v1.1.0 |
| [`99`](docs/specs/99-lich-su-chinh-sua.md) | Lịch sử chỉnh sửa |

Các điểm **CẦN XÁC NHẬN** được liệt kê ở mục cuối của từng file `02`–`05` và trong nội dung `06`, `07`. Không code theo giả định tại các điểm này.

## 4. Chạy và kiểm thử mã nguồn hiện tại (v1.1.0, đang chuyển đổi – Giai đoạn 0)

Yêu cầu: Python 3.12 (`.python-version`), Docker (PostgreSQL 17), pip ≥ 25.1.

```bash
cd logistics-3pl-platform
docker compose up -d db redis                            # PostgreSQL 17 (dev, test) + Redis (hàng đợi; số 15 cho test)
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev-lock.txt                # phụ thuộc chạy + công cụ dev (đều đã khóa)
cp .env.example .env                                     # rồi điền JWT_SECRET, TOTP_ENCRYPTION_KEY
uvicorn app.main:create_app --factory --reload           # http://127.0.0.1:8000/docs; tự chạy alembic upgrade head
python -m app.worker                                     # worker: email, OCR + AI, gửi outbox sự kiện (cần JOB_QUEUE_URL=redis://localhost:6379/0)
python -m app.shared.events.cli dispatch [--failed]      # xếp gửi lại sự kiện còn chờ trong outbox
python -m app.shared.events.cli replay control           # Luồng 2 xử lý lại sự kiện từ outbox (idempotent, không cần Redis)
python -m app.shared.identity.cli create-admin --username admin --full-name "Quản trị"   # admin đầu tiên
pytest                                                   # 839 test: 618 tích hợp (PostgreSQL logistics_3pl_test, Redis số 15) + 220 đơn vị + 1 model thật (bỏ qua mặc định)
ruff check . && ruff format --check .
```

Toàn bộ bằng Docker: `docker compose up -d --build` – `db`, `redis` (AOF, volume `redisdata`), `api`, `worker` (cùng image, `python -m app.worker`); dữ liệu CSDL trong volume `pgdata`, file tải lên tại `./data/uploads/`.

Chạy test trong image (stage `test` của `Dockerfile` – cùng phụ thuộc chạy và mã nguồn với image chạy):

```bash
docker build --target test -t logistics-3pl-platform:test .
docker run --rm --network logistics-3pl-platform_default \
  -e TEST_DATABASE_URL=postgresql+psycopg://logistics:logistics@db:5432/logistics_3pl_test \
  -e TEST_JOB_QUEUE_URL=redis://redis:6379/15 logistics-3pl-platform:test
```

| File | Vai trò |
|---|---|
| `pyproject.toml` | Phụ thuộc chốt cứng `==`, nhóm `dev`, cấu hình pytest, ruff |
| `requirements-lock.txt` | `pip freeze` phụ thuộc chạy; Docker cài từ file này |
| `requirements-ocr-paddle-lock.txt` | Nhóm tùy chọn `ocr-paddle` (PaddleOCR) – chỉ cài khi `DOCAI_OCR_ENGINE=paddle`, lệnh riêng sau lock chính |
| `requirements-dev-lock.txt` | `pip freeze` phụ thuộc chạy + nhóm `dev`; máy dev cài từ file này. Đổi phiên bản trong `pyproject.toml` thì tạo lại **cả hai** file lock |
| `alembic.ini`, `migrations/` | Migration Alembic. Ứng dụng tự `upgrade head` khi khởi động; CSDL cũ tạo bằng `create_all` được đánh dấu revision `0001`. Đổi model: `alembic revision --autogenerate --rev-id 0002 -m "..."`, xem lại file sinh ra; `alembic check` báo model lệch migration |
| `deploy/postgres/init/` | Script tạo CSDL test khi khởi tạo volume lần đầu |

Cấu hình: sao chép `.env.example` thành `.env` và đặt **`JWT_SECRET`** (bắt buộc, ≥ 32 ký tự) và **`TOTP_ENCRYPTION_KEY`** (bắt buộc, khóa Fernet riêng – sinh bằng `python -m app.core.secretbox`, không dùng lại `JWT_SECRET`); thiếu thì ứng dụng không khởi động. Biến khác: `DATABASE_URL` (mặc định CSDL dev ở trên), `TEST_DATABASE_URL` (tên CSDL bắt buộc kết thúc `_test`), `UPLOAD_DIR` (mặc định `./data/uploads`), `POSTGRES_PASSWORD` (mặc định `logistics` – chỉ cho máy dev), `POSTGRES_PORT`; danh sách đầy đủ: [`06`](docs/specs/06-quy-uoc-api-bao-mat.md) §6, `app/core/config.py`. Mã v1.1.0 nằm tại `app/legacy_v1/`, API `/api/v1/*` đánh dấu deprecated; mô tả chi tiết: [`90-hien-trang-v1.1.md`](docs/specs/90-hien-trang-v1.1.md).

**Thay đổi thư viện (Giai đoạn 6, lý do bảo mật):** thêm `cryptography==50.0.2` vào phụ thuộc chạy để mã hóa `sys_users.totp_secret` bằng Fernet (khóa `TOTP_ENCRYPTION_KEY`) thay vì lưu dạng rõ. Hai file lock tạo lại trong venv sạch – chỉ thêm đúng dòng `cryptography==50.0.2` (`cffi`, `pycparser` đã có sẵn), các gói khác giữ nguyên phiên bản. Nâng cấp hệ thống đang chạy: đặt `TOTP_ENCRYPTION_KEY` **trước** khi khởi động bản mới – migration `0008` mã hóa secret hiện có bằng khóa này.

**Thay đổi thư viện (Giai đoạn 7):** thêm `rq==2.9.1` + `redis==8.1.0` (hàng đợi tác vụ nền bền vững qua `JOB_QUEUE_URL` – email mời, đặt lại mật khẩu không mất khi tiến trình dừng, có thử lại). Chọn rq 2.9.1 (06/2026) thay vì 3.0.0 (phát hành 28/10/2026, bản major mới). Phụ thuộc bắc cầu mới trong lock: `croniter`, `python-dateutil`, `tzdata`; các gói cũ giữ nguyên phiên bản.

**Thay đổi thư viện (Giai đoạn 9 – AI local trích xuất chứng từ):** thêm `pypdfium2==5.15.1`, `pillow==13.0.0`, `python-docx==1.2.0`, `openpyxl==3.1.5` (bắc cầu `lxml`, `et_xmlfile`; gói cũ giữ nguyên phiên bản). OCR mặc định dùng chương trình **Tesseract** của hệ điều hành: macOS `brew install tesseract tesseract-lang`, Debian/Ubuntu `apt install tesseract-ocr tesseract-ocr-vie` (image Docker đã cài). PaddleOCR là nhóm tùy chọn, không cài mặc định: `pip install -r requirements-ocr-paddle-lock.txt` (lệnh riêng, sau lock chính), đặt `DOCAI_OCR_ENGINE=paddle`, `DOCAI_PADDLE_MODEL_DIR` (model tải sẵn).

### AI local trích xuất chứng từ (Giai đoạn 9)

Không gọi API cloud nào: chỉ máy chủ **Ollama** trong máy / mạng nội bộ (`OLLAMA_URL`, mặc định `http://localhost:11434`; địa chỉ ngoài mạng nội bộ → ứng dụng không khởi động). Chuẩn bị: cài Ollama, `ollama pull qwen3.8:27b` (model mẫu có sẵn trong danh mục sau migration `0010`).

**Admin thêm model** (không sửa mã – danh mục nằm trong bảng `ai_models`):

```bash
ollama pull qwen3-vl:8b                                   # 1. tải model vào máy chủ Ollama nội bộ
curl -X POST http://127.0.0.1:8000/api/v2/admin/ai-models -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"code": "qwen3-vl-8b", "display_name": "Qwen3-VL 8B (nhanh)",
  "model_id": "qwen3-vl:8b", "model_type": "vision", "supports_pdf": true, "supports_image": true,
  "supports_docx": true}'                                 # 2. thêm vào danh mục (endpoint bỏ trống = OLLAMA_URL)
curl -X POST http://127.0.0.1:8000/api/v2/admin/ai-models/2:probe -H "Authorization: Bearer $ADMIN_TOKEN"
                                                          # 3. kiểm máy chủ trả lời + model đã pull
```

`model_type`: `vision` (đọc ảnh trang + văn bản OCR), `text` (chỉ văn bản OCR), `ocr` (model OCR qua Ollama, chọn ở `ocr_model_id` thay Tesseract). `endpoint` riêng (vd. `http://gpu-01:11434`) chỉ nhận localhost / mạng nội bộ. Tắt / bật: `POST …/{id}:deactivate` / `:activate`; xóa khỏi danh mục: `DELETE …/{id}` (mềm – job cũ vẫn giữ model đã dùng, job mới không chọn được). Loại chứng từ mới: thêm file `app/shared/docai/schemas/<code>.json` (xem `party.json`), khởi động lại API + worker.

**Người thực hiện chọn model ở bước công việc** (`ops_staff`, `manager`):

1. `GET /api/v2/ai-models/active` → danh sách model đang bật (không có địa chỉ máy chủ); `GET /api/v2/doc-types` → loại chứng từ + trường của form.
2. `POST /api/v2/doc-jobs` (multipart): `file` (jpg, png, tiff, webp, pdf, docx, xlsx, txt ≤ 20 MB), `doc_type` (vd. `party`, `service_contract`, `shipment`, `commercial_invoice`), **`model_id`** (bắt buộc – id model đã chọn; hệ thống không tự chọn) → `202`, job `queued`; OCR + AI chạy nền (worker).
3. `GET /api/v2/doc-jobs/{id}` → `draft` (giá trị + độ tin cậy từng trường), `form` (body tự điền cho API tạo bản ghi), `status` `ready_for_review` / `needs_review` (có trường < 0,9 hoặc thiếu trường bắt buộc); `GET …/extractions` → văn bản OCR theo trang, bbox.
4. Sửa: `PATCH /api/v2/doc-jobs/{id}/review` `{"fields": {"tax_code": "0101234567"}}`; duyệt: `POST …/approve` `{"confirmed_fields": [...]}` (trường độ tin cậy thấp giữ nguyên giá trị AI phải xác nhận từng trường); từ chối: `POST …/reject` `{"reason": "…"}`.
5. Sau khi duyệt: gọi API tạo bản ghi kèm `form` + `?source_doc_job_id={id}` (`create_hint` trong kết quả), vd. `POST /api/v2/md/parties?source_doc_job_id=12`. Job chưa duyệt / khác loại / đã dùng → lỗi, không tạo bản ghi.

**Duyệt 2 người (Giai đoạn 10):** job hợp đồng và hóa đơn giá trị lớn (`DOCAI_DUAL_APPROVAL_THRESHOLDS`) sau lần duyệt đầu ở trạng thái `pending_second_approval`; người duyệt thứ 2 (thường là `manager`) gọi lại `POST …/approve` – phải khác người tải lên và khác người duyệt đầu. Hóa đơn: sau khi duyệt, `POST /api/v2/ops/commercial-invoices?source_doc_job_id={id}` với `form` + `shipment_id` hoặc `contract_id`.

**Luồng 2 – kế hoạch, phân công tự động (Giai đoạn 11):** worker nhận EV-101 → kế hoạch hợp đồng nháp; EV-111 → các bước từ quy trình mẫu + phân công tự động theo vai trò, năng lực, tải → EV-202 → Luồng 1 đặt người phụ trách lô. Manager đặt năng lực/sức chứa: `PUT /api/v2/control/staff-profiles/{user_id}` `{"skills": {"CUSTOMS_CLEARANCE": 4, "DOCS": 3}, "max_load_hours": "40"}`; xem `GET /api/v2/control/workload`, điều chuyển `POST /api/v2/control/assignments`.

**Theo dõi thực thi (Giai đoạn 12):** người được giao cập nhật bước `POST /api/v2/control/tasks/{id}:start` · `PATCH` (% tiến độ) · `:block` (lý do) · `:unblock` · `:complete`; theo dõi `GET /api/v2/control/tracking/shipments?health=late`; manager xem cảnh báo chậm / lỗi `GET /api/v2/control/alerts?mine=true`, `:acknowledge`, `:resolve`. Worker quét chậm trễ mỗi `CONTROL_SCAN_INTERVAL_SEC` giây.

**Đồng bộ bước, leo thang, Zalo (Giai đoạn 13):** bước của lô có ở cả Luồng 2 (`/api/v2/control/tasks/…`) và Luồng 1 (`/api/v2/ops/tasks/{id}:start|:complete|:block|:unblock`), đồng bộ hai chiều qua outbox (Luồng 2 là nguồn chuẩn; lệch → `POST /api/v2/control/shipment-plans/{id}:resync`). Cảnh báo chưa xử lý sau 2 giờ leo thang lên `business_manager`. Gửi cảnh báo qua email (cần SMTP) và Zalo OA: đặt `ZALO_TOKEN` + `zalo_user_id` trong hồ sơ (`PUT /api/v2/control/staff-profiles/{user_id}`).

**Luồng 4 – KPI vận hành (Giai đoạn 14):** worker nhận sự kiện từ `l3pl:events:assessment`, dựng bảng `asm_fact_*`. Báo cáo (`manager`, `business_manager`, `admin`): `GET /api/v2/assessment/kpi?from=2026-10-01&to=2026-10-31`, theo hợp đồng (kèm so SLA) `/kpi/contracts`, theo lô `/kpi/shipments`, theo nhân viên `/kpi/staff`. Sau khi nâng cấp lên `0015`, dựng dữ kiện từ lịch sử: `python -m app.shared.events.cli replay assessment`.

**KPI-01…14, mục tiêu, tính định kỳ (Giai đoạn 15):** mục tiêu KPI đặt trong kế hoạch hợp đồng (`PUT /api/v2/control/contract-plans/{id}/kpi-targets`, `:submit`) và có hiệu lực khi `business_manager` `:approve` (EV-201). Worker tự tính KPI mỗi ngày lúc `ASSESSMENT_KPI_CALC_HOUR` giờ (mặc định 01:00 giờ Việt Nam) – số đo ở `GET /api/v2/assessment/kpi/measurements`; KPI kém mục tiêu → cảnh báo cho manager, business_manager (`/api/v2/control/alerts?alert_type=kpi_below_target`, email / Zalo). Tính ngay: `POST /api/v2/assessment/kpi/calc-runs` (admin).

**Địa điểm, chứng từ, điểm kiểm soát (Giai đoạn 16):** danh mục `GET|POST /api/v2/md/locations` (admin, manager ghi; khách hàng xem địa điểm của mình). Nhân viên phụ trách lô tải chứng từ `POST /api/v2/ops/shipments/{id}/documents` (multipart `file`, `doc_type`) → lần kiểm tra chờ; ghi kết quả `POST /api/v2/ops/document-checks/{id}:complete` (`ok` / `missing` / `invalid` + lý do). Lô chỉ `:verify-documents` khi lần kiểm tra mới nhất đạt (BR-103). Điểm kiểm soát: `POST /api/v2/ops/shipments/{id}/checkpoints`, xác nhận `POST /api/v2/ops/checkpoints/{id}:evaluate`.

**Cổng gửi chứng từ, địa điểm lô, quy tắc (Giai đoạn 17):** tạo lô cần `origin_location_id`, `destination_location_id` (địa điểm đang hoạt động trong `/api/v2/md/locations`). Khách hàng gửi chứng từ `POST /api/v2/portal/shipments/{id}/documents`; chứng từ mới khi lô đã `docs_verified` đưa lô về `docs_collecting`. Chứng từ bắt buộc theo loại hình vận chuyển: `GET|POST /api/v2/ops/document-requirements` – thiếu thì lần kiểm tra tự ghi "thiếu".

Test với model thật (bỏ qua mặc định): `DOCAI_LIVE_OLLAMA=1 pytest tests/shared/docai/test_live_ollama.py`.

## 5. Giấy phép

Chưa có file `LICENSE`. **CẦN XÁC NHẬN** giấy phép áp dụng.
