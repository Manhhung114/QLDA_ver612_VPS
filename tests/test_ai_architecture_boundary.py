from __future__ import annotations

import ast
from pathlib import Path

from qlda.composition.runtime_features import FEATURES, RuntimeStage


ROOT = Path(__file__).resolve().parents[1]
APP_AI = ROOT / "src/qlda/application/ai"
APP_DATA_HUB = ROOT / "src/qlda/application/contractor_data_hub"
INFRA_AI = ROOT / "src/qlda/infrastructure/ai"
NATIVE_AI = ROOT / "src/qlda/infrastructure/native_ai.py"
PLANNER = ROOT / "src/qlda/autonomy/ai_planner.py"
STREAMLIT_APP = ROOT / "src/qlda/presentation/streamlit/app.py"
LEGACY_PROVIDER = INFRA_AI / "legacy_provider.py"

RETIRED_RUNTIME_AI = {
    "ai_claim_context.py",
    "ai_live_context.py",
    "ai_service.py",
    "ai_streaming.py",
    "ai_vo_context.py",
    "ai_vps_pdf_fullscan.py",
    "ai_vps_pdf_vision.py",
    "boq_ai_fullscan.py",
    "cashflow_ai_context.py",
    "contract_ai_deep_scan.py",
    "contract_ai_large_pdf.py",
    "contractor_ai_context.py",
    "contractor_data_official_ai.py",
    "contractor_data_shared_ai.py",
    "document_pdf_vision_provider.py",
    "gemini_resilience.py",
    "owner_material_ai_context.py",
    "project_cost_ai_context.py",
}


def _runtime_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("qlda.runtime_core"):
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = str(node.module or "")
            if module.startswith("qlda.runtime_core"):
                hits.append(module)
    return hits


def test_application_ai_has_no_runtime_core_dependency() -> None:
    for path in APP_AI.glob("*.py"):
        assert not _runtime_imports(path), f"application/ai must stay native: {path.name}"


def test_contractor_data_hub_application_has_no_runtime_core_dependency() -> None:
    offenders: list[str] = []
    for path in APP_DATA_HUB.glob("*.py"):
        imports = _runtime_imports(path)
        if imports:
            offenders.append(f"{path.name}: {imports}")
    assert not offenders, "Contractor Data Hub must use infrastructure AI ports: " + "; ".join(offenders)


def test_native_ai_adapter_has_no_runtime_core_dependency() -> None:
    assert not _runtime_imports(NATIVE_AI)


def test_infrastructure_ai_has_zero_runtime_core_bridges() -> None:
    offenders: list[str] = []
    for path in INFRA_AI.glob("*.py"):
        imports = _runtime_imports(path)
        if imports:
            offenders.append(f"{path.name}: {imports}")
    assert not offenders, "runtime_core AI bridge is forbidden: " + "; ".join(offenders)
    assert not LEGACY_PROVIDER.exists(), "legacy_provider.py must be deleted after native provider migration"


def test_runtime_core_ai_compatibility_files_are_deleted() -> None:
    runtime_dir = ROOT / "src/qlda/runtime_core"
    leftovers = sorted(name for name in RETIRED_RUNTIME_AI if (runtime_dir / name).exists())
    assert not leftovers, f"Retired runtime_core AI compatibility still exists: {leftovers}"


def test_streamlit_uses_native_ai_facade() -> None:
    source = STREAMLIT_APP.read_text(encoding="utf-8")
    retired_import = "qlda.runtime_core." + "ai_service"
    assert retired_import not in source
    assert "qlda.infrastructure.ai.presentation_facade" in source
    assert "set_ai_workspace_scope" not in source


def test_runtime_ai_stage_is_deleted() -> None:
    assert "ai" not in {stage.value for stage in RuntimeStage}
    assert all(getattr(feature.stage, "value", "") != "ai" for feature in FEATURES)



def test_planner_has_no_regex_json_parser() -> None:
    source = PLANNER.read_text(encoding="utf-8")
    assert "_extract_json" not in source
    assert "re.search" not in source


def test_text_planner_fallback_is_explicit_opt_in() -> None:
    source = PLANNER.read_text(encoding="utf-8")
    assert "QLDA_AI_TEXT_PLANNER_FALLBACK" in source
    assert 'os.environ.get("QLDA_AI_TEXT_PLANNER_FALLBACK", "0")' in source
