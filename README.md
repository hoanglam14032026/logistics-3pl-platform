# Nền tảng vận hành và đào tạo nghiệp vụ Logistics 3PL

> **Đặc tả v2.3** (08/11/2026) – thư mục [`docs/specs/`](docs/specs/00-muc-luc-va-quy-uoc.md).
> **Mã nguồn hiện tại: v1.1.0** – đang chuyển đổi theo đặc tả v2.x. Hiện trạng: [`docs/specs/90-hien-trang-v1.1.md`](docs/specs/90-hien-trang-v1.1.md). Lộ trình chuyển đổi: [`docs/specs/07`](docs/specs/07-cau-truc-ma-nguon-chuyen-doi-lo-trinh.md).

> **Trạng thái mới nhất - G22: P1 chuan hoa EV + P2 Learning (resources 7 loai, topics T01..T07, courses, progress) đã xong - 88b6386**
> - Stage 22: AI Gateway - lỗi có kiểu 502/504, router `/api/ai` chỉ `ops_staff`.
> - Stage 23: Nhập tay giấy tờ theo hợp đồng thay AI đọc bill (BR-103), `staff_bill.html`, migration 0022 + 0023.
> - P0.1: `tests/architecture/` N3/N4/N6 - 4 passed. P0.2: E2E-01..08 - 8 passed. Tổng `tests/architecture/ + tests/e2e/` = 16 passed.
> - P3: `ruff` xanh, `alembic check`: No new upgrade operations detected.
> - Nhánh chuẩn: `stage-23-ai-operations` = `giai-doan-0` = `ce7ae7b`.

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
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d db
alembic upgrade head
uvicorn app.main:create_app --factory --reload --port 8000