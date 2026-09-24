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
    "autonomy": ModuleSpec(
        name="autonomy",
        package="qlda.autonomy",
        owns=(
            "V7.7 data integrity and evidence",
            "V7.8 unified AI tool/service boundary",
            "V7.9 event automation",
            "V8.0 AI planner/executor and approval gate",
            "V8.1 project supervisor",
            "V8.2 semi-autonomous policy",
            "V9.0 project digital twin and what-if engine",
        ),
        dependencies=("production_progress", "boq", "ipc", "vo", "schedule", "infrastructure"),
        migration_state="native-platform",
    ),
}


def module_names() -> tuple[str, ...]:
    return tuple(MODULES)
