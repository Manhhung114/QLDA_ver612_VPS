from __future__ import annotations

"""Fail CI when architectural debt grows.

This guard is deliberately migration-safe: existing compatibility debt is
allow-listed as a shrinking baseline, while any *new* debt is rejected. The
materialized Streamlit shell is frozen at its current size; new UI work must live
in modular presentation files.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "qlda"
TESTS = ROOT / "tests"
RUNTIME = SRC / "runtime_core"
LEGACY_APP = SRC / "presentation" / "streamlit" / "app.py"
ENTRYPOINT = SRC / "presentation" / "streamlit" / "main.py"

LEGACY_APP_MAX_BYTES = 315_715
ENTRYPOINT_MAX_BYTES = 8_192

ALLOWED_DEBT_FILENAMES = frozenset(
    {
        "boq_claim_price_header_guard.py",
        "boq_claim_price_recovery.py",
        "claim_material_period_guard.py",
        "default_workspace_admin_guard.py",
        "ipc_claim_number_fix.py",
        "ipc_claim_patch.py",
        "ipc_claim_summary_fix.py",
        "production_progress_overview_patch.py",
    }
)
DEBT_SUFFIXES = ("_fix.py", "_patch.py", "_recovery.py", "_guard.py")

ALLOWED_RUNTIME_UI_COMPAT_FEATURES = frozenset(
    {
        "upload-ui-policy",
        "owner-supplied-materials",
        "project-cost-management",
        "finance-consistency-ui",
        "production-progress-overview",
        "production-progress-shared-ai",
        "production-progress-source-exact",
    }
)

ALLOWED_RUNTIME_CORE_DEPENDENCIES = frozenset(
    {"src/qlda/application/contractor_data_hub/service.py"}
)

# Compatibility modules that have completed strangler migration. They must stay
# deleted and no source/test import is allowed to reintroduce them.
RETIRED_MODULES = {
    "qlda.runtime_core.autonomy_runtime": RUNTIME / "autonomy_runtime.py",
    "qlda.runtime_core.autonomy_overview_patch": RUNTIME / "autonomy_overview_patch.py",
    "qlda.runtime_core.advanced_automation_ui": RUNTIME / "advanced_automation_ui.py",
    "qlda.runtime_core.contractor_access_patch": RUNTIME / "contractor_access_patch.py",
    "qlda.runtime_core.finance_title_policy": RUNTIME / "finance_title_policy.py",
    "qlda.runtime_core.document_selection_autopen": RUNTIME / "document_selection_autopen.py",
    "qlda.runtime_core.contractor_data_admin_visibility": RUNTIME / "contractor_data_admin_visibility.py",
    "qlda.runtime_core.document_management_uniform_interaction": RUNTIME / "document_management_uniform_interaction.py",
    "qlda.runtime_core.document_management_vps_ui": RUNTIME / "document_management_vps_ui.py",
    "qlda.runtime_core.attachment_upload_reopen_fix": RUNTIME / "attachment_upload_reopen_fix.py",
}

# One legacy IPC compatibility module still recompiles a materialized function.
# Track the exact number of calls so this debt can shrink but can never spread or
# silently increase. Delete this entry when ipc_claim_patch.py is fully retired.
ALLOWED_DYNAMIC_EXECUTION = {
    "src/qlda/runtime_core/ipc_claim_patch.py": {"exec": 1, "eval": 0},
}


def _python_files(path: Path):
    return sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts)


def check_runtime_init_side_effect_free() -> list[str]:
    path = RUNTIME / "__init__.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    errors: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        errors.append(f"{path.relative_to(ROOT)} must remain import-time side-effect free: {type(node).__name__}")
    forbidden = ("install_", "_original_initialize", "initialize_runtime =", "initialize_ai_runtime =")
    for token in forbidden:
        if token in source:
            errors.append(f"{path.relative_to(ROOT)} contains forbidden runtime wiring token: {token}")
    return errors


def check_layer_boundaries() -> list[str]:
    errors: list[str] = []
    seen_legacy_dependencies: set[str] = set()
    for layer in (SRC / "domain", SRC / "application"):
        for path in _python_files(layer):
            source = path.read_text(encoding="utf-8")
            if "qlda.runtime_core" not in source:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative in ALLOWED_RUNTIME_CORE_DEPENDENCIES:
                seen_legacy_dependencies.add(relative)
                continue
            errors.append(f"{relative} imports compatibility runtime_core")
    stale = ALLOWED_RUNTIME_CORE_DEPENDENCIES - seen_legacy_dependencies
    for relative in sorted(stale):
        errors.append(f"Remove stale runtime_core dependency baseline entry: {relative}")
    return errors


def check_streamlit_shell_frozen() -> list[str]:
    errors: list[str] = []
    if not LEGACY_APP.is_file():
        return [f"Missing frozen legacy Streamlit shell: {LEGACY_APP.relative_to(ROOT)}"]
    if LEGACY_APP.stat().st_size > LEGACY_APP_MAX_BYTES:
        errors.append(
            f"{LEGACY_APP.relative_to(ROOT)} grew to {LEGACY_APP.stat().st_size} bytes; "
            f"limit is {LEGACY_APP_MAX_BYTES}. Extract new work into presentation modules."
        )
    if not ENTRYPOINT.is_file():
        errors.append(f"Missing modular Streamlit entrypoint: {ENTRYPOINT.relative_to(ROOT)}")
    elif ENTRYPOINT.stat().st_size > ENTRYPOINT_MAX_BYTES:
        errors.append(
            f"{ENTRYPOINT.relative_to(ROOT)} is {ENTRYPOINT.stat().st_size} bytes; "
            f"entrypoint must stay <= {ENTRYPOINT_MAX_BYTES} bytes."
        )
    return errors


def check_no_new_patch_debt() -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for path in _python_files(RUNTIME):
        if not path.name.endswith(DEBT_SUFFIXES):
            continue
        if path.name in ALLOWED_DEBT_FILENAMES:
            seen.add(path.name)
            continue
        errors.append(
            f"New runtime compatibility debt is prohibited: {path.relative_to(ROOT)}. "
            "Implement the behavior in domain/application/presentation instead."
        )
    stale = ALLOWED_DEBT_FILENAMES - seen
    for name in sorted(stale):
        errors.append(f"Remove stale compatibility-debt baseline entry: {name}")
    return errors


def check_runtime_ui_feature_budget() -> list[str]:
    """Prevent any new Streamlit/UI feature from being owned by runtime_core."""
    from qlda.composition.runtime_features import FEATURES, RuntimeStage

    errors: list[str] = []
    seen: set[str] = set()
    for feature in FEATURES:
        if feature.stage is not RuntimeStage.UI:
            continue
        if not str(feature.module).startswith("qlda.runtime_core"):
            continue
        if feature.name in ALLOWED_RUNTIME_UI_COMPAT_FEATURES:
            seen.add(feature.name)
            continue
        errors.append(
            f"New UI compatibility feature {feature.name!r} points to {feature.module}; "
            "new UI must live under qlda.presentation."
        )
    stale = ALLOWED_RUNTIME_UI_COMPAT_FEATURES - seen
    for name in sorted(stale):
        errors.append(f"Remove stale runtime UI compatibility baseline entry: {name}")
    return errors


def check_retired_modules_stay_retired() -> list[str]:
    errors: list[str] = []
    for module, path in RETIRED_MODULES.items():
        if path.exists():
            errors.append(f"Retired compatibility module was restored: {path.relative_to(ROOT)} ({module})")

    scan_roots = [SRC]
    if TESTS.exists():
        scan_roots.append(TESTS)
    for root in scan_roots:
        for path in _python_files(root):
            relative = path.relative_to(ROOT).as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in RETIRED_MODULES:
                    errors.append(f"{relative} imports retired module {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in RETIRED_MODULES:
                            errors.append(f"{relative} imports retired module {alias.name}")
    return errors


def check_no_dynamic_source_execution() -> list[str]:
    errors: list[str] = []
    seen: dict[str, dict[str, int]] = {}
    for path in _python_files(SRC):
        relative = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            errors.append(f"Cannot parse {relative}: {exc}")
            continue
        counts = {"exec": 0, "eval": 0}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id in counts:
                counts[node.func.id] += 1
        if not any(counts.values()):
            continue
        allowed = ALLOWED_DYNAMIC_EXECUTION.get(relative)
        if allowed is None:
            errors.append(f"Dynamic source execution is prohibited: {relative} has {counts}")
            continue
        seen[relative] = counts
        for name in counts:
            if counts[name] > int(allowed.get(name, 0)):
                errors.append(
                    f"Dynamic source execution debt increased: {relative} {name}={counts[name]} > baseline={allowed.get(name, 0)}"
                )
            elif counts[name] < int(allowed.get(name, 0)):
                errors.append(
                    f"Remove stale dynamic-execution baseline: {relative} {name}={counts[name]} < baseline={allowed.get(name, 0)}"
                )
    stale = set(ALLOWED_DYNAMIC_EXECUTION) - set(seen)
    for relative in sorted(stale):
        errors.append(f"Remove stale dynamic-execution baseline entry: {relative}")
    return errors


def run_checks() -> list[str]:
    errors: list[str] = []
    errors.extend(check_runtime_init_side_effect_free())
    errors.extend(check_layer_boundaries())
    errors.extend(check_streamlit_shell_frozen())
    errors.extend(check_no_new_patch_debt())
    errors.extend(check_runtime_ui_feature_budget())
    errors.extend(check_retired_modules_stay_retired())
    errors.extend(check_no_dynamic_source_execution())
    return errors


def main() -> int:
    errors = run_checks()
    if errors:
        print("ARCHITECTURE GUARD FAILED")
        for error in errors:
            print(f" - {error}")
        return 1
    print("ARCHITECTURE GUARD OK")
    print(f" - frozen Streamlit shell <= {LEGACY_APP_MAX_BYTES} bytes")
    print(f" - tracked compatibility-debt files: {len(ALLOWED_DEBT_FILENAMES)}")
    print(f" - tracked runtime UI compatibility features: {len(ALLOWED_RUNTIME_UI_COMPAT_FEATURES)}")
    print(f" - tracked application->runtime_core dependencies: {len(ALLOWED_RUNTIME_CORE_DEPENDENCIES)}")
    print(f" - retired compatibility modules locked out: {len(RETIRED_MODULES)}")
    print(f" - tracked legacy dynamic-execution modules: {len(ALLOWED_DYNAMIC_EXECUTION)}")
    print(" - no new domain/application dependency on runtime_core")
    print(" - no new runtime_core-owned UI feature")
    print(" - retired compatibility imports cannot return")
    print(" - dynamic exec/eval cannot spread or increase")
    print(" - runtime_core import is side-effect free")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
