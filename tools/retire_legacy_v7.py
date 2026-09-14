from __future__ import annotations

"""One-time repository retirement for QLDA V7.

Production already runs from ``src/qlda``. This migration makes repository,
tests and CI match production, then removes obsolete root copies.
"""

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "qlda"
TESTS = ROOT / "tests"
WORKFLOWS = ROOT / ".github" / "workflows"
MANIFEST = ROOT / "docs" / "architecture" / "V7.6_PACKAGED_RUNTIME_MANIFEST.txt"

IMPORT_ENGINE_MAP = {
    "boq_background_v624": "qlda.import_engines.boq_background",
    "boq_persist_v624": "qlda.import_engines.boq_persistence",
    "boq_snapshot_v624": "qlda.import_engines.boq_snapshot",
    "ipc_background_v624": "qlda.import_engines.ipc_background",
    "ipc_persist_v624": "qlda.import_engines.ipc_persistence",
    "schedule_background_v624": "qlda.import_engines.schedule_background",
    "schedule_persist_v624": "qlda.import_engines.schedule_persistence",
    "vo_background_v624": "qlda.import_engines.vo_background",
    "vo_persist_v624": "qlda.import_engines.vo_persistence",
}

HISTORICAL_TEST_PATTERNS = (
    "test_v6*.py",
    "test_v700*.py",
    "test_v710*.py",
    "test_v720*.py",
    "test_v730*.py",
    "test_v740*.py",
    "test_v750*.py",
    "test_v760*.py",
)

KEEP_WORKFLOWS = {
    "v7-native-regression.yml",
    "v7-docker-check.yml",
    "v7-retirement-migrate.yml",
}

LEGACY_ROOT_EXTRA = {
    "streamlit_app.py",
    "build_v621_webopt.py",
    "local_file_server_background.py",
    "local_file_server_v622.py",
}

REGRESSION_WORKFLOW = r'''name: V7 Native Regression

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  native-regression:
    runs-on: ubuntu-24.04
    env:
      PYTHONPATH: src
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip
          cache-dependency-path: requirements.lock
      - name: Install system runtime
        run: |
          sudo apt-get update
          sudo apt-get install -y --no-install-recommends default-jre-headless
      - name: Install locked dependencies
        run: python -m pip install --disable-pip-version-check -r requirements.lock
      - name: Compile packaged source and tests
        run: |
          python -m compileall -q src/qlda tests
          python -m py_compile src/qlda/presentation/streamlit/app.py
      - name: Repository retirement contract
        run: python -m unittest tests.test_v7_repository_contract -v
      - name: Full native regression suite
        run: python -m unittest discover -s tests -p 'test_*.py' -v
      - name: Application smoke imports
        run: |
          python - <<'PY'
          import qlda
          from qlda.bootstrap import get_application
          from qlda.presentation.api.app import app
          assert qlda.__version__ == '7.6'
          assert qlda.LEGACY_ADAPTERS == ()
          assert qlda.LEGACY_RUNTIME is False
          services = get_application()
          assert services.excel and services.ai and services.files and services.jobs
          paths = set(app.openapi().get('paths', {}))
          assert '/api/health' in paths
          assert '/api/v1/ai/ask' in paths
          print('V7 native application smoke OK')
          PY
'''

DOCKER_WORKFLOW = r'''name: V7 Docker Check

on:
  push:
    branches: [main]
    paths:
      - 'Dockerfile'
      - 'requirements.lock'
      - 'src/**'
      - '.github/workflows/v7-docker-check.yml'
  workflow_dispatch:

jobs:
  docker:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - name: Build packaged image
        run: docker build -t qlda-v7:${GITHUB_SHA} .
      - name: Verify non-root packaged runtime
        run: |
          python - <<'PY'
          import json, subprocess
          image = 'qlda-v7:' + __import__('os').environ['GITHUB_SHA']
          data = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], text=True))[0]['Config']
          assert data.get('User') == 'qlda', data.get('User')
          cmd = ' '.join(data.get('Cmd') or [])
          assert 'src/qlda/presentation/streamlit/app.py' in cmd, cmd
          print('Docker user/entrypoint OK')
          PY
          docker run --rm --entrypoint python qlda-v7:${GITHUB_SHA} -c "import qlda; assert qlda.LEGACY_RUNTIME is False; print(qlda.__version__)"
'''

CONTRACT_TEST = r'''from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "qlda"

LEGACY_PATTERNS = (
    "streamlit_app.py",
    "build_v621_webopt.py",
    "v621_webopt_source",
)


class V7RepositoryContractTests(unittest.TestCase):
    def test_metadata_is_native_v7(self):
        import qlda
        self.assertEqual(qlda.__version__, "7.6")
        self.assertEqual(qlda.LEGACY_ADAPTERS, ())
        self.assertIs(qlda.LEGACY_RUNTIME, False)

    def test_obsolete_v6_runtime_is_absent_from_root(self):
        for name in LEGACY_PATTERNS:
            self.assertFalse((ROOT / name).exists(), name)
        offenders = []
        for path in ROOT.glob("*.py"):
            low = path.name.lower()
            if re.search(r"(?:^|_)v(?:615|621|622|623|624)(?:_|\\.|$)", low):
                offenders.append(path.name)
            if low.startswith(("v622_", "v623_", "v624_")):
                offenders.append(path.name)
        self.assertEqual(sorted(set(offenders)), [])

    def test_packaged_runtime_has_no_root_legacy_imports(self):
        offenders = []
        for path in SRC.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.ImportFrom):
                    module = node.module
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if re.search(r"(?:^|_)v(?:615|621|622|623|624)(?:_|$)", alias.name):
                            offenders.append(f"{path.relative_to(ROOT)}: {alias.name}")
                if module and re.search(r"(?:^|_)v(?:615|621|622|623|624)(?:_|$)", module):
                    offenders.append(f"{path.relative_to(ROOT)}: {module}")
        self.assertEqual(offenders, [])

    def test_production_entrypoints_are_packaged(self):
        service = (ROOT / "vps/qlda.service").read_text(encoding="utf-8")
        docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("src/qlda/presentation/streamlit/app.py", service)
        self.assertIn("src/qlda/presentation/streamlit/app.py", docker)
        self.assertNotIn("streamlit_app.py", service)
        self.assertIn("USER qlda", docker)
        self.assertTrue((ROOT / "requirements.lock").exists())


if __name__ == "__main__":
    unittest.main()
'''


def load_mapping() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if " -> " not in line:
            continue
        old, new = line.split(" -> ", 1)
        if old.strip() and new.strip().startswith("qlda."):
            mapping[old.strip()] = new.strip()
    mapping.update(IMPORT_ENGINE_MAP)
    return mapping


def module_path(target: str) -> str:
    return "src/" + target.replace(".", "/") + ".py"


def rewrite_python(text: str, mapping: dict[str, str]) -> str:
    for old in sorted(mapping, key=len, reverse=True):
        target = mapping[old]
        escaped = re.escape(old)
        text = re.sub(
            rf"(?m)^(\s*)from\s+{escaped}(\s+import\s+)",
            rf"\1from {target}\2",
            text,
        )

        def repl_import(match: re.Match[str]) -> str:
            indent = match.group(1)
            alias_clause = match.group(2) or ""
            if alias_clause:
                return f"{indent}import {target} as {alias_clause.split()[-1]}"
            return f"{indent}import {target} as {old}"

        text = re.sub(
            rf"(?m)^(\s*)import\s+{escaped}(\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?\s*$",
            repl_import,
            text,
        )
        for quote in ('"', "'"):
            text = text.replace(f"{quote}{old}{quote}", f"{quote}{target}{quote}")
            text = text.replace(f"{quote}{old}.", f"{quote}{target}.")
            text = text.replace(f"{quote}{old}.py{quote}", f"{quote}{module_path(target)}{quote}")
        text = text.replace(f'"{old}.py"', f'"{module_path(target)}"')
        text = text.replace(f"'{old}.py'", f"'{module_path(target)}'")
    text = text.replace('"streamlit_app.py"', '"src/qlda/presentation/streamlit/app.py"')
    text = text.replace("'streamlit_app.py'", "'src/qlda/presentation/streamlit/app.py'")
    return text


def migrate_tests(mapping: dict[str, str]) -> None:
    for pattern in HISTORICAL_TEST_PATTERNS:
        for path in TESTS.glob(pattern):
            path.unlink()
    for path in sorted(TESTS.glob("test_*.py")):
        path.write_text(rewrite_python(path.read_text(encoding="utf-8"), mapping), encoding="utf-8")
    for path in sorted(TESTS.glob("test_*.py")):
        new_name = re.sub(r"_v(?:622|623|624)(?=\.py$)", "", path.name)
        new_name = re.sub(r"^test_v(?:622|623|624)_", "test_", new_name)
        if new_name != path.name:
            target = path.with_name(new_name)
            if target.exists():
                raise RuntimeError(f"test rename collision: {path.name} -> {target.name}")
            path.rename(target)
    (TESTS / "test_v7_repository_contract.py").write_text(CONTRACT_TEST, encoding="utf-8")


def retire_workflows() -> None:
    WORKFLOWS.mkdir(parents=True, exist_ok=True)
    for path in list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml")):
        if path.name not in KEEP_WORKFLOWS:
            path.unlink()
    (WORKFLOWS / "v7-native-regression.yml").write_text(REGRESSION_WORKFLOW, encoding="utf-8")
    (WORKFLOWS / "v7-docker-check.yml").write_text(DOCKER_WORKFLOW, encoding="utf-8")


def legacy_root_candidates(mapping: dict[str, str]) -> set[Path]:
    values = {ROOT / f"{name}.py" for name in mapping}
    values.update(ROOT / name for name in LEGACY_ROOT_EXTRA)
    for path in ROOT.glob("*.py"):
        low = path.name.lower()
        if re.search(r"(?:^|_)v(?:615|621|622|623|624)(?:_|\.|$)", low):
            values.add(path)
        if low.startswith(("v622_", "v623_", "v624_")):
            values.add(path)
    return {path for path in values if path.exists()}


def retire_root_sources(mapping: dict[str, str]) -> None:
    for path in sorted(legacy_root_candidates(mapping)):
        path.unlink()
    parts = ROOT / "v621_webopt_source"
    if parts.exists():
        shutil.rmtree(parts)


def harden_docker_and_lock_consumers() -> None:
    docker_path = ROOT / "Dockerfile"
    docker = docker_path.read_text(encoding="utf-8")
    docker = docker.replace("COPY requirements.txt ./requirements.txt\nRUN python -m pip install --no-cache-dir -r requirements.txt", "COPY requirements.lock ./requirements.lock\nRUN python -m pip install --no-cache-dir -r requirements.lock")
    if "USER qlda" not in docker:
        marker = "EXPOSE 8501\n"
        replacement = (
            "RUN groupadd --system qlda \\\n"
            "    && useradd --system --gid qlda --create-home --home-dir /home/qlda qlda \\\n"
            "    && mkdir -p /app/data/runtime \\\n"
            "    && chown -R qlda:qlda /app /home/qlda\n\n"
            "ENV HOME=/home/qlda\n"
            "USER qlda\n\n"
            "EXPOSE 8501\n"
        )
        if marker not in docker:
            raise RuntimeError("Docker EXPOSE anchor missing")
        docker = docker.replace(marker, replacement, 1)
    docker_path.write_text(docker, encoding="utf-8")

    for rel in ("vps/deploy.sh", "vps/install.sh"):
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        text = text.replace("requirements.txt", "requirements.lock")
        path.write_text(text, encoding="utf-8")


def rename_theme_doc() -> None:
    old = ROOT / "docs/ui/V7.6.1_COLOR_THEME.md"
    new = ROOT / "docs/ui/V7.6_COLOR_THEME.md"
    if old.exists() and not new.exists():
        old.rename(new)
        new.write_text(new.read_text(encoding="utf-8").replace("# V7.6.1 Color Theme", "# V7.6 Color Theme"), encoding="utf-8")


def assert_no_legacy_imports(mapping: dict[str, str]) -> None:
    failures: list[str] = []
    for base in (SRC, TESTS):
        for path in base.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for old in mapping:
                if re.search(rf"(?m)^\s*(?:from|import)\s+{re.escape(old)}(?:\s|\.|$)", text):
                    failures.append(f"{path.relative_to(ROOT)} imports {old}")
    if failures:
        raise RuntimeError("legacy imports remain:\n" + "\n".join(failures[:100]))


def main() -> None:
    mapping = load_mapping()
    if len(mapping) < 60:
        raise RuntimeError(f"packaged mapping unexpectedly small: {len(mapping)}")
    migrate_tests(mapping)
    retire_workflows()
    retire_root_sources(mapping)
    harden_docker_and_lock_consumers()
    rename_theme_doc()
    assert_no_legacy_imports(mapping)
    print(f"V7 repository retirement prepared; mapped modules={len(mapping)}")


if __name__ == "__main__":
    main()
