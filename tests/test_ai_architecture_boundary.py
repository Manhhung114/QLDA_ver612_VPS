from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_AI = ROOT / "src/qlda/application/ai"
APP_DATA_HUB = ROOT / "src/qlda/application/contractor_data_hub"
INFRA_AI = ROOT / "src/qlda/infrastructure/ai"
NATIVE_AI = ROOT / "src/qlda/infrastructure/native_ai.py"
PLANNER = ROOT / "src/qlda/autonomy/ai_planner.py"
BOOTSTRAP = ROOT / "src/qlda/runtime_core/bootstrap.py"
RUNTIME_FEATURES = ROOT / "src/qlda/composition/runtime_features.py"
RUNTIME_SETTINGS_BRIDGE = ROOT / "src/qlda/runtime_core/runtime_settings_bridge.py"


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


def test_native_ai_adapter_does_not_import_runtime_provider() -> None:
    assert not _runtime_imports(NATIVE_AI)


def test_infrastructure_ai_has_zero_runtime_core_bridges() -> None:
    offenders: list[str] = []
    for path in INFRA_AI.glob("*.py"):
        imports = _runtime_imports(path)
        if imports:
            offenders.append(f"{path.name}: {imports}")
    assert not offenders, "AI infrastructure must be runtime_core-free: " + "; ".join(offenders)


def test_runtime_composition_has_no_ai_stage() -> None:
    source = RUNTIME_FEATURES.read_text(encoding="utf-8")
    assert 'AI = "ai"' not in source
    assert "RuntimeStage.AI" not in source
    assert "qlda.runtime_core.ai_" not in source


def test_bootstrap_never_installs_legacy_ai_stage() -> None:
    source = BOOTSTRAP.read_text(encoding="utf-8")
    assert "install_stage(RuntimeStage.AI)" not in source
    assert "initialize_business_runtime()" in source


def test_runtime_settings_bridge_does_not_patch_ai_service() -> None:
    source = RUNTIME_SETTINGS_BRIDGE.read_text(encoding="utf-8")
    assert "qlda.runtime_core.ai_service" not in source
    assert "AISettings.from_env" not in source
    assert "GeminiSettings.from_env" not in source


def test_planner_has_no_regex_json_parser() -> None:
    source = PLANNER.read_text(encoding="utf-8")
    assert "_extract_json" not in source
    assert "re.search" not in source


def test_text_planner_fallback_is_explicit_opt_in() -> None:
    source = PLANNER.read_text(encoding="utf-8")
    assert "QLDA_AI_TEXT_PLANNER_FALLBACK" in source
    assert 'os.environ.get("QLDA_AI_TEXT_PLANNER_FALLBACK", "0")' in source
