# 03 – Luồng 2: Quản lý, tổ chức và kiểm soát triển khai hợp đồng

> Module `app/flows/control` · API `/api/v2/control` · Bảng `ctl_*` · Mã yêu cầu `CTL-Fxx` · Quy tắc `BR-2xx`

## 1. Phạm vi

Luồng của **người quản lý**, áp dụng chu trình Hoạch định – Tổ chức – Kiểm soát cho từng hợp đồng dịch vụ đang triển khai:

| Chức năng quản lý | Nội dung trong hệ thống |
|---|---|
| Hoạch định | Kế hoạch triển khai hợp đồng: người phụ trách, nhóm thực hiện, mốc, mục tiêu KPI, rủi ro |
| Tổ chức | Phân công lô hàng/bước cho nhân viên; **điều phối tiến trình dịch vụ**; phê duyệt các yêu cầu từ Luồng 1 theo **chính sách phê duyệt nhiều cấp** |
| Kiểm soát | Điểm kiểm soát 4 mắt; **giám sát** tiến độ thời gian thực và cảnh báo; quản lý sự cố và hành động khắc phục, phòng ngừa (CAPA) |

**Độc lập với Luồng 1**: Luồng 2 **không đọc trực tiếp** bảng `ops_*`. Tiến độ được dựng thành bảng chiếu riêng (`ctl_shipment_progress`, `ctl_task_progress`) từ sự kiện EV-1xx; số liệu tài chính hợp đồng đọc qua `operations.public`. Mọi quyết định của Luồng 2 trả về Luồng 1 bằng sự kiện EV-2xx.

### 1.1. Vai trò trong Luồng 2

| Vai trò | Mã | Nhiệm vụ |
|---|---|---|
| Người quản lý triển khai hợp đồng | `manager` (trong kế hoạch: `account_manager`, `controller`) | Lập kế hoạch triển khai; phân công; **giám sát, điều phối tiến trình dịch vụ của hợp đồng thật** (§3.5); đánh giá điểm kiểm soát; xử lý sự cố, CAPA; **phê duyệt cấp 1** |
| **Người quản lý hệ thống kinh doanh** | `business_manager` | **Người phê duyệt thứ hai (cấp 2)** với các quyết định hợp đồng – tài chính và công bố bộ quy tắc `production` (§3.2.1); phê duyệt kế hoạch triển khai hợp đồng; theo dõi tổng hợp kinh doanh (SLA, lãi gộp, phải thu) của toàn bộ hợp đồng |
| **Người hướng dẫn – giám sát, điều phối hợp đồng thực hành** | `instructor` (trong kế hoạch `training`: `coordinator`) | **Chỉ với hợp đồng `training`** của lớp mình: **giám sát** tiến trình các lô, nhận và xử lý cảnh báo; **điều phối**: điều chuyển người thực hiện, đặt mức ưu tiên lô, đôn đốc, xử lý điểm nghẽn, duyệt gia hạn và thay đổi trình tự bước (§3.5); đánh giá điểm kiểm soát; phê duyệt theo **cùng chính sách phê duyệt như hợp đồng thật** ở những cấp không giao cho người học (BR-207, BR-211). **Không** tham gia kế hoạch, điều phối, phê duyệt của hợp đồng `production` |
| Người học (đóng vai) | `learner` (trong kế hoạch `training`: vai được giao) | Thực hiện các vai trong kế hoạch triển khai hợp đồng thực hành – nhân viên chứng từ, hải quan, giao nhận, `account_manager`, `controller` – để quy trình chạy đầy đủ như hợp đồng thật (BR-211) |

**Nguyên tắc hợp đồng thực hành** (đã chốt 08/11/2026): hợp đồng `training` đi qua **đúng quy trình của hợp đồng thật** – kế hoạch triển khai, phân công, điểm kiểm soát 4 mắt, chính sách phê duyệt nhiều cấp với cùng ngưỡng, giám sát cảnh báo, sự cố – CAPA, quyết toán, thanh lý; chỉ khác người đảm nhiệm vai (người học, người hướng dẫn) và phạm vi dữ liệu (BR-01).

## 2. Luồng nghiệp vụ

```mermaid
sequenceDiagram
    participant L1 as Luồng 1 – Vận hành
    participant L2 as Luồng 2 – Quản lý
    participant M as Người quản lý (cấp 1)
    participant B as Quản lý hệ thống kinh doanh (cấp 2)
    participant I as Người điều phối (QL – hợp đồng thật; GV – hợp đồng thực hành)
    participant L4 as Luồng 4 – KPI
    L1->>L2: EV-101 Hợp đồng kích hoạt
    M->>L2: Lập kế hoạch triển khai
    B->>L2: Phê duyệt kế hoạch
    L2->>L1: EV-201 Kế hoạch (mốc, mục tiêu KPI)
    L2->>L4: EV-201 Mục tiêu KPI theo hợp đồng
    L1->>L2: EV-111 Lô hàng mới
    M->>L2: Phân công
    L2->>L1: EV-202 Người phụ trách
    L1->>L2: EV-131/132/133 Tiến độ bước
    L2-->>I: Cảnh báo (M-01…M-10)
    I->>L2: Điều phối: điều chuyển, ưu tiên, đôn đốc
    L2->>L1: EV-202 / EV-207 Chỉ đạo điều phối
    L1->>L2: EV-132 bước có điểm kiểm soát hoàn thành
    M->>L2: Đánh giá điểm kiểm soát
    L2->>L1: EV-204 passed / failed
    L1->>L2: EV-134 Yêu cầu phê duyệt
    M->>L2: Duyệt cấp 1
    B->>L2: Duyệt cấp 2 (nếu chính sách yêu cầu)
    L2->>L1: EV-203 Quyết định cuối cùng
    M->>L2: Ghi nhận sự cố, CAPA
    L2->>L4: EV-205 / EV-206
    L4->>L2: EV-403 Cảnh báo KPI
```

## 3. Mô hình dữ liệu

### 3.1. Kế hoạch triển khai hợp đồng

`ctl_contract_plans`: `contract_id` (tham chiếu Luồng 1), snapshot `contract_no`, `customer_name`, `valid_from`, `valid_to`, `service_scopes`; `mode`; `account_manager_id`; `plan_version`; `status` (`draft` → `pending_approval` → `approved` → `revised` → `closed`); `approved_by` (`business_manager`; `instructor` với `training`), `approved_at`; `notes`.

| Bảng con | Trường chính |
|---|---|
| `ctl_plan_members` | `user_id`, `role_in_contract` (`account_manager`, `doc_staff`, `customs_staff`, `transport_coordinator`, `warehouse_staff`, `controller`; với `training` thêm `coordinator` và `business_manager_proxy` – vai cấp 2 do người hướng dẫn đảm nhiệm), `is_role_play` (người học đóng vai) |
| `ctl_plan_milestones` | `name`, `due_date`, `criteria`, `status` |
| `ctl_plan_kpi_targets` | `kpi_code` (danh mục Luồng 4), `target_value`, `warning_threshold`, `source` (`contract_sla` / `internal`) – mặc định lấy từ SLA hợp đồng trong EV-101 |
| `ctl_plan_risks` | `description`, `likelihood` (1–5), `impact` (1–5), `score = likelihood × impact`, `mitigation`, `owner_id`, `status` |

### 3.2. Phê duyệt nhiều cấp

`ctl_approval_requests`: bản sao từ EV-134 (`ops_request_id`, `contract_id`, `shipment_id?`, `request_type`, `reference`, `amount?`, `currency?`, `reason`, `requested_by`) + `policy_id`, `required_levels` (1 hoặc 2), `current_level`, `decision` (`pending`/`approved`/`rejected`), `final_decided_at`, `due_at` (theo cấp hiện tại).

`ctl_approval_decisions`: `request_id`, `level`, `approver_id`, `approver_role`, `decision` (`approved`/`rejected`), `note`, `decided_at`.

`ctl_approval_policies` (cấu hình bởi `admin`; `business_manager` xem): `request_type`, `condition` (JSONLogic theo `amount`, `currency`, loại sai lệch…), `levels` (JSON: danh sách cấp, mỗi cấp có vai trò được duyệt và `due_hours`), `effective_from`, `status`. **Một chính sách dùng chung cho cả `production` và `training`** (nguyên tắc hợp đồng thực hành, §1.1).

`ctl_approval_thresholds` (**do `admin` nhập**): `threshold_code` (`X_EXTRA_CHARGE`, `Y_PAYMENT_VOUCHER`, `Z_STAFF_ADVANCE`), `amount`, `currency`, `effective_from`, `note`, `entered_by`. Giá trị là **giá trị giả định** do quản trị hệ thống nhập (phục vụ vận hành thử và đào tạo), thay đổi được bất kỳ lúc nào; yêu cầu phê duyệt áp dụng ngưỡng hiệu lực tại thời điểm tạo (BR-202 f). Chưa có ngưỡng → hệ thống áp **cấp 2 cho mọi giá trị** (an toàn mặc định) và cảnh báo `admin`.

#### 3.2.1. Chính sách phê duyệt mặc định

X, Y, Z là ngưỡng trong `ctl_approval_thresholds`.

| Loại yêu cầu (`request_type`) | Cấp 1 | Cấp 2 – người phê duyệt thứ hai |
|---|---|---|
| `DISCREPANCY_WAIVER` (miễn trừ sai lệch chứng từ) | `account_manager` | – |
| `SKIP_STEP`, `DEADLINE_EXTENSION`, `OUT_OF_SEQUENCE` | `account_manager` hoặc `controller` (`training`: thêm `coordinator` – người hướng dẫn) | – |
| `EXTRA_CHARGE` (phí phát sinh) | `account_manager` | `business_manager` khi giá trị > X |
| `PAYMENT_VOUCHER` (phiếu chi) | `account_manager` | `business_manager` khi giá trị > Y |
| `STAFF_ADVANCE` (tạm ứng nhân viên) | `account_manager` | `business_manager` khi giá trị > Z |
| `CONTRACT_AMENDMENT` (phụ lục) | `manager` | `business_manager` – **luôn** |
| `CONTRACT_TERMINATION` (chấm dứt trước hạn) | `manager` | `business_manager` – **luôn** |
| `SETTLEMENT` (bảng quyết toán) | `manager` | `business_manager` – **luôn** |
| `RULESET_PUBLISH` (công bố bộ quy tắc `production`) | `manager` (đề nghị công bố) | `business_manager` – **luôn** |
Kế hoạch triển khai hợp đồng do `account_manager` lập, `business_manager` phê duyệt.

**Ánh xạ vai với hợp đồng `training`** (cùng chính sách, cùng số cấp, cùng ngưỡng):

| Vai trong chính sách | Người đảm nhiệm ở hợp đồng `training` |
|---|---|
| `account_manager`, `controller`, `manager` (cấp 1) | Người học được giao vai đó trong kế hoạch; nếu không giao → người hướng dẫn |
| `business_manager` (cấp 2, phê duyệt kế hoạch) | Người hướng dẫn với vai `business_manager_proxy` |
| `coordinator` (giám sát, điều phối) | Người hướng dẫn phụ trách lớp |

### 3.3. Tổ chức thực hiện

`ctl_assignments`: `shipment_id`, `task_id?`, `assignee_id`, `assigned_by`, `assigned_at`, `reason`, `is_current`. Lưu toàn bộ lịch sử phân công.

### 3.4. Kiểm soát

`ctl_checkpoints`: `shipment_id`, `task_id`, `step_code`, `checkpoint_type` (`DOC_REVIEW`, `HS_REVIEW`, `DECLARATION_APPROVAL`, `DELIVERY_CHECK`…), `performer_id` (người làm bước), `controller_id`, `attempt_no`, `checklist_results` (JSON: mục, đạt/không đạt, ghi chú – mẫu checklist lấy từ `md_process_steps.checklist`), `result` (`pending`/`passed`/`failed`), `comments`, `evaluated_at`.

`ctl_incidents`: `incident_no`, `shipment_id?`, `contract_id`, `incident_type` (`DELAY`, `DOCUMENT_ERROR`, `CUSTOMS_ISSUE`, `CARGO_DAMAGE`, `CARGO_SHORTAGE`, `CUSTOMER_COMPLAINT`, `COST_OVERRUN`, `SAFETY`, `OTHER`), `severity` (`low`/`medium`/`high`/`critical`), `description`, `detected_at`, `reported_by`, `attributed_to?` (nhân viên, nếu xác định được), `root_cause_category` (`people`, `method`, `equipment`, `material`, `measurement`, `environment`, `partner`, `customer`), `root_cause_detail`, `financial_impact` NUMERIC + `currency`, `liability` (`company`/`customer`/`third_party`/`force_majeure`), `compensation_amount` (giá trị bồi thường đã thống nhất với khách hàng, nếu có), `status` (`open` → `investigating` → `action_in_progress` → `resolved` → `closed`).

`ctl_capa_actions`: `incident_id`, `action_type` (`corrective`/`preventive`), `description`, `owner_id`, `due_date`, `status` (`planned`/`in_progress`/`done`/`verified`), `verified_by`, `verified_at`.

### 3.5. Giám sát và điều phối tiến trình dịch vụ

Bảng chiếu (chỉ Luồng 2 ghi, dựng từ sự kiện): `ctl_shipment_progress` (`shipment_id`, `contract_id`, `status`, `required_delivery_at`, `assignee_id`, `priority`, số bước tổng/xong/trễ, `open_discrepancies`, `open_incidents`, `bottleneck` – bước đang chặn tiến trình), `ctl_task_progress` (`task_id`, `shipment_id`, `step_code`, `planned_start`, `planned_end`, `actual_start`, `actual_end`, `status`, `block_reason_code`).

`ctl_monitoring_rules` (cấu hình) và `ctl_alerts` (`rule_code`, `subject_type`, `subject_id`, `level` (`info`/`warning`/`critical`), `message`, `raised_at`, `recipients[]` (`production`: `account_manager`; `training`: `coordinator` – người hướng dẫn – và người học giữ vai `account_manager`), `acknowledged_by`, `acknowledged_at`, `escalated`, `resolution_note`).

`ctl_coordination_actions` (nhật ký điều phối): `shipment_id`, `task_id?`, `action_type` (`reassign`, `set_priority`, `remind`, `expedite`, `request_extension`, `note`), `target_user_id?`, `priority?` (`normal`/`high`/`urgent`), `message`, `alert_id?` (cảnh báo được xử lý), `performed_by`, `performed_at`. Hành động có tác động sang Luồng 1 phát EV-202 (điều chuyển) hoặc EV-207 (ưu tiên, đôn đốc, khẩn trương).

| Mã | Quy tắc giám sát | Mức | Tham số mặc định (**CẦN XÁC NHẬN**) |
|---|---|---|---|
| M-01 | Bước có nguy cơ trễ: thời gian đã dùng > x% thời gian chuẩn, chưa xong | warning | x = 80% |
| M-02 | Bước trễ hạn: hiện tại > `planned_end`, chưa xong | warning → critical sau y giờ | y = 4 |
| M-03 | Lô có nguy cơ vỡ SLA: (`required_delivery_at` − hiện tại) < tổng thời gian chuẩn các bước còn lại | critical | |
| M-04 | Sai lệch chứng từ chặn chưa xử lý quá z giờ | warning | z = 24 |
| M-05 | Yêu cầu phê duyệt chờ quá `due_at` của cấp hiện tại | warning | 4 giờ làm việc/cấp |
| M-06 | Bước bị `blocked` quá w giờ | warning | w = 8 |
| M-07 | Nhận EV-403 (KPI dưới ngưỡng) | theo KPI | |
| M-08 | Yêu cầu dịch vụ của khách hàng chưa được quyết định khi tới `response_due_at` (EV-113, EV-114) | warning → critical sau 2 giờ | |
| M-09 | Hợp đồng `liquidating` quá n ngày chưa hoàn tất thanh lý (EV-104, EV-102) | warning | n = 30 |
| M-10 | Tạm ứng nhân viên quá hạn hoàn ứng (EV-115, EV-116) | warning | Theo `due_date`. **Đã làm (Giai đoạn 20)**: cảnh báo `staff_advance_overdue` (không gắn lô) cho mọi `manager`; quét định kỳ (tác vụ rq `advance_alerts.scheduled_scan`, chu kỳ `CONTROL_SCAN_INTERVAL_SEC`) đọc tạm ứng quá hạn qua `operations.public.overdue_staff_advances` (N4); EV-116 → tự đóng; không lặp; leo thang BR-205 |

Giám sát, điều phối hợp đồng `production` do `account_manager` (người quản lý) thực hiện; hợp đồng `training` do người hướng dẫn (`coordinator`) thực hiện, cùng quy tắc giám sát và cùng tham số. Cảnh báo chưa được xác nhận sau thời hạn được **leo thang**: `production` → `business_manager`; `training` → người hướng dẫn với vai `business_manager_proxy` (BR-205).

## 4. Quy tắc nghiệp vụ

| Mã | Quy tắc |
|---|---|
| **BR-201** | Nguyên tắc 4 mắt: `controller_id` ≠ `performer_id` của bước. Người kiểm soát phải là thành viên kế hoạch có vai trò `controller` hoặc `account_manager` (`training`: thêm `coordinator`) |
| **BR-202** | Phê duyệt nhiều cấp: (a) người duyệt ≠ người tạo yêu cầu; (b) người duyệt cấp 2 ≠ người duyệt cấp 1; (c) cấp 2 chỉ do người có vai trò `business_manager` (`training`: `business_manager_proxy`); (d) từ chối ở bất kỳ cấp nào là quyết định cuối cùng; (e) EV-203 chỉ phát khi có quyết định cuối cùng; (f) chính sách và ngưỡng áp dụng là bản hiệu lực tại thời điểm tạo yêu cầu. Ngoại lệ duy nhất cho (b): hợp đồng `training` mà lớp chỉ có một người hướng dẫn và không giao vai cấp 1 cho người học → người hướng dẫn duyệt cả hai cấp, mỗi cấp ghi riêng một quyết định, đánh dấu `training_single_instructor` |
| **BR-203** | Phê duyệt miễn trừ sai lệch phải có lý do và rủi ro chấp nhận; lưu vào nhật ký, không sửa được sau khi gửi EV-203 |
| **BR-204** | Lô chỉ được phân công cho thành viên của kế hoạch triển khai hợp đồng tương ứng |
| **BR-205** | Cảnh báo `critical` chưa xác nhận sau 2 giờ → leo thang lên `business_manager` (tham số cấu hình) |
| **BR-206** | Sự cố mức `high`/`critical` bắt buộc có ít nhất một CAPA trước khi `resolved`; chỉ đóng (`closed`) khi mọi CAPA `verified` |
| **BR-207** | Chế độ `training`: áp dụng **cùng quy trình, cùng chính sách phê duyệt (số cấp, ngưỡng), cùng quy tắc giám sát** như `production`; vai được đảm nhiệm theo bảng ánh xạ §3.2.1; cảnh báo, yêu cầu phê duyệt chỉ gửi tới người hướng dẫn và người học của lớp, không gửi tới quản lý vận hành thật (BR-01) |
| **BR-208** | Kế hoạch phải `approved` trước khi phân công lô đầu tiên của hợp đồng; sửa kế hoạch đã duyệt tạo `plan_version` mới, phê duyệt lại và phát lại EV-201 |
| **BR-209** | Người hướng dẫn **chỉ** được là thành viên kế hoạch của hợp đồng `training` thuộc lớp mình phụ trách; hệ thống từ chối thêm `instructor` (khi không đồng thời có vai `manager`/`business_manager`) vào kế hoạch `production` và từ chối mọi hành động điều phối, phê duyệt của họ trên dữ liệu `production` (`403`) |
| **BR-210** | Mọi hành động điều phối ghi `ctl_coordination_actions`; điều chuyển người thực hiện phải có lý do; người nhận việc mới nhận thông báo trong danh sách việc (Luồng 1) |
| **BR-211** | Kế hoạch `training` phải giao đủ các vai thực hiện cho người học (ít nhất các vai có bước trong quy trình mẫu); vai `account_manager`, `controller` có thể giao cho người học hoặc để người hướng dẫn đảm nhiệm. Một người học không giữ đồng thời vai thực hiện và vai kiểm soát/phê duyệt cấp 1 của **cùng một lô** (giữ nguyên tắc 4 mắt) |

## 5. Yêu cầu chức năng

| Mã | Yêu cầu | Ưu tiên |
|---|---|---|
| CTL-F01 | Tự tạo kế hoạch nháp khi nhận EV-101 (điền sẵn mục tiêu KPI từ SLA); sửa, gửi phê duyệt, phiên bản hóa kế hoạch | Cao |
| CTL-F02 | Quản lý thành viên, mốc, rủi ro (ma trận rủi ro 5×5) | Trung bình |
| CTL-F03 | Bảng phân công: lô chưa có người phụ trách, khối lượng việc theo nhân viên (số bước đang mở, số bước trễ); phân công, điều chuyển | Cao |
| CTL-F04 | Hộp phê duyệt theo cấp: danh sách chờ của tôi (cấp 1/cấp 2), lịch sử quyết định các cấp, quyết định kèm ghi chú | Cao |
| CTL-F05 | Hàng đợi điểm kiểm soát: xem minh chứng (qua `operations.public`), chấm checklist, đạt/không đạt | Cao |
| CTL-F06 | Sự cố và CAPA: ghi nhận, phân tích nguyên nhân, theo dõi hành động | Cao |
| CTL-F07 | Giám sát: bảng điều khiển theo hợp đồng/lô/nhân viên; biểu đồ Gantt kế hoạch – thực tế; danh sách cảnh báo | Cao |
| CTL-F08 | Hiển thị KPI vận hành của hợp đồng/nhân viên so với mục tiêu (dữ liệu từ `assessment.public.get_kpi_values`, BR-05) | Cao |
| CTL-F09 | Báo cáo định kỳ triển khai hợp đồng (tuần/tháng): tiến độ, SLA, sự cố, CAPA; xuất PDF/XLSX | Trung bình |
| CTL-F10 | Hộp phê duyệt hợp đồng – tài chính: phụ lục, chấm dứt, phí phát sinh, phiếu chi, tạm ứng, bảng quyết toán, công bố bộ quy tắc; hiển thị so sánh trước/sau (phụ lục, bộ quy tắc), bảng tính A, T, C, D, E, F, S (quyết toán), chứng từ gốc (phiếu chi) qua `operations.public` | Cao |
| CTL-F11 | Ghi nhận trách nhiệm và giá trị bồi thường khi đóng sự cố (`liability`, `compensation_amount`) – đầu vào cho quyết toán | Cao |
| CTL-F12 | **Bảng điều phối tiến trình dịch vụ** (`account_manager` với hợp đồng `production`; người hướng dẫn với hợp đồng `training` của lớp – cùng giao diện, cùng chức năng): lô theo mốc và mức ưu tiên; điểm nghẽn (bước `blocked`, chờ phê duyệt, chờ chứng từ khách hàng, chờ điểm kiểm soát); hành động điều phối (điều chuyển, đặt ưu tiên, đôn đốc, khẩn trương, đề nghị/duyệt gia hạn); xử lý và xác nhận cảnh báo; nhật ký điều phối | Cao |
| CTL-F13 | Cấu hình chính sách phê duyệt và **nhập ngưỡng giá trị giả định X, Y, Z** (`admin`); lịch sử thay đổi ngưỡng | Cao |
| CTL-F15 | Giao vai cho người học trong kế hoạch `training` (đóng vai), kiểm tra BR-211 | Cao |
| CTL-F14 | Bảng tổng hợp kinh doanh (`business_manager`): hợp đồng theo trạng thái, SLA/KPI so với mục tiêu, doanh thu – chi phí trực tiếp – lãi gộp, phải thu, tạm ứng quá hạn (đọc qua `operations.public`, `assessment.public`) | Cao |

## 6. API (tóm tắt)

| Phương thức | Đường dẫn | Mô tả |
|---|---|---|
| GET/POST | `/api/v2/control/contract-plans` | |
| GET/PATCH | `/api/v2/control/contract-plans/{id}` | Gồm thành viên, mốc, mục tiêu KPI, rủi ro |
| POST | `/api/v2/control/contract-plans/{id}:submit` · `:approve` · `:revise` · `:close` | |
| GET/POST | `/api/v2/control/assignments` | Lọc theo `shipment_id`, `assignee_id` |
| GET | `/api/v2/control/workload` | Khối lượng việc theo nhân viên |
| GET | `/api/v2/control/approval-requests` | `?decision=pending&level=1|2&approver=me` |
| POST | `/api/v2/control/approval-requests/{id}:approve` · `:reject` | Ghi quyết định của cấp hiện tại |
| GET/POST | `/api/v2/control/approval-policies` · PATCH `/{id}` | `admin` (ghi), `business_manager` (đọc) |
| GET/POST | `/api/v2/control/approval-thresholds` · PATCH `/{code}` | `admin` nhập ngưỡng X, Y, Z |
| GET | `/api/v2/control/checkpoints` | `?result=pending&controller_id=me` |
| POST | `/api/v2/control/checkpoints/{id}:evaluate` | Body: `checklist_results`, `result`, `comments` |
| GET/POST | `/api/v2/control/incidents` · PATCH `/{id}` · POST `/{id}:resolve` · `:close` | |
| GET/POST | `/api/v2/control/incidents/{id}/capa-actions` · POST `/capa-actions/{id}:verify` | |
| GET | `/api/v2/control/coordination/board?contract_id=&cohort_id=` | Bảng điều phối (CTL-F12) |
| POST | `/api/v2/control/coordination/actions` | Hành động điều phối (→ EV-202/EV-207) |
| GET | `/api/v2/control/dashboard/contracts/{contract_id}` · `/dashboard/shipments/{shipment_id}` · `/dashboard/business` | `business` cho `business_manager` |
| GET | `/api/v2/control/alerts` · POST `/alerts/{id}:acknowledge` | |
| GET | `/api/v2/control/reports/contract/{contract_id}?period=2026-11` | |

## 7. Sự kiện

- **Phát**: EV-201, EV-202, EV-203 (chỉ khi có quyết định cuối cùng), EV-204, EV-205, EV-206, EV-207.
- **Nhận**: EV-101, EV-102, EV-103 (phụ lục có hiệu lực → tạo phiên bản kế hoạch nháp để rà soát mục tiêu KPI, thành viên – BR-208), EV-104, EV-113, EV-114, EV-115, EV-116, EV-111, EV-112, EV-121, EV-122, EV-131, EV-132, EV-133, EV-134, EV-141, EV-142 (dựng bảng chiếu, sinh điểm kiểm soát, yêu cầu phê duyệt); EV-402 (hiển thị kết quả học tập của nhân viên – nếu được phép, `06` §4); EV-403.

Điểm kiểm soát được tạo khi nhận EV-132 của bước có `is_checkpoint = true` (thông tin cờ có sẵn trong `planned_tasks` của EV-111).

## 7a. Đã làm (Giai đoạn 11, 09/11/2026)

- **Nhận sự kiện**: bộ nhận dùng chung `app/shared/events/consumer.py` – đọc Redis Stream `l3pl:events:control` (consumer group `control`, tác vụ rq xếp sau khi bộ phát outbox gửi), mỗi sự kiện xử lý trong một giao dịch cùng dòng `sys_event_consumptions` (UNIQUE `consumer` + `event_id`) → không xử lý trùng (N5); lỗi → thử lại `JOB_MAX_RETRIES` lần, hết lượt `dead` + `event.consume_failed`; `python -m app.shared.events.cli replay control [--dead]` xử lý thẳng từ `sys_events` (không cần Redis).
- **EV-101** → `ctl_contract_plans` `draft` (bản chụp hợp đồng, `service_scopes`), `ctl_plan_kpi_targets` từ SLA (`source = contract_sla`), `account_manager_id` = `manager` ít kế hoạch đang mở nhất (thành viên `account_manager`). Giai đoạn 15: `PUT /contract-plans/{id}/kpi-targets` (manager, khi `draft` / `revised`), `:submit` (manager) → `pending_approval` → `:approve` (`business_manager` → `approved`, phát EV-201 kèm toàn bộ mục tiêu) hoặc `:reject` (lý do → `draft`); `:revise` (manager, kế hoạch đã duyệt → `revised`, `plan_version + 1`). `If-Match` tùy chọn. Nhật ký `plan.kpi_targets`, `plan.submit`, `plan.approve`, `plan.reject`, `plan.revise`. EV-403 (M-07) → cảnh báo `kpi_below_target` (`shipment_id` null, `contract_id`) cho mọi manager + business_manager (email / Zalo), business_manager xác nhận / đóng được, không leo thang BR-205.
- **EV-111** → `ctl_shipment_plans` + `ctl_shipment_tasks` từ quy trình mẫu `approved` (`md_process_templates` – cùng `direction`, có `transport_mode`, chung ≥ 1 dịch vụ với hợp đồng; nhiều quy trình → chung nhiều dịch vụ nhất). Thời gian kế hoạch: giờ chuẩn cộng dồn theo `seq` từ lúc tạo lô. Không có quy trình khớp → kế hoạch lô không có bước, vẫn phân công lô.
- **Phân công tự động (EV-202)** – `app/flows/control/assign.py`: vai trò (`ops_staff` đang hoạt động, hồ sơ `is_available`), phạm vi BR-204 (thành viên kế hoạch do manager thêm, nếu có), năng lực (`ctl_staff_profiles.skills` so với `required_skill` các bước), tải (giờ chuẩn các bước đang mở / `max_load_hours`, mặc định 40). Thứ tự: thiếu ít năng lực nhất → không vượt sức chứa → tỉ lệ tải sau khi nhận thấp nhất → mức năng lực cao → ít bước mở → id. Lịch sử `ctl_assignments` (chỉ ghi thêm, `is_current`, `score` giải thích lựa chọn), nhật ký `assignment.change` / `assignment.unassigned`, EV-202 (luồng nhận: 1) cùng giao dịch. Bước điểm kiểm soát: `controller_id` = `account_manager`, trùng người thực hiện → manager khác (BR-201, CHECK CSDL). Luồng 1 nhận EV-202 → `ops_shipments.assignee_id` (nhật ký `shipment.assign`, `source = EV-202`); `:assign` của Luồng 1 deprecated.
- **EV-112** sang `closed`/`cancelled` → đóng các bước (giảm tải).
- **API** `/api/v2/control`: `contract-plans` (+ `/{id}`), `shipment-plans` (+ `/{shipment_id}`: bước, lịch sử phân công), `my-tasks`, `assignments` (GET; POST – manager điều chuyển có lý do), `assignments:auto`, `workload`, `staff-profiles` (GET, PUT `/{user_id}`). `manager` toàn quyền; `business_manager`, `admin` đọc; `ops_staff` kế hoạch / lô / bước của mình.
- Chưa làm: duyệt kế hoạch (BR-208 – chưa chặn phân công khi kế hoạch còn nháp), quản lý thành viên/mốc/rủi ro qua API, phân công theo từng bước (mọi bước `ops_staff` của lô giao cùng một người – Luồng 1 mới có người phụ trách cấp lô), EV-131/132/133.

## 7b. Đã làm (Giai đoạn 12, 09/11/2026) – theo dõi thực thi, sự kiện, cảnh báo

- **Cập nhật thực tế bước** (`ctl_shipment_tasks`: `open` → `in_progress` → `done`; `blocked` có lý do; `closed` khi lô đóng/hủy): `POST /api/v2/control/tasks/{id}:start` (bước phụ thuộc phải xong) · `PATCH /tasks/{id}` (% tiến độ) · `:block` (lỗi / bị chặn, có lý do) · `:unblock` · `:complete`; người được giao bước hoặc `manager`. Mỗi thao tác: `ctl_task_updates` (chỉ ghi thêm), nhật ký `task.*`, sự kiện vào outbox – cùng giao dịch.
- **Theo dõi**: `GET /api/v2/control/tracking/shipments` (lọc `health`), `/tracking/shipments/{shipment_id}` (kế hoạch – thực tế từng bước, `delay_hours`), `/tracking/contract-plans/{plan_id}` (số lô theo tình trạng, tiến độ chung, cảnh báo đang mở), `/tasks/{id}` (lịch sử cập nhật). Tình trạng lô: `blocked` > `late` (dự kiến xong = bây giờ + giờ chuẩn còn lại, sau `required_delivery_at`) > `at_risk` (có bước quá `planned_end`) > `on_track`; `completed`, `closed`. Tiến độ = giờ chuẩn đã làm (bước xong + % bước đang làm) / tổng giờ chuẩn.
- **Sự kiện Luồng 2**: phân công EV-202 (Giai đoạn 11); `ctl.task.started`, `ctl.task.completed` (kèm giờ trễ), `ctl.task.blocked`, `ctl.task.delayed`, `ctl.alert.raised` – luồng nhận Luồng 4 (`assessment`). Mã dạng tên vì 01 §5 chưa có mã EV cho các sự kiện này (EV-131/132/133 là bước của Luồng 1) – **CẦN XÁC NHẬN**.
- **Cảnh báo** `ctl_alerts` (M-01…M-10 – mới làm 3 loại): `task_blocked` (critical, khi báo chặn; tự đóng khi gỡ chặn), `task_overdue` (bước chưa xong quá `planned_end`; warning, trễ ≥ `CONTROL_OVERDUE_CRITICAL_HOURS` → critical – nâng mức cảnh báo đang mở; tự đóng khi bước xong), `shipment_late_risk` (critical; tự đóng khi hết nguy cơ). Người nhận: `account_manager` của kế hoạch (không có → mọi manager); không lặp (UNIQUE `dedup_key` khi chưa xử lý). Quét định kỳ: tác vụ rq tự xếp lại mỗi `CONTROL_SCAN_INTERVAL_SEC` (worker xếp lần đầu khi khởi động), `POST /alerts:scan`. API `GET /alerts` (`status`, `alert_type`, `shipment_id`, `mine`), `POST /alerts/{id}:acknowledge`, `:resolve` (ghi chú) – `manager`; `business_manager`, `admin` đọc. Nhật ký `alert.raise` / `alert.escalate` / `alert.acknowledge` / `alert.resolve`.
- Chưa làm: lịch làm việc (giờ chuẩn tính theo giờ lịch). Leo thang, gửi email/Zalo, đồng bộ với Luồng 1: Giai đoạn 13 (§7c).

## 7c. Đã làm (Giai đoạn 13, 09/11/2026) – đồng bộ bước với Luồng 1, BR-205, gửi cảnh báo ra ngoài

- **Đồng bộ bước Luồng 2 ↔ Luồng 1** (`ctl_shipment_tasks` ↔ `ops_tasks`): Luồng 2 là nguồn chuẩn, mỗi bước có `revision` tăng mỗi lần đổi trạng thái / người thực hiện.
  - Luồng 2 đổi (API Luồng 2, phân công, tạo lô) → `ctl.task.synced` (trạng thái đầy đủ, `origin = control`) → Luồng 1 ghi đè khi `revision` ≥ bản đang có (sự kiện cũ bỏ qua) và phát EV-131 / EV-132 / EV-133 / `ops.task.unblocked` (`origin = control`) – Luồng 2 nhận lại thì bỏ qua (không vòng lặp).
  - Luồng 1 đổi (`/api/v2/ops/tasks/{id}:start|:complete|:block|:unblock` – người phụ trách lô) → cập nhật tạm (`pending_local`) + EV-131/132/133/`ops.task.unblocked` (`origin = ops`, `base_revision`) → Luồng 2 kiểm tra (bước phụ thuộc, trạng thái): hợp lệ → áp dụng, `revision + 1`, xác nhận `ctl.task.synced` (`origin = ops`; Luồng 2 không phát `ctl.task.*` cho Luồng 4 vì Luồng 4 đã nhận EV-13x); đã đúng trạng thái → xác nhận lại; không hợp lệ (xung đột / Luồng 1 lệch) → gửi trạng thái chuẩn (`conflict = true`), Luồng 1 quay về (nhật ký `ops_task.sync_conflict`), Luồng 2 ghi `task.sync_conflict` + cảnh báo `task_sync_conflict` (warning). Hai bên luôn hội tụ về trạng thái của Luồng 2.
  - Lô hủy/đóng: Luồng 1 đóng `ops_tasks`, Luồng 2 đóng `ctl_shipment_tasks` khi nhận EV-112. Manager `POST /api/v2/control/shipment-plans/{shipment_id}:resync` gửi lại trạng thái chuẩn mọi bước (khi một bên lỡ sự kiện). Migration `0014` chép bước đã có sang `ops_tasks`.
- **BR-205 leo thang**: cảnh báo chưa xử lý (`open` hoặc `acknowledged`, chưa `resolved`) sau `CONTROL_ESCALATION_HOURS` (2) giờ → leo thang một lần lên `business_manager` (`escalated_at`, `escalated_to_role`), nhật ký `alert.escalate` (`kind = business_manager`, `rule = BR-205`), sự kiện `ctl.alert.escalated`, gửi thông báo cho mọi `business_manager`. `business_manager` xác nhận / đóng được cảnh báo đã leo thang (`GET /alerts?mine=true` = cảnh báo đã leo thang). Chạy trong lần quét định kỳ. Khác 03 BR-205 gốc (chỉ `critical` chưa xác nhận): theo yêu cầu Giai đoạn 13 áp dụng mọi cảnh báo chưa xử lý – **CẦN XÁC NHẬN**.
- **Gửi cảnh báo ra ngoài** `ctl_alert_notifications`: email (tài khoản có email) và Zalo OA (khi đặt `ZALO_TOKEN`, người nhận có `ctl_staff_profiles.zalo_user_id`); cảnh báo mới → account_manager (hoặc mọi manager), leo thang → mọi business_manager. Tạo dòng cùng giao dịch với cảnh báo; sau commit xếp tác vụ rq `deliver_notification(id)` (chỉ id); không có hàng đợi → gửi ngay trong tiến trình. Lỗi → thử lại như Giai đoạn 7; hết lượt → `failed` + nhật ký `alert.notify_failed`, tác vụ vào FailedJobRegistry.

## 8. Điểm CẦN XÁC NHẬN

1. Tham số các quy tắc giám sát M-01…M-10 và thời hạn leo thang.
2. Kênh gửi cảnh báo: trong ứng dụng (mặc định), email, Zalo/Teams.
3. Người quản lý có được xem kết quả học tập của nhân viên mình không (quyền riêng tư).

Đã chốt (08/11/2026): người phê duyệt thứ hai là người quản lý hệ thống kinh doanh (`business_manager`); người hướng dẫn chỉ giám sát, điều phối **hợp đồng thực hành**, theo đúng quy trình của hợp đồng thật (BR-207, BR-209, BR-211); ngưỡng giá trị phê duyệt là giá trị giả định do `admin` nhập (§3.2).
