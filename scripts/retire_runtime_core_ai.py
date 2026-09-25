from __future__ import annotations

"""One-shot migration for retiring runtime_core AI compatibility.

The script is intentionally conservative: it patches only known presentation
imports/scope wiring, deletes only AI compatibility modules, and fails if any
remaining source/test still references a retired module. The CI workflow removes
this script and itself only after the migration has been applied in the checkout.
"""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/qlda"
APP = SRC / "presentation/streamlit/app.py"
RUNTIME = SRC / "runtime_core"
INFRA_AI = SRC / "infrastructure/ai"

RETIRED_MODULES = (
    "ai_claim_context",
    "ai_live_context",
    "ai_service",
    "ai_streaming",
    "ai_vo_context",
    "ai_vps_pdf_fullscan",
    "ai_vps_pdf_vision",
    "boq_ai_fullscan",
    "cashflow_ai_context",
    "contract_ai_deep_scan",
    "contract_ai_large_pdf",
    "contractor_ai_context",
    "contractor_data_official_ai",
    "contractor_data_shared_ai",
    "document_pdf_vision_provider",
    "gemini_resilience",
    "owner_material_ai_context",
    "project_cost_ai_context",
)

LEGACY_TESTS = (
    "test_ai_claim_context.py",
    "test_ai_live_context.py",
    "test_boq_ai_fullscan.py",
    "test_contractor_ai_context.py",
    "test_contractor_data_shared_ai.py",
    "test_gemini_resilience.py",
)


def _patch_app() -> None:
    source = APP.read_text(encoding="utf-8")
    old_import = "from qlda.runtime_core.ai_service import ("
    new_import = "from qlda.infrastructure.ai.presentation_facade import ("
    if old_import in source:
        source = source.replace(old_import, new_import, 1)
    elif new_import not in source:
        raise SystemExit("Cannot locate the Streamlit AI assistant import")

    source = source.replace(
        "    set_ai_workspace_scope as _v622_set_ai_workspace_scope,\n",
        "",
    )

    scope_block = re.compile(
        r"\n    # CONTRACTOR accounts set a ContextVar guard so ProjectContextBuilder,\n"
        r"    # attachment catalog and every AI helper remain inside the authorized workspace\.\n"
        r"    _v622_set_ai_workspace_scope\(\n"
        r"        int\(pid\) if _user_approval_role\(_cloud_identity\(\)\) == \"CONTRACTOR\" else None\n"
        r"    \)\n"
    )
    source, count = scope_block.subn("\n", source, count=1)
    if count == 0 and "_v622_set_ai_workspace_scope" in source:
        raise SystemExit("Cannot safely remove the legacy AI workspace ContextVar block")

    APP.write_text(source, encoding="utf-8")


def _delete_legacy_files() -> None:
    for stem in RETIRED_MODULES:
        path = RUNTIME / f"{stem}.py"
        if path.exists():
            path.unlink()
    legacy_provider = INFRA_AI / "legacy_provider.py"
    if legacy_provider.exists():
        legacy_provider.unlink()
    for name in LEGACY_TESTS:
        path = ROOT / "tests" / name
        if path.exists():
            path.unlink()


def _find_retired_references() -> list[str]:
    needles = [f"qlda.runtime_core.{name}" for name in RETIRED_MODULES]
    needles += ["qlda.infrastructure.ai.legacy_provider", "_v622_set_ai_workspace_scope"]
    offenders: list[str] = []
    roots = [SRC, ROOT / "tests"]
    for base in roots:
        for path in base.rglob("*.py"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            hits = sorted({needle for needle in needles if needle in text})
            if hits:
                offenders.append(f"{path.relative_to(ROOT)} -> {', '.join(hits)}")
    return offenders


def _assert_native_state() -> None:
    source = APP.read_text(encoding="utf-8")
    assert "qlda.infrastructure.ai.presentation_facade" in source
    assert "qlda.runtime_core.ai_service" not in source
    assert "set_ai_workspace_scope" not in source
    leftovers = [stem for stem in RETIRED_MODULES if (RUNTIME / f"{stem}.py").exists()]
    if leftovers:
        raise SystemExit(f"Runtime AI compatibility files remain: {leftovers}")
    if (INFRA_AI / "legacy_provider.py").exists():
        raise SystemExit("legacy_provider.py still exists")
    offenders = _find_retired_references()
    if offenders:
        raise SystemExit("Retired AI references remain:\n" + "\n".join(offenders))


def main() -> int:
    _patch_app()
    _delete_legacy_files()
    _assert_native_state()
    print("runtime_core AI compatibility retirement migration is clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
