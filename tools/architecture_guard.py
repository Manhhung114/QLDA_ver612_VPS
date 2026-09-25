from __future__ import annotations

"""Fail CI when architectural debt grows.

This guard is deliberately migration-safe: existing compatibility modules are
allow-listed, but new ``*_fix``/``*_patch``/``*_recovery``/``*_guard`` files are
rejected.  The large materialized Streamlit shell is frozen at its current size;
new UI work must live in modular presentation files.
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

# Compatibility debt present at the point the guard was introduced. These files
# may be migrated/deleted, but the set must never grow.
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
        # A future harmless constant is okay; imports/calls/assignments are not.
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
    for layer in (SRC / "domain", SRC / "application"):
        for path in _python_files(layer):
            source = path.read_text(encoding="utf-8")
            if "qlda.runtime_core" in source:
                errors.append(f"{path.relative_to(ROOT)} imports compatibility runtime_core")
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
    for path in _python_files(RUNTIME):
        if path.name.endswith(DEBT_SUFFIXES) and path.name not in ALLOWED_DEBT_FILENAMES:
            errors.append(
                f"New runtime compatibility debt is prohibited: {path.relative_to(ROOT)}. "
                "Implement the behavior in domain/application/presentation instead."
            )
    return errors


def check_no_dynamic_source_execution() -> list[str]:
    errors: list[str] = []
    for path in _python_files(SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"exec", "eval"}:
                errors.append(f"Dynamic source execution is prohibited: {path.relative_to(ROOT)}:{node.lineno}")
    return errors


def run_checks() -> list[str]:
    errors: list[str] = []
    errors.extend(check_runtime_init_side_effect_free())
    errors.extend(check_layer_boundaries())
    errors.extend(check_streamlit_shell_frozen())
    errors.extend(check_no_new_patch_debt())
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
    print(f" - allowed legacy debt files: {len(ALLOWED_DEBT_FILENAMES)}")
    print(" - domain/application do not depend on runtime_core")
    print(" - runtime_core import is side-effect free")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
