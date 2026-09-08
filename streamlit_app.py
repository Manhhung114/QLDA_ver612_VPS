from __future__ import annotations

import base64
import gzip
import os
from functools import lru_cache
from pathlib import Path

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

install_vps_postgres_resilience(_postgres_backend)
_postgres_backend.install_postgres_backend()

# Route every AI snapshot to the same PostgreSQL LIVE backend and append
# project-level IPC/Claim + signed VO detail context.
from ai_live_context_v622 import install_ai_live_context
from ai_claim_context_v622 import install_ai_claim_context
from ai_vo_context_v622 import install_ai_vo_context

install_ai_live_context()
install_ai_claim_context()
install_ai_vo_context()

# IPC workbooks are large and openpyxl ReadOnlyWorksheet random cell access is
# extremely slow. Install the sequential parser/cache first, then label-based
# legacy fixes, then the adaptive semantic parser. Multicore Excel is installed
# after those parser semantics so child processes can build previews while the
# parent parses metadata/payment/GTHT. Claim-number guard remains last so the
# explicit filename controls the final save target.
from ipc_claim_fast_v622 import install_ipc_claim_fast_path
from ipc_claim_summary_fix_v622 import install_ipc_claim_summary_fix
from ipc_adaptive_parser_v622 import install_ipc_adaptive_parser
from multicore_excel_v622 import install_multicore_excel
from ipc_claim_number_fix_v622 import install_ipc_claim_number_fix

install_ipc_claim_fast_path()
install_ipc_claim_summary_fix()
install_ipc_adaptive_parser()
install_multicore_excel()
install_ipc_claim_number_fix()

from build_v621_webopt import _finalize_source
from v622_auth_refresh_v4 import patch_auth_refresh_v4
from v622_boq_multisheet_patch import patch_boq_multisheet
from v622_ipc_claim_patch import patch_ipc_claims
from v622_vo_claim_patch import patch_vo_claims
from v622_report_cost_patch import patch_report_cost


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


@lru_cache(maxsize=1)
def _compiled_vps_app(signature):
    del signature
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
    return compile(source, str(_ROOT / "streamlit_app_v622_postgresql.py"), "exec")


exec(_compiled_vps_app(_SIGNATURE), globals(), globals())
