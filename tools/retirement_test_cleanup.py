from __future__ import annotations

"""Normalize the small set of tests that still validate V6 source patching.

The business assertions in those files are preserved. Only tests whose sole
purpose was to rebuild/patch the old generated Streamlit source are removed.
This runs inside the guarded V7 retirement workflow before the full suite.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def _strip_method(text: str, name: str) -> str:
    pattern = re.compile(rf"(?m)^    def {re.escape(name)}\(")
    match = pattern.search(text)
    if not match:
        return text
    start = match.start()
    next_method = re.search(r"(?m)^    def [A-Za-z_]", text[match.end():])
    main_guard = re.search(r"(?m)^if __name__", text[match.end():])
    candidates = []
    if next_method:
        candidates.append(match.end() + next_method.start())
    if main_guard:
        candidates.append(match.end() + main_guard.start())
    end = min(candidates) if candidates else len(text)
    return text[:start].rstrip() + "\n\n" + text[end:]


def _strip_top_level_function(text: str, name: str) -> str:
    match = re.search(rf"(?m)^def {re.escape(name)}\(", text)
    if not match:
        return text
    start = match.start()
    following = text[match.end():]
    next_top = re.search(r"(?m)^(?:def |class |if __name__)", following)
    end = match.end() + next_top.start() if next_top else len(text)
    return text[:start].rstrip() + "\n\n" + text[end:]


def _drop_import_lines(text: str, needles: tuple[str, ...]) -> str:
    """Drop matching single- or multi-line import statements safely."""
    source = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(source):
        line = source[index]
        stripped = line.lstrip()
        is_import = stripped.startswith("from ") or stripped.startswith("import ")
        if is_import and any(needle in line for needle in needles):
            # Parenthesized imports leave continuation lines behind if only the
            # first line is removed. Consume through the matching close paren.
            balance = line.count("(") - line.count(")")
            index += 1
            while balance > 0 and index < len(source):
                balance += source[index].count("(") - source[index].count(")")
                index += 1
            continue
        output.append(line)
        index += 1
    return "\n".join(output).rstrip() + "\n"


def _edit(name: str, *, strip_methods: tuple[str, ...] = (), drop_import_needles: tuple[str, ...] = (), strip_functions: tuple[str, ...] = (), replacements: tuple[tuple[str, str], ...] = ()) -> None:
    path = TESTS / name
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    for old, new in replacements:
        text = text.replace(old, new)
    for method in strip_methods:
        text = _strip_method(text, method)
    for func in strip_functions:
        text = _strip_top_level_function(text, func)
    text = _drop_import_lines(text, drop_import_needles)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    # Mixed business + old generated-source tests: retain the business coverage.
    _edit(
        "test_contract_management.py",
        strip_methods=("test_navigation_source_hides_contract_sheet_by_role",),
    )
    _edit(
        "test_contractor_access_control.py",
        strip_methods=("test_generated_ui_enforces_roles_and_hides_other_contractors",),
        drop_import_needles=("contractor_workspace_patch", "contractor_access_patch"),
    )
    _edit(
        "test_contractor_workspace.py",
        strip_methods=("test_generated_ui_keeps_operations_on_workspace_but_ai_on_master",),
        drop_import_needles=("contractor_workspace_patch", "contractor_sidebar_patch"),
    )
    _edit(
        "test_default_workspace_admin_guard.py",
        strip_methods=("test_generated_ui_hides_default_and_scopes_non_admin_ai",),
        drop_import_needles=("contractor_workspace_patch", "contractor_access_patch"),
    )
    _edit(
        "test_legal_standards_backfill.py",
        strip_methods=("test_legal_ui_installs_standard_backfill",),
        drop_import_needles=("legal_qlda_patch",),
    )
    _edit(
        "test_original_import_storage.py",
        strip_methods=("test_production_patch_archives_all_business_import_sources",),
        strip_functions=("_raw_vps_source",),
        drop_import_needles=("build_v621_webopt", "_patch"),
    )
    _edit(
        "test_vps_status.py",
        strip_methods=("test_v7_tools_wiring_is_admin_only",),
    )

    # Cross-test helpers follow the migrated filenames.
    _edit(
        "test_ipc_background.py",
        replacements=(("tests.test_ipc_claim_v622", "tests.test_ipc_claim"),),
    )
    _edit(
        "test_vo_background.py",
        replacements=(("tests.test_vo_claim_v622", "tests.test_vo_claim"),),
    )

    # These tests existed only to validate the V6 source-builder/patch pipeline.
    # The V7 repository contract + direct packaged app compilation supersede them.
    for obsolete in (
        "test_schedule_management.py",
        "test_ui_v7_compact.py",
        "test_v411_drive_only.py",
        "test_performance_v1.py",
    ):
        path = TESTS / obsolete
        if path.exists():
            path.unlink()

    print("Normalized remaining patch-era tests for V7 packaged runtime")


if __name__ == "__main__":
    main()
