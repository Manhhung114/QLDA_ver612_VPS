from __future__ import annotations

from types import ModuleType
from typing import Any

from qlda.runtime import legacy_import


def load_module(module_name: str) -> ModuleType:
    """Single gateway from the modular package to V6.22-V6.24 compatibility code."""
    return legacy_import(module_name)


def resolve(module_name: str, attribute: str) -> Any:
    """Resolve one public symbol lazily from a compatibility implementation."""
    return getattr(load_module(module_name), attribute)


def call_main(module_name: str) -> Any:
    """Run a legacy CLI main() behind a stable modular entrypoint."""
    main = resolve(module_name, "main")
    if not callable(main):
        raise TypeError(f"{module_name}.main is not callable")
    return main()
