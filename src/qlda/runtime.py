from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

SRC_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]


def ensure_repo_root_on_path() -> Path:
    """Keep V6.22-V6.24 root modules importable during incremental migration."""
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    return REPO_ROOT


def legacy_import(module_name: str) -> ModuleType:
    """Import one compatibility module without leaking path setup to callers."""
    name = str(module_name or "").strip()
    if not name:
        raise ValueError("module_name is required")
    ensure_repo_root_on_path()
    return importlib.import_module(name)
