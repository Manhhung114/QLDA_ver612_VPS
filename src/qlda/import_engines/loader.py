from __future__ import annotations

import importlib
from types import ModuleType

ENGINE_MODULES = {
    "boq_background": "qlda.import_engines.boq_background",
    "boq_persistence": "qlda.import_engines.boq_persistence",
    "boq_snapshot": "qlda.import_engines.boq_snapshot",
    "ipc_background": "qlda.import_engines.ipc_background",
    "ipc_persistence": "qlda.import_engines.ipc_persistence",
    "vo_background": "qlda.import_engines.vo_background",
    "vo_persistence": "qlda.import_engines.vo_persistence",
    "schedule_background": "qlda.import_engines.schedule_background",
    "schedule_persistence": "qlda.import_engines.schedule_persistence",
}


def load_engine(name: str) -> ModuleType:
    key = str(name or "").strip()
    module_name = ENGINE_MODULES.get(key)
    if not module_name:
        raise KeyError(f"Unknown import engine: {key}")
    return importlib.import_module(module_name)
