from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
import os


class MppReadError(RuntimeError):
    pass


def _safe_get(obj: Any, name: str, default=None):
    try:
        value = getattr(obj, name)
        return default if value is None else value
    except Exception:
        return default


def _to_iso_date(value: Any) -> str:
    """Convert Microsoft Project COM date values to YYYY-MM-DD.

    Project/pywin32 can return a Python datetime, a pywintypes Time object,
    an OLE Automation serial number, or a localized/formatted string.
    """
    if value is None:
        return ""

    # Native datetime/date and pywintypes TimeType usually land here.
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    # Some COM wrappers expose date()-like objects without subclassing datetime.
    try:
        v = value.date()
        if isinstance(v, datetime):
            return v.date().isoformat()
        if isinstance(v, date):
            return v.isoformat()
    except Exception:
        pass

    # OLE Automation DATE: days since 1899-12-30.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            # Project dates in modern files are safely within this broad range.
            if 1000 < float(value) < 100000:
                return (datetime(1899, 12, 30) + timedelta(days=float(value))).date().isoformat()
        except Exception:
            pass

    text = str(value).strip()
    if not text or text.upper() in {"NA", "N/A", "NONE", "NAT"}:
        return ""

    # Common Project/Windows date renderings, including the DD-MM-YYYY style
    # visible in the supplied MPP screenshot and 2-digit year/AM-PM variants.
    fmts = (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y",
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
        "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
        "%d-%m-%y %H:%M:%S", "%d-%m-%y", "%d/%m/%y %H:%M:%S", "%d/%m/%y",
        "%m/%d/%y %I:%M:%S %p", "%m/%d/%y %I:%M %p", "%m/%d/%y",
        "%a %m/%d/%y %I:%M:%S %p", "%a %m/%d/%y %I:%M %p", "%a %m/%d/%y",
    )
    for fmt in fmts:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass

    # Last pass: strip weekday prefixes and try only the leading date token.
    pieces = text.replace(',', ' ').split()
    for token in pieces:
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d-%m-%y", "%d/%m/%y", "%m/%d/%y"):
            try:
                return datetime.strptime(token, fmt).date().isoformat()
            except ValueError:
                pass
    return ""


def _to_float(value: Any, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _to_int(value: Any, default=0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return int(default)


def _planned_progress(start_s: str, finish_s: str, status_date: date) -> int:
    if not start_s or not finish_s:
        return 0
    try:
        start = datetime.strptime(start_s, "%Y-%m-%d").date()
        finish = datetime.strptime(finish_s, "%Y-%m-%d").date()
    except ValueError:
        return 0
    if finish < start or status_date < start:
        return 0
    if status_date >= finish:
        return 100
    total = max(1, (finish - start).days + 1)
    elapsed = max(0, (status_date - start).days + 1)
    return max(0, min(100, round(elapsed * 100 / total)))


def _calc_status(start_s: str, finish_s: str, planned: int, actual: int) -> str:
    today = date.today()
    if actual >= 100:
        return "Hoàn thành"
    try:
        start = datetime.strptime(start_s, "%Y-%m-%d").date()
        finish = datetime.strptime(finish_s, "%Y-%m-%d").date()
    except ValueError:
        return "Chưa xác định"
    if today < start and actual <= 0:
        return "Chưa bắt đầu"
    if today > finish and actual < 100:
        return "Chậm tiến độ"
    delta = int(actual) - int(planned)
    if delta < -1:
        return "Chậm tiến độ"
    if delta > 1:
        return "Nhanh tiến độ"
    return "Đúng tiến độ"


def _norm_path(value: Any) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(value)))
    except Exception:
        return str(value or "").strip().lower()


def _same_path(a: Any, b: Any) -> bool:
    if not a or not b:
        return False
    return _norm_path(a) == _norm_path(b)


def _com_error_text(exc: Exception) -> str:
    """Return useful information from pywintypes.com_error without hiding HRESULT."""
    parts = [str(exc)]
    hresult = getattr(exc, "hresult", None)
    if hresult is None and getattr(exc, "args", None):
        try:
            if isinstance(exc.args[0], int):
                hresult = exc.args[0]
        except Exception:
            pass
    if isinstance(hresult, int):
        unsigned = hresult & 0xFFFFFFFF
        parts.append(f"HRESULT: 0x{unsigned:08X} ({hresult})")
    strerror = getattr(exc, "strerror", None)
    if strerror:
        parts.append(f"COM: {strerror}")
    excepinfo = getattr(exc, "excepinfo", None)
    if excepinfo:
        try:
            desc = excepinfo[2]
            source = excepinfo[1]
            if source:
                parts.append(f"Nguồn: {source}")
            if desc:
                parts.append(f"Chi tiết: {desc}")
        except Exception:
            pass
    # Preserve order while removing duplicate lines.
    out = []
    for p in parts:
        if p and p not in out:
            out.append(p)
    return "\n".join(out)


def _find_open_project(app: Any, wanted_path: Path):
    """Find wanted MPP among projects already open in an MS Project instance."""
    try:
        projects = app.Projects
        count = int(projects.Count)
    except Exception:
        projects = None
        count = 0

    if projects is not None:
        for i in range(1, count + 1):
            try:
                p = projects.Item(i)
                full_name = _safe_get(p, "FullName", "")
                if _same_path(full_name, wanted_path):
                    return p
            except Exception:
                continue

    # Fallback: ActiveProject may not be visible in Projects collection in some states.
    try:
        p = app.ActiveProject
        if p is not None and _same_path(_safe_get(p, "FullName", ""), wanted_path):
            return p
    except Exception:
        pass
    return None


@dataclass
class MppTask:
    source_uid: int
    task_id: int
    wbs: str
    outline_level: int
    name: str
    start_date: str
    end_date: str
    duration: float
    planned_progress: int
    actual_progress: int
    status: str
    predecessor: str
    resource_names: str
    baseline_start: str
    baseline_finish: str
    is_summary: int
    is_milestone: int
    critical: int
    total_slack: float
    note: str

    def dict(self):
        return asdict(self)


class MppComReader:
    """Read .mpp through Microsoft Project desktop COM automation.

    V2.1 behavior:
    1. If Microsoft Project is already running and the selected file is open, attach to it.
    2. Otherwise try to create a separate COM instance with DispatchEx.
    3. If DispatchEx is not supported, fall back to Dispatch.
    4. Never close/quit a Project instance that was already open before QLDA connected.
    """

    PROG_ID = "MSProject.Application"

    def read(self, mpp_path: str | Path) -> dict:
        path = Path(mpp_path).expanduser().resolve()
        if not path.exists():
            raise MppReadError(f"Không tìm thấy file: {path}")
        if path.suffix.lower() not in {".mpp", ".mpt"}:
            raise MppReadError("Vui lòng chọn file Microsoft Project .mpp hoặc .mpt")
        if os.name != "nt":
            raise MppReadError("Đọc MPP qua COM chỉ hoạt động trên Windows có Microsoft Project desktop.")

        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore
        except ImportError as exc:
            raise MppReadError(
                "Chưa có pywin32 trong đúng môi trường Python đang chạy app.\n\n"
                "Chạy:\npython -m pip install --upgrade pywin32"
            ) from exc

        app = None
        project = None
        owned_app = False
        opened_by_us = False
        com_initialized = False
        dispatch_errors: list[str] = []

        try:
            # Explicit initialization makes COM robust when this reader is later moved to a worker thread.
            pythoncom.CoInitialize()
            com_initialized = True

            # A. Prefer attaching to an already-running Project instance.
            try:
                active_app = win32com.client.GetActiveObject(self.PROG_ID)
                project = _find_open_project(active_app, path)
                if project is not None:
                    app = active_app
            except Exception as exc:
                dispatch_errors.append("GetActiveObject: " + _com_error_text(exc))

            # B. No matching open file: create a separate Project instance.
            if app is None:
                try:
                    app = win32com.client.DispatchEx(self.PROG_ID)
                    owned_app = True
                except Exception as exc:
                    dispatch_errors.append("DispatchEx: " + _com_error_text(exc))

            # C. Some Project installations do not behave well with DispatchEx. Fall back to Dispatch.
            if app is None:
                try:
                    app = win32com.client.Dispatch(self.PROG_ID)
                    # If there was no active object this likely created Project; however, to avoid
                    # killing a user's instance unexpectedly we conservatively do not claim ownership.
                    owned_app = False
                except Exception as exc:
                    dispatch_errors.append("Dispatch: " + _com_error_text(exc))
                    detail = "\n\n".join(dispatch_errors[-3:])
                    raise MppReadError(
                        "Microsoft Project đang có trên máy nhưng QLDA không tạo/kết nối được COM Automation.\n\n"
                        "QLDA đã thử: Project đang mở → DispatchEx → Dispatch.\n\n"
                        f"Chi tiết kỹ thuật:\n{detail}\n\n"
                        "Cách kiểm tra nhanh:\n"
                        "1) Đóng toàn bộ Microsoft Project, mở lại file MPP rồi thử Đồng bộ.\n"
                        "2) Chạy file test_project_com.py đi kèm để xem HRESULT.\n"
                        "3) Nếu HRESULT là 0x80040154 (Class not registered), Repair Microsoft Project/Office."
                    ) from exc

            # Do not hide or alter the UI of a Project instance that belonged to the user.
            if owned_app:
                try:
                    app.Visible = False
                except Exception:
                    pass
                try:
                    app.DisplayAlerts = False
                except Exception:
                    pass

            # If the wanted file wasn't already open, open it read-only.
            if project is None:
                # Dispatch fallback may have connected to an existing app; check all open projects first.
                project = _find_open_project(app, path)

            if project is None:
                try:
                    app.FileOpen(Name=str(path), ReadOnly=True)
                    opened_by_us = True
                    project = app.ActiveProject
                except Exception as exc:
                    raise MppReadError(
                        f"Đã kết nối được Microsoft Project COM nhưng Project không mở được file:\n{path}\n\n"
                        f"{_com_error_text(exc)}"
                    ) from exc

            if project is None:
                raise MppReadError("Microsoft Project đã kết nối nhưng không trả về ActiveProject.")

            hours_per_day = _to_float(_safe_get(project, "HoursPerDay", 8), 8)
            if hours_per_day <= 0:
                hours_per_day = 8.0
            minutes_per_day = hours_per_day * 60.0

            project_start = _to_iso_date(_safe_get(project, "ProjectStart"))
            project_finish = _to_iso_date(_safe_get(project, "ProjectFinish"))
            project_name = str(_safe_get(project, "Name", path.stem) or path.stem)
            title = str(_safe_get(project, "Title", "") or "").strip()
            manager = str(_safe_get(project, "Manager", "") or "").strip()
            status_date_s = _to_iso_date(_safe_get(project, "StatusDate"))
            try:
                status_date = datetime.strptime(status_date_s, "%Y-%m-%d").date() if status_date_s else date.today()
            except ValueError:
                status_date = date.today()

            result_tasks: list[MppTask] = []
            tasks = project.Tasks
            scan = {
                "slots": int(tasks.Count),
                "nonempty": 0,
                "missing_id": 0,
                "missing_date": 0,
                "imported": 0,
                "uid_fallback": 0,
            }
            date_examples: list[str] = []

            for i in range(1, int(tasks.Count) + 1):
                try:
                    task = tasks.Item(i)
                except Exception:
                    continue
                if task is None:
                    continue

                task_id = _to_int(_safe_get(task, "ID", 0), 0)
                name = str(_safe_get(task, "Name", "") or "").strip()
                if not name:
                    continue
                scan["nonempty"] += 1

                # Microsoft Project Task exposes UniqueID (not UID). V2.1 used UID,
                # which evaluates to 0 through _safe_get and caused every real task
                # to be skipped before Start/Finish were even checked.
                uid = _to_int(_safe_get(task, "UniqueID", 0), 0)
                if uid <= 0:
                    uid = _to_int(_safe_get(task, "UID", 0), 0)  # compatibility fallback
                if uid <= 0 and task_id > 0:
                    uid = task_id
                    scan["uid_fallback"] += 1

                if uid <= 0 or task_id <= 0:
                    scan["missing_id"] += 1
                    continue

                start_raw = _safe_get(task, "Start")
                finish_raw = _safe_get(task, "Finish")
                start_s = _to_iso_date(start_raw)
                finish_s = _to_iso_date(finish_raw)
                if not start_s or not finish_s:
                    scan["missing_date"] += 1
                    if len(date_examples) < 3:
                        date_examples.append(
                            f"ID {task_id} - {name}: Start={start_raw!r}; Finish={finish_raw!r}"
                        )
                    continue

                baseline_start = _to_iso_date(_safe_get(task, "BaselineStart"))
                baseline_finish = _to_iso_date(_safe_get(task, "BaselineFinish"))
                plan_start = baseline_start or start_s
                plan_finish = baseline_finish or finish_s
                planned = _planned_progress(plan_start, plan_finish, status_date)
                actual = max(0, min(100, _to_int(_safe_get(task, "PercentComplete", 0), 0)))

                duration_min = _to_float(_safe_get(task, "Duration", 0), 0)
                duration_days = round(duration_min / minutes_per_day, 2) if duration_min > 0 else 0.0
                slack_min = _to_float(_safe_get(task, "TotalSlack", 0), 0)
                slack_days = round(slack_min / minutes_per_day, 2)

                wbs = str(_safe_get(task, "WBS", "") or "").strip()
                if not wbs:
                    wbs = str(_safe_get(task, "OutlineNumber", "") or "").strip()

                result_tasks.append(MppTask(
                    source_uid=uid,
                    task_id=task_id,
                    wbs=wbs,
                    outline_level=max(1, _to_int(_safe_get(task, "OutlineLevel", 1), 1)),
                    name=name,
                    start_date=start_s,
                    end_date=finish_s,
                    duration=duration_days,
                    planned_progress=planned,
                    actual_progress=actual,
                    status=_calc_status(start_s, finish_s, planned, actual),
                    predecessor=str(_safe_get(task, "Predecessors", "") or "").strip(),
                    resource_names=str(_safe_get(task, "ResourceNames", "") or "").strip(),
                    baseline_start=baseline_start,
                    baseline_finish=baseline_finish,
                    is_summary=1 if bool(_safe_get(task, "Summary", False)) else 0,
                    is_milestone=1 if bool(_safe_get(task, "Milestone", False)) else 0,
                    critical=1 if bool(_safe_get(task, "Critical", False)) else 0,
                    total_slack=slack_days,
                    note=str(_safe_get(task, "Notes", "") or "").strip(),
                ))
                scan["imported"] += 1

            if not result_tasks:
                details = (
                    f"Project có {scan['slots']} vị trí task; {scan['nonempty']} task có tên; "
                    f"{scan['missing_id']} task thiếu ID/UniqueID; {scan['missing_date']} task không chuyển được Start/Finish."
                )
                if date_examples:
                    details += "\n\nMẫu giá trị ngày COM:\n- " + "\n- ".join(date_examples)
                raise MppReadError(
                    "Đã mở được file MPP nhưng chưa nhập được công việc.\n\n"
                    + details
                    + "\n\nBản V2.2 đã sửa trường định danh Task.UniqueID và mở rộng bộ chuyển đổi ngày Project."
                )

            return {
                "path": str(path),
                "project_name": project_name,
                "title": title,
                "manager": manager,
                "project_start": project_start,
                "project_finish": project_finish,
                "status_date": status_date_s,
                "hours_per_day": hours_per_day,
                "diagnostics": scan,
                "tasks": [t.dict() for t in result_tasks],
            }
        finally:
            # Only close a file that QLDA itself opened.
            if app is not None and opened_by_us:
                try:
                    app.FileCloseEx(0)  # pjDoNotSave
                except Exception:
                    try:
                        app.FileClose()
                    except Exception:
                        pass
            # Only quit an application instance that QLDA itself created via DispatchEx.
            if app is not None and owned_app:
                try:
                    app.Quit()
                except Exception:
                    pass
            if com_initialized:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
