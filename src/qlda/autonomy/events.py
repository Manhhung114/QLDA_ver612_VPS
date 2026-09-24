from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from typing import Callable, Iterable

from .models import DomainEvent

EventHandler = Callable[[DomainEvent], None]


class EventBus:
    """V7.9 event engine with deterministic IDs and replay-safe delivery."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._queue: deque[DomainEvent] = deque()
        self._seen: set[str] = set()

    @staticmethod
    def make_event_id(event: DomainEvent) -> str:
        raw = json.dumps(
            [event.event_type, event.project_id, event.workspace_project_id, event.payload, event.occurred_at],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return "EVT-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20].upper()

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._handlers[str(event_type)].append(handler)

    def publish(self, event: DomainEvent) -> str:
        event_id = event.event_id or self.make_event_id(event)
        if event_id in self._seen:
            return event_id
        object.__setattr__(event, "event_id", event_id)
        self._queue.append(event)
        return event_id

    def drain(self, *, limit: int = 1000) -> int:
        handled = 0
        while self._queue and handled < int(limit):
            event = self._queue.popleft()
            if event.event_id in self._seen:
                continue
            for handler in tuple(self._handlers.get(event.event_type, ())):
                handler(event)
            for handler in tuple(self._handlers.get("*", ())):
                handler(event)
            self._seen.add(event.event_id)
            handled += 1
        return handled


DEFAULT_EVENT_TYPES = (
    "GOOGLE_SHEET_UPDATED",
    "DOCUMENT_UPLOADED",
    "BOQ_IMPORTED",
    "IPC_IMPORTED",
    "RFI_OVERDUE",
    "NCR_OVERDUE",
    "INSPECTION_REJECTED",
    "SCHEDULE_DELAYED",
    "CONTRACT_EXPIRING",
    "PRODUCTION_CHANGED",
    "PAYMENT_OVERDUE",
    "NEW_DRAWING_REVISION",
    "DATA_INTEGRITY_FAILED",
    "APPROVAL_GRANTED",
)


def events_from_changes(project_id: int, changes: Iterable[dict]) -> list[DomainEvent]:
    out: list[DomainEvent] = []
    for change in changes:
        kind = str(change.get("event_type") or change.get("type") or "").upper()
        if not kind:
            continue
        out.append(
            DomainEvent(
                event_type=kind,
                project_id=int(project_id),
                workspace_project_id=(int(change["workspace_project_id"]) if change.get("workspace_project_id") else None),
                actor=str(change.get("actor") or "system"),
                payload={k: v for k, v in change.items() if k not in {"event_type", "type", "actor", "workspace_project_id"}},
            )
        )
    return out
