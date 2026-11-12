# 99 – Lịch sử chỉnh sửa

> Mục 10.1–10.5 trích nguyên văn từ README.md bản 2.1 (07/11/2026). Mục 10.6 ghi đợt tái cấu trúc đặc tả v2.0 (08/11/2026). Mục 10.10 ghi các quyết định nền tảng kỹ thuật trước Giai đoạn 0.

## 10. Bảng chỉnh sửa

Phiên bản `1.1.0` (07/11/2026). Trạng thái trước khi chỉnh sửa (code và README tiếng Anh cũ) lưu tại commit `5f2b336`; xem lại bằng `git show 5f2b336:README.md` hoặc `git diff 5f2b336`.

### 10.1. Tài liệu – so với README.md cũ

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
| Không có lịch sử thay đổi | Mục 10.2–10.4 ghi toàn bộ chỉnh sửa mã nguồn | Lưu vết sửa lỗi ngay trong tài liệu đặc tả |

### 10.2. Mã nguồn – lỗi khiến ứng dụng không khởi động

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `app/database/__init__.py` import `app.models`, trong khi `app/models` import `Base` từ `app.database` | Module database không import models; models được import tại `app/main.py` trước `create_all()` | Import vòng tròn: `ImportError: cannot import name 'Base'` |
| `app/models/__init__.py` định nghĩa lại cả 4 model đã có trong file riêng | `__init__.py` chỉ re-export từ các file model | `InvalidRequestError: Table 'business_guides' is already defined` |
| 4 router dùng `Optional` nhưng không import | Bổ sung `from typing import Optional` | `NameError: name 'Optional' is not defined` |

### 10.3. Mã nguồn – lỗi chức năng và thay đổi hành vi

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Prefix ở `main.py` cộng với route lặp tên nhóm → `/api/v1/guide/guide`, `/api/v1/training/training/documents`, `/api/v1/business-data/business-data/records`, `/api/v1/video/video/resources` | `/api/v1/guide`, `/api/v1/training/documents`, `/api/v1/business-data/records`, `/api/v1/video/resources`; route trong router chỉ còn `""` và `/{id}` | Đường dẫn bị lặp đoạn, không khớp đặc tả |
| Schema `*Update` bắt buộc đủ trường kèm `id`; `id` trong body ghi đè khóa chính; ghi đè được `file_path`/`video_path` tùy ý | Schema `*Update` toàn trường tùy chọn, không có `id` và đường dẫn file | Không cập nhật từng phần được; rủi ro đổi khóa chính và trỏ sai file |
| PUT gửi `null` cho cột NOT NULL gây lỗi 500 | `app/core/crud.py` – `apply_update()` trả 422 | Lỗi máy chủ thay vì lỗi dữ liệu đầu vào |
| Tên file upload `{title}_{hash(tên file)}.{suffix}` → sinh `..pdf`; `hash()` đổi giá trị mỗi lần chạy; tiêu đề đưa vào tên file; thumbnail `{title}_thumbnail` ghi đè nhau | `app/core/storage.py` – tên file = UUID4 + phần mở rộng gốc viết thường | Tên file sai, không ổn định, rủi ro path traversal, mất thumbnail |
| DELETE xóa file trên đĩa trước khi commit CSDL | Commit xóa bản ghi trước, sau đó mới xóa file | Tránh mất file khi commit CSDL thất bại |
| `data_content` bắt buộc trong schema dù cột CSDL cho phép null | Chuyển thành tùy chọn | Schema không khớp CSDL |
| `app/schemas/__init__.py` định nghĩa trùng toàn bộ schema | Chỉ re-export | Loại trùng lặp, tránh hai định nghĩa lệch nhau |
| Cú pháp Pydantic v1 (`class Config: orm_mode = True`, `.dict()`) trên Pydantic 2.5 | `ConfigDict(from_attributes=True)`, `model_dump()` | Cảnh báo deprecated |
| Handler `async def` gọi SQLAlchemy đồng bộ | Đổi sang `def`; chỉ POST có đọc file upload giữ `async` | Truy vấn đồng bộ trong `async` chặn event loop |
| Thư mục upload tạo khi import module | Tạo khi lưu file | Tránh tác dụng phụ khi import (kể cả khi chạy test) |
| POST trả 200 | POST trả 201 Created | Đúng ngữ nghĩa HTTP cho tạo mới |
| Danh sách không có thứ tự xác định | Sắp xếp tăng dần theo `id` | Kết quả ổn định |
| Không kiểm tra độ dài trường | Kiểm tra theo giới hạn cột CSDL; `duration` ≥ 0 | Trả 422 thay vì lưu dữ liệu sai |
| `DATABASE_URL` ghi cứng; đường dẫn upload ghi cứng | Đọc `DATABASE_URL`, `UPLOAD_DIR` từ biến môi trường (`app/core/config.py`), mặc định như cũ | Cho phép test và Docker dùng vị trí dữ liệu riêng |
| `version` `1.0.0` ghi cứng ở 2 nơi | `1.1.0`; endpoint `/` lấy từ `app.version` | Một nguồn duy nhất cho số phiên bản |

### 10.4. Mã nguồn – bổ sung

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Không có test | `tests/` (18 test), `requirements-dev.txt`, `pytest.ini` | Kiểm chứng hành vi đặc tả |
| `Dockerfile` sao chép toàn bộ project, không khai báo volume; không có `.dockerignore`, `docker-compose.yml` | `Dockerfile` chỉ sao chép `app/`, khai báo biến môi trường và volume `/app/data`, `/app/uploads`; thêm `.dockerignore`, `docker-compose.yml` | Image gọn, không mất dữ liệu khi xóa container |
| `.gitignore` không loại CSDL và file upload | Loại `*.db`, `uploads/`, `data/`, `.pytest_cache/` | Không đưa dữ liệu chạy thật vào git |
| `app/api`, `app/api/v1` và các thư mục con không có `__init__.py` | Bổ sung `__init__.py` | Package Python tường minh |
| README tiếng Anh, thiếu 3 nhóm chức năng; có thêm `README-v2.md`, `CHANGELOG.md` | Chỉ còn `README.md` (bản đặc tả này) | Một tài liệu duy nhất |

### 10.5. Chưa xử lý (CẦN XÁC NHẬN)

- Xác thực/phân quyền; giới hạn dung lượng và định dạng file upload.
- Migration CSDL (Alembic). CSDL tạo từ bản cũ vẫn dùng được vì cấu trúc bảng không đổi.
- Thư mục `venv/` cũ (Python 3.15, chưa cài thư viện) chưa xóa.
- Docker chưa được chạy thử.
- Giấy phép (README cũ ghi MIT, không có file `LICENSE`).

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

### 10.7. Hiệu chỉnh đặc tả v2.1 (08/11/2026)

| Nội dung cũ (v2.0) | Nội dung mới (v2.1) | Lý do sửa |
|---|---|---|
| `04` §2, §3.3: phỏng vấn là một loại học liệu video/audio + bản ghi; nghĩa của "phỏng vấn" đánh dấu CẦN XÁC NHẬN | Kho dữ liệu phỏng vấn video/audio với 2 quy trình nhập: (1) phỏng vấn online trên hệ thống, ghi hình/ghi âm, tự lưu vào kho; (2) người hướng dẫn tải file từ tài khoản. Bổ sung phiên phỏng vấn, khách mời không cần tài khoản, xác nhận đồng ý điện tử, xử lý media, hậu kỳ, trạng thái thu nhận; BR-305 (sửa), BR-308–BR-310; LRN-F10–F13; API; 2 phương án kỹ thuật phòng họp | Yêu cầu của chủ dự án (điểm 1) |
| `02` §5: ma trận và quy tắc lưu dạng dữ liệu cấu hình, chủ ghi `manager`; quy tắc R-01…R-14 cài trong code | `02` §5.3: thư viện bộ đánh giá trong code + bộ quy tắc có phiên bản (`md_rule_sets`) phạm vi `production` (`manager`) và `training` (`instructor`); thêm, sửa, xóa dòng ma trận và quy tắc; sao chép, chạy thử, công bố, ngừng, nhập/xuất; R-01…R-14 thành dữ liệu khởi tạo; BR-101 (sửa), BR-102 (sửa), BR-106; OPS-F13; cập nhật `01` §4, `05` (tình huống tham chiếu bộ quy tắc, BR-409), `06` RBAC | Yêu cầu của chủ dự án (điểm 2) |
| Ngưỡng, trọng số KPI, thang điểm là giá trị minh họa | Giữ nguyên | Yêu cầu của chủ dự án (điểm 3) |
| `00` §4, `02` §10: cổng khách hàng CẦN XÁC NHẬN, mặc định không làm; kế toán, công nợ ngoài phạm vi; hợp đồng chỉ có `:close` | `02` §9: cổng khách hàng (PRT-01…PRT-10, API `/api/v2/portal`, tài khoản `customer` có xác thực 2 lớp, BR-111). `02` §3.6: yêu cầu dịch vụ. `02` §3.7: góp ý bản nháp, phụ lục hợp đồng, phiên bản hợp đồng. `02` §3.8–3.9: biểu giá, khoản phí (dịch vụ, chi hộ, phát sinh, giảm trừ SLA, bồi thường), thanh toán, bảng kê đối soát, quyết toán S = A + T + C − D − E − F, trình tự thanh lý và biên bản thanh lý; máy trạng thái hợp đồng thêm `liquidating`; §6.5 bảng quyết toán; BR-107–BR-111; OPS-F14–F18 | Yêu cầu của chủ dự án (điểm 4) |
| Danh mục sự kiện 24 mã | Thêm EV-103, EV-104, EV-113, EV-114; mở rộng EV-102, EV-205, EV-206 (trách nhiệm, bồi thường – Luồng 1 nhận), EV-401 (bộ quy tắc) | Đồng bộ tích hợp giữa các luồng cho các chức năng mới |
| `03` | Loại phê duyệt `EXTRA_CHARGE`, `CONTRACT_AMENDMENT`, `CONTRACT_TERMINATION`, `SETTLEMENT`; thẩm quyền (BR-202); giám sát M-08, M-09; CTL-F10, CTL-F11; sự cố ghi trách nhiệm và giá trị bồi thường | Phê duyệt và kiểm soát các nghiệp vụ hợp đồng – tài chính mới |
| `05` 13 KPI | Thêm KPI-14 Tỷ lệ phản hồi yêu cầu dịch vụ đúng hạn | Đo chất lượng xử lý yêu cầu qua cổng (dùng cho cả vận hành và đào tạo) |
| `07` lộ trình 6 giai đoạn, E2E-01, E2E-02 | Thêm giai đoạn 1b (phỏng vấn online), 4b (thanh lý – quyết toán); E2E-03…E2E-06; thư viện bổ sung | Phản ánh phạm vi mới |

### 10.8. Hiệu chỉnh đặc tả v2.2 (08/11/2026)

| Nội dung cũ (v2.1) | Nội dung mới (v2.2) | Lý do sửa |
|---|---|---|
| `04` §3.3.1: 2 phương án phòng phỏng vấn online (A – Zoom/Meet/Teams; B – WebRTC tự triển khai), CẦN XÁC NHẬN | `04` §3.3.1a: chốt WebRTC tự triển khai – máy chủ media SFU (đề xuất LiveKit, thay thế Jitsi), dịch vụ ghi, TURN/STUN, backend cấp token phòng, kiểm tra đồng ý phía máy chủ; dữ liệu nằm trên hạ tầng đơn vị; hạ tầng xác định bằng PoC. Sửa `lrn_interview_sessions`, API (token phòng, webhook ghi), `06` §6 cấu hình, `07` (giai đoạn 1b là PoC + hoàn thiện, `deploy/`, E2E-06) | Quyết định của chủ dự án (điểm 1) |
| `02` §1: kế toán, hóa đơn ngoài phạm vi, tích hợp phần mềm kế toán CẦN XÁC NHẬN; `ops_contract_payments`; thuế suất, tỷ giá, thuế với chi hộ CẦN XÁC NHẬN | `02` §3.10: sổ thu – chi theo hợp đồng theo kế toán thống kê kiểu đội sản xuất – khoản mục T01–T04, C01–C08 (cấu hình được); phiếu thu/phiếu chi có chứng từ gốc, tỷ giá ghi sổ; tạm ứng – hoàn ứng nhân viên; BR-112–BR-115; báo cáo BC-TC-01…06 (lãi gộp hợp đồng). Không tích hợp phần mềm kế toán và hóa đơn điện tử. Bỏ `ops_contract_payments` (thay bằng phiếu thu/chi); F trong công thức quyết toán lấy từ sổ thu – chi; chốt cách xử lý thuế với chi hộ, giảm trừ, bồi thường; thuế suất do người quản lý khai | Quyết định của chủ dự án (điểm 2) |
| `02` §3.7, §3.9.2: ký số/ký tay CẦN XÁC NHẬN; trạng thái `signed`, file `signed_file_id` | `02` §3.11: quy trình ký tay – gửi file scan – xác nhận: sinh văn bản từ mẫu có mã tham chiếu và số trang, ký tay, tải scan, kiểm tra số trang, bên kia đối chiếu và xác nhận; `ops_signed_documents`, `md_document_templates`; BR-116–BR-118; API nội bộ và cổng | Quyết định của chủ dự án (điểm 3) |
| `03`: phê duyệt một cấp theo thẩm quyền, ngưỡng CẦN XÁC NHẬN; người hướng dẫn chỉ thay vai quản lý ở `training`; câu hỏi người thứ hai duyệt bộ quy tắc | `03` viết lại: vai trò `business_manager` (người quản lý hệ thống kinh doanh – người phê duyệt thứ hai); chính sách phê duyệt nhiều cấp cấu hình được (`ctl_approval_policies`, `ctl_approval_decisions`), bảng chính sách mặc định; người hướng dẫn với vai trò `coordinator` giám sát, điều phối tiến trình dịch vụ (bảng điều phối CTL-F12, `ctl_coordination_actions`, EV-207); BR-202 (sửa), BR-205, BR-207, BR-209, BR-210; M-10; CTL-F13, CTL-F14. Công bố bộ quy tắc `production` qua phê duyệt `RULESET_PUBLISH` cấp 2 | Quyết định của chủ dự án (điểm 4) |
| Sự kiện | Thêm EV-115, EV-116 (tạm ứng), EV-207 (chỉ đạo điều phối); mở rộng EV-111 (`is_checkpoint`), EV-134 (`contract_id`, `amount`), EV-203 (quyết định các cấp) | Đồng bộ tích hợp |
| `00`, `06` | Vai trò `business_manager`; mô tả lại `instructor`, `manager`, `ops_staff`; thuật ngữ mới; RBAC thêm cột `business_manager`, dòng sổ thu – chi; xác thực 2 lớp cho `business_manager`, xác thực lại khi duyệt cấp 2 | Đồng bộ phân quyền |
| `07` | Thêm E2E-07 (thu – chi, tạm ứng), E2E-08 (giám sát, điều phối); sửa E2E-03, E2E-04, E2E-06; lộ trình giai đoạn 2, 3 bổ sung phạm vi | Phản ánh phạm vi mới |

### 10.9. Hiệu chỉnh đặc tả v2.3 (08/11/2026)

| Nội dung cũ (v2.2) | Nội dung mới (v2.3) | Lý do sửa |
|---|---|---|
| `03` §1.1, BR-207, BR-209: người hướng dẫn (`coordinator`) giám sát, điều phối cả hợp đồng `production` (CẦN XÁC NHẬN); hợp đồng `training` phê duyệt một cấp | Người hướng dẫn **chỉ** giám sát, điều phối hợp đồng thực hành của lớp mình; hợp đồng `training` đi qua **đúng quy trình của hợp đồng thật** (cùng chính sách phê duyệt nhiều cấp, cùng ngưỡng, cùng quy tắc giám sát); người học đóng vai trong kế hoạch (`is_role_play`), người hướng dẫn giữ vai `coordinator` và `business_manager_proxy`; bảng ánh xạ vai §3.2.1; BR-201, BR-202 (ngoại lệ một người hướng dẫn), BR-205, BR-207, BR-209 viết lại; BR-211, CTL-F15 mới; giám sát – điều phối hợp đồng thật do `account_manager`. Cập nhật `00` §4, `02` BR-107, `06` RBAC, `07` E2E-08 | Quyết định của chủ dự án (điểm 1) |
| `03` §3.2: chính sách phê duyệt do `business_manager` cấu hình; ngưỡng X, Y, Z CẦN XÁC NHẬN | Ngưỡng X, Y, Z là **giá trị giả định do `admin` nhập** (`ctl_approval_thresholds`, có lịch sử); chính sách phê duyệt do `admin` cấu hình, `business_manager` xem; chưa có ngưỡng → áp cấp 2 cho mọi giá trị; API `/approval-thresholds`; RBAC thêm dòng chính sách – ngưỡng | Quyết định của chủ dự án (điểm 2) |
| `02` §3.10.5, §11: phân bổ chi phí chung CẦN XÁC NHẬN | **Không phân bổ** chi phí chung; kết quả hợp đồng dừng ở lãi gộp trên chi phí trực tiếp | Quyết định của chủ dự án (điểm 3) |
| `04` §3.3.1, §9; `06` `TRANSCRIPTION_BACKEND`: dịch vụ chuyển giọng nói thành văn bản CẦN XÁC NHẬN | `04` §3.3.1b: nhận dạng tiếng nói **đặt tại máy chủ đơn vị** (mô hình mã nguồn mở hỗ trợ tiếng Việt; ứng viên họ Whisper/`faster-whisper`, PhoWhisper; chọn sau PoC đo WER và thời gian xử lý), `TranscriptionEngine`, từ điển thuật ngữ chuyên ngành, chạy trong mạng nội bộ; `06` biến `TRANSCRIPTION_ENGINE`, `TRANSCRIPTION_MODEL_PATH`, `TRANSCRIPTION_DEVICE`; `07` giai đoạn 1b bổ sung PoC nhận dạng tiếng nói, hạ tầng máy chủ nhận dạng | Quyết định của chủ dự án (điểm 4) |

### 10.10. Chốt nền tảng kỹ thuật trước Giai đoạn 0 (08/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `07` §2: Python 3.12 – CẦN XÁC NHẬN | Python 3.12: `.python-version`, `python:3.12-slim`, `requires-python = ">=3.12,<3.13"` | Quyết định của chủ dự án (điểm 1) |
| `07` §2: phiên bản thư viện CẦN XÁC NHẬN | Chốt cứng `==` trong `pyproject.toml` (FastAPI 0.162.2, Pydantic 2.14.3, SQLAlchemy 2.0.57, Alembic, Uvicorn, psycopg, python-jose, passlib); `requirements-lock.txt` từ `pip freeze` | Quyết định của chủ dự án (điểm 2) |
| `07` §2: `PyJWT`, `argon2-cffi` | `python-jose[cryptography]`, `passlib[argon2]` | Quyết định của chủ dự án (điểm 2) |
| `07` §2, `06` §6: SQLite cho dev/test, PostgreSQL môi trường thật – CẦN XÁC NHẬN | PostgreSQL 17 cho dev/test/prod (`logistics_3pl_dev`, `logistics_3pl_test`) qua `docker-compose`; SQLite chỉ cho test đơn vị không đụng CSDL; thêm biến `TEST_DATABASE_URL` | Quyết định của chủ dự án (điểm 3) |
| `06` §3: xác thực 2 lớp ngay từ đầu | Hoãn tới cổng khách hàng; bước 0.6 làm JWT + refresh + RBAC + khóa tạm; có sẵn cột `totp_secret`, `is_2fa_enabled` | Quyết định của chủ dự án (điểm 4) |
| `07` §1: tên repo `fastapi-todo` – CẦN XÁC NHẬN | `logistics-3pl-platform`; xóa `_to_delete/` (đã lưu trong commit `3600a61`) và `venv/` | Quyết định của chủ dự án (điểm 5) |

### 10.11. Mã nguồn – Giai đoạn 0, bước 0.0–0.1 (08/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `requirements.txt`, `requirements-dev.txt`, `pytest.ini` | `pyproject.toml` (chốt cứng `==`, nhóm `dev`, cấu hình pytest, ruff), `requirements-lock.txt`, `.python-version` | `07` §1, §2; mục 10.10 |
| Mã v1 tại `app/api/v1`, `app/core`, `app/database`, `app/models`, `app/schemas` | Chuyển nguyên vào `app/legacy_v1/` (giữ lịch sử git); `/api/v1/*` đánh dấu `deprecated` trên OpenAPI; ruff bỏ qua `app/legacy_v1` | `07` §1, §3.3; `06` §1 |
| CSDL mặc định SQLite `./business_guide.db`; test trên file SQLite tạm | Mặc định PostgreSQL `logistics_3pl_dev`; test trên `logistics_3pl_test` (`TEST_DATABASE_URL`, chặn chạy nếu tên CSDL không kết thúc `_test`); dọn thư mục tạm sau phiên test | Mục 10.10 (điểm 3) |
| `docker-compose.yml` chỉ có dịch vụ `api`, dữ liệu SQLite trong `./data/` | Thêm dịch vụ `db` (`postgres:17`, healthcheck, volume `pgdata`, script tạo CSDL test); `api` chờ `db` sẵn sàng; Dockerfile `python:3.12-slim`, cài từ `requirements-lock.txt` | Mục 10.10; đã chạy thử `docker compose up` |
| `status.HTTP_422_UNPROCESSABLE_ENTITY` | `status.HTTP_422_UNPROCESSABLE_CONTENT` | Hằng số cũ deprecated trong Starlette 1.x |

### 10.12. Chốt lại thư viện JWT, băm mật khẩu; khóa phụ thuộc dev (08/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `07` §2 (mục 10.10): `python-jose[cryptography]==3.5.0`, `passlib[argon2]==1.7.4` | `pyjwt==2.15.1`, `argon2-cffi==25.1.0` (quay về đặc tả gốc); gỡ `python-jose`, `passlib`, `ecdsa` cùng các gói bắc cầu chỉ chúng dùng (`rsa`, `pyasn1`, `six`, `cryptography`) khỏi `requirements-lock.txt` | `passlib` ngừng bảo trì từ 2020, dựa vào module `crypt` đã bị gỡ ở Python 3.13; `python-jose` kéo theo `ecdsa` dính CVE-2024-23342 – quyết định của chủ dự án |
| Phụ thuộc dev chỉ chốt `==` trong `pyproject.toml`, phụ thuộc bắc cầu không khóa; cài `pip install -r requirements-lock.txt --group dev` | Thêm `requirements-dev-lock.txt` (pip freeze phụ thuộc chạy + nhóm `dev` trên venv sạch); máy dev cài từ file này | Khóa đủ cả môi trường dev – quyết định của chủ dự án |
| – | Giữ `POSTGRES_PASSWORD` mặc định `logistics` trong `docker-compose.yml`, chỉ dùng cho máy dev, không dùng cho môi trường thật | Quyết định của chủ dự án |

### 10.13. Mã nguồn – Giai đoạn 0, bước 0.2: cấu hình (08/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `app/legacy_v1/core/config.py` đọc `os.getenv` (`DATABASE_URL`, `UPLOAD_DIR`) | `app/core/config.py`: `Settings` (pydantic-settings 2.16.2) đủ biến `06` §6, kiểm tra kiểu/giá trị khi khởi động, `get_settings()` có cache; mã v1 lấy `DATABASE_URL`, `UPLOAD_DIR` từ đây | `07` §1; `06` §6 |
| `JWT_SECRET` chưa được đọc | Bắt buộc, ≥ 32 ký tự, kiểu `SecretStr` (không lộ khi in/log); `docker-compose.yml` truyền `JWT_SECRET` từ `.env` vào dịch vụ `api`; thêm `.env.example`; `.dockerignore` loại `.env` khỏi image | `06` §6 |
| `UPLOAD_DIR` mặc định `uploads`; Docker `/app/uploads`, `docker-compose.yml` gắn `./uploads:/app/uploads` | Mặc định `./data/uploads` theo `06` §6; Dockerfile `UPLOAD_DIR=/app/data/uploads`, `docker-compose.yml` gắn `./data/uploads:/app/data/uploads` | Thống nhất với đặc tả; volume khớp `UPLOAD_DIR` |
| `06` §6: `SMTP_*` | `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`; thêm ghi chú cách đọc biến (ưu tiên môi trường hơn `.env`, danh sách cách nhau bằng dấu phẩy) | Cụ thể hóa tên biến |
| – | `tests/core/test_config.py`: 17 test đơn vị (mặc định, bắt buộc `JWT_SECRET`, giá trị sai bị từ chối, đọc `.env`) | `07` §4.1 |
| `app/main.py` tạo `app = FastAPI(...)` ở cấp module, gọi `Base.metadata.create_all()` lúc import | `create_app()` (07 §1): CORS theo `CORS_ORIGINS`, middleware `X-Request-ID`, exception handler chung, `/health`, gắn router v1 bên trong hàm; lifespan kiểm tra kết nối CSDL, `create_all` bảng v1 (tạm, tới khi có Alembic – bước 0.3), đóng pool khi tắt. Chạy: `uvicorn app.main:create_app --factory` | `07` §1 |
| Lỗi trả `{"detail": ...}` của FastAPI | `app/core/errors.py`: mọi lỗi 4xx/5xx (kể cả `/api/v1/*`, lỗi validation, lỗi không xử lý được) trả `{"error": {code, message, details, request_id}}`; `AppError` cho mã mới; lỗi 500 không lộ chi tiết, ghi log kèm `request_id` | `06` §2.2 |
| – | `tests/core/test_create_app.py`: 12 test (`/health`, CORS, `X-Request-ID`, định dạng lỗi 404/422/409/500) | `07` §4.1 |

### 10.13a. Rà soát lại bước 0.2 – sửa lỗi (08/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `CORS_ORIGINS=*` được chấp nhận trong khi CORS bật `allow_credentials` | Từ chối `*` khi khởi động; phải liệt kê từng nguồn | Bảo mật: `*` + credentials cho mọi trang web gọi API kèm thông tin đăng nhập |
| `JWT_SECRET` toàn khoảng trắng (≥ 32 ký tự) được chấp nhận | Yêu cầu ≥ 32 ký tự không phải khoảng trắng | Bảo mật |
| Lỗi 500 không có header CORS (handler chạy ngoài lớp CORS) | `UnhandledErrorMiddleware` bắt lỗi bên trong lớp CORS, request_id | Frontend đọc được lỗi và `request_id` |
| JSON hỏng trả 422 | Trả 400 (`VALIDATION_ERROR`, "Request sai cú pháp") | `06` §2.1 |
| `message` mặc định tiếng Anh ("Not Found", "Method Not Allowed") | Tiếng Việt theo mã lỗi; giữ `detail` riêng của nơi ném lỗi (vd. mã v1) | `06` §2.2 |
| `HEAD /health` trả 405 | `/health` nhận `GET`, `HEAD` | Công cụ healthcheck dùng `HEAD` |
| `.gitignore`, `.dockerignore` có dòng `uploads/` (thư mục cũ, đã xóa) | Bỏ; dữ liệu upload nằm trong `data/` (đã có trong cả hai file) | Dọn phần sót của v1.1.0 |
| – | 7 test hồi quy (`test_config.py` +2, `test_create_app.py` +5) | `07` §4.1 |

### 10.14. Mã nguồn – Giai đoạn 0, bước 0.3: Alembic (08/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Lifespan gọi `Base.metadata.create_all()` | Lifespan gọi `app/core/migrations.py` `upgrade_to_head()`: `alembic upgrade head` dưới khóa advisory PostgreSQL (nhiều worker/container khởi động cùng lúc chỉ một tiến trình chạy migration); lỗi migration thì ứng dụng dừng | `07` §1, §2 (Alembic quản lý schema) |
| – | `alembic.ini`, `migrations/` (URL lấy từ `DATABASE_URL` của Settings, không ghi trong file); revision gốc `0001_v1_baseline`: 4 bảng v1.1.0 đúng như `create_all` đã tạo (chưa sửa các điểm lệch `06` §2.4 – làm ở migration sau) | `07` §1 |
| CSDL đã tạo bằng `create_all` không có lịch sử migration | Có đủ 4 bảng v1, chưa có `alembic_version` → tự đánh dấu (stamp) `0001`, không tạo lại bảng, giữ dữ liệu; chỉ có một phần bảng → dừng, báo lỗi để kiểm tra thủ công. Đã chạy trên `logistics_3pl_dev`: `0001 (head)`, `alembic check` không lệch | Chuyển tiếp không mất dữ liệu |
| Test: `drop_all`/`create_all` trước mỗi test | Mỗi phiên test: xóa schema `public` của CSDL test rồi `upgrade head`; mỗi test `TRUNCATE … RESTART IDENTITY`. `tests/test_migrations.py`: 8 test (1 head, upgrade từ trống, schema khớp model, downgrade base rồi upgrade lại, chạy lặp không đổi, stamp CSDL `create_all` giữ dữ liệu, từ chối schema thiếu bảng, lifespan tự migrate) | `07` §6 điểm 3 |
| Docker image chỉ chép `app/` | Chép thêm `alembic.ini`, `migrations/` | Ứng dụng trong container tự migrate; dùng được `alembic` CLI |
| `tests/conftest.py` đọc `TEST_DATABASE_URL` bằng `os.getenv` (bỏ qua file `.env`) | Đọc qua `Settings` (biến môi trường + `.env`) | Đặt `TEST_DATABASE_URL` trong `.env` (vd. đổi cổng) bị lặng lẽ bỏ qua, test chạy vào CSDL mặc định – phát hiện khi kiểm chứng fastapi-template |

### 10.15. Mã nguồn – Giai đoạn 0, bước 0.6: xác thực, phân quyền (08/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Chưa có xác thực; `JWT_SECRET` chỉ được đọc | `app/core/security.py` (Argon2id – `argon2-cffi`; JWT HS256 – `PyJWT`; refresh token ngẫu nhiên lưu SHA-256), `app/core/rbac.py` (`Role` – 8 vai trò `00` §4, `require_roles`), `app/shared/identity/` (model, service, router, CLI) | `06` §3, §4; `07` §1 |
| – | Migration `0002`: `sys_users` (cột chung `06` §2.4, khóa tạm, `totp_secret` NULL, `is_2fa_enabled` = false), `sys_user_roles` (CHECK 8 vai trò), `sys_refresh_tokens` | `01` §4; lộ trình 0.6 (`06` §3) |
| – | API: `POST /api/v2/auth/{login,refresh,logout,change-password}`, `GET /api/v2/auth/me`; `GET/POST /api/v2/users`, `GET/PATCH /api/v2/users/{id}` (`If-Match` → `409 VERSION_CONFLICT`), `POST /api/v2/users/{id}:unlock` – chỉ `admin` | `06` §1 (thêm `/api/v2/users`), §2.1–2.4, §4 |
| `06` §3: khóa tạm sau 5 lần sai (chỉ nêu cho `customer`) | Áp dụng mọi tài khoản; thời gian khóa 15 phút; cấu hình `AUTH_MAX_FAILED_LOGINS`, `AUTH_LOCKOUT_MINUTES` | Lộ trình 0.6 "khóa tạm sau nhiều lần đăng nhập sai"; thời gian khóa **CẦN XÁC NHẬN** |
| `app/legacy_v1/database.py` tạo engine riêng | Dùng chung `engine` của `app/core/db.py` (một pool); `Base` v1 giữ riêng; Alembic so cả hai `MetaData` | Một pool kết nối; `Base` v2 có quy ước tên ràng buộc |
| – | `app/core/db.py` (`Base`, `AuditColumnsMixin` – `row_version` là `version_id_col`), `app/core/pagination.py` (`Page[T]`, `page_size` ≤ 100, `sort`), `app/core/timeutil.py` (`now_utc`, xuất ISO 8601 hậu tố `Z`) | `06` §2.3, §2.4; `07` §1 |
| `/health` một route `GET` + `HEAD` (bước 0.2 – sửa lỗi) | Hai route; `HEAD` ẩn khỏi OpenAPI | Một route hai phương thức sinh trùng `operation_id` trên OpenAPI |
| – | 98 test mới (31 đơn vị `security`, `rbac`; 67 tích hợp PostgreSQL – đăng nhập, khóa tạm gồm đăng nhập sai song song, refresh xoay vòng/dùng lại, đổi mật khẩu, RBAC 7 vai trò không phải admin, CRUD người dùng, CLI); tổng 160 | `07` §4.1 |

### 10.16. Mã nguồn – Giai đoạn 1: danh mục khách hàng, đối tác `md_parties` (08/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `01` §4: `md_parties` chỉ nêu loại đối tác, `party_type`, mã số thuế | `app/shared/masterdata/`: bảng `md_parties` (8 `party_type`, `tax_code` duy nhất – kiểm định dạng MST Việt Nam khi `country_code = VN`, liên hệ, `status` `active`/`archived`); danh sách trường **CẦN XÁC NHẬN** | `01` §4; `06` §2.4, §2.5 |
| – | API `/api/v2/md/parties`: danh sách (`q`, `party_type`, `country_code`, `status`; `06` §2.3), tạo, xem, PATCH (`If-Match`), `:archive`, `:restore`; không có DELETE | `06` §1, §2.1, §2.5 |
| – | Quyền: đọc – 7 vai trò nội bộ; ghi – `manager`, `admin`; `customer` không truy cập (`403`) | `06` §4 (danh mục `md_*`) |
| `02` §9.2: tài khoản `customer` gắn một `md_parties.id`, cờ `is_customer_admin` | `sys_users.party_id` (FK → `md_parties`, RESTRICT), `is_customer_admin` (CHECK → có `party_id`). Tài khoản `customer`: bắt buộc gắn party loại `customer` đang hoạt động, **không kèm vai trò khác**; tài khoản nội bộ không gắn party; PATCH không đổi được nội bộ ↔ customer | `02` §9.2; tách dữ liệu khách hàng (BR-111) |
| `06` §4: `manager` tạo tài khoản `customer` "cho khách hàng của hợp đồng" | `POST /api/v2/md/parties/{id}/users` (`manager`, `admin`): vai trò luôn `customer`, body không nhận `roles`/`party_id`; `GET …/users` xem tài khoản của khách hàng. Chưa có hợp đồng (Luồng 1) nên chưa kiểm "khách hàng của hợp đồng có `portal_enabled`" – bổ sung khi làm hợp đồng | `06` §4; **CẦN XÁC NHẬN** điều kiện khi chưa có hợp đồng |
| – | Không lưu trữ được khách hàng còn tài khoản `customer` hoạt động; không đổi `party_type` khỏi `customer` khi đã có tài khoản; tạo tài khoản (`FOR SHARE`) và lưu trữ (`FOR UPDATE`) khóa dòng party – không lọt khi chạy song song | Nhất quán tài khoản ↔ khách hàng |
| – | Migration `0003` (CHECK trên `sys_users` viết tay – autogenerate không phát hiện CHECK thêm vào bảng có sẵn); `app/core/versioning.py` (`ETag`/`If-Match` dùng chung), `like_pattern` chuyển vào `app/core/pagination.py` | `07` §6 điểm 3 |
| – | 66 test mới (49 `md_parties`, 17 tài khoản customer – gồm RBAC 9 vai trò, ràng buộc CSDL, tạo/lưu trữ song song); tổng 226 | `07` §4.1 |

Chưa làm (ngoài phạm vi bước này): tài khoản `is_customer_admin` mời, khóa tài khoản cùng khách hàng (cổng khách hàng – Giai đoạn 2); 2FA bắt buộc cho `customer` (`06` §3, Giai đoạn 2); nhập `md_parties` từ `business_data_records` v1 (`07` §3).

### 10.17. Mã nguồn – Giai đoạn 2: nhật ký thao tác, giới hạn tần suất, customer admin (08/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `06` §5: `sys_audit_logs` (danh sách trường) | `app/shared/audit/`: ghi cùng giao dịch; trường cho phép; `before`/`after` chỉ trường thay đổi; trigger chặn `UPDATE`/`DELETE`; `ip`, `request_id` từ ngữ cảnh request (`app/core/request_context.py`, middleware). Ghi: party (tạo/sửa/lưu trữ/khôi phục), tài khoản (tạo/sửa/khóa/mở/đặt lại – đổi mật khẩu/tự khóa tạm/mở khóa), đăng nhập thất bại (kèm lý do), vượt giới hạn tần suất, dùng lại refresh token | `06` §5 |
| `06` §3: "giới hạn tần suất gọi API" (không nêu ngưỡng, cách làm) | `app/core/rate_limit.py`: bộ đếm cửa sổ cố định trong PostgreSQL; `POST /api/v2/auth/login` 20/IP, 10/tên đăng nhập mỗi 60 giây (cấu hình được); `429 RATE_LIMITED` + `Retry-After`; kiểm tra trước khi băm mật khẩu | `06` §3; ngưỡng **CẦN XÁC NHẬN** |
| `06` §2.2: danh mục mã lỗi không có mã cho `429` | Thêm `RATE_LIMITED` | Giới hạn tần suất cần mã lỗi riêng |
| `02` §9.2: `is_customer_admin` "được mời, khóa tài khoản khác của cùng khách hàng" | `/api/v2/portal/users`: xem, mời (vai trò luôn `customer`, không cấp `is_customer_admin`), `:deactivate` (tài khoản khác cùng khách hàng); ngoài phạm vi → `404`; thu hồi cờ `is_customer_admin` có hiệu lực ngay | `02` §9.2; `06` §4; chi tiết **CẦN XÁC NHẬN** (khóa được customer admin khác; mở lại chỉ `admin`; mời qua email chờ SMTP) |
| `identity.service`: vô hiệu hóa tài khoản chỉ qua PATCH của admin | `deactivate_user()` dùng chung (admin, customer admin): thu hồi phiên + nhật ký | Một đường xử lý |
| – | Migration `0004`: `sys_audit_logs` (+ trigger, function viết tay – autogenerate không sinh trigger), `sys_rate_limit_counters` | `07` §6 điểm 3 |
| – | 39 test mới (9 nhật ký – gồm không lộ mật khẩu/hash/token, bất biến, lỗi không để lại dòng; 5 bộ đếm – gồm 40 lượt song song đếm đúng; 4 giới hạn đăng nhập theo IP/tên đăng nhập; 21 customer admin – gồm không chạm khách hàng khác, tài khoản nội bộ); tổng 265 | `07` §4.1 |

### 10.18. Mã nguồn – Giai đoạn 3: hợp đồng dịch vụ, `portal_enabled`, API đọc nhật ký (08/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `02` §3.1, §6.1: `ops_service_contracts` (đầy đủ trường, máy trạng thái) | `app/flows/operations/` (Luồng 1 – module đầu tiên): phần lõi hợp đồng, `contract_no` `HD-yyyy-nnnn` (`app/core/numbering.py`, `sys_doc_sequences`); `draft` → `active` ⇄ `suspended` → `expired`; API `/api/v2/ops/service-contracts` (danh sách, tạo, xem, PATCH nháp với `If-Match`, `:activate`, `:suspend`, `:resume`); gắn router khi `ops` ∈ `ENABLED_FLOWS` | `02` §3.1, §6.1, §8; phần chưa làm ghi tại `02` §3.1, §6.1 |
| `02` §6.1: kích hoạt cần biểu giá, `contract_file_id` | Tạm thời: `signed_date`, `valid_to ≥ hôm nay`, khách hàng đang hoạt động | Chưa có `02` §3.8, §3.11 – **CẦN XÁC NHẬN** |
| `06` §4: `manager` tạo tài khoản `customer` "cho khách hàng của hợp đồng" (Giai đoạn 1: chưa kiểm hợp đồng) | Cần hợp đồng `active`, đang trong hạn, `portal_enabled` – áp dụng cho `manager` và customer admin; `admin` không bị giới hạn | `06` §4; `02` §9.2 |
| `app/shared/masterdata` không biết tới hợp đồng | Điểm móc `CUSTOMER_ACCOUNT_GUARDS`, `ARCHIVE_GUARDS`, `TYPE_CHANGE_GUARDS`; Luồng 1 đăng ký trong `create_app()` – `shared` không import `flows` | `01` §3 (N4) |
| `06` §5: chưa có API đọc nhật ký | `GET /api/v2/audit-logs`, `/{id}` (`admin`; lọc theo hành động/tiền tố, đối tượng, người thao tác, `request_id`, khoảng thời gian) | Yêu cầu Giai đoạn 3 |
| – | `app/core/timeutil.py`: `today_vn()` (UTC+7 cố định – không phụ thuộc tzdata) | Ngày hiệu lực theo giờ Việt Nam (`06` §2.4) |
| – | Migration `0005`: `ops_service_contracts`, `sys_doc_sequences` | `07` §6 điểm 3 |
| – | 77 test mới (37 hợp đồng – gồm RBAC 8 vai trò, 20 hợp đồng tạo song song không trùng số, tự hết hạn; 18 điều kiện cổng – gồm tạm dừng hợp đồng song song với tạo tài khoản; 22 API nhật ký); 10 test cũ cập nhật (tạo tài khoản qua manager/customer admin giờ cần hợp đồng); tổng 342 | `07` §4.1 |

### 10.19. Mã nguồn – Giai đoạn 4: chi tiết hợp đồng, phiên bản, file, thanh lý/đóng, cổng xem hợp đồng (08/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `02` §3.1, §3.8: phạm vi, SLA, biểu giá, phiên bản (chưa làm ở Giai đoạn 3) | `ops_contract_scopes`, `ops_contract_sla`, `ops_contract_rates` theo phiên bản (tiền `NUMERIC(18,2)`, trả về dạng chuỗi); `ops_contract_versions` (bản chụp); PATCH nháp nhận mảng (thay toàn bộ – JSON Merge Patch); `GET …/versions`, `…/versions/{n}` | `02` §3.1, §3.8; `06` §2.1, §2.4 |
| `02` §3.7: thay đổi khi hiệu lực qua phụ lục (đề nghị → đàm phán → phê duyệt Luồng 2 → ký → xác nhận) | Tạm thời `POST …/versions` (`manager`, kèm file `amendment`): `contract_version + 1`, mang sang bảng con không đổi, bản chụp, nhật ký `contract.new_version` | Chưa có Luồng 2 – **CẦN XÁC NHẬN** |
| `02` §3.1: `contract_file_id` → `sys_files` | `ops_contract_attachments` (nhiều file/hợp đồng, `kind`, `customer_visible`) + `sys_files`; `POST/GET …/attachments` | Hợp đồng có nhiều loại file (hợp đồng, phụ lục, biên bản thanh lý) |
| `06` §2.6: quy định file (chưa có mã) | `app/shared/files/` (`sys_files`, `LocalStorage`, nhận dạng magic bytes, `GET /api/v2/files/{id}` kiểm quyền theo chủ sở hữu) | `06` §2.6; `07` §1 |
| `02` §6.1: kích hoạt cần biểu giá, `contract_file_id` (Giai đoạn 3: chưa kiểm) | Kiểm ≥ 1 phạm vi, có biểu giá trong phạm vi, có file `signed_contract` | `02` §6.1; xác nhận bản scan (§3.11) **CẦN XÁC NHẬN** |
| `02` §3.9.2, §6.1: thanh lý → đóng (cần quyết toán, biên bản đã xác nhận) | `:start-liquidation`, `:close` (`manager`; đóng cần file `liquidation_minutes`); hợp đồng `closed` chỉ đọc – service + trigger CSDL trên hợp đồng và mọi bảng con | Yêu cầu Giai đoạn 4; BR-110, BR-116, phê duyệt `CONTRACT_TERMINATION` **CẦN XÁC NHẬN** |
| PRT-01 (cổng – chưa làm) | `/api/v2/portal/contracts` (+ `/{id}`, `/versions`, `/attachments`): chỉ hợp đồng của khách hàng mình (BR-111), không có trường nội bộ | `02` §9.3, §9.4 |
| `tests/conftest.py` xóa thư mục tạm trong fixture phiên | `atexit` – `pytest --co` không để lại thư mục `l3pl_test_*` | Phát hiện 26 thư mục tạm bị bỏ lại |
| – | Migration `0006` (6 bảng, 4 cột, 2 function + 6 trigger viết tay) | `07` §6 điểm 3 |
| – | 84 test mới (32 chi tiết/phiên bản/thanh lý/đóng – gồm 8 câu SQL bị trigger chặn; 15 file hợp đồng – gồm Range, 413/415/422, không file mồ côi khi lỗi CSDL; 24 kho lưu trữ/nhận dạng; 13 cổng xem hợp đồng); 4 test cũ cập nhật (kích hoạt cần phạm vi, biểu giá, file); tổng 426 | `07` §4.1 |

### 10.20. Mã nguồn – Giai đoạn 5: xác thực 2 lớp, SMTP, thư mời, quên/đặt lại mật khẩu (08/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `06` §3: `customer` bắt buộc 2FA (Giai đoạn 0–4: chỉ có cột `totp_secret`, `is_2fa_enabled`) | TOTP RFC 6238 (`app/core/totp.py`, thư viện chuẩn – không thêm `pyotp`); đăng nhập 2 bước (`mfa_token`); bắt buộc theo `AUTH_2FA_REQUIRED_ROLES` (mặc định `customer`); claim `amr`, refresh token `mfa`; bật/tắt; `admin` xóa 2FA; mã sai tính vào khóa tạm, chặn dùng lại mã | `06` §3; `02` §9.2 |
| – | `totp_secret` lưu dạng rõ | Chưa có `cryptography` – **CẦN XÁC NHẬN** thêm thư viện để mã hóa |
| Giai đoạn 1–2: `manager`, customer admin tạo tài khoản customer **kèm mật khẩu ban đầu** | Không mật khẩu, `email` bắt buộc; thư mời có đường dẫn đặt mật khẩu (72 giờ, dùng một lần); `:resend-invite`; `invitation_sent` trong phản hồi | Yêu cầu Giai đoạn 5 (thay quyết định tạm ở 10.17) |
| – | Quên/đặt lại mật khẩu qua email (30 phút, dùng một lần; không lộ tài khoản; giới hạn tần suất; thu hồi phiên, mở khóa tạm) | Yêu cầu Giai đoạn 5 |
| `SMTP_*` có trong cấu hình, chưa dùng | `app/shared/notifications/mail.py`: `smtp` (STARTTLS/SSL, timeout), `console` (máy dev), `memory` (test); thêm `SMTP_SECURITY`, `SMTP_TIMEOUT_SEC`, `MAIL_BACKEND`, `PUBLIC_BASE_URL` | `06` §6 |
| – | Migration `0007`: `sys_user_tokens`; `sys_users.totp_enabled_at`, `totp_last_step`; `sys_refresh_tokens.mfa`. Nâng cấp hệ thống đang chạy: customer phải đăng nhập lại và thiết lập 2FA | `07` §6 điểm 3 |
| – | 55 test mới (16 TOTP – vector RFC 4226/6238; 15 2FA – gồm mfa_token không thay access token, token chỉ có mật khẩu, refresh cũ, dùng lại mã, 5 mã sai khóa tạm; 24 email – gồm token chỉ lưu hash, dùng một lần, hết hạn, sai mục đích, không lộ tài khoản, lỗi SMTP, SMTP giả lập); fixture `login` đi hết bước 2FA; 22 test cũ cập nhật (tạo tài khoản customer không kèm mật khẩu); tổng 481 | `07` §4.1 |

### 10.21. Mã nguồn – Giai đoạn 6: mã hóa totp_secret, gửi email bằng tác vụ nền (09/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `totp_secret` lưu dạng rõ (Giai đoạn 5, **CẦN XÁC NHẬN**) | Mã hóa Fernet (`app/core/secretbox.py`) bằng khóa riêng `TOTP_ENCRYPTION_KEY` (bắt buộc, kiểm đúng dạng khóa Fernet, không trùng `JWT_SECRET`; nhiều khóa để xoay khóa); chỉ giải mã lúc kiểm mã; sai khóa → `500`, không tính nhập sai mã | Yêu cầu Giai đoạn 6 – bảo mật |
| Phụ thuộc chạy không có `cryptography` | Thêm `cryptography==50.0.2` (`pyproject.toml`, hai file lock tạo lại trong venv sạch – chỉ thêm dòng này); `07` §2 cập nhật | Lý do bảo mật (Fernet) |
| Gửi thư đồng bộ trong request; `invitation_sent` trong phản hồi tạo tài khoản; `:resend-invite` trả `200 {"invitation_sent": …}` | Thư mời, đường dẫn đặt lại mật khẩu, thư báo mật khẩu đã đổi (mới) gửi bằng FastAPI `BackgroundTasks` sau khi trả response; lỗi → log + nhật ký `user.mail_failed` (thay `user.invite_failed`); tạo tài khoản trả `UserOut` (bỏ `invitation_sent`); `:resend-invite` trả `202 {"message": …}` | Yêu cầu Giai đoạn 6; quên mật khẩu không còn lộ tài khoản tồn tại qua thời gian phản hồi |
| – | Migration `0008`: `totp_secret` VARCHAR(64) → VARCHAR(255), mã hóa secret hiện có, CHECK `ck_sys_users_totp_secret_encrypted`; downgrade giải mã lại. Nâng cấp: đặt `TOTP_ENCRYPTION_KEY` trước khi khởi động | `07` §6 điểm 3 |
| – | 20 test mới (3 mã hóa/xoay khóa; 8 cấu hình khóa; 3 2FA – CSDL chỉ có bản mã, CHECK chặn dạng rõ, sai khóa không khóa tài khoản; 5 gửi nền – hàm nghiệp vụ chỉ xếp thư, lỗi gửi/lỗi bất ngờ/lỗi ghi nhật ký không làm lỗi request, thư báo đổi mật khẩu; 1 migration dữ liệu 0007 → 0008 → 0007); 7 test cũ cập nhật | `07` §5 |

### 10.22. Mã nguồn – Giai đoạn 7: hàng đợi bền vững, giới hạn tần suất mọi API, build Docker (09/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Thư gửi bằng `BackgroundTasks` – mất khi tiến trình dừng, không thử lại; token cấp trong request | `app/core/jobs.py` (rq + Redis, `JOB_QUEUE_URL`), worker `python -m app.worker` (kèm bộ lập lịch); tác vụ `deliver_invitation` / `deliver_password_reset` / `deliver_password_changed` chỉ mang id, **cấp token lúc gửi**; thử lại `JOB_MAX_RETRIES` lần theo `JOB_RETRY_INTERVALS`; hết lượt → `user.mail_failed` (thêm `attempts`) + FailedJobRegistry. Không có `JOB_QUEUE_URL` / Redis lỗi lúc xếp: chạy nền trong tiến trình như Giai đoạn 6 | Yêu cầu Giai đoạn 7 |
| `06` §3: giới hạn tần suất API khác, tải file – chưa làm | Middleware `app/shared/identity/throttle.py`: mọi `/api/…` theo tài khoản / IP, thêm hạn mức upload (`multipart`) và tải `/api/v2/files/…`; kiểm trước khi đọc body; `429 RATE_LIMITED` + `Retry-After`; nhật ký `api.rate_limited`. `rate_limit.hit` chỉ dọn bộ đếm cũ khi mở cửa sổ mới | Yêu cầu Giai đoạn 7; `06` §3 |
| Phụ thuộc không có hàng đợi | `rq==2.9.1`, `redis==8.1.0` (+ `croniter`, `python-dateutil`, `tzdata` bắc cầu); `07` §2 | Hàng đợi bền vững |
| `Dockerfile` một stage; `docker-compose.yml`: `db`, `api` | `Dockerfile` nhiều stage (`runtime` mặc định, `test` có công cụ dev + `tests/`); compose thêm `redis` (AOF), `worker`, `env_file: .env` cho `api`/`worker`; `.dockerignore` không loại `tests/` (chỉ stage `test` chép) | Chạy pytest trong image; yêu cầu Giai đoạn 7 |
| – | Không thêm migration (bảng `sys_rate_limit_counters` từ `0004` dùng được; hàng đợi ở Redis) – head vẫn `0008` | – |
| – | 22 test mới (7 hàng đợi trên Redis thật – gồm tác vụ còn sau khi API dừng, thử lại tới khi gửi được, hết lượt, kill -9 worker thật giữa lúc gửi, Redis không chạy; 7 giới hạn tần suất – gồm token giả, upload bị chặn không lưu file, body không bị đọc; 8 cấu hình); 5 test cũ cập nhật (token cấp lúc gửi, `attempts`) | `07` §5 |

### 10.23. Mã nguồn – Giai đoạn 8: lô hàng (vận đơn), theo dõi trên cổng, sự kiện (09/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `02` §3.2, §6.2, §8: lô hàng (chưa làm) | `ops_shipments`, `ops_shipment_status_history`, `ops_delivery_records`; `/api/v2/ops/shipments` (tạo, xem, PATCH nháp, `:submit` / `:verify-documents` / `:start` / `:deliver` / `:close` / `:cancel`, `:assign`); số `LH-yyyy-nnnnnn`; nhật ký `shipment.*` | Yêu cầu Giai đoạn 8 |
| PRT-06 (chưa làm) | `/api/v2/portal/shipments` (+ `/{id}` có mốc tiến độ) – BR-111 | Yêu cầu Giai đoạn 8 |
| Yêu cầu: "EV-101 khi vận đơn được tạo, cập nhật trạng thái" | Theo danh mục `01` §5: tạo lô = **EV-111** `ops.shipment.created`, đổi trạng thái = **EV-112** `ops.shipment.status_changed`, giao hàng = EV-142; **EV-101** = `ops.contract.activated` – phát khi kích hoạt hợp đồng. Ghi vào `sys_audit_logs` (`event.<mã>`, phong bì `01` §5) cùng giao dịch – **CẦN XÁC NHẬN** | Giữ đúng mã sự kiện mà Luồng 2, 4 nhận (`03` §6, `05`) |
| `close_contract`: chưa kiểm lô hàng | Chặn đóng khi còn lô chưa `closed`/`cancelled` (BR-110 – phần lô) | `02` §3.9 |
| – | Migration `0009` (3 bảng, 2 function + 3 trigger viết tay) | `07` §6 điểm 3 |
| – | 39 test mới (35 lô hàng – gồm ma trận 8 vai trò + `customer`, người được phân công, 10 lô tạo song song không trùng số, tạm dừng hợp đồng song song với tạo lô, đủ vòng đời + EV-111/112/142, chuyển sai, hủy, If-Match, trigger chặn sửa/xóa, sự kiện cùng giao dịch, EV-101; 4 cổng – BR-111, không lộ dữ liệu nội bộ); 2 test cũ cập nhật (nhật ký có `event.EV-101`, head `0009`) | `07` §5 |

### 10.24. Mã nguồn – Giai đoạn 9: AI local trích xuất chứng từ cho toàn bộ tiến trình (09/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Nhập chứng từ thủ công; không có AI | Module chung `app/shared/docai/`: Upload → OCR → AI trích xuất → tự điền form → người duyệt xem, sửa → duyệt / từ chối. `POST /api/v2/doc-jobs` (multipart, `model_id` bắt buộc) → `202`; `GET /api/v2/doc-jobs`, `/{id}` (bản nháp + độ tin cậy + `form`), `/{id}/extractions` (văn bản theo trang, bbox); `PATCH /{id}/review`; `POST /{id}/approve`, `/{id}/reject`; `GET /api/v2/doc-types` | Yêu cầu Giai đoạn 9 |
| – | Danh mục model `ai_models` do `admin` quản lý: `/api/v2/admin/ai-models` (CRUD, xóa mềm, `:activate`/`:deactivate`/`:probe`); `GET /api/v2/ai-models/active` cho người thực hiện chọn (không lộ `endpoint`). Seed `qwen3.8:27b` (vision, `OLLAMA_URL`) | Yêu cầu Giai đoạn 9 – admin thêm model không sửa mã |
| – | Chỉ gọi model local: `OLLAMA_URL`/`endpoint` phải là localhost / mạng nội bộ – kiểm khi khởi động (sai → không chạy), khi lưu, mỗi lần gọi (DNS, không proxy, không chuyển hướng) – `app/core/netguard.py` | `06` §3.1 – thông tin nội bộ không ra ngoài |
| `shared/files`: pdf, jpg, png, docx, xlsx | Thêm nhận dạng `tiff`, `webp`, `txt` (UTF-8, chặn HTML/script/thực thi giả dạng), nhận ra `doc`/`xls` cũ để từ chối rõ; mục đích `doc_ai_source` ≤ 20 MB | `06` §2.6 |
| – | Chuyển đổi trước OCR: pdf → lớp chữ hoặc ảnh từng trang (`pypdfium2`), docx → văn bản + ảnh nhúng, xlsx → văn bản từng sheet, ảnh → chuẩn hóa; chống ảnh bom, zip bom. OCR: Tesseract `vie+eng` (mặc định), PaddleOCR (tùy chọn), model `ocr` qua Ollama | Yêu cầu Giai đoạn 9 |
| – | Độ tin cậy từng trường = model tự đánh giá, hạ về ≤ 0,5 khi giá trị không có trong văn bản; < 0,9 hoặc thiếu trường bắt buộc → `needs_review` (phải sửa hoặc xác nhận từng trường); ≥ 0,9 vẫn phải duyệt | Yêu cầu Giai đoạn 9 |
| – | Loại chứng từ = file JSON trong `app/shared/docai/schemas/` (`party`, `service_contract`, `shipment`, `commercial_invoice`); file sai → không khởi động | Admin thêm loại mới không sửa mã lõi |
| API tạo `md_parties`, `ops_service_contracts`, `ops_shipments` | Thêm `?source_doc_job_id=` (chỉ thêm nối – logic tạo không đổi): job phải `approved`, đúng loại đích, chưa dùng; cột `source_doc_job_id` (FK, UNIQUE) + `doc_job.linked` trong cùng giao dịch | Chưa duyệt không tạo được bản ghi nghiệp vụ |
| Hàng đợi chỉ cho email | Tác vụ `process_doc_job(job_id, user_id)` qua rq + Redis, thử lại `JOB_MAX_RETRIES` lần, hết lượt → `failed` + `doc.failed`; Redis lỗi → chạy nền trong tiến trình API + log; `enqueue(..., timeout=)` (`DOCAI_JOB_TIMEOUT_SEC`) | Yêu cầu Giai đoạn 9 |
| Ollama runner MLX không hỗ trợ đầu ra có cấu trúc (`501`) | Gọi lại không có `format`, JSON Schema đưa vào lời nhắc; đầu ra vẫn kiểm kiểu | Model mẫu `qwen3.8:27b` chạy runner MLX |
| Phụ thuộc | `pypdfium2==5.15.1`, `pillow==13.0.0`, `python-docx==1.2.0`, `openpyxl==3.1.5` (+ `lxml`, `et_xmlfile`); nhóm tùy chọn `ocr-paddle` + `requirements-ocr-paddle-lock.txt` (không vào lock chính: ~57 gói, gồm SDK tải model đám mây; `paddlex` ghim `PyYAML==6.0.2`); `Dockerfile` cài `tesseract-ocr`, `tesseract-ocr-vie`; compose: `OLLAMA_URL` cho `api`/`worker`, worker gắn `./data/uploads` | `07` §2 |
| – | Migration `0010`: `ai_models`, `sys_doc_jobs`, `sys_doc_extractions`, cột `source_doc_job_id` ×3; trigger: không xóa model/job, job đã duyệt/từ chối/lỗi không đổi nội dung, `sys_doc_extractions` chỉ ghi thêm. Không sửa `0001`–`0009` | `07` §6 điểm 3 |
| – | 155 test mới (91 đơn vị: chặn địa chỉ ngoài, cấu hình, schema, chuẩn hóa, độ tin cậy, chuyển đổi, OCR, nhận dạng file; 63 tích hợp PostgreSQL + Redis: quy trình đầy đủ, bắt buộc chọn model, model tắt/xóa, file, sửa/duyệt/từ chối, nối bản ghi ×3, phân quyền, hàng đợi thử lại/hết lượt, Redis lỗi, proxy, DNS ra ngoài, trigger; 1 chạy với model thật – bỏ qua mặc định); `test_storage_sniff` (txt giờ là `text/plain`), `test_migrations` (head `0010`) cập nhật | `07` §5 |

### 10.25. Mã nguồn – Giai đoạn 10: hóa đơn thương mại, outbox sự kiện, duyệt 2 người (09/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Loại chứng từ `commercial_invoice` không có bảng nghiệp vụ | `ops_commercial_invoices` + `ops_commercial_invoice_items` (gắn hợp đồng bắt buộc, lô tùy chọn – có lô thì hợp đồng theo lô; tổng tiền NUMERIC(18,2) trả **chuỗi** "12500.50"; trùng (hợp đồng, người bán, số) → `409`); `/api/v2/ops/commercial-invoices` (GET danh sách/chi tiết, POST, `?source_doc_job_id=`); schema `commercial_invoice` v2 có `target` + `form_field` (gồm dòng hàng) | Yêu cầu Giai đoạn 10 |
| Sự kiện ghi tạm vào `sys_audit_logs` (`01` §5) | Outbox `sys_events` cùng giao dịch + bộ phát rq (hàng `events`) → Redis Stream cho Luồng 2, 4; thử lại như Giai đoạn 7, hết lượt `failed` + `outbox.dispatch_failed`; luồng tắt → chờ (N6); CLI `python -m app.shared.events.cli dispatch`; worker nghe `default` + `events`. Giữ dòng `event.<mã>` trong nhật ký | `01` N5, N6; yêu cầu Giai đoạn 10 |
| – | Sự kiện `doc.extracted` / `doc.failed` / `doc.approval_pending` / `doc.approved` / `doc.rejected` / `doc.linked` (không chứa giá trị trích được) | Yêu cầu Giai đoạn 10 ("doc.*") |
| Job trích xuất: một người duyệt | Duyệt 2 người theo `dual_approval` của schema: hợp đồng luôn, hóa đơn ≥ `DOCAI_DUAL_APPROVAL_THRESHOLDS`; trạng thái `pending_second_approval`; người thứ 2 ≠ người tải lên, ≠ người duyệt đầu (`403` + CHECK CSDL) | Yêu cầu Giai đoạn 10 |
| `doc_job.linked.overrides` so chuỗi | So số theo giá trị (`1250.5` = `1250.50`), bỏ qua danh sách (dòng hàng) | Tránh báo khác sai |
| – | Migration `0011`: 3 bảng, 3 cột `sys_doc_jobs`, `status` → 32 ký tự, CHECK trạng thái + duyệt 2 người, trigger (outbox không sửa nội dung/xóa; hóa đơn không xóa; dòng hàng chỉ ghi thêm); chuyển sự kiện cũ từ nhật ký sang outbox | `07` §6 điểm 3 |
| – | 37 test mới (36 tích hợp, 1 đơn vị – ngưỡng duyệt 2 người; 9 outbox trên Redis thật – cùng giao dịch, rollback, chỉ id trong hàng đợi, gửi Luồng 2/4, gửi lặp, luồng tắt, thử lại, hết lượt + gửi lại, Redis lỗi, trigger; 14 hóa đơn; 12 duyệt 2 người + hóa đơn từ job + sự kiện `doc.*`; 2 migration – chuyển sự kiện cũ, tên CHECK); 3 test cũ cập nhật (hợp đồng cần 2 người, schema hóa đơn có đích, head `0011`) | `07` §5 |

### 10.26. Mã nguồn – Giai đoạn 11: Luồng 2 – lập kế hoạch, phân công tự động (09/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Outbox gửi stream, chưa có người nhận | Bộ nhận dùng chung `app/shared/events/consumer.py` + `sys_event_consumptions` (UNIQUE luồng + `event_id`, ghi cùng giao dịch – không xử lý trùng); bộ phát xếp tác vụ `consume(<luồng>)` sau khi gửi; lỗi thử lại như Giai đoạn 7, hết lượt `dead` + `event.consume_failed`; CLI `consume`, `replay [--dead]`; `app/handlers.py` gom đăng ký của các luồng | Yêu cầu Giai đoạn 11 (mục 3); `01` N5 |
| Luồng 2 chưa có | `app/flows/control/`: EV-101 → `ctl_contract_plans` (+ thành viên, KPI từ SLA, account_manager); EV-111 → `ctl_shipment_plans` + `ctl_shipment_tasks` từ quy trình mẫu; EV-112 đóng/hủy → đóng bước | Yêu cầu Giai đoạn 11 (mục 1); `03` CTL-F01, CTL-F03 |
| Manager gán tay `:assign` (Giai đoạn 8) | Phân công tự động theo vai trò, năng lực (`ctl_staff_profiles`), tải công việc; `ctl_assignments` (lịch sử, chỉ ghi thêm), nhật ký `assignment.change`, EV-202 → Luồng 1 đặt `assignee_id`; manager điều chuyển qua `POST /api/v2/control/assignments`; `:assign` deprecated | Yêu cầu Giai đoạn 11 (mục 2); `03` §3.3, BR-201, BR-204 |
| – | API `/api/v2/control/…` (kế hoạch, kế hoạch lô, bước của tôi, phân công, khối lượng việc, hồ sơ năng lực); `/api/v2/md/process-templates` | Yêu cầu Giai đoạn 11 (mục 4) |
| Quy trình mẫu chưa có | `md_process_templates`, `md_process_steps` (`app/shared/masterdata`, thêm `required_skill`), seed `QT-NK-SEA-FCL` v1, `QT-VC-NOIDIA` v1 | Cần để sinh bước khi nhận EV-111 |
| EV-111 `data` | Thêm `direction`, `transport_mode`, `required_delivery_at`, `service_codes` (giữ `schema_version` 1) | Luồng 2 chọn quy trình mẫu không cần đọc bảng Luồng 1 (N2) |
| – | Migration `0012`: 10 bảng, seed 2 quy trình (13 bước), trigger (không xóa vết xử lý / lịch sử phân công; lịch sử chỉ được bỏ `is_current`) | `07` §6 điểm 3 |
| `tests/conftest.py` xóa sạch mọi bảng | Nạp lại dữ liệu seed (quy trình mẫu) sau mỗi lần xóa | Quy trình mẫu là dữ liệu migration |
| – | 18 test mới (17 Luồng 2 trên PostgreSQL + Redis thật – chuỗi outbox → Luồng 2 → EV-202 → Luồng 1, năng lực, tải/sức chứa, không có người, BR-204, BR-201, quy trình nội địa / không có quy trình, không xử lý trùng, lỗi → thử lại → dead, replay không Redis, hủy lô giảm tải, điều chuyển tay, phân quyền, hồ sơ năng lực, tắt Luồng 2, trigger; 1 migration); 3 test cũ cập nhật (EV-111 thêm trường, head `0012`, tên CHECK) | `07` §5 |

### 10.27. Mã nguồn – Giai đoạn 12: Luồng 2 – theo dõi thực thi, sự kiện, cảnh báo (09/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Bước của lô chỉ `open` / `closed` | `open` → `in_progress` → `done`, `blocked` (lý do); thực tế `actual_start`/`actual_end`, `progress_pct`; API `:start` / `PATCH` / `:block` / `:unblock` / `:complete`; `ctl_task_updates` (chỉ ghi thêm), nhật ký `task.*`; tải công việc tính mọi bước chưa xong | Yêu cầu Giai đoạn 12 (mục 1 – cập nhật thực tế) |
| – | `/api/v2/control/tracking/shipments`, `/tracking/shipments/{id}`, `/tracking/contract-plans/{id}`, `/tasks/{id}`: tình trạng (on_track, at_risk, late, blocked, completed, closed), % tiến độ, bước hiện tại, dự kiến xong, trễ | Yêu cầu Giai đoạn 12 (mục 1) |
| Luồng 2 chỉ phát EV-202 | Thêm `ctl.task.started` / `completed` / `blocked` / `delayed`, `ctl.alert.raised` (luồng nhận 4) qua outbox | Yêu cầu Giai đoạn 12 (mục 2) – mã EV chính thức **CẦN XÁC NHẬN** |
| – | `ctl_alerts`: `task_blocked`, `task_overdue` (warning → critical), `shipment_late_risk`; người nhận account_manager / mọi manager; không lặp; tự đóng; quét định kỳ (tác vụ rq tự xếp lại, `CONTROL_SCAN_INTERVAL_SEC`); API `/alerts` + `:acknowledge` / `:resolve` / `:scan`; nhật ký `alert.*` | Yêu cầu Giai đoạn 12 (mục 3); `03` §3.5 |
| – | Migration `0013`: 8 cột + 5 CHECK `ctl_shipment_tasks`, `ctl_task_updates`, `ctl_alerts`, trigger (nhật ký bước chỉ ghi thêm, không xóa cảnh báo) | `07` §6 điểm 3 |
| – | 9 test mới (PostgreSQL + Redis thật – vòng đời bước + sự kiện tới stream Luồng 4, chặn → cảnh báo → gỡ tự đóng, quét quá hạn → nâng mức → xong tự đóng, nguy cơ trễ hạn giao, API cảnh báo + phân quyền, theo dõi + phân quyền, hủy lô đóng bước đang làm, tác vụ quét tự xếp lại, trigger/CHECK); test migration cập nhật (head `0013`, tên CHECK) | `07` §5 |

### 10.28. Mã nguồn – Giai đoạn 13: đồng bộ bước Luồng 2 ↔ Luồng 1, BR-205, cảnh báo qua email / Zalo (09/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Bước chỉ ở Luồng 2 | `ops_tasks` (Luồng 1) đồng bộ theo `revision` (Luồng 2 nguồn chuẩn): `ctl.task.synced` → Luồng 1 ghi đè + phát EV-131/132/133/`ops.task.unblocked`; thao tác ở Luồng 1 → EV-13x → Luồng 2 kiểm tra, áp dụng / xác nhận / trả trạng thái chuẩn khi xung đột (cảnh báo `task_sync_conflict`); không vòng lặp (`origin`); lô hủy/đóng đóng bước hai bên; `:resync` | Yêu cầu Giai đoạn 13 (mục 1) |
| Cảnh báo không leo thang | BR-205: chưa xử lý sau `CONTROL_ESCALATION_HOURS` → `business_manager` (một lần), nhật ký `alert.escalate`, `ctl.alert.escalated`; business_manager xử lý cảnh báo đã leo thang | Yêu cầu Giai đoạn 13 (mục 2); `03` BR-205 |
| Cảnh báo chỉ trong ứng dụng | `ctl_alert_notifications` + tác vụ rq gửi email / Zalo OA (`ZALO_TOKEN`), thử lại như Giai đoạn 7, hết lượt `failed` + `alert.notify_failed`; không có hàng đợi → gửi ngay; `zalo_user_id` trong hồ sơ nhân viên | Yêu cầu Giai đoạn 13 (mục 3) |
| Cảnh báo tạo khi quét lấy giờ hệ thống | Lấy thời điểm của lần quét (`raised_at`) | Leo thang tính đúng thời gian chưa xử lý |
| – | Migration `0014`: `ops_tasks` (chép bước đã có), `ctl_alert_notifications`, cột `revision`, `escalated_at`, `escalated_to_role`, `zalo_user_id`, loại cảnh báo `task_sync_conflict`, trigger không xóa thông báo | `07` §6 điểm 3 |
| – | 13 test mới (PostgreSQL + Redis thật; Zalo giả lập trên 127.0.0.1): đồng bộ L2 → L1 + EV-13x, L1 → L2, đổi đồng thời hội tụ, Luồng 1 lệch → xung đột → sửa, sự kiện cũ bỏ qua + phân quyền + hủy lô, điều chuyển, `:resync`, leo thang sau 2 giờ + email business_manager, không leo thang cảnh báo đã xử lý, Zalo (token không lọt hàng đợi/log), Zalo lỗi → thử lại → gửi được, email hết lượt → failed + FailedJobRegistry, không hàng đợi → gửi ngay); 3 test cũ cập nhật (head `0014`, kết quả quét có `escalated`, nhật ký hồ sơ có `zalo_user_id`) | `07` §5 |

### 10.29. Mã nguồn – Giai đoạn 14: Luồng 4 – KPI vận hành, báo cáo (09/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Stream `l3pl:events:assessment` có sự kiện nhưng không ai nhận | Luồng 4 đăng ký xử lý EV-101, EV-111/112/142, EV-131/132/133, `ops.task.unblocked`, `ctl.task.*`, `ctl.alert.*` với bộ nhận dùng chung (`sys_event_consumptions` – không xử lý trùng); dựng `asm_fact_*` | Yêu cầu Giai đoạn 14 (mục 1); `05` §6, §11 |
| EV-133, `ops.task.unblocked`, `ctl.task.synced` không gửi Luồng 4; `ctl.task.*` không có `revision` | Thêm luồng nhận `assessment`; thêm `revision` (giữ `schema_version` 1) – khử trùng một thay đổi đến qua nhiều sự kiện; EV-13x `origin = ops` không đổi trạng thái tới khi Luồng 2 xác nhận | Số bước bị chặn đúng (EV-133 trong yêu cầu); không tính thay đổi bị Luồng 2 từ chối |
| Chưa có KPI | KPI-07, KPI-03, giờ trễ trung bình, số bước bị chặn, số cảnh báo – theo toàn bộ / hợp đồng (so SLA) / lô / nhân viên, tính khi đọc, chỉ `production` | Yêu cầu Giai đoạn 14 (mục 2); `05` §3 |
| – | `GET /api/v2/assessment/kpi?from=&to=` + `/kpi/contracts[/{id}]`, `/kpi/shipments[/{id}]`, `/kpi/staff[/{user_id}]` – `manager`, `business_manager`, `admin` | Yêu cầu Giai đoạn 14 (mục 3) |
| – | Migration `0015`: `asm_fact_contracts`, `asm_fact_shipments`, `asm_fact_tasks`, `asm_fact_task_blocks`, `asm_fact_alerts` | `07` §6 điểm 3 |
| – | 10 test mới: sự kiện dựng tay (số liệu toàn bộ / hợp đồng + SLA / lô / nhân viên, biên kỳ giờ VN, mẫu số 0 → null, khử trùng chặn, sự kiện cũ / lệch thứ tự, training không tính, gửi lặp), phân quyền + kiểm tham số, tắt Luồng 4 → không có API, chuỗi thật qua Redis Stream + replay; `test_migrations` head `0015` + vòng downgrade/upgrade | `07` §5 |

### 10.30. Mã nguồn – Giai đoạn 15: Luồng 4 – KPI-01…14, mục tiêu từ EV-201, tính định kỳ, cảnh báo EV-403 (09/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Kế hoạch chỉ ở `draft`; không có EV-201; SLA so trực tiếp từ EV-101 | Luồng 2: sửa mục tiêu KPI, gửi duyệt, `business_manager` duyệt / trả lại, sửa sau duyệt (phiên bản); duyệt → EV-201. Luồng 4: `asm_kpi_targets`, danh mục `asm_kpi_definitions` không chứa mục tiêu | Yêu cầu Giai đoạn 15 (mục 1) – mục tiêu không đặt cứng trong mã |
| Chỉ KPI-03, KPI-07 | `formulas.py`: KPI-01…10, 14 trên `production`; KPI-11/12/13 chỉ `training` (`applicable = false`); dữ kiện mới cho EV-113/114/121/122/141/204/205/206 | Yêu cầu Giai đoạn 15 (mục 2); `05` §3 |
| KPI chỉ tính khi gọi API | Worker tính hằng ngày (rq, `ASSESSMENT_KPI_CALC_HOUR`) kỳ tuần / tháng / quý → `asm_fact_kpi_measurements` (chỉ thêm), `asm_calc_runs`; `POST /kpi/calc-runs` | Yêu cầu Giai đoạn 15 (mục 3); `05` BR-407 |
| Không có cảnh báo KPI | Kém mục tiêu → `warning`, kém ngưỡng → `critical`; một lần mỗi (KPI, hợp đồng, kỳ); EV-403 + nhật ký `kpi.alert`; Luồng 2 → cảnh báo `kpi_below_target` cho manager + business_manager (email / Zalo) | Yêu cầu Giai đoạn 15 (mục 4) |
| – | Migration `0016`: 10 bảng `asm_*`, cột KPI-02 của `asm_fact_shipments`, seed danh mục, trigger chỉ thêm; `ctl_alerts.shipment_id` null được + `contract_id`, loại `kpi_below_target`, mục đích thông báo `kpi` | `07` §6 điểm 3 |
| – | 9 test mới (KPI-01…14 toàn bộ / hợp đồng / nhân viên / lô, danh mục không có mục tiêu, mục tiêu hiện hành từ EV-201, tính định kỳ + cảnh báo + chạy lại không lặp + chỉ thêm + chuỗi EV-403 → Luồng 2 → email, EV-403 trùng / training, tính lỗi, lịch hằng ngày, quyền API, duyệt kế hoạch qua Redis); `test_migrations` head `0016` + vòng 0016 | `07` §5 |

### 10.31. Mã nguồn – Giai đoạn 16: địa điểm, kiểm tra chứng từ BR-103, điểm kiểm soát của lô (09/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Không có danh mục địa điểm | `md_locations` (mã, tên, loại, địa chỉ, tọa độ, `party_id`); admin, manager ghi; customer xem địa điểm của mình; lưu trữ / khôi phục | Yêu cầu Giai đoạn 16 (mục 1) |
| `:verify-documents` chỉ do nhân viên xác nhận; chưa có chứng từ lô | `ops_documents` (phiên bản, không xóa), tải lên → EV-121 + lần kiểm tra `pending`; ghi đủ / thiếu / sai + lý do → EV-122; BR-103 chặn `docs_verified` khi chưa có / đang chờ / lần mới nhất không đạt | Yêu cầu Giai đoạn 16 (mục 2); `02` BR-103 |
| Không có điểm kiểm soát lô | `ops_checkpoints` (nhận hàng, thông quan, xuất kho, giao hàng), xác nhận qua / trượt + thời điểm thực tế → `ops.checkpoint.evaluated`; thông quan đạt → EV-141 | Yêu cầu Giai đoạn 16 (mục 3) |
| `asm_fact_checkpoints` chỉ cho EV-204 | thêm `source` (khóa chính), `checkpoint_type`; Luồng 4 nhận `ops.checkpoint.evaluated` → KPI-05 | KPI-05/06 có nguồn thật |
| – | Migration `0017`; trigger chỉ thêm / không xóa | `07` §6 điểm 3 |
| – | 16 test mới (địa điểm: quyền, kiểm tra dữ liệu, khách hàng chỉ thấy của mình, sửa / lưu trữ; chứng từ: phiên bản, quyền, loại file; kiểm tra + BR-103; điểm kiểm soát; chuỗi thật tới KPI-01/02/05/06); test lô hàng cũ thêm bước kiểm tra chứng từ trước `:verify-documents`; `test_migrations` head `0017` | `07` §5 |

### 10.32. Mã nguồn – Giai đoạn 17: cổng gửi chứng từ, lô quay về docs_collecting, địa điểm lô, quy tắc chứng từ bắt buộc (09/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Chỉ nhân viên tải chứng từ | `POST /api/v2/portal/shipments/{id}/documents` – khách hàng gửi cho lô của mình (BR-111), sniff nội dung, EV-121, `channel = portal` | Yêu cầu Giai đoạn 17 (mục 1) |
| Chứng từ mới sau `docs_verified` không đổi trạng thái | Lô quay về `docs_collecting`, lần kiểm tra mới đang chờ, nhật ký `shipment.reopen_documents`, EV-112 | Yêu cầu Giai đoạn 17 (mục 2); BR-103 |
| Điểm đi / đến là văn bản tự do | `origin_location_id`, `destination_location_id` → `md_locations` đang hoạt động, bắt buộc với lô mới (schema + trigger INSERT); văn bản = bản chụp; lô cũ giữ nguyên; AI trích xuất không còn điền sẵn nơi đi / đến | Yêu cầu Giai đoạn 17 (mục 3) |
| Nhân viên tự ghi mọi dòng "thiếu" | `ops_document_requirements` (CSDL, seed 8 quy tắc) → dòng `missing` `source = auto`; API quản lý quy tắc | Yêu cầu Giai đoạn 17 (mục 4) |
| – | Migration `0018` | `07` §6 điểm 3 |
| – | 6 test mới + cập nhật test cũ (payload lô dùng id địa điểm; kiểm tra chứng từ đủ 3 loại bắt buộc của lô biển nhập khẩu); `test_migrations` head `0018` | `07` §5 |

### 10.33. Mã nguồn – Giai đoạn 18: Luồng 3 đào tạo – hợp đồng đào tạo, buổi học, đánh giá người học (09/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Chưa có Luồng 3 | `app/flows/learning`: hợp đồng đào tạo (khách hàng, người hướng dẫn, người học, lịch, học phí), `admin` / `manager` ghi, cổng khách hàng / người học xem | Yêu cầu Giai đoạn 18 (mục 1) |
| – | Buổi học, điểm danh, hoàn thành (người hướng dẫn), nhật ký; sự kiện EV-302, EV-303, `lrn.*` | Yêu cầu Giai đoạn 18 (mục 2) |
| KPI-11/12/13 không đo | Luồng 4: điểm buổi học (người hướng dẫn chấm, người học xem qua cổng), dữ kiện đào tạo, KPI-11/12/13 theo người học / hợp đồng | Yêu cầu Giai đoạn 18 (mục 3); chấm điểm thuộc Luồng 4 (`04` §1, `05` §1) |
| – | Migration `0019` (9 bảng, trigger không xóa) | `07` §6 điểm 3 |
| – | 7 test mới (quyền, kiểm tra dữ liệu, hiển thị theo vai trò, buổi học + điểm danh + sự kiện, chấm điểm + cổng + KPI qua chuỗi sự kiện thật, vòng đời hợp đồng, tắt luồng); `test_migrations` head `0019` | `07` §5 |

### 10.34. Mã nguồn – Giai đoạn 19: quyết toán BR-110, chấm dứt trước hạn, phụ lục duyệt 2 người (10/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| `:close` chỉ cần biên bản thanh lý + lô đóng | Quyết toán `final` (hóa đơn đã ghi + KPI qua `assessment.public`), manager lập, business_manager duyệt, sự kiện cho Luồng 4; `:close` cần quyết toán đã duyệt | Yêu cầu Giai đoạn 19 (mục 1); BR-110 |
| `:start-liquidation` cho chấm dứt trước hạn chỉ cần lý do | Đề xuất chấm dứt (manager) → business_manager duyệt / từ chối; lô chưa bắt đầu tự hủy, lô đang làm phải hoàn tất; EV-104 | Yêu cầu Giai đoạn 19 (mục 2); `02` §3.9.2 |
| `POST …/versions` áp phiên bản ngay (manager) | Phụ lục duyệt 2 người khác nhau → phiên bản mới, bản cũ giữ; EV-103; bỏ áp trực tiếp | Yêu cầu Giai đoạn 19 (mục 3); `02` §3.7 |
| – | Migration `0020` (4 bảng, CHECK 4 mắt / công thức số dư, trigger không xóa) | `07` §6 điểm 3 |
| – | 5 test mới; viết lại test phiên bản / thanh lý / đóng theo quy trình mới; `test_migrations` head `0020` | `07` §5 |

### 10.35. Mã nguồn – Giai đoạn 20: sổ thu – chi, tạm ứng nhân viên, bảng kê đối soát (11/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| – | Khoản mục thu – chi (seed T01–T05, C01–C08), phiếu thu / chi `draft → submitted → approved → recorded` / `void`, số dư quỹ, BC-TC-01 | `02` §3.10.1, §3.10.2, BR-112, BR-114 |
| – | Tạm ứng `requested → approved → disbursed → settling → settled` / `rejected`, phiếu C08 / `offset` / T05 tự sinh, BR-115 (n = 2), EV-115, EV-116; M-10 ở Luồng 2 | `02` §3.10.3; `03` M-10 |
| Bản nháp đề xuất: role ACCOUNTANT / OPERATOR, mã BR-111…114, tối đa 3 tạm ứng, OVERDUE 30 ngày chặn tạo mới, xuất PDF | Dùng role sẵn có, mã BR của `02`, n = 2, hạn 7 ngày + cảnh báo M-10 (không chặn), CSV + HTML in | `02` thắng bản nháp (quyết định 11/11/2026) |
| `ops_contract_settlements` chỉ `final` theo hợp đồng | Thêm `periodic_statement` theo khách hàng + tháng (`contract_id` NULL), `settlement_type` VARCHAR(20); `:confirm` / `:dispute` cho `customer` và `manager` (ghi nhận thay); `disputed` → `:reissue` bản mới | Quyết định PO 11/11/2026; `02` §6.5 |
| Hóa đơn thương mại tạo được ở mọi ngày | Kỳ có bảng kê `confirmed` / `closed` của khách hàng → không tạo / sửa hóa đơn, dòng hàng (service + trigger) | Guard duy nhất thêm vào logic Giai đoạn 0–19 |
| – | Migration `0021` (3 bảng, mở rộng `ops_contract_settlements`, `ctl_alerts`; trigger BR-112, khóa bảng kê, khóa hóa đơn) | `07` §6 điểm 3 |
| – | 9 test mới (`tests/flows/operations/test_cashbook_g20.py`); `test_migrations` head `0021` | `07` §5 |

### 10.36. Mã nguồn – Giai đoạn 22 – 23: trợ lý AI hỏi đáp, nhập tay giấy tờ theo hợp đồng (12/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Giai đoạn 22: gateway nuốt lỗi thành chuỗi (HTTP 200), `httpx` trong `async def`, router `/api/ai` không phân quyền, `/track` dữ liệu giả | Lỗi model → `AppError` 502 / 504; endpoint `def` (thread pool); chỉ `ops_staff`; `/track` đọc lô thật; giới hạn `prompt` 1–4000 ký tự | Rà soát Giai đoạn 22 |
| Giai đoạn 23 bản đầu: AI đọc ảnh bill (`from-bill-image`, `read-bill*`, 4 cột `ai_*`, `staff_bill.html`) | Bỏ hẳn; nhân viên **nhập tay** giấy tờ theo hợp đồng (`ops_document_catalog`, `ops_contract_documents`, xem file theo trang, tự động lưu `If-Match`, `:confirm` / `:reopen` / `:void`); migration `0022` (gỡ cột `ai_*`) | Quyết định PO 12/11/2026: nhập tay thay AI tự động |
| `update` ghi nhật ký `contract_document.save` kể cả khi giá trị không đổi | Chỉ ghi khi `received_date` / `note` / bên gửi / trường thực sự đổi; ngày bị xóa ghi `null` | Lỗi phát hiện khi rà soát (giao diện gửi đủ trường mỗi lần lưu) |
| `doc_entry.html`: luôn hỏi "rời trang?", PATCH thừa khi chuyển ô / giấy tờ, giờ cố định `+07:00`, mất nội dung khi 409 | Cờ `dirty`, bộ đếm lưu được đặt lại; giờ theo múi giờ trình duyệt; 409 hỏi trước khi bỏ nội dung đang nhập; `StaticFiles` theo đường dẫn tuyệt đối | Như trên |
| `conftest` không nạp lại danh mục seed `ops_document_catalog` sau TRUNCATE (khóa `code`, không có `id`) | Thêm vào `SEED_TABLES`; bỏ giả định cột `id` | Test Giai đoạn 23 không chạy được |
| – | Test: `test_ai_gateway`, `test_ai_router`, thêm 2 test `test_contract_documents`, `tests/e2e/test_doc_entry_ui.py` (Playwright); `test_migrations` head `0022` | `07` §5 |

## §10.37 - P0.1 Architecture Tests (07 §4.2)
- Thêm tests/architecture/: test_import_linter.py (N4), test_no_cross_flow_fk.py (N3), test_enabled_flows.py (N6)
- Chạy: pytest tests/architecture/ -v -> 4 passed
- Nhánh canonical: stage-23-ai-operations
- Hướng AI: nhập tay là chính, docai + Qwen27B chỉ dự phòng


### 10.37. Mã nguồn – Giai đoạn 23 (sửa sau rà soát): nối giấy tờ nhập tay vào BR-103, sửa giao diện (12/11/2026)

| Nội dung cũ | Nội dung mới | Lý do sửa |
|---|---|---|
| Giấy tờ cấp lô nhập tay (`ops_contract_documents`) tách khỏi chứng từ lô; B/L đã nhập mà lô vẫn báo thiếu | Xác nhận giấy tờ cấp lô trùng loại chứng từ lô → phiên bản `ops_documents` (bản sao file, `source_contract_document_id`), lần kiểm tra chờ, EV-121, lô `docs_verified` quay về `docs_collecting`; không file / loại file không nhận → nêu lý do | Rà soát Giai đoạn 23: BR-103, KPI không thấy dữ liệu nhập tay |
| – | Migration `0023` (`ops_documents.source_contract_document_id`); `files.copy`, `files.allowed_for` | `07` §6 điểm 3 |
| Giao diện: đổi hợp đồng không lưu nốt; mã truy cập 15 phút không làm mới, tài khoản đã bật 2 lớp không vào được; đối tác / lô chỉ 100 dòng đầu, lỗi tải bị bỏ qua; hợp đồng chưa có lô → 422 không hướng dẫn; không có ô `boolean`; không biết còn thiếu trường nào; lý do qua `prompt()` | Lưu nốt trước khi đổi; refresh token xoay vòng + bước TOTP, mất phiên giữ phần đang nhập; tìm đối tác phía máy chủ, đọc hết các trang lô, báo lỗi tải; chặn khai báo cấp lô khi chưa có lô; ô Có / Không; đếm + đánh dấu trường bắt buộc trống; hộp thoại lý do | Rà soát Giai đoạn 23 |
| – | 5 test `test_contract_documents_br103.py`; 6 test Playwright mới; `test_migrations` head `0023` | `07` §5 |
| Màn hình giấy tờ không thấy hợp đồng đã ký, phụ lục, biên bản thanh lý | Nhóm "Hồ sơ hợp đồng – chỉ xem" trong ô chọn giấy tờ (file đính kèm + thông tin phụ lục), xem ảnh / pdf, báo thiếu bản hợp đồng đã ký; 1 test Playwright | Rà soát Giai đoạn 23 (mục 4) |


