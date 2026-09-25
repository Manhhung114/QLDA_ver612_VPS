# AI Architecture

## Trạng thái hiện tại

Roadmap hiện đại hóa AI production đã hoàn tất theo chuỗi:

```text
AI Boundary
    -> Telemetry + Audit
    -> Unified Context Port
    -> Document ingestion + provenance
    -> pgvector / RAG
    -> Native Tool Calling
    -> AI Quality Eval
    -> Migrate legacy AI modules
    -> Retire runtime_core AI compatibility
```

Các invariant hiện tại:

- `application/ai` là boundary native và không import `runtime_core`;
- `application/contractor_data_hub` không import `runtime_core`;
- `infrastructure/ai` có **zero runtime_core bridge**;
- provider execution dùng `NativeProviderGateway` cho OpenAI/Gemini;
- cấu hình provider/Admin encrypted settings được đọc bởi `provider_settings.py`, không monkey-patch `runtime_core.ai_service`;
- RAG tenant-scoped theo `workspace_project_id`, có provenance, checksum dedup và lexical fallback;
- AI Planner dùng native tool/function calling, không regex-parse JSON;
- telemetry/audit ghi theo workspace và fail-open;
- deterministic AI quality eval chạy trong AI Quality Gate;
- `RuntimeStage.AI` đã bị xóa khỏi runtime composition;
- `initialize_ai_runtime()` chỉ còn là bootstrap alias tương thích, không cài AI patch;
- `runtime_settings_bridge` không còn import/patch `ai_service`.

Một số file AI lịch sử có thể còn nằm trong `runtime_core` để phục vụ truy vết lịch sử/rollback, nhưng **không còn nằm trong production composition và không còn là dependency được phép của AI native**. Việc thêm lại AI runtime stage hoặc bridge bị regression test chặn.

## Mục tiêu và boundary

AI của QLDA hỗ trợ kỹ sư nhưng không được trở thành đường tắt bỏ qua nghiệp vụ. Mỗi nhà thầu là một AI tenant độc lập theo `workspace_project_id`. LLM không được SQL trực tiếp, không tự phê duyệt IPC/VO/hồ sơ/NCR và không sửa tiến độ ngoài ToolRegistry/Approval Gate.

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
  - NativeProviderGateway
  - provider_settings
  - OpenAI/Gemini native function calling
  - embeddings
  - PostgreSQL pgvector/lexical retrieval
  - durable telemetry/audit
        |
        v
external AI providers
```

Không có mũi tên từ `infrastructure/ai` sang `runtime_core`.

## Provider boundary

`NativeProviderGateway` là provider boundary duy nhất cho chat/report/legal/vision/Data Hub. Provider settings được resolve theo thứ tự tương thích với deployment hiện tại:

- Admin-managed encrypted settings nếu `managed_ai=true`;
- nếu không, environment/Streamlit secrets ưu tiên;
- OpenAI/Gemini model có default an toàn;
- thiếu API key trả lỗi domain rõ ràng, không rơi về legacy engine.

`infrastructure/ai/legacy_provider.py` chỉ là deprecated import shim trỏ tới native gateway cho caller cũ chưa đổi tên import; file này **không import `runtime_core` và không chứa compatibility engine**.

## Grounded retrieval / RAG

Contractor Data Hub chuẩn hóa Google Sheets/Drive/PDF/XLSX thành record có `content` và provenance. Worker/indexer đưa record cùng operational narrative vào `qlda_ai_chunks`. Mỗi chunk luôn mang `workspace_project_id`; vector search và lexical fallback đều lọc tenant trước khi trả kết quả.

```text
Google/Drive/Data Hub + operational records
        |
        v
normalized records + provenance
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
[NGUỒN n: source_ref] + prompt
        |
        v
NativeProviderGateway
```

Nếu PostgreSQL chưa có quyền tạo extension `vector`, chat vẫn hoạt động bằng lexical retrieval tenant-filtered. Indexing là content-addressed nên record không đổi checksum không bị embed lại.

### Dữ liệu số authoritative

RAG tìm nội dung/bằng chứng, không thay thế deterministic engines. Ví dụ:

- điều khoản/hạn phản hồi -> retrieval/RAG;
- IPC vượt BOQ -> reconciliation/SQL deterministic;
- giá trị VO -> engine VO + dữ liệu hợp đồng/BOQ;
- Project Health -> deterministic supervisor indicators.

LLM không được tự cộng lũy kế IPC để tạo số authoritative.

## Native tool calling

`StructuredAIPlanner` ưu tiên OpenAI/Gemini native function calling. Model chỉ lựa chọn function; thực thi vẫn đi theo:

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

Text-JSON planner chỉ là opt-in fallback (`QLDA_AI_TEXT_PLANNER_FALLBACK=1`) và parse bằng `json.loads` nghiêm ngặt. Mặc định provider failure quay về deterministic `HeuristicPlanner`.

## Audit / telemetry

`qlda_ai_audit` lưu theo workspace các metadata cần thiết: request/plan id, provider/model, input hash, context/source refs, tool/arguments/reason, latency, token usage/cost khi cấu hình được, fallback/success/error code. Secrets được redact và telemetry fail-open.

## Quality evaluation và architecture guard

CI không gọi provider thật. Golden/deterministic eval bao phủ:

- retrieval Recall@K / precision / provenance;
- tenant leakage = 0;
- citation recall và hallucinated source detection;
- forbidden answer assertion detection;
- planner tool recall và forbidden/unknown tool detection;
- provider failure -> deterministic fallback mặc định;
- schema required fields;
- không regex JSON extraction;
- application/Data Hub không import `runtime_core`;
- **mọi file `infrastructure/ai` phải có zero `runtime_core` imports**;
- runtime composition không được có `RuntimeStage.AI`;
- bootstrap không được `install_stage(RuntimeStage.AI)`;
- runtime settings bridge không được patch `AISettings/GeminiSettings`.

Workflow `.github/workflows/ai-quality.yml` compile cả native AI boundary lẫn runtime retirement boundary trước khi chạy regression suite.

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

## Quy tắc từ đây về sau

1. AI capability mới phải đi qua `application/ai` + `infrastructure/ai`.
2. Không thêm provider/context monkey patch vào `runtime_core`.
3. Không khôi phục `RuntimeStage.AI`.
4. Không cho LLM vượt ToolRegistry/RBAC/Data Integrity/Approval Gate.
5. Mọi nguồn RAG phải tenant-scoped và mang provenance.
6. Thay đổi AI boundary phải qua AI Quality Gate.
