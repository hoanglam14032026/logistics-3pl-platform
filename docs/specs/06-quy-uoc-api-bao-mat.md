# 06 – Quy ước API, dữ liệu, file, bảo mật và phân quyền

> Áp dụng cho **mọi** luồng. Các mục đánh dấu (C3)–(C10) xử lý những sai sót của v1.1.0 đã nêu trong `90-hien-trang-v1.1.md`.

## 1. Cấu trúc đường dẫn

```
/api/v2/ops/...          Luồng 1
/api/v2/control/...      Luồng 2
/api/v2/learning/...     Luồng 3
/api/v2/assessment/...   Luồng 4
/api/v2/portal/...       Cổng khách hàng (thuộc Luồng 1, chỉ vai trò customer); /api/v2/portal/users – customer admin
/api/v2/md/...           Danh mục dùng chung (gồm bộ quy tắc, ma trận chứng từ); /api/v2/md/parties (khách hàng, đối tác)
/api/v2/auth/...         Đăng nhập, làm mới token, đăng xuất, đổi mật khẩu, người dùng hiện tại
/api/v2/users/...        Quản lý người dùng, vai trò (admin – §4)
/api/v2/audit-logs       Tra cứu nhật ký thao tác (admin – §5)
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

`code` thuộc danh mục cố định: `VALIDATION_ERROR`, `NOT_FOUND`, `FORBIDDEN`, `UNAUTHENTICATED`, `INVALID_STATE_TRANSITION`, `DUPLICATE`, `VERSION_CONFLICT`, `FILE_TOO_LARGE`, `UNSUPPORTED_FILE_TYPE`, `RATE_LIMITED` (`429`, header `Retry-After` – thêm ở Giai đoạn 2), `INTERNAL_ERROR`.

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

**Đã làm (Giai đoạn 4, 08/11/2026)** – `app/shared/files/`: `sys_files` (`storage_key` = `<module>/<yyyy>/<mm>/<uuid4>`, tên gốc đã làm sạch, MIME theo nội dung, SHA-256, `owner_type`/`owner_id`); `LocalStorage` ghi theo luồng 1 MiB vào file tạm rồi đổi tên, dừng ngay khi vượt dung lượng, chặn `storage_key` thoát khỏi `UPLOAD_DIR`; nhận dạng magic bytes `pdf`, `jpg`, `png`, `docx`, `xlsx`, `pptx` (chưa dùng thư viện `filetype`); mục đích `contract_document` (`pdf, jpg, png, docx, xlsx` ≤ 20 MB, ghi đè bằng `FILE_LIMITS_JSON`); `0 byte` → `422`, quá dung lượng → `413`, sai loại → `415`; lỗi sau khi ghi file → xóa file (không file mồ côi). `GET /api/v2/files/{id}`: kiểm quyền qua `ACCESS_CHECKERS[owner_type]` (module sở hữu đăng ký), không có quyền → `404`; `Range`, `Content-Disposition` (tên UTF-8), `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox`, `Cache-Control: private, no-store`. Chưa làm: upload theo phần (> 100 MB), S3, quét mã độc, dọn file mồ côi định kỳ. Giới hạn kích thước body ở reverse proxy (vd. `client_max_body_size`) – Starlette nhận trọn file trước khi ứng dụng kiểm dung lượng.

**Giai đoạn 9 (09/11/2026)** – mục đích `doc_ai_source` (chứng từ đưa vào AI trích xuất): `jpg, png, tiff, webp, pdf, docx, xlsx, txt` ≤ 20 MB. Nhận dạng thêm `tiff`, `webp`, `txt` theo nội dung: `txt` chỉ nhận khi **toàn bộ** file là UTF-8, không có byte điều khiển và không mở đầu như HTML/XML/SVG/script (file thực thi hay HTML đổi đuôi → `415`); `doc`/`xls` cũ (OLE2) nhận ra để trả `415` kèm hướng dẫn lưu lại thành `docx`/`xlsx` (chưa chuyển đổi – **CẦN XÁC NHẬN**). Quyền tải file gốc: `owner_type = doc_job` – người tạo job, `manager`.

## 3. Xác thực

- Đăng nhập tài khoản nội bộ, mật khẩu băm Argon2/bcrypt; JWT truy cập (15 phút) + refresh token (7 ngày, thu hồi được).
- **Đã làm (bước 0.6, 08/11/2026)** – `app/core/security.py`, `app/core/rbac.py`, `app/shared/identity/`:
  - Mật khẩu: Argon2id (`argon2-cffi`, tham số mặc định m = 64 MiB, t = 3, p = 4; băm lại khi tham số đổi); 8–128 ký tự,
    không trùng tên đăng nhập. Tên đăng nhập `[a-z0-9._-]{3,64}`, không phân biệt hoa thường.
  - Access token: JWT HS256 (`PyJWT`), thuật toán cố định khi giải mã; claim `iss`, `sub`, `type = access`, `roles`,
    `iat`, `exp`, `jti`, `pwd_at`. Mỗi request đọc lại người dùng từ CSDL: tài khoản bị vô hiệu hóa, đổi mật khẩu
    (`pwd_at` lệch `password_changed_at`) có hiệu lực ngay; vai trò lấy từ CSDL, không tin claim `roles`.
  - Refresh token: chuỗi ngẫu nhiên 384 bit (không phải JWT), CSDL chỉ lưu SHA-256 (`sys_refresh_tokens`); xoay vòng
    mỗi lần `POST /api/v2/auth/refresh`; dùng lại token đã xoay vòng → thu hồi cả họ token (`family_id`);
    `logout` thu hồi phiên; đổi/đặt lại mật khẩu, vô hiệu hóa tài khoản → thu hồi mọi phiên.
  - Khóa tạm: sai `AUTH_MAX_FAILED_LOGINS` (5) lần liên tiếp → khóa `AUTH_LOCKOUT_MINUTES` (15) phút, áp dụng mọi tài khoản;
    đang khóa thì mật khẩu đúng cũng bị từ chối; đăng nhập đúng đặt lại bộ đếm; `admin` mở khóa qua
    `POST /api/v2/users/{id}:unlock`. Dòng người dùng khóa `SELECT … FOR UPDATE` khi kiểm tra (đăng nhập song song
    không vượt ngưỡng). Sai mật khẩu và tài khoản không tồn tại: cùng thông báo, cùng thời gian xử lý.
  - Không cần access token: `login`, `refresh`, `logout` (xác thực bằng mật khẩu/refresh token). Phản hồi có token:
    `Cache-Control: no-store`. Lỗi `401` có header `WWW-Authenticate: Bearer`.
  - Tài khoản `admin` đầu tiên: `python -m app.shared.identity.cli create-admin` (mật khẩu nhập ẩn hoặc qua stdin).
  - Thời gian khóa 15 phút, độ dài mật khẩu tối thiểu 8: **CẦN XÁC NHẬN** (đặc tả gốc chỉ nêu "5 lần sai").
- **Giới hạn tần suất (Giai đoạn 2, 08/11/2026)** – `app/core/rate_limit.py`: `POST /api/v2/auth/login` tối đa
  `AUTH_LOGIN_RATE_LIMIT_PER_IP` (20) lần/IP và `AUTH_LOGIN_RATE_LIMIT_PER_USERNAME` (10) lần/tên đăng nhập mỗi
  `AUTH_LOGIN_RATE_WINDOW_SEC` (60) giây; vượt → `429 RATE_LIMITED` + `Retry-After`, kiểm tra **trước** khi băm mật khẩu
  (lần bị chặn không tính là đăng nhập sai). Bộ đếm cửa sổ cố định trong PostgreSQL (`sys_rate_limit_counters`) – đúng khi
  chạy nhiều worker/container. Sau reverse proxy: chạy `uvicorn --proxy-headers --forwarded-allow-ips=<IP proxy>` để
  đếm theo IP thật. Ngưỡng: **CẦN XÁC NHẬN**.
- **Giới hạn tần suất mọi API và tải file (Giai đoạn 7, 09/11/2026)** – middleware `app/shared/identity/throttle.py`,
  cùng bộ đếm PostgreSQL: mọi request `/api/…` theo tài khoản (access token hợp lệ – `RATE_LIMIT_API_PER_USER` = 300)
  hoặc theo IP khi chưa đăng nhập / token không hợp lệ (`RATE_LIMIT_API_PER_IP` = 120); thêm `multipart/form-data`
  (`RATE_LIMIT_FILE_UPLOAD_PER_USER` = 20) và `GET /api/v2/files/…` (`RATE_LIMIT_FILE_DOWNLOAD_PER_USER` = 120); mỗi
  `RATE_LIMIT_WINDOW_SEC` = 60 giây. Vượt → `429 RATE_LIMITED` + `Retry-After` (như `/login`), kiểm **trước** khi đọc
  body (file bị từ chối không được nhận); lần đầu vượt trong cửa sổ ghi nhật ký `api.rate_limited`. Token giả (sai chữ
  ký) tính theo IP – không làm cạn hạn mức của tài khoản khác. Áp cho mọi vai trò (đặc tả nêu `customer`); ngưỡng
  **CẦN XÁC NHẬN**; `RATE_LIMIT_ENABLED=false` tắt.
- **Xác thực 2 lớp TOTP (Giai đoạn 5, 08/11/2026)** – `app/core/totp.py` (RFC 6238 viết bằng thư viện chuẩn, kiểm theo
  vector RFC; HMAC-SHA1, 6 số, 30 giây, lệch ±1 chu kỳ; mã đã dùng không dùng lại – `totp_last_step`),
  `app/shared/identity/twofactor.py`:
  - Đăng nhập 2 bước: `POST /login` đúng mật khẩu → token, hoặc `mfa_required` / `mfa_setup_required` + `mfa_token`
    (JWT `type = mfa`, `AUTH_MFA_TOKEN_TTL_SEC` = 300 giây; không dùng được như access token) → `POST /login/totp`, hoặc
    `/login/2fa-setup` + `/login/2fa-enable`. Mã sai tính như đăng nhập sai (khóa tạm); đúng mật khẩu chưa đặt lại bộ
    đếm tới khi qua lớp 2.
  - Bắt buộc theo vai trò `AUTH_2FA_REQUIRED_ROLES` (mặc định `customer`); đặc tả nêu thêm `manager`, `business_manager`
    – đặt `customer,manager,business_manager` để áp dụng (**CẦN XÁC NHẬN**). Tài khoản bắt buộc không tắt được 2FA.
  - Access token có claim `amr` (`pwd` / `pwd`, `otp`); tài khoản đã bật/bắt buộc 2FA chỉ nhận token có `otp`.
    Refresh token lưu `mfa`; phiên chưa qua 2 lớp của tài khoản bắt buộc 2FA không làm mới được.
  - Người đã đăng nhập: `POST /2fa/setup`, `/2fa/enable` (thu hồi phiên cũ, trả token mới), `/2fa/disable` (mật khẩu +
    mã). `admin`: `POST /api/v2/users/{id}:reset-2fa` (mất thiết bị – thu hồi phiên, thiết lập lại lần sau).
  - `totp_secret` **mã hóa Fernet** (Giai đoạn 6, 09/11/2026 – `app/core/secretbox.py`, thư viện `cryptography`) bằng
    khóa riêng `TOTP_ENCRYPTION_KEY` (không dùng lại `JWT_SECRET`); chỉ giải mã lúc kiểm mã. CHECK CSDL chặn ghi dạng
    rõ. Xoay khóa: `<khóa mới>,<khóa cũ>`. Sai khóa → `500 INTERNAL_ERROR`, không tính là nhập sai mã. Mất khóa: mọi
    người dùng phải được `admin` `:reset-2fa` rồi thiết lập lại. OTP qua email (02 §9.2 "hoặc"): chưa làm.
- **Thư mời, quên/đặt lại mật khẩu (Giai đoạn 5)** – `app/shared/identity/email_flows.py`, `app/shared/notifications/`:
  token 256 bit, chỉ lưu SHA-256 (`sys_user_tokens`), dùng một lần, có hạn (`INVITE_TOKEN_TTL_HOURS` = 72,
  `PASSWORD_RESET_TOKEN_TTL_MIN` = 30); cấp token mới → token cũ cùng mục đích vô hiệu; đường dẫn
  `PUBLIC_BASE_URL/accept-invite#token=…`, `/reset-password#token=…` (token sau `#` – không lọt log máy chủ).
  `POST /api/v2/auth/invitations/accept`, `/password/forgot` (luôn `202` cùng nội dung; giới hạn tần suất như đăng nhập),
  `/password/reset` (thu hồi mọi phiên, mở khóa tạm, gửi thư báo mật khẩu đã đổi; 2FA giữ nguyên).
  **Gửi thư là tác vụ nền** (Giai đoạn 6–7): request ghi nhật ký, commit rồi xếp tác vụ – tham số chỉ là id, **token
  cấp lúc gửi** (hàng đợi không chứa token). Request không chờ SMTP (quên mật khẩu trả về cùng thời gian dù tài khoản
  có tồn tại hay không).
  - Có `JOB_QUEUE_URL` (Giai đoạn 7): hàng đợi bền vững **rq + Redis** (`app/core/jobs.py`, Redis bật AOF), worker
    riêng `python -m app.worker` (dịch vụ `worker` trong docker-compose). API, Redis hay worker dừng/khởi động lại: tác
    vụ không mất; worker chết giữa chừng → rq chạy lại tác vụ bị bỏ dở. Gửi lỗi → thử lại `JOB_MAX_RETRIES` (5) lần sau
    `JOB_RETRY_INTERVALS` (10, 30, 120, 600, 1800 giây); mỗi lần thử cấp token mới (token chưa gửi được bị vô hiệu);
    hết lượt → log + nhật ký `user.mail_failed` (`purpose`: `invite` / `password_reset` / `password_changed`, `error`,
    `attempts`), tác vụ giữ trong FailedJobRegistry `JOB_FAILURE_TTL_DAYS` (30) ngày. Thư mời bỏ qua nếu tài khoản đã
    nhận lời mời / bị vô hiệu hóa lúc chạy. rq chạy "ít nhất một lần": hiếm khi có thể nhận 2 thư (thư sau thay thư
    trước).
  - Không có `JOB_QUEUE_URL` (máy dev, test) hoặc Redis lỗi lúc xếp: chạy nền trong tiến trình API (`BackgroundTasks`)
    – một lần, không thử lại, mất nếu tiến trình dừng; lỗi → log + `user.mail_failed`.
  - Request không lỗi vì gửi thư; gửi lại `:resend-invite` (`202`) hoặc quên mật khẩu lần nữa.
- Tích hợp SSO của trường/doanh nghiệp (Google Workspace, Microsoft Entra ID): **CẦN XÁC NHẬN**.
- Mọi endpoint trừ `/health`, `/ready`, `/api/v2/auth/login`, trang khách mời phỏng vấn (`/api/v2/learning/interview-invitations/{token}` – xác thực bằng token, `04` BR-309) và webhook của máy chủ media phỏng vấn (xác thực bằng chữ ký) yêu cầu xác thực.
- Tài khoản `customer`: bắt buộc xác thực 2 lớp; khóa tạm sau 5 lần đăng nhập sai; giới hạn tần suất gọi API và tải file theo tài khoản; phiên tối đa 8 giờ. **Lộ trình (đã chốt 08/11/2026):** Giai đoạn 0 (bước 0.6) chỉ làm JWT + refresh token + RBAC + khóa tạm sau nhiều lần đăng nhập sai; bảng `sys_users` có sẵn cột `totp_secret` (NULL) và `is_2fa_enabled` (mặc định `false`); endpoint và luồng xác thực 2 lớp làm cùng cổng khách hàng (Giai đoạn 2). Tài khoản nội bộ có quyền phê duyệt hợp đồng – tài chính (`manager`, `business_manager`) cũng bắt buộc xác thực 2 lớp; thao tác phê duyệt cấp 2 yêu cầu xác thực lại (nhập lại OTP) nếu phiên đã quá 30 phút.

### 3.1. AI local trích xuất chứng từ – bảo mật nội bộ (Giai đoạn 9, 09/11/2026)

Mọi thông tin nội bộ không ra ngoài: chỉ dùng model AI chạy trong máy / mạng nội bộ, **không gọi API cloud nào**. Áp dụng
cho mọi tiến trình có nhập chứng từ qua module chung `app/shared/docai/` (hợp đồng, lô hàng/vận đơn, khách hàng/đối tác,
hóa đơn…).

| Quy định | Nội dung |
|---|---|
| Máy chủ model | Chỉ Ollama (`provider = ollama`) tại `OLLAMA_URL` hoặc `endpoint` riêng của model trong danh mục `ai_models`. Kiểm khi khởi động (`OLLAMA_URL` sai → ứng dụng **không khởi động**), khi admin lưu model (`422`) và **mỗi lần gọi** (`app/core/netguard.py`) |
| Địa chỉ cho phép | `http`/`https`, không tên đăng nhập/mật khẩu/query trong URL; máy chủ: IP loopback, mạng riêng RFC 1918, `fc00::/7`; tên `localhost`, tên một nhãn (dịch vụ docker), hậu tố `.localhost`, `.local`, `.internal`, `.lan`, `.home.arpa`, hoặc `AI_ALLOWED_HOSTS`. Chặn IP công khai, link-local `169.254.0.0/16` (metadata máy ảo đám mây), CGNAT, multicast |
| Lúc kết nối | Phân giải DNS – **mọi** địa chỉ phải nội bộ, kết nối thẳng IP đã kiểm (chống DNS rebinding); bỏ qua `HTTP(S)_PROXY`; không theo chuyển hướng; giới hạn dung lượng phản hồi; không ghi nội dung chứng từ vào log |
| Dữ liệu | File gốc (kho `shared/files`, nội bộ), văn bản OCR + bbox, đầu ra thô của model, kết quả đã chuẩn hóa chỉ lưu trong CSDL (`sys_doc_jobs`, `sys_doc_extractions`) và ổ đĩa nội bộ. Mã hóa `totp_secret` (Fernet) giữ nguyên |
| Hàng đợi | rq + Redis (Giai đoạn 7): tác vụ `process_doc_job` chỉ mang `job_id`, `user_id` – Redis không chứa file, văn bản, kết quả AI, token, bí mật. Không đưa token, mật khẩu vào log |
| OCR | Tesseract (`vie+eng`) chạy cục bộ qua tiến trình con; PaddleOCR (tùy chọn) chỉ nạp model tải sẵn (`DOCAI_PADDLE_MODEL_DIR`), tắt kiểm tra/tải model từ Internet; hoặc model loại `ocr` trong danh mục |
| Chọn model | Người thực hiện **bắt buộc** chọn `model_id` (danh sách `GET /api/v2/ai-models/active`, không có `endpoint`); hệ thống không tự chọn model mặc định. `AI_MODEL_DEFAULT` chỉ đánh dấu gợi ý hiển thị |
| Duyệt | Upload → OCR → AI trích xuất → tự điền form (không lưu bảng nghiệp vụ) → người duyệt xem, sửa → duyệt/từ chối. Độ tin cậy < `DOCAI_MIN_CONFIDENCE` (0,9) hoặc thiếu trường bắt buộc → `needs_review` (trường thấp phải sửa hoặc xác nhận từng trường); ≥ 0,9 vẫn phải duyệt. Chưa duyệt → không tạo được bản ghi nghiệp vụ (`source_doc_job_id`) |
| Duyệt 2 người (Giai đoạn 10) | Loại chứng từ có `dual_approval` trong schema JSON: hợp đồng (`service_contract`) luôn; hóa đơn (`commercial_invoice`) khi `total_amount` ≥ ngưỡng của đồng tiền (`DOCAI_DUAL_APPROVAL_THRESHOLDS`, mặc định VND 500.000.000, USD/EUR 20.000 – **CẦN XÁC NHẬN**; đồng tiền không có ngưỡng → luôn 2 người). Lần 1 → `pending_second_approval`; lần 2 phải do người **khác người tải lên và khác người duyệt đầu** (`403`), CSDL kiểm thêm bằng CHECK. Người tải lên được là người duyệt đầu. Đang chờ người thứ 2: không sửa được; người thứ 2 từ chối được |
| Lời nhắc | Văn bản chứng từ được đánh dấu là **dữ liệu**, model được dặn bỏ qua câu lệnh trong văn bản; đầu ra luôn qua kiểm kiểu + người duyệt |

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

**Đã làm – AI trích xuất chứng từ (Giai đoạn 9)**: danh mục model `/api/v2/admin/ai-models` – chỉ `admin` (xem đủ, thêm, sửa, xóa mềm, bật/tắt, thử kết nối); `GET /api/v2/ai-models/active` – `ops_staff`, `manager`, `admin` (không có `endpoint`, tên model trong máy chủ); job `/api/v2/doc-jobs`, `/api/v2/doc-types` – `ops_staff` (tải lên, xem, sửa, duyệt/từ chối job **của mình**), `manager` (mọi job); vai trò khác, `customer` → `403`; job không có quyền → `404`. Tạo bản ghi từ job vẫn theo quyền của API tạo (`md_parties`: `manager`, `admin`; hợp đồng: `ops_staff`, `manager`; lô hàng: `ops_staff`) và phải thấy được job.

**Đã làm – Cổng gửi chứng từ, quy tắc chứng từ (Giai đoạn 17)**: `customer` gửi / xem chứng từ lô của khách hàng mình qua `/api/v2/portal/shipments/{id}/documents` (lô khác → `404`; không tải file chứng từ về được); `/api/v2/ops/document-requirements` – đọc: vai trò nội bộ; thêm, bật / tắt: `admin`, `manager`. Nhật ký `shipment.reopen_documents`, `document_requirement.*`.

**Đã làm – Địa điểm, chứng từ, điểm kiểm soát (Giai đoạn 16)**: `/api/v2/md/locations` – đọc: vai trò nội bộ (mọi địa điểm), `customer` (chỉ địa điểm đang hoạt động gắn khách hàng của mình, khác → `404`); tạo / sửa / lưu trữ / khôi phục: `admin`, `manager` (khác dòng "Danh mục dùng chung khác `md_*`" ở bảng trên – `manager` G, **CẦN XÁC NHẬN**). Chứng từ lô, lần kiểm tra, điểm kiểm soát – đọc như lô hàng; tải lên, ghi kết quả, tạo / xác nhận điểm kiểm soát – `ops_staff` phụ trách lô; `customer` không truy cập (file chứng từ cũng không). Nhật ký `location.*`, `document.upload`, `document_check.complete`, `checkpoint.create`, `checkpoint.evaluate`.

**Đã làm – Mục tiêu KPI, tính KPI định kỳ (Giai đoạn 15)**: mục tiêu KPI của kế hoạch – `manager` sửa, gửi duyệt, sửa sau duyệt; `business_manager` duyệt / trả lại (manager không tự duyệt). `/api/v2/assessment/kpi/definitions|targets|measurements|calc-runs|alerts` – đọc như báo cáo KPI; `POST /kpi/calc-runs` – chỉ `admin`. Cảnh báo KPI ở `/api/v2/control/alerts` – `business_manager` xác nhận / đóng được. Nhật ký `kpi.alert`, `kpi.calc_failed`.

**Đã làm – L4 KPI vận hành (Giai đoạn 14)**: `/api/v2/assessment/kpi*` – chỉ `manager`, `business_manager`, `admin` (Đ); `ops_staff`, `instructor`, `learner`, `customer` → `403` (hẹp hơn dòng "L4 – Kết quả KPI vận hành" ở bảng trên: `ops_staff` Đ\*, `instructor` Đ – **CẦN XÁC NHẬN**). Chỉ dữ liệu `production`. Đọc báo cáo không ghi nhật ký.

**Đã làm – Đồng bộ bước, leo thang, thông báo (Giai đoạn 13)**: `/api/v2/ops/shipments/{id}/tasks` – đọc như lô hàng; `/api/v2/ops/tasks/{id}:start|:complete|:block|:unblock` – `ops_staff` phụ trách lô/bước; `POST /api/v2/control/shipment-plans/{id}:resync` – `manager`; cảnh báo đã leo thang (BR-205): `business_manager` xác nhận / đóng được. Nhật ký `ops_task.*`, `ops_task.sync_conflict`, `task.sync_conflict`, `task.resync`, `alert.escalate` (`kind`: `severity` / `business_manager`), `alert.notify_failed`.

**Đã làm – L2 Theo dõi thực thi, cảnh báo (Giai đoạn 12)**: cập nhật bước `/api/v2/control/tasks/{id}` (`:start`, `PATCH`, `:block`, `:unblock`, `:complete`) – người được giao bước, `manager`; theo dõi `/tracking/*` – như kế hoạch (`ops_staff`: lô của mình); cảnh báo `/alerts` – `manager` (xác nhận, đóng, quét), `business_manager`, `admin` đọc. Nhật ký `task.start` / `task.progress` / `task.block` / `task.unblock` / `task.complete`, `alert.raise` / `alert.escalate` / `alert.acknowledge` / `alert.resolve`.

**Đã làm – L2 Kế hoạch, phân công (Giai đoạn 11)**: `/api/v2/control/*` – `manager` Q (điều chuyển có lý do, chạy lại phân công tự động, khối lượng việc, hồ sơ năng lực); `business_manager`, `admin` Đ; `ops_staff` Đ\* (kế hoạch mình là thành viên / có lô mình phụ trách, lô và bước của mình); vai trò khác, `customer` → `403`. Nhật ký: `plan.create`, `shipment_plan.create`, `assignment.change` / `assignment.unassigned`, `staff_profile.update`, `event.consume_failed`. `:assign` của Luồng 1 deprecated (thay bằng `POST /api/v2/control/assignments` → EV-202).

**Đã làm – Hóa đơn thương mại (Giai đoạn 10)**: `/api/v2/ops/commercial-invoices` – đọc `ops_staff`, `manager`, `business_manager`, `instructor`, `admin`; tạo `ops_staff` (gắn lô **được phân công**), `manager`; không sửa/xóa. Người duyệt thứ 2 của job trích xuất thực tế là `manager` (`ops_staff` chỉ thấy job của mình).

**Đã làm – L1 Lô hàng (Giai đoạn 8)**: đọc `ops_staff`, `manager`, `business_manager`, `instructor`, `admin`; tạo `ops_staff`; sửa nháp, chuyển trạng thái, hủy – `ops_staff` **được phân công** (khác → `403`); `manager` chỉ đọc + phân công lại (tạm); `customer` xem mốc tiến độ lô của khách hàng mình qua cổng; `learner`, `content_editor` → `403`. Gửi chứng từ (`customer` – G): chưa làm.

## 5. Nhật ký và giám sát

- `sys_audit_logs`: `actor_id`, `action`, `entity_type`, `entity_id`, `before` (JSON), `after` (JSON), `ip`, `request_id`, `at`. Không ghi nội dung file, mật khẩu, token.
  **Đã làm (Giai đoạn 2, 08/11/2026)** – `app/shared/audit/`: ghi trong cùng giao dịch với thay đổi; chỉ ghi trường trong
  danh sách cho phép, `before`/`after` chỉ gồm trường thay đổi; trigger CSDL chặn `UPDATE`/`DELETE` (môi trường thật: cấp
  cho tài khoản CSDL của ứng dụng chỉ `INSERT`, `SELECT` trên bảng). Hành động: `party.create`/`update`/`archive`/`restore`;
  `user.create`/`update`/`deactivate`/`activate`/`password_reset`/`password_change`/`lock` (tự động, `actor_id` NULL)/`unlock`;
  `auth.login_failed` (`after.reason`: `unknown_user`, `invalid_password`, `locked`, `disabled`), `auth.rate_limited`
  (lần đầu vượt ngưỡng mỗi cửa sổ), `auth.refresh_reuse_detected`; `contract.create`/`update`/`activate`/`suspend`/`resume`/
  `expire` (Giai đoạn 3). **API đọc (Giai đoạn 3)**: `GET /api/v2/audit-logs` (`admin`; §2.3 – lọc `action` khớp đúng hoặc
  tiền tố `party.`, `entity_type` + `entity_id`, `actor_id`, `request_id`, `from`/`to` ISO 8601 có múi giờ; mặc định
  `-at`), `GET /api/v2/audit-logs/{id}`; không có API sửa/xóa.
  **Giai đoạn 10**: `invoice.create`; `doc_job.approve` có `stage` (`single` / `first` / `second`); `outbox.dispatch_failed` (sự kiện hết lượt gửi); mọi sự kiện vẫn có dòng `event.<mã>`.
  **Giai đoạn 9**: `ai_model.create`/`update`/`delete`/`activate`/`deactivate`; `file.uploaded` (sha256, MIME, dung lượng, chủ sở hữu); `doc_job.create` (loại chứng từ, model đã chọn: `ai_model_id`, `model_code`, `model_ref`, OCR), `doc_job.extracted` (AI trích ra gì: giá trị + độ tin cậy từng trường, lần chạy, thời gian OCR/AI – tác vụ nền ghi `request_id`, `ip` của request tải lên), `doc_job.review` (từng trường: giá trị AI / trước / sau), `doc_job.approve` (dữ liệu đã duyệt, trường được xác nhận), `doc_job.reject` (lý do), `doc.failed` (lỗi, số lần thử), `doc_job.linked` (bản ghi nghiệp vụ tạo từ job + trường người tạo đổi khác dữ liệu đã duyệt). Giá trị dài rút gọn trong nhật ký; bản đầy đủ ở `sys_doc_jobs`/`sys_doc_extractions`.
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
| `TOTP_ENCRYPTION_KEY` | – | Bắt buộc (Giai đoạn 6). Khóa Fernet (32 byte base64 url-safe – `python -m app.core.secretbox`) mã hóa `totp_secret`; khác `JWT_SECRET`; nhiều khóa cách dấu phẩy để xoay khóa (khóa đầu mã hóa) |
| `AUTH_MAX_FAILED_LOGINS`, `AUTH_LOCKOUT_MINUTES` | 5 / 15 | Khóa tạm sau N lần đăng nhập sai liên tiếp (§3) |
| `AUTH_LOGIN_RATE_LIMIT_PER_IP`, `AUTH_LOGIN_RATE_LIMIT_PER_USERNAME`, `AUTH_LOGIN_RATE_WINDOW_SEC` | 20 / 10 / 60 | Giới hạn tần suất đăng nhập, quên mật khẩu (§3) |
| `AUTH_2FA_REQUIRED_ROLES`, `AUTH_MFA_TOKEN_TTL_SEC`, `TOTP_ISSUER` | `customer` / 300 / `Logistics 3PL` | Xác thực 2 lớp (§3) |
| `INVITE_TOKEN_TTL_HOURS`, `PASSWORD_RESET_TOKEN_TTL_MIN`, `PUBLIC_BASE_URL` | 72 / 30 / `http://localhost:5173` | Thư mời, đặt lại mật khẩu (§3) |
| `SMTP_SECURITY`, `SMTP_TIMEOUT_SEC`, `MAIL_BACKEND` | `starttls` / 10 / `auto` | `auto`: SMTP nếu có `SMTP_HOST`, ngược lại `console` (ghi log – chỉ máy dev) |
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
| `JOB_QUEUE_URL` | – | Hàng đợi tác vụ nền bền vững rq + Redis (`redis://…`) – Giai đoạn 7: gửi email; sau này: xử lý media, tính KPI, phát sự kiện. Chưa đặt: chạy nền trong tiến trình API (chỉ máy dev) |
| `TEST_JOB_QUEUE_URL` | `redis://localhost:6379/15` | Redis cho test hàng đợi – bộ test xóa sạch CSDL Redis này (không được là số 0) |
| `JOB_MAX_RETRIES`, `JOB_RETRY_INTERVALS`, `JOB_TIMEOUT_SEC`, `JOB_FAILURE_TTL_DAYS` | 5 / `10,30,120,600,1800` / 60 / 30 | Thử lại khi tác vụ lỗi (§3) |
| `RATE_LIMIT_ENABLED`, `RATE_LIMIT_WINDOW_SEC` | `true` / 60 | Giới hạn tần suất mọi API (§3) |
| `RATE_LIMIT_API_PER_USER`, `RATE_LIMIT_API_PER_IP`, `RATE_LIMIT_FILE_UPLOAD_PER_USER`, `RATE_LIMIT_FILE_DOWNLOAD_PER_USER` | 300 / 120 / 20 / 120 | Số request tối đa mỗi cửa sổ |
| `EVENT_STREAM_PREFIX`, `EVENT_STREAM_MAXLEN` | `l3pl:events:` / 100000 | Giai đoạn 10 – Redis Stream gửi sự kiện outbox cho từng luồng nhận (`<prefix>control`, `<prefix>assessment`) trên `JOB_QUEUE_URL`; giữ khoảng N bản ghi mỗi stream |
| `DOCAI_DUAL_APPROVAL_THRESHOLDS` | `VND:500000000,USD:20000,EUR:20000` | Giai đoạn 10 – hóa đơn có tổng tiền ≥ ngưỡng của đồng tiền cần 2 người duyệt; đồng tiền không có trong danh sách → luôn 2 người |
| `CONTROL_SCAN_INTERVAL_SEC`, `CONTROL_OVERDUE_CRITICAL_HOURS` | 300 / 24 | Giai đoạn 12 – chu kỳ quét chậm trễ của Luồng 2 (tác vụ rq tự xếp lại; 0 = tắt); bước trễ từ N giờ → cảnh báo `critical` |
| `CONTROL_ESCALATION_HOURS` | 2 | Giai đoạn 13 – BR-205: cảnh báo chưa xử lý sau N giờ → leo thang `business_manager` |
| `ZALO_TOKEN`, `ZALO_API_URL`, `ZALO_TIMEOUT_SEC` | – / `https://openapi.zalo.me/v3.0/oa/message/cs` / 10 | Giai đoạn 13 – gửi cảnh báo qua Zalo OA (access token của Official Account; không ghi log/Redis); không đặt `ZALO_TOKEN` → chỉ email. Người nhận cần `zalo_user_id` trong hồ sơ (`PUT /api/v2/control/staff-profiles/{user_id}`). Lưu ý: nội dung cảnh báo (số lô, bước, lý do) gửi qua dịch vụ Zalo bên ngoài – **CẦN XÁC NHẬN** |
| `OLLAMA_URL` | `http://localhost:11434` | Giai đoạn 9 – máy chủ model AI local mặc định (model có `endpoint` NULL). **Chỉ localhost / mạng nội bộ** (§3.1) – sai thì ứng dụng không khởi động. Danh mục model đọc từ bảng `ai_models`, không cấu hình bằng biến |
| `AI_ALLOWED_HOSTS` | rỗng | Tên máy chủ nội bộ thêm (cách dấu phẩy); lúc gọi vẫn kiểm DNS ra địa chỉ mạng riêng |
| `AI_MODEL_DEFAULT` | – | `code` model được đánh dấu `suggested` trong `GET /api/v2/ai-models/active` – chỉ gợi ý hiển thị, API vẫn bắt buộc `model_id` |
| `DOCAI_OCR_ENGINE`, `DOCAI_TESSERACT_CMD`, `DOCAI_OCR_LANGS`, `DOCAI_PADDLE_MODEL_DIR`, `DOCAI_OCR_TIMEOUT_SEC` | `tesseract` / `tesseract` / `vie+eng` / – / 120 | OCR cục bộ; `paddle` cần `DOCAI_PADDLE_MODEL_DIR` (model tải sẵn `det/`, `rec/`) và nhóm `ocr-paddle` |
| `DOCAI_MIN_CONFIDENCE` | `0.9` | Dưới ngưỡng / thiếu trường bắt buộc → `needs_review` |
| `DOCAI_MAX_PAGES`, `DOCAI_PDF_DPI`, `DOCAI_MAX_TEXT_CHARS`, `DOCAI_VISION_MAX_PAGES` | 30 / 200 / 60000 / 4 | Giới hạn chuyển đổi, lời nhắc; số trang ảnh gửi model `vision` |
| `DOCAI_LLM_TIMEOUT_SEC`, `DOCAI_JOB_TIMEOUT_SEC` | 600 / 1800 | Thời gian chờ một lần gọi model; một lần chạy tác vụ trong worker |

Đọc bằng `app/core/config.py` (pydantic-settings): biến môi trường ưu tiên hơn file `.env` (mẫu `.env.example`); không phân biệt hoa thường; biến danh sách (`ENABLED_FLOWS`, `CORS_ORIGINS`, `TURN_URLS`, `JOB_RETRY_INTERVALS`, `AI_ALLOWED_HOSTS`) viết cách nhau bằng dấu phẩy; `FILE_LIMITS_JSON` là chuỗi JSON. Biến khác trong `.env` (vd. `POSTGRES_PASSWORD` của docker-compose) được bỏ qua.
