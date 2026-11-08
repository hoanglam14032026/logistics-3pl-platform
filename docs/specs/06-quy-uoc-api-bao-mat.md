# 06 – Quy ước API, dữ liệu, file, bảo mật và phân quyền

> Áp dụng cho **mọi** luồng. Các mục đánh dấu (C3)–(C10) xử lý những sai sót của v1.1.0 đã nêu trong `90-hien-trang-v1.1.md`.

## 1. Cấu trúc đường dẫn

```
/api/v2/ops/...          Luồng 1
/api/v2/control/...      Luồng 2
/api/v2/learning/...     Luồng 3
/api/v2/assessment/...   Luồng 4
/api/v2/portal/...       Cổng khách hàng (thuộc Luồng 1, chỉ vai trò customer)
/api/v2/md/...           Danh mục dùng chung (gồm bộ quy tắc, ma trận chứng từ)
/api/v2/auth/...         Đăng nhập, làm mới token
/api/v2/files/{file_id}  Tải file (kiểm quyền theo luồng sở hữu)
/health, /ready          Kiểm tra sức khỏe (không cần đăng nhập)
```

- Danh từ số nhiều, chữ thường, gạch nối: `/service-contracts`, `/shipments/{shipment_id}/documents`.
- Chuyển trạng thái dùng **hành động** rõ nghĩa: `POST /shipments/{id}:submit-documents`, không cho PUT trực tiếp trường `status`.
- `/api/v1/*` (v1.1.0) giữ trong giai đoạn chuyển tiếp, đánh dấu `deprecated` trên OpenAPI. Thời hạn gỡ bỏ: **CẦN XÁC NHẬN** (đề xuất: sau khi hoàn thành Giai đoạn 2, `07` §5).

## 2. Quy ước chung

### 2.1. Phương thức và mã trạng thái

| Thao tác | Phương thức | Thành công |
|---|---|---|
| Tạo | `POST` | `201` + header `Location` |
| Đọc | `GET` | `200` |
| Cập nhật từng phần | `PATCH` (thay `PUT` của v1.1.0; JSON Merge Patch) | `200` |
| Hành động chuyển trạng thái | `POST /...:{action}` | `200` |
| Lưu trữ (ẩn) | `POST /...:archive` | `200` |
| Xóa cứng | `DELETE` – chỉ với bản ghi nháp (`draft`) hoặc `admin` | `204` |

| Mã lỗi | Khi nào |
|---|---|
| `400` | Request sai cú pháp (JSON hỏng, multipart hỏng) |
| `401` | Chưa đăng nhập / token hết hạn |
| `403` | Không đủ quyền (RBAC §4) hoặc vi phạm phạm vi dữ liệu (BR-01) |
| `404` | Không tồn tại **hoặc** người dùng không có quyền thấy bản ghi (không tiết lộ sự tồn tại) |
| `409` | Chuyển trạng thái không hợp lệ; trùng khóa nghiệp vụ (số hợp đồng, số chứng từ); xung đột phiên bản (`If-Match`) |
| `413` | File vượt dung lượng cho phép |
| `415` | Loại file không được phép |
| `422` | Dữ liệu không hợp lệ (kiểu, độ dài, bắt buộc, giá trị null, vi phạm quy tắc nghiệp vụ khi nhập) |

### 2.2. Định dạng lỗi thống nhất (C5)

Mọi lỗi 4xx/5xx – kể cả lỗi validation của Pydantic – được chuyển về một định dạng bằng exception handler chung:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Dữ liệu không hợp lệ",
    "details": [
      {"field": "body.title", "issue": "string_too_short", "message": "Tối thiểu 1 ký tự"}
    ],
    "request_id": "8f1c…"
  }
}
```

`code` thuộc danh mục cố định: `VALIDATION_ERROR`, `NOT_FOUND`, `FORBIDDEN`, `UNAUTHENTICATED`, `INVALID_STATE_TRANSITION`, `DUPLICATE`, `VERSION_CONFLICT`, `FILE_TOO_LARGE`, `UNSUPPORTED_FILE_TYPE`, `INTERNAL_ERROR`.

### 2.3. Danh sách, lọc, sắp xếp, phân trang (C6, C7)

| Tham số | Quy ước |
|---|---|
| `page`, `page_size` | Mặc định `1`, `20`; tối đa `page_size = 100` |
| `status` | Giá trị theo từng thực thể; với thực thể có trạng thái hoạt động: `active` (mặc định) / `archived` / `all` |
| Lọc chuỗi | So khớp chính xác; **chuỗi rỗng → 422** |
| `q` | Tìm gần đúng (không phân biệt hoa thường) trên các trường được khai báo cho từng danh sách |
| `sort` | `field` hoặc `-field`; danh sách trường cho phép khai báo theo từng endpoint; mặc định `-created_at` |
| Khoảng thời gian | `from`, `to` (ISO 8601) |

Phản hồi danh sách:

```json
{"items": [ ], "total": 135, "page": 1, "page_size": 20}
```

### 2.4. Dữ liệu và thời gian (C3, C10)

- Mọi bảng nghiệp vụ có: `id` (BIGINT tự tăng), `created_at`, `created_by`, `updated_at`, `updated_by`, `row_version` (khóa lạc quan, gửi qua `If-Match`). Các cột trạng thái/thời gian **`NOT NULL`**.
- Thời gian lưu kiểu có múi giờ, giá trị UTC; API trả ISO 8601 có hậu tố `Z`. Giao diện hiển thị theo `Asia/Ho_Chi_Minh`. Dùng `datetime.now(timezone.utc)`; không dùng `datetime.utcnow`.
- Ngày nghiệp vụ không có giờ (ngày ký hợp đồng, ngày hóa đơn) dùng kiểu `DATE`.
- Tiền tệ: `NUMERIC(18,2)` + mã tiền tệ ISO 4217; **không** dùng `float`. Số lượng, trọng lượng: `NUMERIC(18,3)` + mã đơn vị.
- Mã định danh nghiệp vụ (số hợp đồng, số lô) do hệ thống sinh, ví dụ `HD-2026-0001`, `LH-2026-000123`; tiền tố riêng cho `training`: `TR-LH-…`.

### 2.5. Xóa và lưu trữ

- Mặc định **không xóa cứng** dữ liệu nghiệp vụ; dùng `archived`.
- Chứng từ: không xóa, chỉ thay phiên bản (BR-03).
- Xóa cứng chỉ áp dụng cho bản ghi `draft` do chính người tạo xóa, hoặc `admin` (ghi nhật ký).

### 2.6. File (C8, C9)

| Quy định | Nội dung |
|---|---|
| Lưu trữ | Lớp trừu tượng `StorageBackend` (mặc định: thư mục cục bộ `UPLOAD_DIR`; mở rộng: S3/MinIO). CSDL chỉ lưu `storage_key` **tương đối** |
| Đặt tên | `storage_key = <module>/<yyyy>/<mm>/<uuid4>` – không chứa tên người dùng nhập; tên gốc lưu ở `original_filename` |
| API trả về | `file_id`, `original_filename`, `mime_type`, `size_bytes`, `sha256`, `download_url` (`/api/v2/files/{file_id}`). **Không** trả đường dẫn trên server |
| Tải về | `GET /api/v2/files/{file_id}`: kiểm quyền theo thực thể sở hữu file; hỗ trợ `Range` (phát video); header `Content-Disposition` với tên gốc |
| Kiểm tra khi tải lên | Từ chối file 0 byte (`422`); kiểm tra MIME theo nội dung file (magic bytes), không tin phần mở rộng; tính SHA-256 |
| Danh sách loại & dung lượng | Cấu hình theo mục đích (giá trị đề xuất, **CẦN XÁC NHẬN**): chứng từ `pdf, jpg, png, xlsx, docx` ≤ 20 MB; tài liệu học thuật `pdf, docx, pptx, xlsx` ≤ 50 MB; video `mp4, webm, mov` ≤ 2 GB; audio phỏng vấn `m4a, mp3, wav, ogg` ≤ 500 MB; ảnh thumbnail `jpg, png, webp` ≤ 2 MB; chứng từ khách hàng gửi qua cổng ≤ 20 MB/file, ≤ 30 file/lần gửi |
| Upload lớn | Ghi theo luồng (stream) xuống đĩa, không đọc toàn bộ vào RAM (khắc phục `await file.read()` của v1.1.0). File > 100 MB (video, audio phỏng vấn) tải theo từng phần, tải tiếp được khi mất kết nối (giao thức tus hoặc multipart upload của S3 – **CẦN XÁC NHẬN**) |
| Xử lý media | Chuyển mã, trích audio, tạo ảnh đại diện chạy ở tác vụ nền (hàng đợi công việc), không chạy trong request |
| Nhất quán | Ghi file → ghi CSDL trong giao dịch; lỗi CSDL → xóa file vừa ghi. Xóa thực thể → xóa file bằng tác vụ dọn dẹp định kỳ (file mồ côi) |
| Quét mã độc | **CẦN XÁC NHẬN** (đề xuất: ClamAV khi triển khai ra Internet) |

## 3. Xác thực

- Đăng nhập tài khoản nội bộ, mật khẩu băm Argon2/bcrypt; JWT truy cập (15 phút) + refresh token (7 ngày, thu hồi được).
- Tích hợp SSO của trường/doanh nghiệp (Google Workspace, Microsoft Entra ID): **CẦN XÁC NHẬN**.
- Mọi endpoint trừ `/health`, `/ready`, `/api/v2/auth/login`, trang khách mời phỏng vấn (`/api/v2/learning/interview-invitations/{token}` – xác thực bằng token, `04` BR-309) và webhook của máy chủ media phỏng vấn (xác thực bằng chữ ký) yêu cầu xác thực.
- Tài khoản `customer`: bắt buộc xác thực 2 lớp; khóa tạm sau 5 lần đăng nhập sai; giới hạn tần suất gọi API và tải file theo tài khoản; phiên tối đa 8 giờ. **Lộ trình (đã chốt 08/11/2026):** Giai đoạn 0 (bước 0.6) chỉ làm JWT + refresh token + RBAC + khóa tạm sau nhiều lần đăng nhập sai; bảng `sys_users` có sẵn cột `totp_secret` (NULL) và `is_2fa_enabled` (mặc định `false`); endpoint và luồng xác thực 2 lớp làm cùng cổng khách hàng (Giai đoạn 2). Tài khoản nội bộ có quyền phê duyệt hợp đồng – tài chính (`manager`, `business_manager`) cũng bắt buộc xác thực 2 lớp; thao tác phê duyệt cấp 2 yêu cầu xác thực lại (nhập lại OTP) nếu phiên đã quá 30 phút.

## 4. Phân quyền (RBAC)

Ký hiệu: **Q** = toàn quyền ghi; **G** = ghi trong phạm vi được giao; **Đ** = chỉ đọc; **Đ\*** = chỉ đọc dữ liệu của bản thân/lớp phụ trách; **–** = không có quyền.

| Nhóm chức năng | ops_staff | manager | business_manager | learner | instructor | content_editor | customer | admin |
|---|---|---|---|---|---|---|---|---|
| L1 – Hợp đồng dịch vụ (`production`) | G (tạo, nhập) | Q (kích hoạt, thanh lý) | Đ; duyệt cấp 2 chấm dứt hợp đồng | – | Đ | – | Đ\* (hợp đồng của mình) | Đ |
| L1 – Góp ý bản nháp, phụ lục | G (lập, trao đổi) | Q (đồng ý, gửi phê duyệt) | Duyệt cấp 2 phụ lục | – | Đ | – | G (đề nghị, trao đổi, tải bản ký) | Đ |
| L1 – Yêu cầu dịch vụ | G (xử lý, nhập thay) | Đ | Đ | – | Đ | – | G (gửi, bổ sung, rút) | Đ |
| L1 – Lô hàng, chứng từ, bước (`production`) | G (lô được phân công) | Đ + phê duyệt qua L2 | Đ | – | Đ | – | Đ\* mốc tiến độ; G gửi chứng từ (BR-111) | Đ |
| L1 – Biểu giá, khoản phí, thanh toán | G (ghi phí, chi hộ, thanh toán) | Q | Đ; duyệt cấp 2 phí phát sinh vượt ngưỡng | – | Đ | – | Đ\*; G khiếu nại | Đ |
| L1 – Bảng kê, quyết toán, biên bản thanh lý | G (lập) | Q (gửi phê duyệt, gửi khách hàng) | Duyệt cấp 2 quyết toán | – | Đ | – | Đ\*; G xác nhận/khiếu nại, tải bản ký | Đ |
| L1 – Sổ thu – chi, tạm ứng – hoàn ứng | G (lập phiếu, đề nghị tạm ứng, hoàn ứng) | Q (duyệt cấp 1, ghi sổ, báo cáo) | Duyệt cấp 2 phiếu chi/tạm ứng vượt ngưỡng; Đ báo cáo BC-TC | – | Đ; G với `training` | – | – | Đ |
| L1 – Dữ liệu `training` | – | – | – | G (lô của mình) | Đ\*; G đóng vai khách hàng (BR-107) | – | – | Đ |
| Bộ quy tắc, ma trận chứng từ `production` | Đ | Q (thêm, sửa, xóa, chạy thử, đề nghị công bố) | Duyệt cấp 2 công bố | – | Đ | – | – | Đ |
| Bộ quy tắc, ma trận chứng từ `training` | – | Đ | Đ | – | Q (khóa/tình huống mình phụ trách) | – | – | Đ |
| L2 – Kế hoạch, phân công, phê duyệt | – | Q (lập kế hoạch, phân công, giám sát – điều phối `production`, duyệt cấp 1) | Q (duyệt kế hoạch, duyệt cấp 2); Đ chính sách phê duyệt | G (vai được giao trong kế hoạch `training`: `account_manager` – phân công, duyệt cấp 1; BR-211) | G – chỉ `training` của lớp mình: `coordinator` (giám sát, điều phối), `business_manager_proxy` (duyệt kế hoạch, duyệt cấp 2), cấp 1 khi không giao cho người học (BR-207, BR-209) | – | – | Đ |
| L2 – Chính sách phê duyệt, ngưỡng giá trị X, Y, Z | – | Đ | Đ | – | Đ | – | – | Q (nhập, sửa) |
| L2 – Điểm kiểm soát | G (người kiểm soát được chỉ định) | Q | Đ | G (vai `controller` được giao – `training`) | G (`training` của lớp mình) | – | – | Đ |
| L2 – Sự cố, CAPA | G (báo cáo) | Q | Đ; nhận leo thang | G (báo cáo, training) | G (training) | – | – | Đ |
| L3 – Học liệu | Đ | Đ | Đ | Đ (đã xuất bản, đúng đối tượng) | Q | Q | – | Đ |
| L3 – Phỏng vấn (phiên online, tải lên, hậu kỳ) | – | – | – | Đ (đã xuất bản) | Q | G (hậu kỳ) | – | Đ |
| L3 – Lớp, ghi danh | – | Đ | – | Đ\* | Q (lớp mình) | – | – | Q |
| L4 – Danh mục KPI, thang điểm | Đ | G (đề xuất mục tiêu) | G (duyệt mục tiêu KPI hợp đồng qua kế hoạch) | Đ | Q | – | – | Q |
| L4 – Tình huống, đáp án | – | – | – | – | Q | – | – | Đ |
| L4 – Kết quả KPI vận hành | Đ\* | Đ | Đ | – | Đ | – | – | Đ |
| L4 – Kết quả học tập | – | Đ (học viên là nhân viên đơn vị mình – **CẦN XÁC NHẬN**) | – | Đ\* | Q (chấm, xác nhận) | – | – | Đ |
| Danh mục dùng chung khác `md_*` | Đ | G (quy trình mẫu, khách hàng/đối tác) | Đ | Đ | Đ | Đ | – | Q |
| Người dùng, vai trò | – | G (tạo tài khoản `customer` cho khách hàng của hợp đồng) | – | – | – | – | G (`is_customer_admin`: tài khoản cùng khách hàng) | Q |

Phạm vi dữ liệu (row-level) được kiểm tra trong lớp `services`, không chỉ ở router.

## 5. Nhật ký và giám sát

- `sys_audit_logs`: `actor_id`, `action`, `entity_type`, `entity_id`, `before` (JSON), `after` (JSON), `ip`, `request_id`, `at`. Không ghi nội dung file, mật khẩu, token.
- Log ứng dụng dạng JSON, có `request_id`.
- `/ready` kiểm tra kết nối CSDL, kho file và độ trễ outbox (số sự kiện tồn > ngưỡng → `503`).

## 6. Cấu hình (biến môi trường)

| Biến | Mặc định | Ghi chú |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://logistics:logistics@localhost:5432/logistics_3pl_dev` | PostgreSQL 17 cho mọi môi trường (đã chốt 08/11/2026) |
| `TEST_DATABASE_URL` | `postgresql+psycopg://logistics:logistics@localhost:5432/logistics_3pl_test` | CSDL riêng cho test tích hợp; bộ test xóa/tạo lại bảng trên CSDL này |
| `STORAGE_BACKEND` | `local` | `local` / `s3` |
| `UPLOAD_DIR` | `./data/uploads` | Khi `local` |
| `JWT_SECRET`, `JWT_ACCESS_TTL_MIN`, `JWT_REFRESH_TTL_DAYS` | – / 15 / 7 | `JWT_SECRET` bắt buộc, không có mặc định, tối thiểu 32 ký tự; thiếu thì ứng dụng không khởi động |
| `ENABLED_FLOWS` | `ops,control,learning,assessment` | Bật/tắt module (N6); giá trị ngoài 4 mã này bị từ chối khi khởi động |
| `EVENT_DISPATCH_INTERVAL_SEC` | `2` | Chu kỳ phát sự kiện outbox |
| `FILE_LIMITS_JSON` | (§2.6) | Ghi đè danh sách loại và dung lượng file |
| `CORS_ORIGINS` | rỗng | Danh sách nguồn frontend được phép (gồm miền của cổng khách hàng) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | – / `587` / – / – / – | Gửi thư mời phỏng vấn, OTP, thông báo cổng khách hàng |
| `MEDIA_SERVER` | `livekit` | Máy chủ media WebRTC tự triển khai: `livekit` / `jitsi` (`04` §3.3.1a) |
| `MEDIA_SERVER_URL`, `MEDIA_SERVER_API_KEY`, `MEDIA_SERVER_API_SECRET` | – | Kết nối máy chủ media, ký token vào phòng; bắt buộc khi bật Quy trình 1 |
| `TURN_URLS`, `TURN_SHARED_SECRET` | – | Cấp thông tin đăng nhập TURN theo phiên |
| `INTERVIEW_MAX_PARTICIPANTS`, `INTERVIEW_MAX_HOURS` | `6`, `3` | `04` §3.3.1a |
| `TRANSCRIPTION_ENGINE` | `faster_whisper` | Động cơ chuyển giọng nói thành văn bản chạy tại máy chủ đơn vị (`04` §3.3.1b) |
| `TRANSCRIPTION_MODEL_PATH`, `TRANSCRIPTION_DEVICE` | – , `cuda` | Đường dẫn mô hình cục bộ; `cuda` / `cpu` |
| `JOB_QUEUE_URL` | – | Hàng đợi tác vụ nền (xử lý media, tính KPI, phát sự kiện) |

Đọc bằng `app/core/config.py` (pydantic-settings): biến môi trường ưu tiên hơn file `.env` (mẫu `.env.example`); không phân biệt hoa thường; biến danh sách (`ENABLED_FLOWS`, `CORS_ORIGINS`, `TURN_URLS`) viết cách nhau bằng dấu phẩy; `FILE_LIMITS_JSON` là chuỗi JSON. Biến khác trong `.env` (vd. `POSTGRES_PASSWORD` của docker-compose) được bỏ qua.
