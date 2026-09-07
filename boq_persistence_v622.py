from __future__ import annotations

import base64
import gzip
import json
import math
from datetime import datetime
from numbers import Real
from typing import Any


TABLE_NAME = "boq_excel_workbooks"
PAYLOAD_PREFIX = "gz1:"


def format_table_number(value: Any) -> Any:
    """Format numeric cells with thousands separators without changing text cells."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return value
    number = float(value)
    if not math.isfinite(number):
        return value
    if abs(number - round(number)) < 1e-9:
        return f"{number:,.0f}"
    text = f"{number:,.4f}".rstrip("0").rstrip(".")
    return text


def _ensure_table(connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME}(
            project_id INTEGER PRIMARY KEY,
            filename TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _encode_result(result: dict[str, Any]) -> str:
    raw = json.dumps(
        result,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    packed = gzip.compress(raw, compresslevel=6)
    return PAYLOAD_PREFIX + base64.b64encode(packed).decode("ascii")


def _decode_result(payload: str) -> dict[str, Any]:
    text = str(payload or "")
    if not text.startswith(PAYLOAD_PREFIX):
        raise ValueError("Định dạng dữ liệu BOQ đã lưu không hợp lệ.")
    packed = base64.b64decode(text[len(PAYLOAD_PREFIX):].encode("ascii"))
    data = json.loads(gzip.decompress(packed).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Dữ liệu BOQ đã lưu không phải workbook hợp lệ.")
    return data


def save_saved_boq_workbook(db, project_id: int, result: dict[str, Any]) -> dict[str, Any]:
    """Persist the parsed workbook at project scope so every authorized account sees it."""
    pid = int(project_id)
    filename = str(result.get("filename") or "BOQ.xlsx")
    batch_id = str(result.get("batch_id") or "")
    payload = _encode_result(result)
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with db.connect() as connection:
        _ensure_table(connection)
        connection.execute(f"DELETE FROM {TABLE_NAME} WHERE project_id=?", (pid,))
        connection.execute(
            f"INSERT INTO {TABLE_NAME}(project_id,filename,batch_id,payload,updated_at) VALUES(?,?,?,?,?)",
            (pid, filename, batch_id, payload, updated_at),
        )

    return {
        "project_id": pid,
        "filename": filename,
        "batch_id": batch_id,
        "updated_at": updated_at,
        "payload_chars": len(payload),
    }


def load_saved_boq_workbook(db, project_id: int) -> dict[str, Any] | None:
    """Load the latest saved workbook for a project, independent of user/session."""
    pid = int(project_id)
    with db.connect() as connection:
        _ensure_table(connection)
        row = connection.execute(
            f"SELECT filename,batch_id,payload,updated_at FROM {TABLE_NAME} WHERE project_id=?",
            (pid,),
        ).fetchone()

    if row is None:
        return None

    try:
        payload = row["payload"]
        filename = row["filename"]
        batch_id = row["batch_id"]
        updated_at = row["updated_at"]
    except Exception:
        filename, batch_id, payload, updated_at = row[0], row[1], row[2], row[3]

    result = _decode_result(str(payload))
    result["filename"] = str(result.get("filename") or filename or "BOQ.xlsx")
    result["batch_id"] = str(result.get("batch_id") or batch_id or "")
    result["_persisted"] = True
    result["_persisted_at"] = str(updated_at or "")
    return result


def delete_saved_boq_workbook(db, project_id: int) -> int:
    pid = int(project_id)
    with db.connect() as connection:
        _ensure_table(connection)
        cur = connection.execute(f"DELETE FROM {TABLE_NAME} WHERE project_id=?", (pid,))
    try:
        return max(0, int(cur.rowcount or 0))
    except Exception:
        return 0
