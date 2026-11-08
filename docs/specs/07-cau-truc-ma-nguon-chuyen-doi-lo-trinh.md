# 07 – Cấu trúc mã nguồn đích, chuyển đổi từ v1.1.0, kiểm thử và lộ trình

## 1. Cấu trúc thư mục đích (v2.0)

```
fastapi-todo/                       # Đổi tên repo: CẦN XÁC NHẬN (đề xuất: logistics-3pl-platform)
├── app/
│   ├── main.py                     # create_app(): middleware, exception handler chung, gắn router theo ENABLED_FLOWS
│   ├── core/                       # Hạ tầng kỹ thuật, không chứa nghiệp vụ
│   │   ├── config.py               # pydantic-settings, đọc biến môi trường (06 §6)
│   │   ├── db.py                   # engine, session, Base, get_db (rollback khi lỗi)
│   │   ├── errors.py               # Định dạng lỗi thống nhất (06 §2.2)
│   │   ├── pagination.py           # Page[T], tham số page/page_size/sort (06 §2.3)
│   │   ├── security.py             # JWT, băm mật khẩu
│   │   ├── rbac.py                 # require_roles(...), kiểm tra phạm vi dữ liệu
│   │   └── timeutil.py             # now_utc(), chuyển múi giờ hiển thị
│   ├── shared/                     # Dùng chung giữa các luồng (N7)
│   │   ├── events/
│   │   │   ├── contracts.py        # Pydantic model cho payload EV-101…EV-403 (hợp đồng sự kiện)
│   │   │   ├── outbox.py           # publish(event) trong cùng transaction
│   │   │   ├── dispatcher.py       # Đọc sys_events, gọi handler, ghi sys_event_consumptions
│   │   │   └── registry.py         # Đăng ký handler theo event_code
│   │   ├── files/                  # StorageBackend (local/S3), service, router /api/v2/files
│   │   ├── audit/                  # sys_audit_logs, ghi tự động qua session event
│   │   ├── identity/               # sys_users, sys_user_roles, router /api/v2/auth
│   │   ├── masterdata/             # md_*: parties, document_types, process_templates, rule_sets (ma trận + quy tắc cấu hình); router /api/v2/md
│   │   └── jobs/                   # Hàng đợi tác vụ nền: xử lý media, tính KPI, gửi thông báo, phát sự kiện
│   ├── flows/
│   │   ├── operations/             # Luồng 1
│   │   │   ├── models.py  schemas.py  router.py  public.py
│   │   │   ├── handlers.py         # Xử lý sự kiện nhận (EV-202, 203, 204, 205, 206, 401)
│   │   │   ├── portal/             # Cổng khách hàng: router /api/v2/portal, schemas riêng (ẩn dữ liệu nội bộ – BR-111)
│   │   │   └── services/
│   │   │       ├── contracts.py  amendments.py  service_requests.py  shipments.py  documents.py  tasks.py  approvals.py
│   │   │       ├── charges.py  settlements.py  liquidation_minutes.py   # biểu giá, phí, quyết toán, sinh biên bản
│   │   │       ├── cashbook.py  staff_advances.py  finance_reports.py  # sổ thu – chi, tạm ứng – hoàn ứng, BC-TC-01…06
│   │   │       ├── signed_documents.py                                  # sinh văn bản từ mẫu, tải scan, xác nhận (§3.11 của 02)
│   │   │       └── checks/         # engine.py (nạp bộ quy tắc đã công bố), evaluators/ (REQUIRED_DOCUMENTS, FIELD_EQUALS, LINE_MATCH…), conditions.py (JSONLogic)
│   │   ├── control/                # Luồng 2 (models, schemas, router, public, handlers, services/{plans, assignments, approvals (chính sách nhiều cấp), checkpoints, incidents, monitoring, coordination, business_dashboard})
│   │   ├── learning/               # Luồng 3 (… services/{topics, resources, citations, courses, progress}, interviews/{sessions.py, recorder/ (livekit.py, jitsi.py – WebRTC tự triển khai), media_pipeline.py, transcripts.py})
│   │   └── assessment/             # Luồng 4 (… kpi/{registry.py, formulas.py, calculator.py}, scoring.py, scenarios.py, grading.py)
│   └── legacy_v1/                  # Mã v1.1.0 chuyển vào đây trong giai đoạn chuyển tiếp (§3.3), sau đó xóa
├── migrations/                     # Alembic
├── scripts/
│   └── migrate_v1_to_v2.py         # Chuyển dữ liệu v1.1.0 (§3)
├── tests/
│   ├── architecture/               # Kiểm tra N2, N4 (§4.2)
│   ├── contracts/                  # Kiểm tra schema sự kiện
│   ├── shared/
│   ├── operations/  control/  learning/  assessment/
│   └── e2e/                        # E2E-01 … E2E-08 (§4.3)
├── docs/specs/                     # Bộ đặc tả này
├── deploy/                         # docker-compose các dịch vụ hạ tầng: PostgreSQL, hàng đợi, máy chủ media WebRTC (SFU + dịch vụ ghi), TURN (coturn)
├── Dockerfile  docker-compose.yml  .dockerignore  .gitignore
├── pyproject.toml                  # Thay requirements*.txt; cấu hình ruff, pytest
└── README.md
```

## 2. Thay đổi nền tảng kỹ thuật

| Hạng mục | v1.1.0 | v2.0 | Lý do |
|---|---|---|---|
| Phiên bản thư viện | FastAPI 0.104.1, Pydantic 2.5.0, SQLAlchemy 2.0.23 | Bản ổn định hiện hành tại thời điểm bắt đầu Giai đoạn 0, chốt cứng trong `pyproject.toml` – **CẦN XÁC NHẬN** khi lập kế hoạch | Bản 2023, chưa kiểm chứng trên Python ≥ 3.13 |
| Python | 3.11 (Docker), `venv/` 3.15 rỗng | 3.12 thống nhất local/Docker/CI – **CẦN XÁC NHẬN**; xóa `venv/` | Một phiên bản duy nhất |
| CSDL | SQLite, `create_all` | SQLite cho dev/test; PostgreSQL cho môi trường thật; **Alembic** quản lý schema | Có migration; hỗ trợ đồng thời nhiều người dùng |
| Thư viện bổ sung | – | `alembic`, `pydantic-settings`, `PyJWT`, `argon2-cffi`, `pyotp` (xác thực 2 lớp), `filetype` (nhận dạng MIME), `openpyxl` (nhập/xuất XLSX), `psycopg`, `jsonschema` (tham số bộ đánh giá), thư viện JSONLogic (biểu thức điều kiện), `docxtpl` + chuyển PDF (biên bản thanh lý, phụ lục, phiếu thu/chi in), `pypdf` (đếm trang, kiểm tra file scan), hàng đợi tác vụ nền (vd. `arq`/`rq` – **CẦN XÁC NHẬN**); SDK quản trị của máy chủ media (vd. `livekit-api`) để cấp token phòng, điều khiển ghi; `faster-whisper` (hoặc thư viện của mô hình được chọn) cho nhận dạng tiếng nói cục bộ; công cụ hệ thống `ffmpeg` (xử lý media) | 02 §5.3, §3.9; 04 §3.3; 06 |
| Hạ tầng phỏng vấn online | – | Máy chủ media WebRTC (SFU + dịch vụ ghi) và TURN/STUN tự triển khai trên máy chủ đơn vị; máy chủ nhận dạng tiếng nói (khuyến nghị có GPU) trong mạng nội bộ; tên miền riêng có chứng chỉ TLS; mở dải cổng UDP cho media và TCP/TLS 443 dự phòng | 04 §3.3.1a |
| Kiểm tra mã | – | `ruff`, test kiến trúc, `pytest-cov` | §4 |

## 3. Chuyển đổi từ v1.1.0

### 3.1. Ánh xạ dữ liệu

| Bảng v1.1.0 | Đích v2.0 | Quy tắc chuyển |
|---|---|---|
| `business_guides` | `lrn_resources` (`GUIDE`) + `lrn_guides` | `title` → `title`; `content` → `body_markdown`; `category` → `topic_codes` theo **bảng ánh xạ category → topic** do người biên soạn lập (**CẦN XÁC NHẬN**); `summary` sinh từ 300 ký tự đầu của nội dung; `is_active = true` → `published`, `false` → `archived`; giữ `created_at` |
| `training_documents` | `lrn_resources` (`ACADEMIC_DOC` hoặc `TEMPLATE` theo ánh xạ) + `sys_files` | File tại `file_path` sao chép sang kho lưu trữ mới, tính SHA-256, nhận dạng MIME; `description` → `summary`; thiếu thông tin trích dẫn/bản quyền (BR-302) → trạng thái `draft` kèm nhãn `legacy_incomplete` |
| `video_resources` | `lrn_resources` (`VIDEO`) + `lrn_videos` + `sys_files` | `video_path`, `thumbnail_path` → `sys_files`; `duration` → `duration_sec`; các trường khác như trên |
| `business_data_records` (`record_type = customer`, `partner`) | `md_parties` | `title` → tên; `data_content` cần làm sạch thủ công (tự do, không có cấu trúc) → xuất danh sách để rà soát trước khi nhập |
| `business_data_records` (`contract`) | `ops_service_contracts` (`draft`) | Thiếu khách hàng, hiệu lực, phạm vi dịch vụ → nhập bổ sung trước khi kích hoạt |
| `business_data_records` (`form`) | `lrn_resources` (`TEMPLATE`) | |
| `business_data_records` (`business_result`, giá trị khác) | Không có đích tương ứng | Xuất ra file lưu trữ `migration_archive/business_data_records.json`, không nhập – **CẦN XÁC NHẬN** |

### 3.2. Yêu cầu đối với script chuyển đổi

- Chạy được nhiều lần (idempotent), có chế độ `--dry-run` sinh báo cáo: số bản ghi theo đích, bản ghi lỗi, file không tìm thấy.
- Không xóa bảng v1 cho tới khi người dùng xác nhận báo cáo; sao lưu file CSDL và thư mục `uploads/` trước khi chạy.
- Ghi `legacy_id` (id v1) vào bản ghi đích để đối soát.

### 3.3. API v1 trong giai đoạn chuyển tiếp

- Sau Giai đoạn 1, `/api/v1/guide`, `/api/v1/training/documents`, `/api/v1/video/resources` thành **chỉ đọc**, đọc từ `lrn_resources` qua lớp chuyển đổi trong `app/legacy_v1/`; `/api/v1/business-data/records` chỉ đọc từ bảng cũ.
- Gỡ bỏ toàn bộ `/api/v1` sau Giai đoạn 2. **CẦN XÁC NHẬN** có hệ thống/người dùng nào đang gọi API v1 không.

## 4. Kiểm thử

### 4.1. Theo luồng

| Mức | Nội dung | Yêu cầu |
|---|---|---|
| Đơn vị | Từng bộ đánh giá (`02` §5.3.3) và cấu hình khởi tạo R-01…R-14 (bảng ca kiểm thử: đạt, vi phạm, biên dung sai); kiểm tra JSON Schema tham số; biểu thức điều kiện; máy trạng thái; công thức KPI; quy đổi điểm; công thức quyết toán A + T + C − D − E − F | Mỗi bộ đánh giá/công thức có test riêng |
| Dịch vụ – API | Từng endpoint: quyền (RBAC, BR-01), validation, mã lỗi, định dạng lỗi | Độ phủ dòng lệnh `services/` ≥ 85% |
| Sự kiện | Handler idempotent (nhận 2 lần), phát đúng payload theo `shared/events/contracts.py` | Bắt buộc với mọi EV |

### 4.2. Kiểm tra kiến trúc (`tests/architecture/`)

- Phân tích import (AST) của `app/flows/<A>/**`: chỉ được import `app.core`, `app.shared`, chính `app.flows.<A>`, và `app.flows.<B>.public` (N4). Vi phạm → test lỗi.
- Phân tích migration Alembic: không có khóa ngoại từ bảng tiền tố luồng này sang bảng tiền tố luồng khác (N3).
- Khởi động ứng dụng với `ENABLED_FLOWS` thiếu một luồng bất kỳ → các luồng còn lại chạy, test của chúng đạt (N6).

### 4.3. Kịch bản đầu – cuối

| Mã | Kịch bản | Kết quả kiểm chứng |
|---|---|---|
| E2E-01 (`production`) | Kích hoạt hợp đồng → kế hoạch duyệt → tạo lô `QT-NK-SEA-FCL` → tải bộ chứng từ có sai lệch R-07 → yêu cầu miễn trừ → duyệt → thực hiện 10 bước, 4 điểm kiểm soát (1 lần không đạt) → giao hàng đúng hạn → tính KPI | KPI-01 = 0%, KPI-05 = 75%, KPI-07 = 100% cho lô; đủ sự kiện trong `sys_events` |
| E2E-02 (`training`) | GV tạo tình huống 5 sai lệch → giao bài → người học ghi nhận 4 đúng, 1 sai → hoàn thành 9/10 bước đúng hạn → GV chấm rubric 8,0 → chốt | Tái lập đúng ví dụ `05` §5: điểm 7,1, xếp loại B; người học không truy cập được đáp án (BR-401) |
| E2E-03 (cổng khách hàng, hợp đồng) | Khách hàng góp ý bản nháp → kích hoạt → gửi yêu cầu dịch vụ → được chấp nhận, sinh lô → gửi chứng từ qua cổng → nhận thông báo bổ sung → gửi bản mới → đề nghị phụ lục tăng biểu giá → duyệt cấp 1, cấp 2 → sinh văn bản phụ lục → ký tay, tải scan, bên kia xác nhận → có hiệu lực → lô mới tính phí theo phiên bản 2, lô cũ giữ phiên bản 1 | Khách hàng không thấy dữ liệu khách hàng khác, nhật ký nội bộ (BR-111); EV-103, EV-113, EV-114 đúng; KPI-14 tính đúng |
| E2E-04 (thanh lý, quyết toán) | Hợp đồng hết hạn → khởi động thanh lý → lập bảng quyết toán gồm phí dịch vụ, chi hộ, 1 lô vi phạm SLA, 1 sự cố bồi thường → duyệt cấp 1 (`manager`) → duyệt cấp 2 (`business_manager`) → khách hàng khiếu nại 1 dòng → lập lại → xác nhận → sinh biên bản → khách hàng tải bản scan đã ký → công ty đối chiếu, xác nhận → tất toán S bằng phiếu thu | S = A + T + C − D − E − F khớp số tính tay; không đóng được hợp đồng trước khi bản scan `confirmed` (BR-116); cùng một người không duyệt được cả 2 cấp (BR-202); hợp đồng `closed`; EV-104, EV-102 đúng; khoản phí `settled` |
| E2E-07 (thu – chi, tạm ứng) | Nhân viên đề nghị tạm ứng 10.000.000 đ cho lô → duyệt → phiếu chi C08 → hoàn ứng 3 khoản (phí nâng hạ C03, phí cảng chi hộ C01, lệ phí chi hộ C01) tổng 8.500.000 đ kèm chứng từ → phiếu thu nhân viên nộp lại 1.500.000 đ → khoản chi hộ của lô liên kết 2 phiếu C01 → báo cáo kết quả hợp đồng | Công nợ tạm ứng = 0, tạm ứng `settled`; BR-113 đúng (thu hồi chi hộ = tổng chứng từ C01); BC-TC-03 lãi gộp = doanh thu dịch vụ − C03 (chi hộ không vào chi phí); phiếu đã ghi sổ không sửa được (BR-112); M-10 không phát sinh |
| E2E-08 (hợp đồng thực hành theo quy trình thật) | Hợp đồng `training`: người hướng dẫn giao vai cho 3 người học (nhân viên chứng từ, nhân viên hải quan, `account_manager`) → người học lập kế hoạch, người hướng dẫn (`business_manager_proxy`) duyệt → bước bị `blocked` quá 8 giờ → cảnh báo M-06 tới người hướng dẫn (`coordinator`) → điều chuyển, đặt ưu tiên `urgent`, đôn đốc → người học lập phiếu chi vượt ngưỡng Y → `account_manager` (người học) duyệt cấp 1 → người hướng dẫn duyệt cấp 2 → người hướng dẫn thử thao tác trên một lô `production` | Cùng số cấp, cùng ngưỡng như hợp đồng thật (BR-207); EV-202, EV-207 đúng; nhật ký điều phối đầy đủ (BR-210); thao tác của người hướng dẫn trên `production` bị từ chối `403` (BR-209); người học không vừa thực hiện vừa kiểm soát cùng một lô (BR-211) |
| E2E-05 (cấu hình quy tắc) | Manager sao chép bộ `production`, thêm quy tắc R-15 (`AGGREGATE_COMPARE` thể tích P/L – B/L, dung sai 3%), xóa dòng ma trận INS, chạy thử, công bố; instructor tạo bộ `training` riêng cho một khóa | Lần kiểm tra mới ghi `ruleset_version` mới; lần kiểm tra cũ tái lập đúng với phiên bản cũ (BR-102); bộ `training` không ảnh hưởng lô `production` |
| E2E-06 (phỏng vấn) | Quy trình 1: lập phiên, mời 1 khách, khách xác nhận đồng ý, vào phòng WebRTC, ghi, nhận file qua webhook của dịch vụ ghi (CI dùng máy chủ media giả lập; kiểm thử nghiệm thu dùng máy chủ thật); Quy trình 2: GV tải file audio 300 MB theo phần + biên bản đồng ý; cả hai qua hậu kỳ, xuất bản | Không ghi được khi khách chưa đồng ý (BR-308); người học không tải được file gốc (BR-310); tìm được đoạn theo chủ đề |

## 5. Lộ trình triển khai

| Giai đoạn | Nội dung | Điều kiện hoàn thành |
|---|---|---|
| **0. Nền tảng** | `core/`, `shared/` (sự kiện, file, nhật ký, người dùng, danh mục dùng chung), Alembic, CI (ruff, pytest, test kiến trúc), Docker | Đạt `06` toàn bộ; test kiến trúc chạy trong CI |
| **1. Luồng 3 – Học liệu** | Chủ đề, 7 loại học liệu, duyệt – xuất bản, tìm kiếm, khóa/lớp/tiến độ; kho phỏng vấn **Quy trình 2** (tải lên) + xử lý media, hậu kỳ; chuyển đổi dữ liệu học liệu v1 | Báo cáo chuyển đổi được xác nhận; API v1 học liệu chỉ đọc; phần Quy trình 2 của E2E-06 đạt |
| **1b. Phỏng vấn online (WebRTC tự triển khai)** | PoC LiveKit (phương án thay thế Jitsi): dựng SFU, dịch vụ ghi, TURN trên máy chủ đơn vị; thử nghiệm tải (số phòng đồng thời, băng thông, CPU ghi, dung lượng lưu trữ) → chốt máy chủ media và cấu hình hạ tầng; PoC chuyển giọng nói thành văn bản tại máy chủ đơn vị (đo WER, thời gian xử lý trên bộ mẫu phỏng vấn nghiệp vụ – `04` §3.3.1b) → chốt mô hình, phần cứng; sau đó hoàn thiện **Quy trình 1** và bản ghi lời tự động | Biên bản PoC có số đo (media và nhận dạng tiếng nói); E2E-06 đạt đầy đủ trên máy chủ media thật |
| **2. Luồng 1 – Vận hành** | Hợp đồng, phụ lục, yêu cầu dịch vụ, lô, chứng từ; thư viện bộ đánh giá + cấu hình bộ quy tắc (khởi tạo R-01…R-14); bước quy trình, quy trình mẫu `QT-NK-SEA-FCL`; biểu giá, khoản phí; **sổ thu – chi theo hợp đồng, tạm ứng – hoàn ứng, BC-TC-01…05**; ký tay – scan – xác nhận; **cổng khách hàng** (PRT-01…PRT-06, PRT-09, PRT-10) | E2E-01 tới bước giao hàng (phê duyệt, kiểm soát dùng sự kiện giả lập); E2E-03, E2E-05, E2E-07 đạt; gỡ API v1 |
| **3. Luồng 2 – Quản lý, kiểm soát** | Kế hoạch, phân công, **phê duyệt nhiều cấp** (chính sách phê duyệt, người phê duyệt thứ hai `business_manager`), điểm kiểm soát, sự cố/CAPA (trách nhiệm, bồi thường), giám sát M-01…M-10, **bảng điều phối** của người hướng dẫn, bảng tổng hợp kinh doanh | E2E-01, E2E-03, E2E-08 chạy đầy đủ với Luồng 2 thật |
| **4. Luồng 4 – Đánh giá KPI** | KPI-01…14, tính định kỳ, tình huống, bài đánh giá, chấm, chốt, phúc khảo, bảng điểm | E2E-01 (KPI) và E2E-02 đạt |
| **4b. Thanh lý – quyết toán** | Bảng kê đối soát, quyết toán, giảm trừ SLA (dùng KPI Luồng 4), bồi thường, biên bản thanh lý; BC-TC-06; cổng khách hàng PRT-07, PRT-08 | E2E-04 đạt |
| **5. Mở rộng** | Thêm quy trình mẫu (§4 của `02`), trắc nghiệm, vấn đáp, báo cáo định kỳ, thông báo email, trích xuất chứng từ tự động | Theo ưu tiên được xác nhận |

Luồng 3 làm trước vì không phụ thuộc luồng khác và kế thừa trực tiếp dữ liệu v1.1.0. Luồng 4 có thể phát triển song song với Luồng 2 bằng sự kiện giả lập (N8). Thời lượng từng giai đoạn: **CẦN XÁC NHẬN** theo nguồn lực nhóm phát triển.

## 6. Tiêu chí hoàn thành một chức năng (Definition of Done)

1. Code đúng mã yêu cầu (`OPS-/CTL-/LRN-/ASM-Fxx`), quy tắc (`BR-xxx`) được dẫn chiếu trong docstring.
2. Có test đơn vị và test API (gồm ca phân quyền); CI đạt, gồm test kiến trúc.
3. Có migration Alembic (nếu đổi schema), chạy được `upgrade` và `downgrade`.
4. OpenAPI có mô tả, ví dụ request/response tiếng Việt.
5. Cập nhật đặc tả nếu hành vi thực tế khác đặc tả; ghi vào `99-lich-su-chinh-sua.md`.
