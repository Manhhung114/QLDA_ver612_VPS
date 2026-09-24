from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    name: str
    at: time
    handler: Callable[..., Any]
    enabled: bool = True


class DailyAutomationScheduler:
    """Small deterministic scheduler suitable for a systemd/cron worker loop.

    It does not sleep or spawn threads itself. The outer worker calls `tick()`
    every minute; a job is executed at most once per Vietnam-local calendar day.
    """

    def __init__(self, jobs: Iterable[ScheduledJob] = ()) -> None:
        self.jobs = list(jobs)
        self._last_run: dict[str, str] = {}

    def register(self, job: ScheduledJob) -> None:
        if any(existing.name == job.name for existing in self.jobs):
            raise ValueError(f"Scheduled job đã tồn tại: {job.name}")
        self.jobs.append(job)

    def tick(self, *, now: datetime | None = None, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        current = now.astimezone(TZ) if now and now.tzinfo else (now.replace(tzinfo=TZ) if now else datetime.now(TZ))
        day = current.date().isoformat()
        context = dict(context or {})
        results: list[dict[str, Any]] = []
        for job in sorted(self.jobs, key=lambda x: (x.at.hour, x.at.minute, x.name)):
            if not job.enabled or self._last_run.get(job.name) == day:
                continue
            due = (current.hour, current.minute) >= (job.at.hour, job.at.minute)
            if not due:
                continue
            try:
                output = job.handler(**context)
            except Exception as exc:
                results.append({"job": job.name, "status": "FAILED", "error": str(exc)})
                # Failed jobs are intentionally retryable on the next tick.
                continue
            self._last_run[job.name] = day
            results.append({"job": job.name, "status": "SUCCESS", "output": output})
        return results


def recommended_daily_schedule(
    *,
    backup: Callable[..., Any],
    sync_sources: Callable[..., Any],
    check_integrity: Callable[..., Any],
    supervise: Callable[..., Any],
    build_report: Callable[..., Any],
    notify: Callable[..., Any],
) -> DailyAutomationScheduler:
    return DailyAutomationScheduler([
        ScheduledJob("backup", time(2, 0), backup),
        ScheduledJob("sync_sources", time(6, 0), sync_sources),
        ScheduledJob("check_integrity", time(6, 15), check_integrity),
        ScheduledJob("project_supervisor", time(6, 20), supervise),
        ScheduledJob("daily_health_report", time(6, 30), build_report),
        ScheduledJob("morning_notification", time(7, 0), notify),
    ])
