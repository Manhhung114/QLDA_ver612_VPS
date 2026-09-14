from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "qlda"


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v760_metadata_is_final_native_runtime():
    import qlda
    assert qlda.__version__ == "7.6"
    assert qlda.LEGACY_ADAPTERS == ()
    assert qlda.LEGACY_RUNTIME is False
    assert qlda.STREAMLIT_ENTRYPOINT == "qlda.presentation.streamlit.app"
    assert _text("VERSION.txt").strip() == "7.6"


def test_legacy_loader_is_retired():
    assert not (SRC / "runtime.py").exists()
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "legacy_import(" not in text, path
        assert "ensure_repo_root_on_path" not in text, path
        assert "sys.path.insert(0" not in text, path


def test_packaged_production_imports_are_not_root_versioned_modules():
    forbidden_suffixes = ("_v622", "_v624")
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            for name in names:
                top = name.split(".", 1)[0]
                assert not top.endswith(forbidden_suffixes), (path, name)
                assert not top.startswith(("v622_", "v623_", "v624_", "v621_")), (path, name)


def test_streamlit_is_source_controlled_and_native():
    text = _text("src/qlda/presentation/streamlit/app.py")
    assert "initialize_runtime" in text
    assert "base64.b64decode" not in text
    assert "gzip.decompress" not in text
    assert "exec(compile(" not in text
    assert "build_v621_webopt" not in text


def test_docker_systemd_and_deploy_use_packaged_entrypoints():
    docker = _text("Dockerfile")
    service = _text("vps/qlda.service")
    deploy = _text("vps/deploy.sh")
    assert "src/qlda/presentation/streamlit/app.py" in docker
    assert "src/qlda/presentation/streamlit/app.py" in service
    assert 'PYTHONPATH="$APP_DIR/src"' in deploy
    assert "build_v621_webopt.py" not in deploy
    assert "_v622.py" not in deploy
    assert "_v624.py" not in deploy
    assert "reconcile_api.sh" in deploy
    assert "reconcile_api_v627.sh" not in deploy


def test_native_api_and_worker_entrypoints_remain_stable():
    api_service = _text("vps/qlda-api.service")
    worker_service = _text("vps/qlda-excel-worker.service")
    assert "qlda.presentation.api.app:app" in api_service
    assert "qlda.modules.excel.worker" in worker_service
