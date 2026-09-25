# Runtime Core Migration Ledger

Mục đích của ledger này là biến việc giảm `runtime_core` thành một chương trình
migration có thứ tự, thay vì tiếp tục vá lỗi theo sự cố.

| Nhóm hiện tại | Target owner | Trạng thái | Nguyên tắc migration |
|---|---|---|---|
| `autonomy_runtime.py` | `qlda.autonomy.runtime` | **Đã tách runtime; facade còn lại** | Chuyển dần caller sang native path rồi xóa facade |
| AI context / provider (`ai_*`, `contract_ai_*`) | `application/ai` + `infrastructure/ai` | Chờ | Tách context builder khỏi provider/network adapter |
| BOQ / IPC / VO business semantics | `domain/commercial` + `application/commercial` | Chờ | Khóa regression công thức trước khi di chuyển |
| `contract_management.py`, `contract_duration.py` | `domain/contracts` + `application/contracts` | Chờ | Domain giữ deadline/rule, UI chỉ render |
| `work_tasks_v1.py`, routing/email | `domain/tasks` + `application/tasks` + `infrastructure/notifications` | Chờ | Không gửi mail từ domain; giữ audit/idempotency |
| Contractor workspace/access | `domain/access` + `application/access` | Chờ | Giữ `workspace_project_id` là tenant boundary |
| Google OAuth/settings store | `infrastructure/google` + `infrastructure/settings` | Chờ | Autonomy không import runtime_core sau khi tách |
| `project_store.py`, `project_database.py` | `infrastructure/postgres` repositories | Chờ | Migrate theo repository/use-case, không big-bang |
| Finance UI / project cost UI | `presentation/streamlit/finance` | Chờ | Business formula phải nằm ngoài renderer |
| Document UI / attachment behavior | `presentation/streamlit/documents` + application use cases | Chờ | Upload/RBAC/approval giữ nguyên |
| Production progress UI | `presentation/streamlit/production` | Một phần native | Xóa patch layer sau khi source-exact renderer thành owner |
| `ui_v7_compact.py` | `presentation/streamlit/components` | Chờ | Tách theme/components khỏi business data access |
| Materialized `app.py` | `presentation/streamlit/pages/*` | **Frozen** | Tách từng navigation group; app.py không được tăng kích thước |

## Thứ tự ưu tiên

### P0 — boundaries và runtime wiring

Đã thực hiện: composition root, import side-effect guard, Streamlit modular entrypoint,
architecture CI, critical-domain CI.

### P1 — các vertical slice có rủi ro thấp

- AI Supervisor / autonomy runtime — bắt đầu bằng facade compatibility.
- Navigation và presentation components.
- Settings/Google OAuth infrastructure.

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
- số file tên `*_fix.py`, `*_patch.py`, `*_recovery.py`, `*_guard.py` không tăng.

## Quy tắc xóa compatibility

Không xóa module cũ chỉ vì code native đã tồn tại. Chỉ xóa khi:

1. code search xác nhận không còn production caller;
2. focused regression xanh;
3. Native Regression xanh;
4. Docker Check xanh;
5. deploy/healthcheck production đã soak ổn định;
6. rollback không phụ thuộc module đó.
