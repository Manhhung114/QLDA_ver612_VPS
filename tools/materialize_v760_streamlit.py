from __future__ import annotations

import ast
import base64
import gzip
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
from v624_excel_background_patch import patch_excel_background_v624
from v622_ui_v7_compact_patch import patch_ui_v7_compact

PARTS = ROOT / "v621_webopt_source"
OUT = ROOT / "src/qlda/presentation/streamlit/materialized_app.py"
MANIFEST = ROOT / "docs/architecture/V7.6_MATERIALIZED_STREAMLIT_IMPORTS.txt"


def build_source() -> str:
    parts = sorted(PARTS.glob("part_*.b64"))
    if len(parts) != 9:
        raise RuntimeError(f"expected 9 V6.21 source parts, found {len(parts)}")
    encoded = "".join(path.read_text(encoding="ascii").strip() for path in parts)
    source = gzip.decompress(base64.b64decode(encoded)).decode("utf-8")
    source = _finalize_source(source)
    source = patch_boq_multisheet(source)
    source = patch_ipc_claims(source)
    source = patch_vo_claims(source)
    source = patch_report_cost(source)
    source = patch_auth_refresh_v4(source)
    source = patch_local_vps(source)
    source = patch_contractor_workspace(source)
    source = patch_contractor_access(source)
    source = patch_single_session(source)
    source = patch_contractor_sidebar_ui(source)
    source = patch_schedule_management(source)
    source = patch_legal_qlda(source)
    source = patch_original_import_storage(source)
    source = patch_excel_background_v624(source)
    source = patch_ui_v7_compact(source)
    compile(source, str(OUT), "exec")
    return source


def imported_modules(source: str) -> list[str]:
    tree = ast.parse(source)
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.add(node.module)
    return sorted(values)


def main() -> None:
    source = build_source()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    package_init = OUT.parent / "__init__.py"
    package_init.write_text(
        '"""Native Streamlit presentation package introduced by V7.6."""\n',
        encoding="utf-8",
    )
    OUT.write_text(
        "# Generated once during the V7.6 conversion from the proven production source.\n"
        "# This file is source-controlled; production MUST NOT regenerate or exec source dynamically.\n\n"
        + source,
        encoding="utf-8",
    )
    modules = imported_modules(source)
    MANIFEST.write_text(
        "V7.6 materialized Streamlit import inventory\n"
        "=========================================\n"
        + "\n".join(modules)
        + "\n",
        encoding="utf-8",
    )
    print(f"materialized {len(source):,} chars -> {OUT.relative_to(ROOT)}")
    print(f"captured {len(modules)} imports -> {MANIFEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
