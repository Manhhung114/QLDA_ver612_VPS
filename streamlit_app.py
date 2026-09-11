from __future__ import annotations

import base64
import gzip
import os
from pathlib import Path

from performance_v1_v622 import get_compiled_app, phase as performance_phase

# Keep the WebOpt resource limits before pandas/numpy/BLAS are imported.
# Heavy Excel parsing uses separate Python processes; numerical libraries stay
# at one thread per process so 4 vCPU are used without nested oversubscription.
for _name, _value in {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "MALLOC_ARENA_MAX": "2",
}.items():
    os.environ.setdefault(_name, _value)

# VPS PostgreSQL startup: install connection-pool resilience before any
# CloudDatabase instance can create/use a pooled SSL connection.
import postgres_backend_v622 as _postgres_backend
from vps_postgres_resilience import install_vps_postgres_resilience
from performance_postgres_v1_v622 import install_performance_postgres_v1

install_vps_postgres_resilience(_postgres_backend)
_postgres_backend.install_postgres_backend()
# Performance V1 keeps all SQL/business semantics intact. It only reuses
# deterministic SQL translations and skips repeated core DDL/migration after
# the first successful database bootstrap in this Streamlit process.
install_performance_postgres_v1(_postgres_backend)

# One account = one active login session. A successful login on another device
# replaces the previous session; refresh/multiple tabs keep the same token.
# This patch covers the PostgreSQL/local backend and adds the same API methods to
# DriveGateway for Google Apps Script deployments.
from single_session_v622 import install_single_session

install_single_session()

# Add the contractor layer before the generated app creates its database object.
# The legacy project itself becomes the default contractor workspace, so existing
# project data is preserved in-place. Additional contractors use hidden child
# project rows and therefore inherit every existing project-scoped feature.
from contractor_workspace_v622 import install_contractor_workspace
from contractor_sidebar_admin_v622 import install_contractor_sidebar_admin
from contractor_workspace_reset_v622 import install_contractor_workspace_reset
from contractor_access_control_v622 import (
    install_contractor_access_control,
    capture_single_contractor_ai_context,
    install_ai_access_guard,
)
from default_workspace_admin_guard_v622 import install_default_workspace_admin_guard

install_contractor_workspace()
# Admin-only contractor controls are rendered directly below the active
# contractor selector. The default workspace cannot be deleted; Admin can reset
# its business data while preserving the project/workspace identity and access.
install_contractor_sidebar_admin()
install_contractor_workspace_reset()
install_contractor_access_control()
# The default workspace is a protected system workspace: only Admin may see,
# select, manage, reset or query it. All other roles receive only non-default
# contractor workspaces; their AI context is pinned to the selected workspace.
install_default_workspace_admin_guard()

# Work Assignment V1 is a separate project/workspace-scoped business module.
# It keeps its own task, comment, file-reference and append-only audit tables;
# attachments still use the existing Drive/VPS storage gateway.
from work_tasks_v1_v622 import install_work_tasks_v1

install_work_tasks_v1()

# Contract duration is entered manually from the signed contract. Expiry is
# derived automatically as effective date + construction duration, and the
# migration preserves existing contract records on SQLite/PostgreSQL.
from contract_duration_v622 import install_contract_duration_v622

install_contract_duration_v622()

# Large contract PDFs are split by page into provider-safe parts before the AI
# request. Scanned pages remain inside the PDF chunks, so no OCR dependency is
# required and files above the former 25 MB per-file limit can still be read.
from contract_ai_large_pdf_v622 import install_contract_ai_large_pdf_v622

install_contract_ai_large_pdf_v622()

# Harden Gemini routing before the generated Streamlit source imports/uses the
# AI assistant. 503/429 failures are retried briefly, then routed across stable
# Flash / Flash-Lite families with per-model cooldown.
from gemini_resilience_v622 import install_gemini_resilience

install_gemini_resilience()

# Route every AI snapshot to the same PostgreSQL LIVE backend and append
# project-level IPC/Claim + signed VO detail context.
from ai_live_context_v622 import install_ai_live_context
from ai_claim_context_v622 import install_ai_claim_context
from ai_vo_context_v622 import install_ai_vo_context

install_ai_live_context()
install_ai_claim_context()
install_ai_vo_context()

# Keep legacy all-in BOQ values unchanged while adding nullable material/labor
# unit prices and calculated component costs for Excel import and AI context.
from boq_cost_components_v622 import install_boq_cost_components

install_boq_cost_components()

# IPC workbooks are large and openpyxl ReadOnlyWorksheet random cell access is
# extremely slow. Install the sequential parser/cache first, then label-based
# legacy fixes, then the adaptive semantic parser. The acceptance-period guard
# is installed after adaptive parsing so it recognizes Kỳ trước / Kỳ này / Lũy kế
# by meaning and arithmetic identity instead of fixed row/cell addresses.
# Multicore Excel is installed after those parser semantics so child processes
# build previews while the parent parses metadata/payment/GTHT.
from ipc_claim_fast_v622 import install_ipc_claim_fast_path
from ipc_claim_summary_fix_v622 import install_ipc_claim_summary_fix
from ipc_adaptive_parser_v622 import install_ipc_adaptive_parser
from ipc_payment_semantic_v622 import install_ipc_payment_semantic
from multicore_excel_v622 import install_multicore_excel
from ipc_claim_number_fix_v622 import install_ipc_claim_number_fix

install_ipc_claim_fast_path()
install_ipc_claim_summary_fix()
install_ipc_adaptive_parser()
install_ipc_payment_semantic()
install_multicore_excel()
install_ipc_claim_number_fix()

# One business vocabulary for BOQ + Claim:
# Khối lượng; Đơn giá -> Vật tư / Nhân công. Legacy DB column names remain
# untouched for backward compatibility.
from boq_claim_terms_v622 import install_boq_claim_terms

install_boq_claim_terms()

# Recover split unit prices from the project-scoped workbook snapshots. This
# repairs BOQ/Claim rows imported before the split-price patch and recognizes
# the real multi-row Excel headers (Đơn giá -> Vật tư/Vật liệu | Nhân công).
from boq_claim_price_recovery_v622 import install_boq_claim_price_recovery

install_boq_claim_price_recovery()

# The Claim description itself can contain the words "Diễn giải khối lượng".
# Prefer the standalone Khối lượng column so C is not confused with column B.
from boq_claim_price_header_guard_v622 import install_boq_claim_price_header_guard

install_boq_claim_price_header_guard()

# For project-wide BOQ material/labor questions, aggregate every imported Excel
# detail row across every saved sheet before the LLM sees the answer. Prompt row
# caps are evidence/display limits only and can never define the total.
from boq_ai_fullscan_v622 import install_boq_ai_fullscan

install_boq_ai_fullscan()

# Claim component values are recalculated from business quantities and split
# unit prices on every component-related AI query. Each Claim is full-scanned
# independently, so IPC-10 uses all IPC-10 rows and never a prompt sample.
from claim_component_fullscan_v622 import install_claim_component_fullscan

install_claim_component_fullscan()

# Period material values are controlled by the semantic payment summary:
# Previous + Current = Cumulative. This corrects shifted/renamed forms and also
# rescans saved sheet snapshots of old Claims without requiring a re-upload.
from claim_material_period_guard_v622 import install_claim_material_period_guard

install_claim_material_period_guard()

# A single contractor workspace still uses BOQ FULL-SCAN minus the cumulative
# components of that contractor's highest numeric IPC period.
from project_remaining_components_v622 import install_project_remaining_components

install_project_remaining_components()

# Capture the fully patched single-workspace AI context BEFORE the outer project
# aggregator is installed. The access guard later forces every non-Admin session
# to this single-workspace context; only Admin may aggregate the default workspace.
capture_single_contractor_ai_context()

# Project AI is the outermost context layer. It enumerates every contractor
# workspace under the selected master project, runs the validated BOQ/Claim
# calculations per contractor, then aggregates them for Admin project-level answers.
from contractor_ai_context_v622 import install_contractor_ai_context

install_contractor_ai_context()
install_ai_access_guard()

from build_v621_webopt import _finalize_source
from v622_auth_refresh_v4 import patch_auth_refresh_v4
from v622_boq_multisheet_patch import patch_boq_multisheet
from v622_ipc_claim_patch import patch_ipc_claims
from v622_vo_claim_patch import patch_vo_claims
from v622_report_cost_patch import patch_report_cost
from v622_local_vps_patch import patch_local_vps
from v622_contractor_workspace_patch import patch_contractor_workspace
from v622_contractor_access_patch import patch_contractor_access
from v622_contractor_sidebar_patch import patch_contractor_sidebar_ui
from v622_single_session_patch import patch_single_session
from v622_schedule_management_patch import patch_schedule_management
from v622_legal_qlda_patch import patch_legal_qlda
from v622_original_import_patch import patch_original_import_storage
from v622_ui_v7_compact_patch import patch_ui_v7_compact


# VPS entrypoint. The historical V6.21 source bundle is rebuilt in memory and
# finalized as V6.22 PostgreSQL, then executed behind Nginx/systemd.
_ROOT = Path(__file__).resolve().parent
_PARTS_DIR = _ROOT / "v621_webopt_source"
_PARTS = tuple(sorted(_PARTS_DIR.glob("part_*.b64")))

if len(_PARTS) != 9:
    raise RuntimeError(
        f"QLDA V6.22 source is incomplete: expected 9 parts, found {len(_PARTS)}."
    )

_SIGNATURE = tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in _PARTS)


def _build_compiled_vps_app():
    try:
        encoded = "".join(p.read_text(encoding="ascii").strip() for p in _PARTS)
        source = gzip.decompress(base64.b64decode(encoded)).decode("utf-8")
    except Exception as exc:
        raise RuntimeError(f"Invalid QLDA V6.22 source bundle: {exc}") from exc

    source = _finalize_source(source)
    source = patch_boq_multisheet(source)
    source = patch_ipc_claims(source)
    source = patch_vo_claims(source)
    source = patch_report_cost(source)
    source = patch_auth_refresh_v4(source)
    source = patch_local_vps(source)
    source = patch_contractor_workspace(source)
    source = patch_contractor_access(source)
    # Single-session patch relies on stable authentication anchors. Apply it
    # before the final contractor main-tab cleanup so production uses the exact
    # same order validated by the Single Session Check workflow.
    source = patch_single_session(source)
    source = patch_contractor_sidebar_ui(source)
    source = patch_schedule_management(source)
    source = patch_legal_qlda(source)
    # Business import sources (BOQ / IPC / VO / MPP / schedule Excel) are kept
    # as exact original files on VPS. Parsed PostgreSQL snapshots remain an
    # index/analysis layer, never the only copy of the uploaded source.
    source = patch_original_import_storage(source)
    # Visual/navigation-only V7 layer is deliberately last: it changes no
    # business formulas, database schema, permissions, upload or workflow logic.
    source = patch_ui_v7_compact(source)
    return compile(source, str(_ROOT / "streamlit_app_v622_postgresql.py"), "exec")


def _compiled_vps_app(signature):
    # lru_cache defined in this entrypoint is recreated by Streamlit on every
    # rerun. Performance V1 stores the immutable code object in an imported
    # process-level module, so all sessions/reruns share one compiled bundle.
    return get_compiled_app(("qlda-v622-generated-app", signature), _build_compiled_vps_app)


with performance_phase("generated_app_exec"):
    exec(_compiled_vps_app(_SIGNATURE), globals(), globals())
