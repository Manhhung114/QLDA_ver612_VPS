from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
import threading

from cloud_db import planned_progress, calc_progress_status


class MppCloudError(RuntimeError):
    pass


_JVM_LOCK = threading.Lock()


def _call(obj: Any, name: str, default=None):
    if obj is None:
        return default
    try:
        fn = getattr(obj, name)
        value = fn() if callable(fn) else fn
        return default if value is None else value
    except Exception:
        return default


def _jstr(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value.toString()).strip()
    except Exception:
        return str(value).strip()


def _iso_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = _jstr(value)
    if not s:
        return ""
    # java.time values normally stringify as 2026-08-14T08:00 or 2026-08-14.
    for candidate in (s[:10], s.split("T", 1)[0], s.split(" ", 1)[0]):
        try:
            return datetime.strptime(candidate, "%Y-%m-%d").date().isoformat()
        except Exception:
            pass
    # Defensive fallback for localized strings.
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except Exception:
            pass
    return ""


def _int(value: Any, default=0) -> int:
    try:
        return int(round(float(str(value))))
    except Exception:
        return default


def _bool(value: Any) -> int:
    try:
        return 1 if bool(value) else 0
    except Exception:
        return 0


def _duration_days(value: Any, default=0.0) -> float:
    if value is None:
        return float(default)
    try:
        amount = float(value.getDuration())
        unit = _jstr(value.getUnits()).upper()
        if "MINUTE" in unit:
            return amount / 480.0
        if "HOUR" in unit:
            return amount / 8.0
        if "WEEK" in unit:
            return amount * 5.0
        if "MONTH" in unit:
            return amount * 20.0
        if "YEAR" in unit:
            return amount * 240.0
        return amount
    except Exception:
        try:
            return float(str(value))
        except Exception:
            return float(default)


def _predecessors(task: Any) -> str:
    out = []
    try:
        rels = task.getPredecessors()
        for rel in rels:
            try:
                source = rel.getSourceTask()
                task_id = _int(source.getID(), 0) if source is not None else 0
                rel_type = _jstr(rel.getType())
                lag = _jstr(rel.getLag())
                text = str(task_id) if task_id else "?"
                if rel_type:
                    # FINISH_START becomes FS-like text for compact display.
                    compact = rel_type.replace("FINISH_START", "FS").replace("START_START", "SS").replace("FINISH_FINISH", "FF").replace("START_FINISH", "SF")
                    text += compact if compact in {"FS","SS","FF","SF"} else f" {compact}"
                if lag and lag not in {"0", "0.0", "0 days", "0 hours"}:
                    text += f" {lag}"
                out.append(text)
            except Exception:
                continue
    except Exception:
        pass
    return ", ".join(out)


def _resources(task: Any) -> str:
    names = []
    try:
        for a in task.getResourceAssignments():
            try:
                r = a.getResource()
                name = _jstr(r.getName()) if r is not None else ""
                if name and name not in names:
                    names.append(name)
            except Exception:
                pass
    except Exception:
        pass
    return ", ".join(names)


def _ensure_jvm():
    try:
        import jpype
        import mpxj  # noqa: F401 -- import prepares MPXJ jars for JPype classpath.
    except Exception as exc:
        raise MppCloudError(
            "Thiếu MPXJ/JPype. Trên Streamlit Community Cloud hãy giữ mpxj và JPype1 trong requirements.txt."
        ) from exc
    if not jpype.isJVMStarted():
        with _JVM_LOCK:
            if not jpype.isJVMStarted():
                try:
                    jpype.startJVM("-Dlog4j2.loggerContextFactory=org.apache.logging.log4j.simple.SimpleLoggerContextFactory")
                except Exception as exc:
                    raise MppCloudError(
                        "Không khởi động được Java JVM. Community Cloud cần packages.txt có default-jre-headless."
                    ) from exc


def read_mpp(path: str | Path, status_date: date | None = None) -> tuple[dict, list[dict]]:
    """Read an uploaded MPP on Linux via MPXJ.

    Returns (project_info, tasks) using field names compatible with the desktop DB.
    """
    status_date = status_date or date.today()
    path = Path(path)
    if not path.exists():
        raise MppCloudError(f"Không tìm thấy file tạm: {path}")
    _ensure_jvm()
    try:
        from org.mpxj.reader import UniversalProjectReader
        project = UniversalProjectReader().read(str(path))
    except Exception as exc:
        raise MppCloudError(f"MPXJ không đọc được file MPP: {exc}") from exc

    props = _call(project, "getProjectProperties")
    project_info = {
        "name": _jstr(_call(props, "getProjectTitle", path.stem)),
        "manager": _jstr(_call(props, "getManager", "")),
        "start_date": _iso_date(_call(props, "getStartDate")),
        "end_date": _iso_date(_call(props, "getFinishDate")),
        "status_date": _iso_date(_call(props, "getStatusDate")),
    }
    if not project_info["name"]:
        project_info["name"] = path.stem

    result: list[dict] = []
    try:
        task_iter = project.getTasks()
    except Exception as exc:
        raise MppCloudError(f"Không đọc được danh sách task: {exc}") from exc

    for task in task_iter:
        try:
            name = _jstr(_call(task, "getName", ""))
            task_id = _int(_call(task, "getID", 0), 0)
            uid = _int(_call(task, "getUniqueID", 0), 0)
            outline = _int(_call(task, "getOutlineLevel", 1), 1)
            # MPXJ may expose a hidden project-summary task at outline level 0.
            if not name or task_id <= 0 or uid <= 0 or outline <= 0:
                continue
            start = _iso_date(_call(task, "getStart"))
            finish = _iso_date(_call(task, "getFinish"))
            if not start or not finish:
                continue
            planned = planned_progress(start, finish, status_date)
            actual = max(0, min(100, _int(_call(task, "getPercentageComplete", 0), 0)))
            duration = _duration_days(_call(task, "getDuration"), 0)
            if duration <= 0:
                try:
                    duration = max(1, (datetime.strptime(finish, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days + 1)
                except Exception:
                    duration = 1
            slack = _duration_days(_call(task, "getTotalSlack"), 0)
            result.append({
                "source_uid": uid,
                "task_id": task_id,
                "wbs": _jstr(_call(task, "getWBS", "")),
                "outline_level": outline,
                "name": name,
                "responsible": "",
                "start_date": start,
                "end_date": finish,
                "duration": round(duration, 2),
                "planned_progress": planned,
                "actual_progress": actual,
                "status": calc_progress_status(start, finish, planned, actual, status_date),
                "predecessor": _predecessors(task),
                "resource_names": _resources(task),
                "baseline_start": _iso_date(_call(task, "getBaselineStart")),
                "baseline_finish": _iso_date(_call(task, "getBaselineFinish")),
                "is_summary": _bool(_call(task, "getSummary", False)),
                "is_milestone": _bool(_call(task, "getMilestone", False)),
                "critical": _bool(_call(task, "getCritical", False)),
                "total_slack": round(slack, 2),
                "note": _jstr(_call(task, "getNotes", "")),
            })
        except Exception:
            # One malformed task should not prevent the rest of the project from loading.
            continue

    if not result:
        raise MppCloudError("File MPP đã mở nhưng không lấy được task hợp lệ có ID/UniqueID/Start/Finish.")

    # Fill project dates from actual task range if properties were blank.
    if not project_info["start_date"]:
        project_info["start_date"] = min(t["start_date"] for t in result)
    if not project_info["end_date"]:
        project_info["end_date"] = max(t["end_date"] for t in result)
    return project_info, result
