# Runtime Core Migration Ledger

Mục đích của ledger này là biến việc giảm `runtime_core` thành một chương trình
migration có thứ tự, thay vì tiếp tục vá lỗi theo sự cố.

| Nhóm hiện tại | Target owner | Trạng thái | Nguyên tắc migration |
|---|---|---|---|
| Autonomy runtime | `qlda.autonomy.runtime` | **Hoàn tất native owner; facade đã xóa** | Worker, API, UI và regression test import trực tiếp native runtime |
| AI Supervisor overview | `qlda.presentation.streamlit.autonomy_overview` | **Đã migrate khỏi runtime_core** | Composition gọi trực tiếp presentation owner; runtime copy đã xóa |
| Advanced Automation UI | `qlda.presentation.streamlit.advanced_automation` | **Đã migrate khỏi runtime_core** | V9.1–V9.6 UI nằm trong presentation; runtime copy đã xóa |
| AI Supervisor navigation | `qlda.presentation.streamlit.ai_supervisor_navigation` | **Đã migrate khỏi runtime_core** | Composition gọi trực tiếp presentation owner; runtime module cũ đã xóa |
| Multiselect visual policy | `qlda.presentation.streamlit.multiselect_tag_style` | **Đã migrate khỏi runtime_core** | Chỉ còn presentation code; runtime module cũ đã xóa |
| Expander behavior policy | `qlda.presentation.streamlit.expander_policy` | **Đã migrate khỏi runtime_core** | Chỉ còn presentation code; runtime module cũ đã xóa |
| Contractor access source patch | `qlda.runtime_core.contractor_access_control` | **Đã xóa patch nguồn cũ** | `contractor_access_patch.py` không còn production caller và không còn trong composition baseline |
| AI context / provider (`ai_*`, `contract_ai_*`) | `application/ai` + `infrastructure/ai` | Chờ | Tách context builder khỏi provider/network adapter |
| BOQ / IPC / VO business semantics | `domain/commercial` + `application/commercial` | Chờ | Khóa regression công thức trước khi di chuyển |
| `contract_management.py`, `contract_duration.py` | `domain/contracts` + `application/contracts` | Chờ | Domain giữ deadline/rule, UI chỉ render |
| `work_tasks_v1.py`, routing/email | `domain/tasks` + `application/tasks` + `infrastructure/notifications` | Chờ | Không gửi mail từ domain; giữ audit/idempotency |
| Contractor workspace/access | `domain/access` + `application/access` | Chờ | Giữ `workspace_project_id` là tenant boundary |
| Google OAuth/settings store | `infrastructure/google` + `infrastructure/settings` | Chờ | Application/Autonomy không import runtime_core sau khi tách |
| `project_store.py`, `project_database.py` | `infrastructure/postgres` repositories | Chờ | Migrate theo repository/use-case, không big-bang |
| Finance UI / project cost UI | `presentation/streamlit/finance` | Chờ | Business formula phải nằm ngoài renderer |
| Document UI / attachment behavior | `presentation/streamlit/documents` + application use cases | Chờ | Upload/RBAC/approval giữ nguyên |
| Production progress UI | `presentation/streamlit/production` | Một phần native | Xóa patch layer sau khi source-exact renderer thành owner |
| `ui_v7_compact.py` | `presentation/streamlit/components` | Chờ | Tách theme/components khỏi business data access |
| Materialized `app.py` | `presentation/streamlit/pages/*` | **Frozen** | Tách từng navigation group; app.py không được tăng kích thước |

## Trạng thái hardening hiện tại

Đã khóa các nguyên tắc sau bằng CI chứ không chỉ bằng tài liệu:

- `runtime_core.__init__` phải side-effect free;
- Domain/Application không được tạo dependency mới vào `runtime_core`;
- `app.py` legacy không được tăng kích thước;
- không được thêm file `*_fix.py`, `*_patch.py`, `*_recovery.py`, `*_guard.py` mới trong `runtime_core`;
- UI feature mới không được sở hữu bởi `runtime_core`;
- các compatibility module đã retire không được tạo lại hoặc import lại;
- không cho phép `exec()` / `eval()` trong packaged source ngoài đúng một debt IPC đã khóa;
- BOQ/IPC/VO/Schedule/Contract/Autonomy có workflow regression riêng.

Các regression cleanup lịch sử `test_cleanup_v1`, `test_cleanup_v2*` đã được hợp nhất thành
`tests/test_cleanup_invariants.py` để giữ cùng invariant nhưng bỏ file test theo phiên bản.

## Thứ tự ưu tiên

### P0 — boundaries và runtime wiring

Đã thực hiện: composition root, import side-effect guard, Streamlit modular entrypoint,
architecture CI, critical-domain CI.

### P1 — các vertical slice có rủi ro thấp

Đang thực hiện theo nguyên tắc **move owner first, delete compatibility second**:

- AI Supervisor navigation — hoàn tất.
- AI Supervisor overview — hoàn tất.
- Advanced Automation UI — hoàn tất.
- Multiselect style policy — hoàn tất.
- Expander behavior policy — hoàn tất.
- Contractor access source patch cũ — đã xóa; runtime dùng `contractor_access_control.py`.
- Autonomy runtime facade — đã xóa; caller dùng `qlda.autonomy.runtime`.
- Settings/Google OAuth infrastructure — tiếp theo.

### P2 — workflow nghiệp vụ trung tâm

- Tasks / document workflow.
- Contract management.
- Production progress.

### P3 — tài chính có rủi ro cao

- BOQ.
- IPC/Claim.
- VO.
- Project Cost/Cashflow.

P3 chỉ migrate khi golden/regression fixtures bao phủ công thức hiện hành. Không
rewrite đồng thời parser + persistence + UI của cùng một nghiệp vụ.

## Metrics cần theo dõi

Mỗi đợt migration phải làm ít nhất một chỉ số tốt hơn và không làm chỉ số khác xấu đi:

- bytes / số file trong `runtime_core` giảm;
- số facade compatibility giảm;
- `app.py` không tăng và dần giảm;
- số import `qlda.runtime_core` từ code native giảm;
- focused regression coverage tăng;
- số file tên `*_fix.py`, `*_patch.py`, `*_recovery.py`, `*_guard.py` không tăng;
- số UI feature còn do `runtime_core` sở hữu chỉ được giảm, không được tăng.

## Quy tắc xóa compatibility

Không xóa module cũ chỉ vì code native đã tồn tại. Chỉ xóa khi:

1. production caller đã chuyển sang native owner;
2. Architecture Guard khóa không cho import compatibility quay lại;
3. focused regression xanh;
4. Native Regression xanh;
5. Docker Check xanh;
6. deploy/healthcheck production vẫn giữ khả năng rollback.

Các file patch đã rời khỏi composition và không có production caller có thể được xóa như dead source,
nhưng phải đồng thời giảm baseline trong Architecture Guard để CI không che giấu nợ cũ.

## Nguyên tắc chống big-bang rewrite

"Triệt để" không có nghĩa xóa hàng chục nghìn dòng legacy trong một commit. Với
QLDA đang chạy production, cách xử lý triệt để là đặt boundary cứng, không cho nợ
mới phát sinh, rồi migrate từng vertical slice có regression và xóa compatibility
ngay khi slice đó đủ điều kiện. Điều này giảm nợ thật mà vẫn bảo toàn khả năng
rollback và dữ liệu nghiệp vụ tài chính.
