# 00 – Mục lục, quy ước tài liệu, thuật ngữ và vai trò

> **Bộ đặc tả v2.3** – 08/11/2026 (v2.3: người hướng dẫn chỉ điều phối hợp đồng thực hành theo quy trình thật; ngưỡng phê duyệt do admin nhập; không phân bổ chi phí chung; chuyển giọng nói thành văn bản tại máy chủ đơn vị – `99` §10.9. v2.1: kho phỏng vấn 2 quy trình; bộ quy tắc cấu hình được; cổng khách hàng, phụ lục, thanh lý – quyết toán – `99` §10.7. v2.2: phỏng vấn online WebRTC tự triển khai; sổ thu – chi theo hợp đồng; ký tay – scan – xác nhận; phê duyệt 2 cấp, giám sát – điều phối – `99` §10.8). Là căn cứ duy nhất cho bước coding v2. Thay thế các file `01`–`04` và `REVIEW-02` cũ (đã xóa khỏi thư mục làm việc; bản gốc `01`–`04` trong git commit `38301d2`, `REVIEW-02` trong commit `3600a61`).
> Mã nguồn hiện tại (v1.1.0) **chưa** theo đặc tả này. Hiện trạng: `90-hien-trang-v1.1.md`. Ánh xạ chuyển đổi: `07` §3.

## 1. Mục lục

| File | Nội dung | Đối tượng đọc chính |
|---|---|---|
| `00-muc-luc-va-quy-uoc.md` | Mục lục, quy ước, thuật ngữ, vai trò người dùng | Tất cả |
| `01-kien-truc-tong-the.md` | 4 luồng xử lý độc lập, nguyên tắc tách luồng, danh mục dùng chung, sự kiện tích hợp | Kiến trúc sư, trưởng nhóm dev |
| `02-luong-1-van-hanh.md` | Luồng 1 – Vận hành: hợp đồng dịch vụ, phụ lục → yêu cầu dịch vụ → lô hàng → bộ chứng từ (bộ quy tắc cấu hình được) → quy trình nghiệp vụ → thanh lý, quyết toán; cổng khách hàng | BA, dev luồng 1, chuyên gia nghiệp vụ |
| `03-luong-2-quan-ly-kiem-soat.md` | Luồng 2 – Quản lý, tổ chức, kiểm soát triển khai hợp đồng | BA, dev luồng 2, người quản lý |
| `04-luong-3-hoc-lieu.md` | Luồng 3 – Học liệu: tài liệu học thuật, video theo chủ đề; kho phỏng vấn video/audio (phỏng vấn online hoặc người hướng dẫn tải lên) | BA, dev luồng 3, người hướng dẫn |
| `05-luong-4-danh-gia-kpi.md` | Luồng 4 – Kiểm tra, đánh giá KPI và kết quả học tập | BA, dev luồng 4, người hướng dẫn |
| `06-quy-uoc-api-bao-mat.md` | Quy ước API, lỗi, phân trang, file, thời gian, xác thực, phân quyền (RBAC), nhật ký | Tất cả dev |
| `07-cau-truc-ma-nguon-chuyen-doi-lo-trinh.md` | Cấu trúc mã nguồn đích, chuyển đổi từ v1.1.0, kiểm thử, lộ trình | Trưởng nhóm dev |
| `90-hien-trang-v1.1.md` | Mô tả code v1.1.0 hiện có (kèm đính chính) | Dev thực hiện chuyển đổi |
| `99-lich-su-chinh-sua.md` | Lịch sử chỉnh sửa tài liệu và mã nguồn | Tất cả |

## 2. Quy ước tài liệu

| Quy ước | Ý nghĩa |
|---|---|
| **CẦN XÁC NHẬN** | Điểm chưa có căn cứ hoặc cần chủ dự án quyết định. Không code theo giả định cho tới khi được xác nhận; nếu buộc phải code thì để thành tham số cấu hình |
| `ops_`, `ctl_`, `lrn_`, `asm_`, `md_`, `sys_` | Tiền tố bảng của Luồng 1, 2, 3, 4, danh mục dùng chung, hệ thống |
| `OPS-`, `CTL-`, `LRN-`, `ASM-` | Tiền tố mã yêu cầu chức năng của từng luồng (vd. `OPS-F03`) |
| `BR-xx` | Quy tắc nghiệp vụ (business rule) |
| `EV-xxx` | Mã sự kiện tích hợp giữa các luồng (danh mục tại `01` §5) |
| `KPI-xx` | Mã chỉ tiêu (danh mục tại `05` §3) |
| Bắt buộc / Tùy chọn | Ký hiệu **B** / **T** trong bảng trường dữ liệu |
| Trạng thái | Viết thường, tiếng Anh, `snake_case` trong code (vd. `docs_verified`); tiếng Việt trong giao diện |

## 3. Thuật ngữ

| Thuật ngữ | Tiếng Anh / viết tắt | Định nghĩa dùng trong hệ thống |
|---|---|---|
| Hợp đồng dịch vụ logistics | Logistics Service Agreement (LSA) | Hợp đồng giữa khách hàng (chủ hàng) và công ty 3PL, quy định phạm vi dịch vụ, SLA, giá, trách nhiệm. **Đầu vào gốc** của Luồng 1 |
| Lô hàng / Job | Shipment / Job file | Một đơn vị thực hiện dịch vụ thuộc một hợp đồng dịch vụ (một lần nhập/xuất, một chuyến vận chuyển) |
| Bộ chứng từ | Shipping documents set | Tập chứng từ khách hàng cung cấp cho một lô hàng |
| Hợp đồng mua bán (ngoại thương) | Sales Contract (S/C) | Hợp đồng giữa người bán và người mua hàng hóa – **không phải** hợp đồng dịch vụ logistics |
| Hóa đơn thương mại | Commercial Invoice (C/I) | |
| Phiếu đóng gói | Packing List (P/L) | |
| Giấy chứng nhận xuất xứ | Certificate of Origin (C/O) | |
| Giấy chứng nhận chất lượng | Certificate of Quality (C/Q) | |
| Vận đơn | Bill of Lading (B/L) / Air Waybill (AWB) | |
| Tờ khai hải quan | Customs Declaration | |
| Điều kiện thương mại | Incoterms® 2020 | Mã 3 ký tự (EXW, FCA, FOB, CIF, DAP, DDP…) |
| Quy trình nghiệp vụ mẫu | Process template | Chuỗi bước chuẩn có phiên bản (vd. nhập khẩu đường biển FCL) |
| Bước thực hiện | Task | Một bước của quy trình mẫu được khởi tạo cho một lô hàng cụ thể |
| Sai lệch chứng từ | Document discrepancy | Kết quả vi phạm một quy tắc kiểm tra chéo bộ chứng từ |
| Điểm kiểm soát | Control checkpoint | Bước cần người thứ hai kiểm tra/phê duyệt (nguyên tắc 4 mắt) |
| Sự cố / ngoại lệ | Incident / Exception | Sai khác so với kế hoạch hoặc quy trình chuẩn, cần xử lý |
| Hành động khắc phục, phòng ngừa | CAPA | Corrective and Preventive Action |
| Mức dịch vụ cam kết | SLA | Chỉ tiêu cam kết trong hợp đồng dịch vụ |
| Học liệu | Learning resource | Hướng dẫn, tài liệu học thuật, video, phỏng vấn, tình huống, mẫu chứng từ |
| Tình huống thực hành | Practice scenario | Bộ hợp đồng dịch vụ + chứng từ mẫu (có thể cài sẵn sai lệch) giao cho người học thực hiện ở chế độ đào tạo |
| Chế độ dữ liệu | `mode = production / training` | Dữ liệu vận hành thật / dữ liệu thực hành của người học. Hai chế độ dùng chung quy trình, tách biệt dữ liệu |
| Cổng khách hàng | Customer portal | Kênh khách hàng tự thao tác với dữ liệu của mình trong Luồng 1 |
| Yêu cầu dịch vụ | Service request / Booking request | Đề nghị của khách hàng thực hiện một lô hàng theo hợp đồng; được chấp nhận thì sinh lô hàng |
| Phụ lục hợp đồng | Contract amendment | Văn bản sửa đổi, bổ sung hợp đồng đang hiệu lực (phạm vi, SLA, biểu giá, thời hạn…); có hiệu lực thì tăng phiên bản hợp đồng |
| Biểu giá | Rate card / Tariff | Đơn giá dịch vụ theo đơn vị tính (lô, container, kg, m³, tờ khai…) quy định trong hợp đồng |
| Chi hộ | Disbursement | Khoản công ty 3PL trả thay khách hàng cho bên thứ ba (phí cảng, phí hãng tàu, thuế nộp hộ…), quyết toán theo chứng từ gốc |
| Bảng kê đối soát | Statement of account | Bảng kê phí theo kỳ để hai bên đối chiếu |
| Quyết toán hợp đồng | Final settlement | Xác định giá trị thực hiện, giảm trừ, bồi thường, đã thanh toán và số còn phải thanh toán khi kết thúc hợp đồng |
| Thanh lý hợp đồng | Contract liquidation | Hai bên xác nhận hoàn thành/chấm dứt nghĩa vụ hợp đồng bằng biên bản thanh lý, căn cứ bảng quyết toán |
| Bộ quy tắc | Rule set | Tập ma trận chứng từ bắt buộc + quy tắc kiểm tra chéo, có phiên bản, do người quản lý (`production`) hoặc người hướng dẫn (`training`) cấu hình |
| Bộ đánh giá | Evaluator | Thành phần code tổng quát thực hiện một kiểu so sánh; quy tắc cấu hình chọn bộ đánh giá và khai tham số |
| Kế toán thống kê thu – chi theo hợp đồng | – | Ghi chép đơn các khoản thu, chi thực tế của từng hợp đồng theo phiếu thu/phiếu chi nội bộ có chứng từ gốc, phân theo khoản mục, để xác định kết quả (lãi gộp) hợp đồng – theo kiểu hạch toán đội sản xuất; không phải kế toán tài chính |
| Tạm ứng – hoàn ứng | Staff advance / settlement | Nhân viên nhận tiền trước để chi cho lô hàng (phí cảng, lệ phí…), sau đó quyết toán bằng chứng từ |
| Phê duyệt cấp 2 | Second-level approval | Quyết định của người phê duyệt thứ hai (người quản lý hệ thống kinh doanh) sau phê duyệt cấp 1 |
| Giám sát, điều phối tiến trình dịch vụ | Service process coordination | Theo dõi tiến trình các lô, xử lý cảnh báo, điểm nghẽn; điều chuyển, đặt ưu tiên, đôn đốc thực hiện |

## 4. Vai trò người dùng

| Mã | Vai trò | Luồng chính | Mô tả |
|---|---|---|---|
| `ops_staff` | Nhân viên vận hành (chứng từ, hải quan, giao nhận, kho) | 1 | Tiếp nhận hợp đồng/chứng từ, thực hiện các bước quy trình, lập phiếu thu/chi, đề nghị tạm ứng – hoàn ứng |
| `manager` | Người quản lý triển khai hợp đồng (trưởng bộ phận, quản lý hợp đồng/khách hàng) | 2 (đọc 1, 4) | Lập kế hoạch triển khai, phân công, kiểm soát, **phê duyệt cấp 1**, xử lý sự cố; cấu hình bộ quy tắc `production` |
| `business_manager` | **Người quản lý hệ thống kinh doanh** | 2 | **Người phê duyệt thứ hai (cấp 2)**: phụ lục, chấm dứt hợp đồng, quyết toán, phí phát sinh/phiếu chi/tạm ứng vượt ngưỡng, công bố bộ quy tắc `production`; phê duyệt kế hoạch triển khai hợp đồng; cấu hình chính sách phê duyệt; theo dõi tổng hợp kinh doanh (`03` §1.1) |
| `learner` | Người học (sinh viên, học viên, nhân viên mới) | 3, 4 (thực hành ở 1, 2 – chế độ training) | Học học liệu, làm tình huống thực hành; **đóng các vai** được giao trong kế hoạch hợp đồng thực hành (nhân viên vận hành, `account_manager`, `controller` – BR-211); xem kết quả |
| `instructor` | Người hướng dẫn (giảng viên, chuyên gia, nhân viên kèm cặp) | 2 (chỉ hợp đồng `training`), 3, 4 | Biên soạn học liệu, phỏng vấn, ra tình huống, chấm, xác nhận kết quả; **giám sát, điều phối thực hiện hợp đồng thực hành** theo đúng quy trình của hợp đồng thật (vai `coordinator`, `business_manager_proxy` – `03` §1.1, BR-207, BR-209); không tham gia hợp đồng `production` |
| `content_editor` | Biên tập học liệu | 3 | Nhập, kiểm duyệt học liệu (có thể gộp với `instructor` – **CẦN XÁC NHẬN**) |
| `admin` | Quản trị hệ thống | Hạ tầng | Người dùng, phân quyền, danh mục dùng chung, cấu hình; **chính sách phê duyệt và ngưỡng giá trị giả định X, Y, Z** (`03` §3.2) |
| `customer` | Khách hàng (chủ hàng) | 1 – qua cổng khách hàng | Gửi yêu cầu dịch vụ, chứng từ; góp ý bản nháp, đề nghị phụ lục hợp đồng; theo dõi tiến độ; xác nhận/khiếu nại bảng kê phí, quyết toán; ký biên bản thanh lý (`02` §9). Mỗi tài khoản gắn một khách hàng; cờ `is_customer_admin` quản lý tài khoản cùng khách hàng |
| Khách mời phỏng vấn | Chuyên gia, nhà quản lý được mời phỏng vấn | 3 | **Không có tài khoản**; vào phiên phỏng vấn online bằng liên kết token và xác nhận đồng ý ghi hình/ghi âm (`04` §3.3) |

Một người dùng có thể có nhiều vai trò (vd. giảng viên kiêm `manager` khi quản lý lớp thực hành). Ma trận quyền: `06` §4.
