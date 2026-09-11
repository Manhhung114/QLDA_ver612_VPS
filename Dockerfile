FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2 \
    QLDA_PARALLEL_EXCEL_MIN_MB=2 \
    QLDA_PARALLEL_MIN_SHEETS=2 \
    QLDA_MP_START_METHOD=forkserver

RUN apt-get update && apt-get install -y --no-install-recommends \
      default-jre-headless \
      build-essential \
      curl \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

RUN python -m py_compile \
      streamlit_app.py \
      build_v621_webopt.py \
      v622_auth_refresh_v4.py \
      v622_schedule_management_patch.py \
      v622_legal_qlda_patch.py \
      legal_qlda_v622.py \
      legal_standards_backfill_v622.py \
      legal_pccc_backfill_v622.py \
      legal_documents.py \
      original_import_storage_v622.py \
      v622_original_import_patch.py \
      v622_boq_multisheet_patch.py \
      boq_multisheet_v622.py \
      boq_persistence_v622.py \
      v622_ipc_claim_patch.py \
      ipc_claim_v622.py \
      ipc_claim_fast_v622.py \
      ipc_claim_summary_fix_v622.py \
      ipc_adaptive_parser_v622.py \
      ipc_claim_number_fix_v622.py \
      ipc_claim_period_v622.py \
      ipc_claim_delete_v622.py \
      multicore_excel_v622.py \
      v622_vo_claim_patch.py \
      vo_claim_v622.py \
      vo_independent_v622.py \
      v622_report_cost_patch.py \
      report_cost_v622.py \
      ai_live_context_v622.py \
      ai_claim_context_v622.py \
      ai_vo_context_v622.py \
      postgres_backend_v622.py \
      vps_postgres_resilience.py \
      streamlit_secrets_v622.py \
      drive_gateway.py \
      local_vps_backend_v622.py \
      local_file_server_v622.py \
      local_vps_runtime_fix_v622.py \
      v622_local_vps_patch.py \
    && python build_v621_webopt.py

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
