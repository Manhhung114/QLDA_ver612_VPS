from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_AI = ROOT / "src/qlda/application/ai"
INFRA_AI = ROOT / "src/qlda/infrastructure/ai"
NATIVE_AI = ROOT / "src/qlda/infrastructure/native_ai.py"
PLANNER = ROOT / "src/qlda/autonomy/ai_planner.py"


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


def test_native_ai_adapter_does_not_import_runtime_provider() -> None:
    assert not _runtime_imports(NATIVE_AI)


def test_only_explicit_legacy_provider_may_bridge_runtime_ai_service() -> None:
    offenders: list[str] = []
    for path in INFRA_AI.glob("*.py"):
        imports = _runtime_imports(path)
        if imports and path.name != "legacy_provider.py":
            offenders.append(f"{path.name}: {imports}")
    assert not offenders, "Unexpected runtime_core imports in infrastructure/ai: " + "; ".join(offenders)


def test_planner_has_no_regex_json_parser() -> None:
    source = PLANNER.read_text(encoding="utf-8")
    assert "_extract_json" not in source
    assert "re.search" not in source
