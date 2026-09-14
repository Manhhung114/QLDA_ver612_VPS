from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectScope:
    """Authorized project/workspace scope independent from any transport or DB."""

    requested_project_id: int
    master_project_id: int
    workspace_project_id: int
    project_code: str
    is_master_scope: bool = False
