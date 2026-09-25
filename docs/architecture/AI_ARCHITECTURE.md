# AI Architecture

## Mục tiêu

AI của QLDA phải hỗ trợ kỹ sư nhưng không được trở thành đường tắt bỏ qua nghiệp vụ.
Mỗi nhà thầu là một AI tenant độc lập theo `workspace_project_id`. LLM không được
SQL trực tiếp, không tự phê duyệt IPC/VO/hồ sơ/NCR và không được sửa tiến độ ngoài
ToolRegistry/Approval Gate.

## Boundary

```text
presentation / autonomy
        |
        v
application/ai
  - AIContextPort
  - AIToolCallingPort
  - AITelemetryPort
  - AIContextService
  - deterministic evals
        |
        v
infrastructure/ai
  - OpenAI/Gemini native function calling
  - embeddings
  - PostgreSQL pgvector/lexical retrieval
  - durable telemetry/audit
  - one explicit legacy provider adapter
```

`application/ai` không được import `runtime_core`. `infrastructure/native_ai.py`
không được import provider legacy trực tiếp. Trong giai đoạn strangler hiện tại,
chỉ `infrastructure/ai/legacy_provider.py` được phép bridge tới engine AI cũ.
Architecture Guard khóa invariant này.

## Grounded retrieval / RAG

Contractor Data Hub đã chuẩn hóa Google Sheets/Drive/PDF/XLSX thành các record có
`content` và provenance. Worker index chính những record đó vào `qlda_ai_chunks`.
Mỗi chunk luôn mang `workspace_project_id`; cả vector search lẫn lexical fallback
đều lọc tenant trước khi trả kết quả.

Pipeline:

```text
Google/Drive/Data Hub
        |
        v
normalized records
        |
        v
SHA/checksum dedup
        |
        +--> pgvector embedding (nếu khả dụng)
        |
        +--> lexical fallback
        |
        v
AIContextService
        |
        v
[NGUỒN n: source_ref] + câu hỏi
```

Indexing là content-addressed: record không đổi checksum không bị embed lại. Nếu
PostgreSQL chưa có quyền tạo extension `vector`, chat vẫn hoạt động và retrieval
chuyển sang lexical tenant-filtered.

### Provenance

Mỗi đoạn đưa cho model có `source_ref`; câu trả lời grounded được yêu cầu giữ nhãn
`[NGUỒN n: ...]`. Retrieval eval đo source coverage, Recall@K và tenant leakage.

### Dữ liệu số authoritative

RAG dùng để tìm nội dung và bằng chứng, **không** thay thế các engine deterministic.
Ví dụ:

- hỏi điều khoản/hạn phản hồi -> retrieval/RAG;
- IPC vượt BOQ bao nhiêu -> `reconcile_ipc_boq`/SQL deterministic;
- giá trị VO -> engine VO + dữ liệu hợp đồng/BOQ;
- Project Health -> Supervisor indicators deterministic.

LLM không được tự cộng lũy kế IPC để tạo số authoritative.

## Native tool calling

`StructuredAIPlanner` ưu tiên OpenAI/Gemini native function calling. JSON schema
được phát hành từ contract ToolSpec/application schema. Model chỉ lựa chọn function;
việc thực thi vẫn đi theo:

```text
Model tool choice
    -> ExecutionPlan
    -> ToolRegistry
    -> RBAC
    -> Data Integrity Gate
    -> Approval Gate
    -> Handler
    -> Audit
```

Text-JSON planner cũ chỉ là opt-in fallback (`QLDA_AUTONOMY_TEXT_PLANNER_FALLBACK`)
và parse bằng `json.loads` nghiêm ngặt; regex JSON extraction đã bị Architecture
Guard cấm. Provider-native path lỗi sẽ quay về HeuristicPlanner deterministic.

## Audit / telemetry

`qlda_ai_audit` lưu theo workspace:

- request/plan id;
- provider/model;
- input hash, context metadata, source refs;
- tool được chọn, arguments, reason;
- latency;
- input/output tokens khi provider trả usage;
- estimated cost chỉ khi operator cấu hình rate;
- fallback/success/error code.

Secrets được redact và telemetry fail-open để lỗi quan sát không làm hỏng nghiệp vụ.
Không hard-code giá API vì pricing thay đổi theo thời gian.

## Quality evaluation

CI không gọi provider thật. Golden/deterministic eval bao phủ:

- retrieval Recall@K / precision / provenance;
- tenant leakage phải bằng 0;
- planner tool recall;
- forbidden/unknown tool detection;
- native planner chỉ nhận registered tools;
- provider failure phải fallback deterministic;
- các schema quan trọng phải có required fields;
- planner không được quay lại regex JSON extraction.

Live/provider eval có thể chạy ngoài CI với bộ dữ liệu đã ẩn thông tin nhạy cảm.

## Cấu hình chính

```text
QLDA_AI_RAG_ENABLED=true
QLDA_AI_EMBEDDING_PROVIDER=auto
QLDA_AI_RAG_TOP_K=8
QLDA_AI_RAG_MAX_CHARS=18000
QLDA_AI_RAG_INDEX_LIMIT=10000
QLDA_AI_TELEMETRY_ENABLED=true
QLDA_AUTONOMY_AI_PLANNER_ENABLED=true
QLDA_AUTONOMY_AI_PROVIDER=openai
QLDA_AUTONOMY_TEXT_PLANNER_FALLBACK=false
```

## Legacy compatibility còn lại

Provider/context engine lịch sử vẫn còn trong `runtime_core` vì nhiều context patch
production đang monkey-patch các assistant class. Nó không còn là dependency được
phép của `application/ai` hay native adapter; toàn bộ bridge đã được gom về một
infrastructure compatibility adapter. Chỉ xóa engine cũ sau khi từng context slice
được migrate và regression tương ứng xanh. Đây là strangler, không phải big-bang.
