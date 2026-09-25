# QLDA Architecture Hardening

## Mục tiêu

Đợt hardening này xử lý nợ kỹ thuật mà không đổi nghiệp vụ, schema dữ liệu, công
thức tài chính, parser BOQ/IPC/VO, RBAC, luồng phê duyệt, upload, transaction hay
rollback production.

Các nguyên tắc bắt buộc từ đây:

1. `qlda.runtime_core` là **compatibility boundary**, không phải nơi phát triển tính năng mới.
2. Import package không được âm thầm cài monkey-patch hoặc thay thế initializer.
3. Runtime wiring chỉ được thực hiện tại composition root có thứ tự rõ ràng.
4. `app.py` Streamlit cũ được đóng băng; production chạy từ entrypoint nhỏ `main.py`.
5. Không được tạo thêm file kiểu `*_fix.py`, `*_patch.py`, `*_recovery.py`, `*_guard.py` trong `runtime_core`.
6. Domain/Application không được phụ thuộc ngược vào `runtime_core`.
7. Mọi thay đổi tài chính/BOQ/IPC/VO/Schedule/Autonomy phải qua regression CI chuyên biệt.

## Những thay đổi đã thực hiện

### 1. Explicit composition root

`src/qlda/composition/runtime_features.py` khai báo toàn bộ compatibility installer
theo 4 stage:

- DATA
- BUSINESS
- AI
- UI

Thứ tự cài đặt là dữ liệu source-controlled, inspect được và test được. Không còn
cơ chế `runtime_core.__init__` tự thay `initialize_runtime` / `initialize_ai_runtime`
tùy theo import order.

### 2. Side-effect-free package import

`src/qlda/runtime_core/__init__.py` không còn thực hiện runtime wiring. Các process
(Streamlit, API, worker) phải đi qua bootstrap/composition rõ ràng.

### 3. Streamlit strangler entrypoint

Production khởi động từ:

`src/qlda/presentation/streamlit/main.py`

`app.py` hiện vẫn chứa materialized V7.6 shell để bảo toàn hành vi production, nhưng
được CI khóa kích thước tại baseline 315,715 bytes. Tính năng mới phải được tách ra
module presentation riêng, không được làm `app.py` lớn hơn.

Đây là bước strangler an toàn: đổi điểm khởi động trước, sau đó tách từng screen/use
case theo regression tests thay vì rewrite 300+ KB UI trong một lần.

### 4. Autonomy runtime extraction

Runtime AI Supervisor/Orchestrator đã được chuyển sang `qlda.autonomy.runtime`.
`qlda.runtime_core.autonomy_runtime` chỉ còn facade tương thích để caller cũ không
bị gãy. Các caller mới phải dùng native path.

### 5. Architecture debt guard

`tools/architecture_guard.py` chặn:

- import-time wiring quay trở lại `runtime_core.__init__`;
- Domain/Application import `qlda.runtime_core`;
- `app.py` legacy tăng kích thước;
- production entrypoint trở thành monolith mới;
- file fix/patch/recovery/guard mới trong runtime_core;
- `exec`/`eval` source động trong `src/qlda`.

Allowlist chỉ là baseline để migration không phá production. Khi một compatibility
file được xóa/migrate thì phải xóa khỏi allowlist; không được thêm file mới vào
allowlist để né guard.

### 6. CI theo lớp rủi ro

Ngoài Native Regression và Docker Check, repo có thêm:

- **Architecture Quality Gate**: boundary, debt growth, compile, repository contract.
- **Critical Domain Regression**: BOQ, IPC, VO, Schedule, Contract, Autonomy, tenant isolation, Supervisor.

## Điều chưa được tuyên bố là hoàn tất

Hardening không có nghĩa toàn bộ legacy đã biến mất. `runtime_core` vẫn còn nhiều
compatibility implementation đã chứng minh trong production. Mục tiêu đúng là:

- không cho nợ kỹ thuật tăng thêm;
- làm dependency/wiring deterministic;
- tách từng vertical slice sang native layer;
- giữ facade tương thích tới khi mọi caller đã chuyển;
- xóa facade và compatibility implementation sau khi regression + production soak
  chứng minh an toàn.

Không được gọi repo là "legacy-free" chỉ vì đã có composition root.

## Definition of Done cho mỗi migration slice

Một slice chỉ được coi là migrate xong khi:

- business rule nằm ở domain/application/autonomy thích hợp;
- persistence/external system nằm ở infrastructure;
- Streamlit chỉ render/gọi use case;
- caller cũ dùng facade mỏng hoặc đã chuyển hết;
- tenant isolation/RBAC/audit vẫn nguyên vẹn;
- focused regression + full native regression + Docker Check xanh;
- compatibility file tương ứng được giảm hoặc xóa.

## Quy tắc phát triển mới

Khi phát sinh lỗi, không tạo thêm `xxx_fix.py`. Trước tiên xác định owner thực của
business rule, thêm regression test tái hiện lỗi, sửa tại module owner và giữ một
compatibility adapter mỏng nếu cần. Mục tiêu của `runtime_core` từ thời điểm này là
**chỉ giảm**, không tăng.
