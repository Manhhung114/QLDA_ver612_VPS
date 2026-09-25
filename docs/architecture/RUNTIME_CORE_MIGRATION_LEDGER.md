# Runtime Core Migration Ledger

Mục đích của ledger này là biến việc giảm `runtime_core` thành một chương trình
migration có thứ tự, thay vì tiếp tục vá lỗi theo sự cố.

| Nhóm hiện tại | Target owner | Trạng thái | Nguyên tắc migration |
|---|---|---|---|
| Autonomy runtime | `qlda.autonomy.runtime` | **Hoàn tất native owner; facade đã xóa** | Worker, API, UI và regression test import trực tiếp native runtime |
| AI Supervisor overview/navigation/automation | `qlda.presentation.streamlit.*` | **Hoàn tất** | Composition gọi trực tiếp presentation owner |
| UI policies (money/expander/multiselect/finance title/upload) | `qlda.presentation.streamlit.*` | **Hoàn tất** | Runtime không còn sở hữu UI feature |
| Document VPS / attachment reopen / selection | `qlda.presentation.streamlit.*` | **Hoàn tất owner** | Runtime compatibility owners đã xóa; business/storage services giữ nguyên |
| Production overview / source-exact / shared view | `qlda.presentation.streamlit.*` | **Hoàn tất owner** | Overview và source-exact đã rời runtime; shared AI context cũ chỉ còn compatibility trong strangler |
| Owner-supplied material UI | `qlda.presentation.streamlit.owner_supplied_materials_ui` | **Hoàn tất owner** | Nghiệp vụ/ledger tạm thời còn ở runtime; UI installer đã rời runtime |
| Project Cost UI | `qlda.presentation.streamlit.project_cost_management_ui` | **Hoàn tất owner** | Công thức PMBOK/ledger tạm thời còn ở runtime; UI installer đã rời runtime |
| Finance consistency UI | `qlda.presentation.streamlit.finance_consistency_ui` | **Hoàn tất owner** | Core finance policy còn ở runtime cho tới P3; presentation installer riêng |
| Contractor access source patch | `qlda.runtime_core.contractor_access_control` | **Đã xóa patch nguồn cũ** | `contractor_access_patch.py` không còn production caller |
| AI application boundary | `qlda.application.ai` | **Hoàn tất native boundary** | Context/retrieval/tool/telemetry contracts không import `runtime_core` |
| AI provider/network + RAG | `qlda.infrastructure.ai` | **Hoàn tất native owner; legacy engine cô lập** | OpenAI/Gemini adapter, embeddings, pgvector/lexical retrieval, telemetry và native tool-calling ở infrastructure; provider engine cũ chỉ còn nằm sau `legacy_provider` compatibility adapter |
| Contractor Data Hub AI provider | application port + infrastructure adapter | **Hoàn tất dependency cleanup** | Application service không còn import `runtime_core`; RAG index chạy theo `workspace_project_id` |
| AI planner/evaluation | `qlda.autonomy` + `application.ai.evaluation` | **Hoàn tất hardening** | Native function calling là mặc định; text JSON chỉ opt-in fallback; plan_id/tool/reason được audit; retrieval/planner eval chạy deterministic trong CI |
| BOQ / IPC / VO business semantics | `domain/commercial` + `application/commercial` | Chờ | Khóa regression công thức trước khi di chuyển |
| `contract_management.py`, `contract_duration.py` | `domain/contracts` + `application/contracts` | Chờ | Domain giữ deadline/rule, UI chỉ render |
| `work_tasks_v1.py`, routing/email | `domain/tasks` + `application/tasks` + `infrastructure/notifications` | Chờ | Không gửi mail từ domain; giữ audit/idempotency |
| Contractor workspace/access | `domain/access` + `application/access` | Chờ | Giữ `workspace_project_id` là tenant boundary |
| Google OAuth/settings store | `infrastructure/google` + `infrastructure/settings` | Chờ | Application/Autonomy không import runtime_core sau khi tách |
| `project_store.py`, `project_database.py` | `infrastructure/postgres` repositories | Chờ | Migrate theo repository/use-case, không big-bang |
| `ui_v7_compact.py` | `presentation/streamlit/components` | Chờ | Tách theme/components khỏi business data access |
| Materialized `app.py` | `presentation/streamlit/pages/*` | **Frozen** | Tách từng navigation group; app.py không được tăng kích thước |

## Trạng thái hardening hiện tại

Đã khóa các nguyên tắc sau bằng CI chứ không chỉ bằng tài liệu:

- `runtime_core.__init__` phải side-effect free;
- Domain/Application không được tạo dependency mới vào `runtime_core`;
- `app.py` legacy không được tăng kích thước;
- không được thêm file `*_fix.py`, `*_patch.py`, `*_recovery.py`, `*_guard.py` mới trong `runtime_core`;
- **không còn bất kỳ ngoại lệ runtime-owned UI feature nào**: mọi feature stage `UI` trong composition phải nằm dưới `qlda.presentation`;
- các compatibility module đã retire không được tạo lại hoặc import lại;
- không cho phép `exec()` / `eval()` trong packaged source ngoài đúng một debt IPC đã khóa;
- BOQ/IPC/VO/Schedule/Contract/Autonomy có workflow regression riêng;
- AI planner không còn regex JSON extraction; native tool calling chỉ nhận tool đã đăng ký;
- RAG retrieval và operational index luôn mang `workspace_project_id` + `source_ref` để kiểm tra tenant leakage/provenance;
- telemetry AI lưu request/plan/tool/reason/latency/token/cost-estimate theo workspace khi dữ liệu tương ứng có sẵn.

Các regression cleanup lịch sử `test_cleanup_v1`, `test_cleanup_v2*` đã được hợp nhất thành
`tests/test_cleanup_invariants.py` để giữ cùng invariant nhưng bỏ file test theo phiên bản.

## Thứ tự ưu tiên

### P0 — boundaries và runtime wiring

**Hoàn tất:** composition root, import side-effect guard, Streamlit modular entrypoint,
architecture CI, critical-domain CI.

### P1 — presentation ownership

**Hoàn tất ownership:**

- AI Supervisor navigation/overview/Advanced Automation;
- money/expander/multiselect/finance title policies;
- VPS document UI, attachment reopen và document selection;
- upload policy;
- production overview, source-exact view và shared Data Hub view;
- Contractor Data Hub admin visibility;
- owner-supplied material installer;
- Project Cost installer;
- Finance Consistency UI installer.

Architecture Guard hiện đặt runtime UI compatibility budget bằng **0**. Các module runtime còn chứa
business/persistence logic chưa được gọi là presentation owner nữa và sẽ được xử lý ở P2/P3.

### P2 — application/domain/infrastructure boundaries

Các slice AI được hoàn tất theo strangler, không big-bang:

1. `application/ai`: context/retrieval/tool-calling/telemetry/evaluation ports.
2. `infrastructure/ai`: provider adapter, embeddings, pgvector + lexical fallback, provenance index, durable telemetry.
3. Autonomy planner: native OpenAI/Gemini function calling từ schema ToolRegistry; deterministic fallback vẫn giữ.
4. Contractor Data Hub: background index theo từng contractor workspace; chat có operational evidence index throttle/fail-open.
5. PDF text/vision giữ SHA/file-identity cache hiện hữu để tránh scan lại file không đổi.

Phần provider engine cũ trong `runtime_core.ai_service` **không còn là application boundary**; nó được cô lập sau
`infrastructure.ai.legacy_provider` để các compatibility AI-context installers cũ tiếp tục chạy trong giai đoạn
strangler. Chỉ xóa engine này khi tất cả installer/context cũ đã có native replacement + regression tương ứng;
không xóa nóng vì sẽ làm mất PDF/deep-scan/context production đang sử dụng.

Các ưu tiên P2 còn lại:

1. Settings/Google OAuth về `infrastructure/settings` và `infrastructure/google`.
2. Tasks/routing/email về `domain/tasks`, `application/tasks`, `infrastructure/notifications`.
3. Contract management về `domain/contracts` + `application/contracts`.
4. Contractor workspace/access về `domain/access` + `application/access`.
5. Repository/store về `infrastructure/postgres`.
6. Tiếp tục tách `app.py` theo navigation page.

### P3 — tài chính/commercial rủi ro cao

- BOQ.
- IPC/Claim.
- VO.
- Project Cost/Cashflow.
- Owner supplied material ledger nếu cần đưa toàn bộ business semantics sang domain/application.

P3 chỉ migrate khi golden/regression fixtures bao phủ công thức hiện hành. Không
rewrite đồng thời parser + persistence + UI của cùng một nghiệp vụ.

`ipc_claim_patch.py` vẫn là debt đã định danh vì còn mang semantics ngày tới hạn/thanh toán bằng
source recompilation. Không xóa trước khi semantics này được đưa native vào IPC core và focused regression xanh.

## Metrics cần theo dõi

Mỗi đợt migration phải làm ít nhất một chỉ số tốt hơn và không làm chỉ số khác xấu đi:

- bytes / số file trong `runtime_core` giảm;
- số facade compatibility giảm;
- `app.py` không tăng và dần giảm;
- số import `qlda.runtime_core` từ code native giảm;
- focused regression coverage tăng;
- số file tên `*_fix.py`, `*_patch.py`, `*_recovery.py`, `*_guard.py` không tăng;
- số UI feature còn do `runtime_core` sở hữu = **0** và không được tăng lại;
- AI retrieval tenant leakage = **0**;
- AI planner unknown/forbidden tool selection = **0** sau validation;
- AI request latency/token/cost-estimate có audit trail theo workspace.

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
