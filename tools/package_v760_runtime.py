from __future__ import annotations

"""One-time V7.6 migration tool.

Materializes the remaining root-level Python runtime into stable package modules
under ``src/qlda/runtime_core`` and rewrites the already-materialized Streamlit
application to consume only packaged code. Root files remain untouched until the
new package passes the V7.6 regression/dependency gates.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "qlda"
RUNTIME = SRC / "runtime_core"
STREAMLIT_SRC = SRC / "presentation" / "streamlit" / "materialized_app.py"
STREAMLIT_OUT = SRC / "presentation" / "streamlit" / "app.py"
MANIFEST = ROOT / "docs" / "architecture" / "V7.6_PACKAGED_RUNTIME_MANIFEST.txt"

BOOTSTRAP_SEEDS = {
    "vps_postgres_resilience",
    "performance_postgres_v1_v622",
    "runtime_settings_bridge_v622",
    "single_session_v622",
    "contractor_workspace_v622",
    "contractor_sidebar_admin_v622",
    "contractor_workspace_reset_v622",
    "contractor_access_control_v622",
    "default_workspace_admin_guard_v622",
    "work_tasks_v1_v622",
    "contract_duration_v622",
    "contract_ai_large_pdf_v622",
    "gemini_resilience_v622",
    "ai_live_context_v622",
    "ai_claim_context_v622",
    "ai_vo_context_v622",
    "boq_cost_components_v622",
    "ipc_claim_fast_v622",
    "ipc_claim_summary_fix_v622",
    "ipc_adaptive_parser_v622",
    "ipc_payment_semantic_v622",
    "multicore_excel_v622",
    "ipc_claim_number_fix_v622",
    "boq_claim_terms_v622",
    "boq_claim_price_recovery_v622",
    "boq_claim_price_header_guard_v622",
    "boq_ai_fullscan_v622",
    "claim_component_fullscan_v622",
    "claim_material_period_guard_v622",
    "project_remaining_components_v622",
    "contractor_ai_context_v622",
}

OVERRIDES = {
    "cloud_db": "project_store",
    "postgres_backend_v622": "project_database",
    "v621_webopt_runtime": "runtime_optimizations",
    "local_vps_runtime_fix_v622": "local_runtime",
    "v615_runtime_patch": "approval_revision",
    "ai_streaming_patch": "ai_streaming",
}


def stable_name(name: str) -> str:
    if name in OVERRIDES:
        return OVERRIDES[name]
    value = re.sub(r"^v\d+_", "", name)
    value = re.sub(r"_v\d+$", "", value)
    return value


def root_modules() -> dict[str, Path]:
    return {path.stem: path for path in ROOT.glob("*.py") if path.is_file()}


def ast_imports(text: str) -> set[str]:
    tree = ast.parse(text)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".", 1)[0])
    return out


def seed_modules(local: dict[str, Path]) -> set[str]:
    seeds = set(BOOTSTRAP_SEEDS)
    seeds.update(name for name in ast_imports(STREAMLIT_SRC.read_text(encoding="utf-8")) if name in local)
    for path in (SRC / "import_engines").glob("*.py"):
        seeds.update(name for name in ast_imports(path.read_text(encoding="utf-8")) if name in local)
    # Native AI compatibility engines must also be moved before qlda.runtime is retired.
    native_ai = (SRC / "infrastructure" / "native_ai.py").read_text(encoding="utf-8")
    for quoted in re.findall(r"[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']", native_ai):
        if quoted in local:
            seeds.add(quoted)
    return {name for name in seeds if name in local}


def closure(local: dict[str, Path], seeds: set[str]) -> set[str]:
    found = set(seeds)
    queue = list(sorted(seeds))
    while queue:
        name = queue.pop()
        text = local[name].read_text(encoding="utf-8")
        for dep in ast_imports(text):
            if dep in local and dep not in found:
                found.add(dep)
                queue.append(dep)
        # Include exact dynamic module-name strings used by importlib/sys.modules.
        for dep in re.findall(r"[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']", text):
            if dep in local and dep not in found:
                found.add(dep)
                queue.append(dep)
    return found


def mapping_for(names: set[str]) -> dict[str, str]:
    mapping = {name: stable_name(name) for name in names}
    reverse: dict[str, str] = {}
    for old, new in mapping.items():
        if new in reverse and reverse[new] != old:
            raise RuntimeError(f"stable module collision: {reverse[new]} and {old} -> {new}")
        reverse[new] = old
    return mapping


def rewrite_imports(text: str, mapping: dict[str, str]) -> str:
    # Longest names first so v622_x does not partially interfere with x_v622.
    for old in sorted(mapping, key=len, reverse=True):
        target = f"qlda.runtime_core.{mapping[old]}"
        escaped = re.escape(old)
        text = re.sub(
            rf"(?m)^(\s*)from\s+{escaped}(\s+import\s+)",
            rf"\1from {target}\2",
            text,
        )

        def replace_import(match: re.Match[str]) -> str:
            indent, alias = match.group(1), match.group(2)
            bind = alias.strip().split()[-1] if alias else old
            return f"{indent}import {target} as {bind}"

        text = re.sub(
            rf"(?m)^(\s*)import\s+{escaped}(\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?\s*$",
            replace_import,
            text,
        )
        # Dynamic import/module-registry references need the qualified package name.
        text = re.sub(
            rf"([\"']){escaped}\1",
            lambda m: f"{m.group(1)}{target}{m.group(1)}",
            text,
        )
    return text


def strip_materialized_bootstrap(text: str) -> str:
    patterns = [
        r"from streamlit_secrets_v622 import apply_streamlit_secrets_to_env\s*\napply_streamlit_secrets_to_env\(\)\s*\n",
        r"from postgres_backend_v622 import install_postgres_backend\s*\ninstall_postgres_backend\(\)\s*\n",
        r"from v621_webopt_runtime import install_runtime\s*\ninstall_runtime\(\)\s*\n",
        r"from v621_webopt_runtime import install_runtime as _install_v621_webopt_runtime\s*\n_install_v621_webopt_runtime\(\)\s*\n",
        r"from local_vps_runtime_fix_v622 import install_local_vps_runtime as _install_local_vps_runtime\s*\n_install_local_vps_runtime\(\)\s*\n",
        r"from ai_streaming_patch import install_ai_streaming\s*\ninstall_ai_streaming\(\)\s*\n",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.M)
    marker = "from __future__ import annotations\n"
    bootstrap = (
        "from __future__ import annotations\n"
        "\nfrom qlda.runtime_core.bootstrap import initialize_runtime\n"
        "initialize_runtime()\n"
    )
    if marker not in text:
        raise RuntimeError("materialized Streamlit future-import marker missing")
    text = text.replace(marker, bootstrap, 1)
    text = text.replace('LEGAL_CACHE_PATH = APP_DIR / "legal_cache.json"', 'LEGAL_CACHE_PATH = DATA_DIR / "legal_cache.json"')
    # Package source is read-only on hardened VPS deployments; mutable runtime data lives on SSD.
    old = 'DATA_DIR = APP_DIR / "data"\nDATA_DIR.mkdir(exist_ok=True)'
    new = (
        'DATA_DIR = Path(os.environ.get("QLDA_RUNTIME_DATA_ROOT", "/opt/qlda/data/runtime"))\n'
        'try:\n'
        '    DATA_DIR.mkdir(parents=True, exist_ok=True)\n'
        'except OSError:\n'
        '    DATA_DIR = Path.cwd() / ".qlda-runtime"\n'
        '    DATA_DIR.mkdir(parents=True, exist_ok=True)'
    )
    if old not in text:
        raise RuntimeError("materialized Streamlit DATA_DIR anchor missing")
    return text.replace(old, new, 1)


def bootstrap_source(mapping: dict[str, str]) -> str:
    def module(old: str) -> str:
        return f"qlda.runtime_core.{mapping[old]}"

    return f'''from __future__ import annotations

"""V7.6 packaged runtime composition.

This preserves proven production semantics while eliminating repository-root
imports, source reconstruction and versioned module names from the production
entrypoints. It is intentionally idempotent because Streamlit reruns modules.
"""

from threading import RLock

_LOCK = RLock()
_READY = False


def initialize_runtime() -> None:
    global _READY
    if _READY:
        return
    with _LOCK:
        if _READY:
            return

        from {module("streamlit_secrets_v622")} import apply_streamlit_secrets_to_env
        apply_streamlit_secrets_to_env()

        import {module("postgres_backend_v622")} as project_database
        from {module("vps_postgres_resilience")} import install_vps_postgres_resilience
        from {module("performance_postgres_v1_v622")} import install_performance_postgres_v1
        install_vps_postgres_resilience(project_database)
        project_database.install_postgres_backend()
        install_performance_postgres_v1(project_database)

        from {module("runtime_settings_bridge_v622")} import install_runtime_settings_bridge
        from {module("single_session_v622")} import install_single_session
        install_runtime_settings_bridge()
        install_single_session()

        from {module("contractor_workspace_v622")} import install_contractor_workspace
        from {module("contractor_sidebar_admin_v622")} import install_contractor_sidebar_admin
        from {module("contractor_workspace_reset_v622")} import install_contractor_workspace_reset
        from {module("contractor_access_control_v622")} import install_contractor_access_control, capture_single_contractor_ai_context, install_ai_access_guard
        from {module("default_workspace_admin_guard_v622")} import install_default_workspace_admin_guard
        install_contractor_workspace()
        install_contractor_sidebar_admin()
        install_contractor_workspace_reset()
        install_contractor_access_control()
        install_default_workspace_admin_guard()

        from {module("work_tasks_v1_v622")} import install_work_tasks_v1
        from {module("contract_duration_v622")} import install_contract_duration_v622
        from {module("contract_ai_large_pdf_v622")} import install_contract_ai_large_pdf_v622
        install_work_tasks_v1()
        install_contract_duration_v622()
        install_contract_ai_large_pdf_v622()

        from {module("gemini_resilience_v622")} import install_gemini_resilience
        from {module("ai_live_context_v622")} import install_ai_live_context
        from {module("ai_claim_context_v622")} import install_ai_claim_context
        from {module("ai_vo_context_v622")} import install_ai_vo_context
        install_gemini_resilience()
        install_ai_live_context()
        install_ai_claim_context()
        install_ai_vo_context()

        from {module("boq_cost_components_v622")} import install_boq_cost_components
        from {module("ipc_claim_fast_v622")} import install_ipc_claim_fast_path
        from {module("ipc_claim_summary_fix_v622")} import install_ipc_claim_summary_fix
        from {module("ipc_adaptive_parser_v622")} import install_ipc_adaptive_parser
        from {module("ipc_payment_semantic_v622")} import install_ipc_payment_semantic
        from {module("multicore_excel_v622")} import install_multicore_excel
        from {module("ipc_claim_number_fix_v622")} import install_ipc_claim_number_fix
        from {module("boq_claim_terms_v622")} import install_boq_claim_terms
        from {module("boq_claim_price_recovery_v622")} import install_boq_claim_price_recovery
        from {module("boq_claim_price_header_guard_v622")} import install_boq_claim_price_header_guard
        install_boq_cost_components()
        install_ipc_claim_fast_path()
        install_ipc_claim_summary_fix()
        install_ipc_adaptive_parser()
        install_ipc_payment_semantic()
        install_multicore_excel()
        install_ipc_claim_number_fix()
        install_boq_claim_terms()
        install_boq_claim_price_recovery()
        install_boq_claim_price_header_guard()

        from {module("boq_ai_fullscan_v622")} import install_boq_ai_fullscan
        from {module("claim_component_fullscan_v622")} import install_claim_component_fullscan
        from {module("claim_material_period_guard_v622")} import install_claim_material_period_guard
        from {module("project_remaining_components_v622")} import install_project_remaining_components
        from {module("contractor_ai_context_v622")} import install_contractor_ai_context
        install_boq_ai_fullscan()
        install_claim_component_fullscan()
        install_claim_material_period_guard()
        install_project_remaining_components()
        capture_single_contractor_ai_context()
        install_contractor_ai_context()
        install_ai_access_guard()

        _READY = True
'''


def assert_packaged_only(path: Path, local: dict[str, Path]) -> None:
    text = path.read_text(encoding="utf-8")
    direct = ast_imports(text)
    leftovers = sorted(name for name in direct if name in local)
    if leftovers:
        raise RuntimeError(f"root imports remain in {path.relative_to(ROOT)}: {leftovers}")


def main() -> None:
    local = root_modules()
    names = closure(local, seed_modules(local))
    mapping = mapping_for(names)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "__init__.py").write_text(
        '"""QLDA V7.6 packaged production runtime core."""\n', encoding="utf-8"
    )

    for old in sorted(names):
        text = local[old].read_text(encoding="utf-8")
        rewritten = rewrite_imports(text, mapping)
        target = RUNTIME / f"{mapping[old]}.py"
        target.write_text(rewritten, encoding="utf-8")
        compile(rewritten, str(target), "exec")
        assert_packaged_only(target, local)

    (RUNTIME / "bootstrap.py").write_text(bootstrap_source(mapping), encoding="utf-8")
    compile((RUNTIME / "bootstrap.py").read_text(encoding="utf-8"), str(RUNTIME / "bootstrap.py"), "exec")

    app = strip_materialized_bootstrap(STREAMLIT_SRC.read_text(encoding="utf-8"))
    app = rewrite_imports(app, mapping)
    STREAMLIT_OUT.write_text(app, encoding="utf-8")
    compile(app, str(STREAMLIT_OUT), "exec")
    assert_packaged_only(STREAMLIT_OUT, local)

    # Rewrite packaged import engines in place; root originals remain available for
    # rollback until the final dependency gate retires them.
    engine_paths = sorted((SRC / "import_engines").glob("*.py"))
    for path in engine_paths:
        text = rewrite_imports(path.read_text(encoding="utf-8"), mapping)
        path.write_text(text, encoding="utf-8")
        compile(text, str(path), "exec")
        assert_packaged_only(path, local)

    lines = [
        "V7.6 packaged runtime manifest",
        "==============================",
        f"module_count={len(mapping)}",
        "",
    ]
    lines.extend(f"{old} -> qlda.runtime_core.{mapping[old]}" for old in sorted(mapping))
    MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"packaged {len(mapping)} root modules into {RUNTIME.relative_to(ROOT)}")
    print(f"wrote native Streamlit candidate: {STREAMLIT_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
