from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    name: str
    package: str
    owns: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    migration_state: str = "native-engine"


MODULES: dict[str, ModuleSpec] = {
    "boq": ModuleSpec(
        name="boq",
        package="qlda.import_engines",
        owns=("BOQ parsing", "BOQ persistence", "BOQ cost components"),
        dependencies=("infrastructure",),
    ),
    "ipc": ModuleSpec(
        name="ipc",
        package="qlda.import_engines",
        owns=("IPC/Claim parsing", "IPC revisions", "IPC persistence"),
        dependencies=("infrastructure",),
    ),
    "vo": ModuleSpec(
        name="vo",
        package="qlda.import_engines",
        owns=("VO parsing", "VO revisions", "VO persistence"),
        dependencies=("infrastructure",),
    ),
    "schedule": ModuleSpec(
        name="schedule",
        package="qlda.import_engines",
        owns=("Schedule Excel", "schedule task persistence"),
        dependencies=("infrastructure",),
    ),
    "production_progress": ModuleSpec(
        name="production_progress",
        package="qlda.application.google_sheets",
        owns=("Google Sheets source registry", "production progress normalization", "progress history"),
        dependencies=("infrastructure",),
        migration_state="native-module",
    ),
    "excel": ModuleSpec(
        name="excel",
        package="qlda.modules.excel",
        owns=("background worker entrypoint",),
        dependencies=("boq", "ipc", "vo", "schedule", "infrastructure"),
        migration_state="native-entrypoint",
    ),
}


def module_names() -> tuple[str, ...]:
    return tuple(MODULES)
