from __future__ import annotations

"""One-shot migration that removes runtime_core AI compatibility without
removing deterministic BOQ/IPC/finance behavior.

The migration is intentionally executable in CI before commit.  It rewires known
provider callers to the native boundary, extracts pure business helpers from old
AI-named modules, removes only prompt/context monkey patches from mixed business
modules, then refuses to commit while any retired reference remains.
"""

import ast
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/qlda"
APP = SRC / "presentation/streamlit/app.py"
RUNTIME = SRC / "runtime_core"
INFRA_AI = SRC / "infrastructure/ai"
TESTS = ROOT / "tests"

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

LEGACY_PROMPT_TESTS = (
    "test_ai_claim_context.py",
    "test_ai_live_context.py",
    "test_contractor_ai_context.py",
    "test_contractor_data_shared_ai.py",
    "test_gemini_resilience.py",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _replace(path: Path, old: str, new: str, *, required: bool = False) -> bool:
    text = _read(path)
    if old not in text:
        if required:
            raise SystemExit(f"Cannot find expected migration marker in {path.relative_to(ROOT)}: {old[:100]!r}")
        return False
    _write(path, text.replace(old, new))
    return True


def _remove_top_level_functions(path: Path, names: set[str]) -> None:
    if not path.exists() or not names:
        return
    source = _read(path)
    tree = ast.parse(source, filename=str(path))
    ranges: list[tuple[int, int]] = []
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            ranges.append((node.lineno, node.end_lineno or node.lineno))
            found.add(node.name)
    lines = source.splitlines(keepends=True)
    for start, end in sorted(ranges, reverse=True):
        del lines[start - 1:end]
    if ranges:
        _write(path, "".join(lines))


def _replace_top_level_function(path: Path, name: str, replacement: str) -> None:
    source = _read(path)
    tree = ast.parse(source, filename=str(path))
    node = next(
        (n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name),
        None,
    )
    if node is None:
        raise SystemExit(f"Function {name} not found in {path.relative_to(ROOT)}")
    lines = source.splitlines(keepends=True)
    new_lines = (replacement.rstrip() + "\n\n").splitlines(keepends=True)
    lines[node.lineno - 1:(node.end_lineno or node.lineno)] = new_lines
    _write(path, "".join(lines))


def _remove_class_method(path: Path, class_name: str, method_name: str) -> None:
    if not path.exists():
        return
    source = _read(path)
    tree = ast.parse(source, filename=str(path))
    ranges: list[tuple[int, int]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                    ranges.append((child.lineno, child.end_lineno or child.lineno))
    lines = source.splitlines(keepends=True)
    for start, end in sorted(ranges, reverse=True):
        del lines[start - 1:end]
    if ranges:
        _write(path, "".join(lines))


def _remove_block(path: Path, start_marker: str, end_marker: str) -> None:
    text = _read(path)
    start = text.find(start_marker)
    if start < 0:
        return
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"Cannot find block end in {path.relative_to(ROOT)}")
    end += len(end_marker)
    _write(path, text[:start] + text[end:])


def _patch_app() -> None:
    source = _read(APP)
    old_import = "from qlda.runtime_core.ai_service import ("
    new_import = "from qlda.infrastructure.ai.presentation_facade import ("
    if old_import in source:
        source = source.replace(old_import, new_import, 1)
    elif new_import not in source:
        raise SystemExit("Cannot locate the Streamlit AI assistant import")

    source = source.replace("    set_ai_workspace_scope as _v622_set_ai_workspace_scope,\n", "")
    scope_block = re.compile(
        r"\n    # CONTRACTOR accounts set a ContextVar guard so ProjectContextBuilder,\n"
        r"    # attachment catalog and every AI helper remain inside the authorized workspace\.\n"
        r"    _v622_set_ai_workspace_scope\(\n"
        r"        int\(pid\) if _user_approval_role\(_cloud_identity\(\)\) == \"CONTRACTOR\" else None\n"
        r"    \)\n"
    )
    source = scope_block.sub("\n", source, count=1)
    if "_v622_set_ai_workspace_scope" in source:
        raise SystemExit("Legacy AI workspace ContextVar remains in Streamlit app")
    _write(APP, source)


def _patch_native_adapter() -> None:
    path = SRC / "infrastructure/native_ai.py"
    text = _read(path)
    if "def answer_grounded(" not in text:
        marker = "    def ask(\n"
        method = '''    def answer_grounded(
        self,
        workspace_project_id: int,
        prompt: str,
        *,
        provider: str = "openai",
        event_type: str = "AI_GROUNDED_ANSWER",
        source_refs: Sequence[str] | None = None,
        use_web: bool | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Answer an already-authorized, already-grounded prompt with audit.

        Use this for application services such as Contractor Data Hub that build
        their own deterministic evidence bundle and must not run a second RAG pass.
        """
        tenant = self._tenant(int(workspace_project_id), int(workspace_project_id))
        started = time.perf_counter()
        try:
            result = self._complete(provider, str(prompt or ""), use_web=use_web)
            self._event(
                tenant, event_type, provider, started,
                input_text=prompt, source_refs=source_refs,
                context=dict(context or {}),
            )
            return result
        except Exception as exc:
            self._event(
                tenant, event_type, provider, started,
                input_text=prompt, source_refs=source_refs,
                success=False, error=exc, context=dict(context or {}),
            )
            raise

'''
        if marker not in text:
            raise SystemExit("Cannot inject NativeAIAdapter.answer_grounded")
        text = text.replace(marker, method + marker, 1)
    _write(path, text)


def _patch_provider_callers() -> None:
    # Site Vision / automation and contract management keep their stable facade
    # names but now resolve from native infrastructure instead of runtime_core.
    _replace(
        SRC / "autonomy/advanced_automation.py",
        "from qlda.runtime_core.ai_service import AISettings, GeminiProjectAssistant, GeminiSettings, OpenAIProjectAssistant",
        "from qlda.infrastructure.ai.presentation_facade import AISettings, GeminiProjectAssistant, GeminiSettings, OpenAIProjectAssistant",
    )
    _replace(
        RUNTIME / "contract_management.py",
        "from qlda.runtime_core.ai_service import (",
        "from qlda.infrastructure.ai.presentation_facade import (",
    )
    _replace(
        SRC / "presentation/streamlit/production_progress_source_exact.py",
        "from qlda.runtime_core.contractor_data_official_ai import extract_official_summaries",
        "from qlda.application.contractor_data_hub.official_summary import extract_official_summaries",
    )

    # Export the currently selected UI provider for other native entry points.
    facade = INFRA_AI / "presentation_facade.py"
    ftext = _read(facade)
    line = '        os.environ["QLDA_AI_PROVIDER"] = self.provider\n'
    if line not in ftext:
        anchor = "    def _apply_process_settings(self) -> None:\n"
        if anchor not in ftext:
            raise SystemExit("Cannot update native presentation provider scope")
        ftext = ftext.replace(anchor, anchor + line, 1)
        _write(facade, ftext)

    # Contractor Data Hub supplies its own tenant-filtered evidence. Route the
    # prompt through NativeAIAdapter.answer_grounded so telemetry/audit is kept.
    hub = SRC / "application/contractor_data_hub/service.py"
    htext = _read(hub)
    old = '''        from qlda.infrastructure.ai.legacy_provider import LegacyAIProvider

        return LegacyAIProvider.data_hub_answer(workspace_scope, prompt)'''
    new = '''        import os
        from qlda.infrastructure.native_ai import NativeAIAdapter

        provider = str(os.environ.get("QLDA_AI_PROVIDER") or "openai").strip().lower()
        source_refs = re.findall(r"\\[DATA:([^\\]]+)\\]", prompt)
        return NativeAIAdapter().answer_grounded(
            workspace_scope,
            prompt,
            provider=provider,
            event_type="AI_CONTRACTOR_DATA_HUB",
            source_refs=source_refs,
            use_web=False,
            context={"master_project_id": int(master_project_id), "workspace_count": len(allowed)},
        )'''
    if old not in htext:
        if "qlda.infrastructure.ai.legacy_provider" in htext:
            raise SystemExit("Unexpected Contractor Data Hub legacy provider block")
    else:
        _write(hub, htext.replace(old, new, 1))


def _patch_mixed_business_modules() -> None:
    # BOQ terminology: retain parser/UI semantics, remove prompt monkey patches.
    terms = RUNTIME / "boq_claim_terms.py"
    _replace_top_level_function(
        terms,
        "_install_boq_terms",
        '''def _install_boq_terms() -> None:
    import qlda.runtime_core.boq_multisheet as boq
    if getattr(boq, "_qlda_boq_claim_terms_installed", False):
        return
    original_parse = boq.parse_boq_workbook

    def parse_with_quantity_terms(data: bytes, filename: str = "BOQ.xlsx"):
        result = original_parse(data, filename)
        result["warnings"] = [_boq_text(x) for x in list(result.get("warnings") or [])]
        return result

    boq.parse_boq_workbook = parse_with_quantity_terms
    boq._qlda_boq_claim_terms_installed = True
    boq._qlda_boq_claim_terms_marker = PATCH_MARKER''',
    )
    _remove_top_level_functions(terms, {"_install_ai_claim_terms"})
    _replace(terms, "        _install_ai_claim_terms()\n", "")

    # BOQ components: keep schema/parser/database extension only.
    components = RUNTIME / "boq_cost_components.py"
    _remove_top_level_functions(components, {"_fmt_nullable_money", "_install_ai_patch"})
    _replace(components, "        _install_ai_patch()\n", "")
    _replace(
        components,
        '    """Install non-destructive BOQ material/labor split for database, parser and AI."""',
        '    """Install non-destructive BOQ material/labor split for database and parser."""',
    )

    # Price recovery remains deterministic; remove prompt append hooks only.
    recovery = RUNTIME / "boq_claim_price_recovery.py"
    _remove_top_level_functions(recovery, {"_install_ai_recovery"})
    _replace(recovery, "        _install_ai_recovery()\n", "")

    # Claim fullscan stays as a reusable calculation service. Remove its prompt
    # installer and make intent parsing self-contained instead of importing AI.
    fullscan = RUNTIME / "claim_component_fullscan.py"
    ftext = _read(fullscan)
    if "import unicodedata" not in ftext:
        ftext = ftext.replace("import re\n", "import re\nimport unicodedata\n", 1)
        _write(fullscan, ftext)
    _replace_top_level_function(
        fullscan,
        "_wanted_claim_no",
        '''def _wanted_claim_no(question: str) -> str:
    text = _norm(question)
    match = re.search(r"(?:claim|ipc)\\s*#?\\s*0*([0-9]+)", text)
    return str(int(match.group(1))) if match else ""''',
    )
    _replace_top_level_function(
        fullscan,
        "_component_intent",
        '''def _component_intent(question: str) -> bool:
    text = _norm(question)
    return bool(
        any(x in text for x in ("claim", "ipc", "nghiem thu"))
        and any(x in text for x in (
            "nhan cong", "vat tu", "vat lieu", "chi phi", "gia tri", "don gia", "luy ke", "khoi luong"
        ))
    )''',
    )
    text = _read(fullscan)
    if "def _norm(" not in text:
        insert = '''\n\ndef _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\\s+", " ", text).strip()
'''
        marker = "\n\ndef _wanted_claim_no"
        text = text.replace(marker, insert + marker, 1)
        _write(fullscan, text)
    _remove_top_level_functions(fullscan, {"install_claim_component_fullscan"})

    # Material period guard remains a business decorator around claim fullscan.
    guard = RUNTIME / "claim_material_period_guard.py"
    _replace_top_level_function(
        guard,
        "install_claim_material_period_guard",
        '''def install_claim_material_period_guard() -> None:
    """Augment deterministic Claim scans with validated period identities."""
    import qlda.runtime_core.claim_component_fullscan as fullscan
    if getattr(fullscan, "_qlda_claim_material_period_guard_installed", False):
        return
    original_fullscan = fullscan.fullscan_claim_components

    def guarded_fullscan(connection, project_id: int, question: str = "", *, persist: bool = False):
        rows = original_fullscan(connection, int(project_id), str(question or ""), persist=persist)
        return [_augment_one(connection, dict(row or {})) for row in rows]

    fullscan.fullscan_claim_components = guarded_fullscan
    fullscan._qlda_claim_material_period_guard_installed = True
    fullscan._qlda_claim_material_period_guard_marker = PATCH_MARKER''',
    )

    # Project remaining is a deterministic helper; no runtime installation needed.
    remaining = RUNTIME / "project_remaining_components.py"
    _replace(
        remaining,
        "from qlda.runtime_core.boq_ai_fullscan import fullscan_boq_component_totals",
        "from qlda.runtime_core.boq_component_fullscan import fullscan_boq_component_totals",
    )
    _remove_top_level_functions(remaining, {"install_project_remaining_components"})

    # Consolidated finance stays business-owned; drop only the old AI VO hook.
    finance = RUNTIME / "finance_consistency.py"
    _remove_block(
        finance,
        "    try:\n        import qlda.runtime_core.ai_vo_context as ai_vo\n",
        "    except Exception:\n        pass\n",
    )


def _rename_boq_fullscan() -> None:
    old = RUNTIME / "boq_ai_fullscan.py"
    new = RUNTIME / "boq_component_fullscan.py"
    if old.exists():
        text = _read(old)
        text = text.replace('PATCH_MARKER = "V6.22 BOQ AI FULLSCAN V1"', 'PATCH_MARKER = "V6.22 BOQ COMPONENT FULLSCAN V2"')
        _write(new, text)
        _remove_top_level_functions(new, {"_component_total_intent", "install_boq_ai_fullscan"})

    old_test = TESTS / "test_boq_ai_fullscan.py"
    new_test = TESTS / "test_boq_component_fullscan.py"
    if old_test.exists():
        text = _read(old_test).replace(
            "from qlda.runtime_core.boq_ai_fullscan import PATCH_MARKER, fullscan_boq_component_totals",
            "from qlda.runtime_core.boq_component_fullscan import PATCH_MARKER, fullscan_boq_component_totals",
        ).replace("class BOQAIFullscanTests", "class BOQComponentFullscanTests")
        _write(new_test, text)
        old_test.unlink()


def _patch_runtime_business_compatibility() -> None:
    # Contractor access control keeps workspace authorization but no longer
    # monkey-patches AI assistants or ContextBuilder.
    access = RUNTIME / "contractor_access_control.py"
    _remove_top_level_functions(access, {"capture_single_contractor_ai_context", "install_ai_access_guard"})
    _replace(access, "    capture_single_contractor_ai_context()\n", "")
    _replace(access, "    install_ai_access_guard()\n", "")

    # ProjectDatabase no longer post-load patches the retired ProjectContextBuilder.
    project_db = RUNTIME / "project_database.py"
    _remove_top_level_functions(project_db, {"_patch_ai_service"})
    _replace(project_db, '    "qlda.runtime_core.ai_service": _patch_ai_service,\n', "")

    # Mutable runtime settings still drive local VPS and multicore settings, but
    # no longer monkey-patch provider classes.
    bridge = RUNTIME / "runtime_settings_bridge.py"
    _remove_block(
        bridge,
        "    # AI assistants historically construct provider settings from environment\n",
        "    try:\n        import qlda.runtime_core.local_vps_backend as lb\n",
    )
    btext = _read(bridge)
    # The previous block removal consumes the start of the local_vps try marker;
    # restore it when needed.
    if "import qlda.runtime_core.local_vps_backend as lb" in btext and "    try:\n        import qlda.runtime_core.local_vps_backend as lb" not in btext:
        btext = btext.replace("        import qlda.runtime_core.local_vps_backend as lb", "    try:\n        import qlda.runtime_core.local_vps_backend as lb", 1)
    btext = btext.replace('        "ai": False,\n', "")
    ai_status = re.compile(
        r"    try:\n        import qlda\.runtime_core\.ai_service as ai\n"
        r"        out\[\"ai\"\] = bool\(getattr\(ai, \"_qlda_admin_settings_bridge_installed\", False\)\)\n"
        r"    except Exception:\n        pass\n"
    )
    btext = ai_status.sub("", btext, count=1)
    _write(bridge, btext)

    # Remove obsolete AI bootstrap stage entirely. Native AI has no runtime patch stage.
    composition = SRC / "composition/runtime_features.py"
    ctext = _read(composition)
    ctext = re.sub(r'^    AI = "ai".*\n', "", ctext, flags=re.M)
    ctext = re.sub(r'^    RuntimeFeature\("claim-component-fullscan".*\n', "", ctext, flags=re.M)
    ctext = re.sub(r'^    RuntimeFeature\("project-remaining-components".*\n', "", ctext, flags=re.M)
    ctext = ctext.replace("    # RuntimeStage.AI intentionally has zero compatibility installers.\n\n", "")
    _write(composition, ctext)

    bootstrap = RUNTIME / "bootstrap.py"
    _remove_top_level_functions(bootstrap, {"initialize_ai_runtime"})
    btext = _read(bootstrap)
    btext = btext.replace("_AI_READY = False\n", "")
    btext = btext.replace("        initialize_ai_runtime()\n", "        initialize_business_runtime()\n")
    btext = btext.replace('    "initialize_ai_runtime",\n', "")
    _write(bootstrap, btext)


def _patch_tests() -> None:
    # Keep BOQ parser/DB regression tests; remove only the old prompt-context test.
    path = TESTS / "test_boq_cost_components.py"
    if path.exists():
        text = _read(path)
        text = text.replace("import qlda.runtime_core.ai_service as ai_service\n", "")
        text = text.replace("from qlda.runtime_core.ai_live_context import install_ai_live_context\n", "")
        text = text.replace(
            "        # Match the real VPS startup order: install the live snapshot wrapper\n"
            "        # first, then extend its BOQ appendix with material/labor components.\n"
            "        install_ai_live_context()\n"
            "        install_boq_cost_components()\n",
            "        install_boq_cost_components()\n",
        )
        _write(path, text)
        _remove_class_method(path, "BOQCostComponentsTests", "test_ai_context_receives_component_prices_and_costs")

    # Architecture assertion now verifies that the compatibility stage itself is gone.
    arch = TESTS / "test_ai_architecture_boundary.py"
    if arch.exists():
        _replace_top_level_function(
            arch,
            "test_runtime_ai_stage_has_no_compatibility_installers",
            '''def test_runtime_ai_stage_is_deleted() -> None:
    assert "ai" not in {stage.value for stage in RuntimeStage}
    assert all(getattr(feature.stage, "value", "") != "ai" for feature in FEATURES)''',
        )

    for name in LEGACY_PROMPT_TESTS:
        p = TESTS / name
        if p.exists():
            p.unlink()


def _delete_legacy_files() -> None:
    for stem in RETIRED_MODULES:
        path = RUNTIME / f"{stem}.py"
        if path.exists():
            path.unlink()
    legacy_provider = INFRA_AI / "legacy_provider.py"
    if legacy_provider.exists():
        legacy_provider.unlink()


def _find_retired_references() -> list[str]:
    needles = [f"qlda.runtime_core.{name}" for name in RETIRED_MODULES]
    needles += [
        "qlda.infrastructure.ai.legacy_provider",
        "_v622_set_ai_workspace_scope",
        "initialize_ai_runtime",
        "RuntimeStage.AI",
    ]
    offenders: list[str] = []
    for base in (SRC, TESTS):
        for path in base.rglob("*.py"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            hits = sorted({needle for needle in needles if needle in text})
            if hits:
                offenders.append(f"{path.relative_to(ROOT)} -> {', '.join(hits)}")
    return offenders


def _assert_native_state() -> None:
    source = _read(APP)
    assert "qlda.infrastructure.ai.presentation_facade" in source
    assert "qlda.runtime_core.ai_service" not in source
    assert "set_ai_workspace_scope" not in source
    leftovers = [stem for stem in RETIRED_MODULES if (RUNTIME / f"{stem}.py").exists()]
    if leftovers:
        raise SystemExit(f"Runtime AI compatibility files remain: {leftovers}")
    if (INFRA_AI / "legacy_provider.py").exists():
        raise SystemExit("legacy_provider.py still exists")
    if not (RUNTIME / "boq_component_fullscan.py").exists():
        raise SystemExit("Neutral BOQ component fullscan service was not retained")
    offenders = _find_retired_references()
    if offenders:
        raise SystemExit("Retired AI references remain:\n" + "\n".join(offenders))


def main() -> int:
    _patch_app()
    _patch_native_adapter()
    _patch_provider_callers()
    _patch_mixed_business_modules()
    _rename_boq_fullscan()
    _patch_runtime_business_compatibility()
    _patch_tests()
    _delete_legacy_files()
    _assert_native_state()
    print("runtime_core AI compatibility retirement migration is clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
