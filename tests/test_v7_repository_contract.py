from __future__ import annotations

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
        self.assertEqual(qlda.STREAMLIT_ENTRYPOINT, "qlda.presentation.streamlit.main")

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

    def test_production_entrypoints_are_packaged_and_modular(self):
        service = (ROOT / "vps/qlda.service").read_text(encoding="utf-8")
        docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        entrypoint = "src/qlda/presentation/streamlit/main.py"
        self.assertIn(entrypoint, service)
        self.assertIn(entrypoint, docker)
        self.assertNotIn("streamlit_app.py", service)
        self.assertIn("USER qlda", docker)
        self.assertTrue((ROOT / entrypoint).exists())
        # The materialized shell remains available only as a compatibility body.
        self.assertTrue((ROOT / "src/qlda/presentation/streamlit/app.py").exists())
        self.assertTrue((ROOT / "requirements.lock").exists())


if __name__ == "__main__":
    unittest.main()
