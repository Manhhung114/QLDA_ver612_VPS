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
RUNTIME = SRC / "runtime_core"
LEGACY_APP = SRC / "presentation" / "streamlit" / "app.py"
ENTRYPOINT = SRC / "presentation" / "streamlit" / "main.py"

LEGACY_APP_MAX_BYTES = 315_715
ENTRYPOINT_MAX_BYTES = 8_192

ALLOWED_DEBT_FILENAMES = frozenset(
    {
        "attachment_upload_reopen_fix.py",
        "autonomy_overview_patch.py",
        "boq_claim_price_header_guard.py",
        "boq_claim_price_recovery.py",
        "claim_material_period_guard.py",
        "contractor_access_patch.py",
        "default_workspace_admin_guard.py",
        "ipc_claim_number_fix.py",
        "ipc_claim_patch.py",
        "ipc_claim_summary_fix.py",
        "production_progress_overview_patch.py",
    }
)
DEBT_SUFFIXES = ("_fix.py", "_patch.py", "_recovery.py", "_guard.py")

# One pre-existing application dependency still calls legacy AI/settings adapters
# from Contractor Data Hub. It is explicitly tracked in the migration ledger and
# may be removed from this baseline, never expanded with another path.
ALLOWED_RUNTIME_CORE_DEPENDENCIES = frozenset(
    {"src/qlda/application/contractor_data_hub/service.py"}
)


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
    # The allow-list is a migration ledger, not a permanent exception. Stale
    # entries must be deleted as soon as their dependency is removed.
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


def run_checks() -> list[str]:
    errors: list[str] = []
    errors.extend(check_runtime_init_side_effect_free())
    errors.extend(check_layer_boundaries())
    errors.extend(check_streamlit_shell_frozen())
    errors.extend(check_no_new_patch_debt())
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
    print(f" - tracked application->runtime_core dependencies: {len(ALLOWED_RUNTIME_CORE_DEPENDENCIES)}")
    print(" - no new domain/application dependency on runtime_core")
    print(" - runtime_core import is side-effect free")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
