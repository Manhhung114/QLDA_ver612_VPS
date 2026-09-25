# AI Architecture

## Trạng thái hiện tại

Roadmap hiện đại hóa AI production đã hoàn thành ở cấp **boundary + retrieval + tool-calling + observability + deterministic quality gate**. Các tiêu chí đã khóa bằng code/test gồm:

- `application/ai` là boundary native, không phụ thuộc `runtime_core`;
- RAG tenant-scoped theo `workspace_project_id`, có provenance và lexical fallback;
- pgvector được dùng khi PostgreSQL cho phép, không làm chat phụ thuộc cứng vào extension;
- AI Planner ưu tiên native OpenAI/Gemini function calling thay vì regex JSON parsing;
- RBAC, Data Integrity Gate, Approval Gate và ToolRegistry vẫn nằm downstream, LLM không override được;
- audit/telemetry theo workspace ghi provider/model/context/source/tool/latency/usage khi khả dụng;
- deterministic AI quality eval chạy riêng trong **AI Quality Gate**;
- chỉ `infrastructure/ai/legacy_provider.py` còn được phép bridge tới AI engine compatibility cũ.

Phần còn lại trong `runtime_core` là **compatibility retirement backlog**, không phải thiếu capability của kiến trúc AI mới. Các slice cũ chỉ được xóa sau khi owner native tương ứng và regression của slice đó đã ổn định.

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

`application/ai` và `application/contractor_data_hub` không được import
`runtime_core`. `infrastructure/native_ai.py` không được import provider legacy
trực tiếp. Trong giai đoạn strangler hiện tại, chỉ
`infrastructure/ai/legacy_provider.py` được phép bridge tới engine AI/settings cũ.
Architecture Guard và `test_ai_architecture_boundary.py` khóa invariant này.

## Grounded retrieval / RAG

Contractor Data Hub đã chuẩn hóa Google Sheets/Drive/PDF/XLSX thành các record có
`content` và provenance. Worker/indexer đưa chính những record đó cùng dữ liệu vận
hành narrative vào `qlda_ai_chunks`. Mỗi chunk luôn mang `workspace_project_id`;
cả vector search lẫn lexical fallback đều lọc tenant trước khi trả kết quả.

Pipeline:

```text
Google/Drive/Data Hub + operational records
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
Answer eval đo citation recall, nguồn trích dẫn không tồn tại và các assertion bị
cấm trong golden fixture.

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

Text-JSON planner chỉ là opt-in compatibility fallback
(`QLDA_AI_TEXT_PLANNER_FALLBACK=1`) và parse bằng `json.loads` nghiêm ngặt; regex
JSON extraction đã bị Architecture Guard/test cấm. Mặc định, native provider failure
không gọi text planner mà quay thẳng về `HeuristicPlanner` deterministic.

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
- grounded-answer citation recall và hallucinated source detection;
- forbidden answer assertion detection;
- planner tool recall;
- forbidden/unknown tool detection;
- native planner chỉ nhận registered tools;
- provider failure phải fallback deterministic mặc định;
- các schema quan trọng phải có required fields;
- planner không được quay lại regex JSON extraction;
- Contractor Data Hub application không được quay lại import `runtime_core`;
- infrastructure AI không được phát sinh bridge thứ hai tới `runtime_core`.

Workflow `.github/workflows/ai-quality.yml` chạy riêng các regression AI architecture,
RAG/provenance, planner contract, quality eval, workspace indexer và contractor tenant isolation.
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
QLDA_AI_TEXT_PLANNER_FALLBACK=0
```

## Legacy compatibility còn lại

Provider/context engine lịch sử vẫn còn trong `runtime_core` vì nhiều context patch
production đang monkey-patch các assistant class. Nó không còn là dependency được
phép của application/native adapters; toàn bộ bridge provider/settings đã được gom
về `infrastructure.ai.legacy_provider`.

Nguyên tắc retirement:

```text
legacy slice
   -> native owner
   -> parity regression
   -> production soak
   -> remove composition entry / compatibility bridge usage
   -> delete legacy file
```

Không xóa hàng loạt `ai_service.py` hoặc các context patch đang phục vụ production.
Mục tiêu tiếp theo là giảm dần bridge compatibility về 0; khi bridge cuối cùng không
còn caller, test/guard sẽ được đổi từ “exactly one bridge” thành “zero runtime AI bridge”.
