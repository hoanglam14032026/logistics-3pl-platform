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
