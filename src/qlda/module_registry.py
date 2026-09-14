from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    name: str
    package: str
    owns: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    migration_state: str = "boundary-created"


MODULES: dict[str, ModuleSpec] = {
    "boq": ModuleSpec(
        name="boq",
        package="qlda.modules.boq",
        owns=("BOQ parsing", "BOQ persistence", "BOQ cost components"),
        dependencies=("infrastructure",),
        migration_state="compatibility-facade",
    ),
    "ipc": ModuleSpec(
        name="ipc",
        package="qlda.modules.ipc",
        owns=("IPC/Claim parsing", "IPC revisions", "IPC persistence"),
        dependencies=("infrastructure",),
        migration_state="compatibility-facade",
    ),
    "vo": ModuleSpec(
        name="vo",
        package="qlda.modules.vo",
        owns=("VO parsing", "VO revisions", "VO persistence"),
        dependencies=("infrastructure",),
        migration_state="compatibility-facade",
    ),
    "schedule": ModuleSpec(
        name="schedule",
        package="qlda.modules.schedule",
        owns=("Schedule Excel", "schedule task persistence"),
        dependencies=("infrastructure",),
        migration_state="compatibility-facade",
    ),
    "excel": ModuleSpec(
        name="excel",
        package="qlda.modules.excel",
        owns=("Excel job queue", "background worker orchestration"),
        dependencies=("boq", "ipc", "vo", "schedule", "infrastructure"),
        migration_state="active-facade",
    ),
}


def module_names() -> tuple[str, ...]:
    return tuple(MODULES)
