from __future__ import annotations

"""ERP theo dõi vật tư do Chủ đầu tư cấp.

Module này mở rộng màn hình Vật tư hiện có, không thay đổi luồng vật tư nhà thầu mua.
Tồn kho được tính từ sổ giao dịch, không cho sửa trực tiếp số tồn.
"""

from datetime import date, datetime
import inspect
from typing import Any

PATCH_MARKER = "V7.6 OWNER SUPPLIED MATERIAL ERP V1"
WAREHOUSE_TABLE = "owner_material_warehouses"
PLAN_TABLE = "owner_material_plans"
LEDGER_TABLE = "owner_material_ledger"

TXN_TYPES = (
    "CĐT_BÀN_GIAO",
    "CẤP_NHÀ_THẦU",
    "XÁC_NHẬN_LẮP_ĐẶT",
    "NHÀ_THẦU_TRẢ_KHO",
    "TRẢ_LẠI_CĐT",
    "ĐIỀU_CHUYỂN_KHO",
    "HAO_HỤT_HƯ_HỎNG_MẤT",
)
TXN_LABELS = {
    "CĐT_BÀN_GIAO": "CĐT bàn giao vào kho",
    "CẤP_NHÀ_THẦU": "Cấp cho nhà thầu",
    "XÁC_NHẬN_LẮP_ĐẶT": "Xác nhận đã lắp đặt",
    "NHÀ_THẦU_TRẢ_KHO": "Nhà thầu trả kho",
    "TRẢ_LẠI_CĐT": "Trả lại CĐT",
    "ĐIỀU_CHUYỂN_KHO": "Điều chuyển kho",
    "HAO_HỤT_HƯ_HỎNG_MẤT": "Hao hụt / hư hỏng / mất",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return float(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return int(default)


def _rowdict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {str(k): row[k] for k in row.keys()}
    except Exception:
        try:
            return dict(row)
        except Exception:
            return {}


def _actor_name(identity: Any) -> str:
    row = _rowdict(identity)
    return _text(row.get("name") or row.get("email") or "Người dùng")


def _resolve_scope(db, workspace_project_id: int) -> dict[str, Any]:
    pid = int(workspace_project_id)
    info = {
        "master_project_id": pid,
        "workspace_project_id": pid,
        "contractor_code": "",
        "contractor_name": "",
        "is_default": 1,
    }
    try:
        from qlda.runtime_core import contractor_workspace as cw
        with db.connect() as connection:
            cw.ensure_schema_connection(connection)
            row = connection.execute(
                f"SELECT master_project_id,workspace_project_id,contractor_code,contractor_name,is_default "
                f"FROM {cw.TABLE_NAME} WHERE workspace_project_id=? LIMIT 1",
                (pid,),
            ).fetchone()
        if row:
            info.update(_rowdict(row))
    except Exception:
        pass
    info["master_project_id"] = _int(info.get("master_project_id"), pid) or pid
    info["workspace_project_id"] = _int(info.get("workspace_project_id"), pid) or pid
    return info


def ensure_schema(db) -> None:
    with db.connect() as connection:
        connection.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {WAREHOUSE_TABLE}(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_project_id INTEGER NOT NULL,
                workspace_project_id INTEGER NOT NULL,
                warehouse_code TEXT NOT NULL,
                warehouse_name TEXT NOT NULL,
                location TEXT DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                UNIQUE(workspace_project_id, warehouse_code)
            );
            CREATE INDEX IF NOT EXISTS idx_owner_mat_wh_workspace
                ON {WAREHOUSE_TABLE}(workspace_project_id, active, id);

            CREATE TABLE IF NOT EXISTS {PLAN_TABLE}(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_project_id INTEGER NOT NULL,
                workspace_project_id INTEGER NOT NULL,
                material_code TEXT NOT NULL,
                material_name TEXT NOT NULL,
                unit TEXT DEFAULT '',
                boq_qty REAL NOT NULL DEFAULT 0,
                vo_qty REAL NOT NULL DEFAULT 0,
                planned_qty REAL NOT NULL DEFAULT 0,
                location TEXT DEFAULT '',
                contractor_code TEXT DEFAULT '',
                contractor_name TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                UNIQUE(workspace_project_id, material_code, location)
            );
            CREATE INDEX IF NOT EXISTS idx_owner_mat_plan_workspace
                ON {PLAN_TABLE}(workspace_project_id, material_code, id);

            CREATE TABLE IF NOT EXISTS {LEDGER_TABLE}(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                txn_code TEXT DEFAULT '',
                master_project_id INTEGER NOT NULL,
                workspace_project_id INTEGER NOT NULL,
                txn_type TEXT NOT NULL,
                txn_date TEXT NOT NULL,
                material_code TEXT NOT NULL,
                material_name TEXT NOT NULL,
                unit TEXT DEFAULT '',
                quantity REAL NOT NULL DEFAULT 0,
                warehouse_from TEXT DEFAULT '',
                warehouse_to TEXT DEFAULT '',
                contractor_code TEXT DEFAULT '',
                contractor_name TEXT DEFAULT '',
                location TEXT DEFAULT '',
                document_no TEXT DEFAULT '',
                task_ref TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_by TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                voided INTEGER NOT NULL DEFAULT 0,
                voided_by TEXT DEFAULT '',
                voided_at TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_owner_mat_ledger_workspace
                ON {LEDGER_TABLE}(workspace_project_id, voided, txn_date, id);
            CREATE INDEX IF NOT EXISTS idx_owner_mat_ledger_material
                ON {LEDGER_TABLE}(workspace_project_id, material_code, voided, id);
            """
        )


def list_warehouses(db, workspace_project_id: int) -> list[dict[str, Any]]:
    ensure_schema(db)
    with db.connect() as connection:
        return [
            _rowdict(r)
            for r in connection.execute(
                f"SELECT * FROM {WAREHOUSE_TABLE} WHERE workspace_project_id=? AND active=1 ORDER BY warehouse_code,id",
                (int(workspace_project_id),),
            ).fetchall()
        ]


def save_warehouse(db, workspace_project_id: int, code: str, name: str, location: str = "") -> None:
    scope = _resolve_scope(db, int(workspace_project_id))
    code, name = _text(code).upper(), _text(name)
    if not code or not name:
        raise ValueError("Mã kho và tên kho là bắt buộc.")
    ensure_schema(db)
    stamp = _now()
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT id FROM {WAREHOUSE_TABLE} WHERE workspace_project_id=? AND warehouse_code=? LIMIT 1",
            (int(workspace_project_id), code),
        ).fetchone()
        if row:
            rid = int(_rowdict(row).get("id") or row[0])
            connection.execute(
                f"UPDATE {WAREHOUSE_TABLE} SET warehouse_name=?,location=?,active=1,updated_at=? WHERE id=?",
                (name, _text(location), stamp, rid),
            )
        else:
            connection.execute(
                f"INSERT INTO {WAREHOUSE_TABLE}(master_project_id,workspace_project_id,warehouse_code,warehouse_name,location,active,created_at,updated_at) "
                "VALUES(?,?,?,?,?,1,?,?)",
                (
                    int(scope["master_project_id"]), int(workspace_project_id), code, name,
                    _text(location), stamp, stamp,
                ),
            )


def list_plans(db, workspace_project_id: int) -> list[dict[str, Any]]:
    ensure_schema(db)
    with db.connect() as connection:
        return [
            _rowdict(r)
            for r in connection.execute(
                f"SELECT * FROM {PLAN_TABLE} WHERE workspace_project_id=? ORDER BY material_code,location,id",
                (int(workspace_project_id),),
            ).fetchall()
        ]


def save_plan(db, workspace_project_id: int, data: dict[str, Any], plan_id: int | None = None) -> int:
    scope = _resolve_scope(db, int(workspace_project_id))
    code = _text(data.get("material_code")).upper()
    name = _text(data.get("material_name"))
    location = _text(data.get("location"))
    if not code or not name:
        raise ValueError("Mã vật tư và tên vật tư là bắt buộc.")
    boq = max(0.0, _float(data.get("boq_qty")))
    vo = _float(data.get("vo_qty"))
    planned = max(0.0, _float(data.get("planned_qty")))
    stamp = _now()
    ensure_schema(db)
    with db.connect() as connection:
        if plan_id:
            connection.execute(
                f"UPDATE {PLAN_TABLE} SET material_code=?,material_name=?,unit=?,boq_qty=?,vo_qty=?,planned_qty=?,location=?,"
                "contractor_code=?,contractor_name=?,note=?,updated_at=? WHERE id=? AND workspace_project_id=?",
                (
                    code, name, _text(data.get("unit")), boq, vo, planned, location,
                    _text(scope.get("contractor_code")), _text(scope.get("contractor_name")),
                    _text(data.get("note")), stamp, int(plan_id), int(workspace_project_id),
                ),
            )
            return int(plan_id)
        cur = connection.execute(
            f"INSERT INTO {PLAN_TABLE}(master_project_id,workspace_project_id,material_code,material_name,unit,boq_qty,vo_qty,planned_qty,location,contractor_code,contractor_name,note,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(scope["master_project_id"]), int(workspace_project_id), code, name,
                _text(data.get("unit")), boq, vo, planned, location,
                _text(scope.get("contractor_code")), _text(scope.get("contractor_name")),
                _text(data.get("note")), stamp, stamp,
            ),
        )
        return int(getattr(cur, "lastrowid", 0) or 0)


def list_ledger(db, workspace_project_id: int, *, include_voided: bool = False) -> list[dict[str, Any]]:
    ensure_schema(db)
    with db.connect() as connection:
        sql = f"SELECT * FROM {LEDGER_TABLE} WHERE workspace_project_id=?"
        if not include_voided:
            sql += " AND voided=0"
        sql += " ORDER BY txn_date DESC,id DESC"
        return [_rowdict(r) for r in connection.execute(sql, (int(workspace_project_id),)).fetchall()]


def _aggregate(ledger: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for row in ledger:
        if _int(row.get("voided")):
            continue
        code = _text(row.get("material_code")).upper()
        qty = max(0.0, _float(row.get("quantity")))
        rec = out.setdefault(code, {
            "received": 0.0, "issued": 0.0, "installed": 0.0,
            "returned_stock": 0.0, "returned_owner": 0.0, "loss": 0.0,
        })
        t = _text(row.get("txn_type"))
        if t == "CĐT_BÀN_GIAO": rec["received"] += qty
        elif t == "CẤP_NHÀ_THẦU": rec["issued"] += qty
        elif t == "XÁC_NHẬN_LẮP_ĐẶT": rec["installed"] += qty
        elif t == "NHÀ_THẦU_TRẢ_KHO": rec["returned_stock"] += qty
        elif t == "TRẢ_LẠI_CĐT": rec["returned_owner"] += qty
        elif t == "HAO_HỤT_HƯ_HỎNG_MẤT": rec["loss"] += qty
    return out


def stock_by_warehouse(ledger: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for row in ledger:
        if _int(row.get("voided")):
            continue
        code = _text(row.get("material_code")).upper()
        qty = max(0.0, _float(row.get("quantity")))
        t = _text(row.get("txn_type"))
        wf, wt = _text(row.get("warehouse_from")), _text(row.get("warehouse_to"))
        if t == "CĐT_BÀN_GIAO" and wt:
            out[(wt, code)] = out.get((wt, code), 0.0) + qty
        elif t == "CẤP_NHÀ_THẦU" and wf:
            out[(wf, code)] = out.get((wf, code), 0.0) - qty
        elif t == "NHÀ_THẦU_TRẢ_KHO" and wt:
            out[(wt, code)] = out.get((wt, code), 0.0) + qty
        elif t == "TRẢ_LẠI_CĐT" and wf:
            out[(wf, code)] = out.get((wf, code), 0.0) - qty
        elif t == "ĐIỀU_CHUYỂN_KHO":
            if wf: out[(wf, code)] = out.get((wf, code), 0.0) - qty
            if wt: out[(wt, code)] = out.get((wt, code), 0.0) + qty
    return out


def material_summary(db, workspace_project_id: int) -> list[dict[str, Any]]:
    plans = list_plans(db, int(workspace_project_id))
    ledger = list_ledger(db, int(workspace_project_id))
    agg = _aggregate(ledger)
    allowed: dict[str, float] = {}
    meta: dict[str, dict[str, str]] = {}
    for p in plans:
        code = _text(p.get("material_code")).upper()
        boq_vo = _float(p.get("boq_qty")) + _float(p.get("vo_qty"))
        plan = _float(p.get("planned_qty"))
        allowed[code] = allowed.get(code, 0.0) + max(0.0, boq_vo if boq_vo > 0 else plan)
        meta.setdefault(code, {"name": _text(p.get("material_name")), "unit": _text(p.get("unit"))})
    for r in ledger:
        code = _text(r.get("material_code")).upper()
        meta.setdefault(code, {"name": _text(r.get("material_name")), "unit": _text(r.get("unit"))})
    result = []
    for code in sorted(set(meta) | set(agg) | set(allowed)):
        a = agg.get(code, {})
        received = _float(a.get("received")); issued = _float(a.get("issued"))
        installed = _float(a.get("installed")); returned_stock = _float(a.get("returned_stock"))
        returned_owner = _float(a.get("returned_owner")); loss = _float(a.get("loss"))
        stock = received + returned_stock - issued - returned_owner
        holding = issued - installed - returned_stock - loss
        reconciliation = received - (stock + holding + installed + returned_owner + loss)
        limit = _float(allowed.get(code))
        result.append({
            "material_code": code, "material_name": meta.get(code, {}).get("name", ""),
            "unit": meta.get(code, {}).get("unit", ""), "allowed_qty": limit,
            "received": received, "issued": issued, "installed": installed,
            "holding": holding, "stock": stock, "loss": loss,
            "returned_owner": returned_owner,
            "over_limit": max(0.0, issued - limit) if limit > 0 else 0.0,
            "reconciliation": reconciliation,
        })
    return result


def _holding_map(ledger: list[dict[str, Any]]) -> dict[str, float]:
    return {
        code: a["issued"] - a["installed"] - a["returned_stock"] - a["loss"]
        for code, a in _aggregate(ledger).items()
    }


def _validate_transaction(db, workspace_project_id: int, txn_type: str, material_code: str, quantity: float, warehouse_from: str = "") -> None:
    ledger = list_ledger(db, int(workspace_project_id))
    code = _text(material_code).upper(); qty = max(0.0, float(quantity))
    if qty <= 0:
        raise ValueError("Số lượng phải lớn hơn 0.")
    if txn_type in {"CẤP_NHÀ_THẦU", "TRẢ_LẠI_CĐT", "ĐIỀU_CHUYỂN_KHO"}:
        wh_stock = stock_by_warehouse(ledger)
        available = wh_stock.get((_text(warehouse_from), code), 0.0)
        if qty > available + 1e-9:
            raise ValueError(f"Không đủ tồn kho tại kho xuất. Tồn khả dụng: {available:,.3f}.")
    if txn_type in {"XÁC_NHẬN_LẮP_ĐẶT", "NHÀ_THẦU_TRẢ_KHO", "HAO_HỤT_HƯ_HỎ_MẤT", "HAO_HỤT_HƯ_HỎNG_MẤT"}:
        available = _holding_map(ledger).get(code, 0.0)
        if qty > available + 1e-9:
            raise ValueError(f"Số lượng vượt vật tư nhà thầu đang giữ: {available:,.3f}.")


def add_transaction(db, workspace_project_id: int, data: dict[str, Any], *, actor: Any = None) -> dict[str, Any]:
    scope = _resolve_scope(db, int(workspace_project_id))
    txn_type = _text(data.get("txn_type")); code = _text(data.get("material_code")).upper()
    name = _text(data.get("material_name")); qty = max(0.0, _float(data.get("quantity")))
    wf, wt = _text(data.get("warehouse_from")), _text(data.get("warehouse_to"))
    if txn_type not in TXN_TYPES: raise ValueError("Loại giao dịch không hợp lệ.")
    if not code or not name: raise ValueError("Mã và tên vật tư là bắt buộc.")
    if txn_type == "CĐT_BÀN_GIAO" and not wt: raise ValueError("CĐT bàn giao phải chọn kho nhận.")
    if txn_type in {"CẤP_NHÀ_THẦU", "TRẢ_LẠI_CĐT"} and not wf: raise ValueError("Giao dịch này phải chọn kho xuất.")
    if txn_type == "NHÀ_THẦU_TRẢ_KHO" and not wt: raise ValueError("Nhà thầu trả kho phải chọn kho nhận.")
    if txn_type == "ĐIỀU_CHUYỂN_KHO" and (not wf or not wt or wf == wt):
        raise ValueError("Điều chuyển phải chọn hai kho khác nhau.")
    _validate_transaction(db, int(workspace_project_id), txn_type, code, qty, wf)
    ensure_schema(db); stamp = _now(); txn_date = _text(data.get("txn_date")) or date.today().isoformat()
    with db.connect() as connection:
        cur = connection.execute(
            f"INSERT INTO {LEDGER_TABLE}(txn_code,master_project_id,workspace_project_id,txn_type,txn_date,material_code,material_name,unit,quantity,warehouse_from,warehouse_to,contractor_code,contractor_name,location,document_no,task_ref,note,created_by,created_at,voided) "
            "VALUES('',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (
                int(scope["master_project_id"]), int(workspace_project_id), txn_type, txn_date,
                code, name, _text(data.get("unit")), qty, wf, wt,
                _text(scope.get("contractor_code")), _text(scope.get("contractor_name")),
                _text(data.get("location")), _text(data.get("document_no")), _text(data.get("task_ref")),
                _text(data.get("note")), _actor_name(actor), stamp,
            ),
        )
        rid = int(getattr(cur, "lastrowid", 0) or 0)
        if rid <= 0:
            row = connection.execute(
                f"SELECT id FROM {LEDGER_TABLE} WHERE workspace_project_id=? ORDER BY id DESC LIMIT 1",
                (int(workspace_project_id),),
            ).fetchone()
            rid = int(_rowdict(row).get("id") or (row[0] if row else 0))
        txn_code = f"VTCDT-{rid:06d}"
        connection.execute(f"UPDATE {LEDGER_TABLE} SET txn_code=? WHERE id=?", (txn_code, rid))
        return _rowdict(connection.execute(f"SELECT * FROM {LEDGER_TABLE} WHERE id=?", (rid,)).fetchone())


def void_transaction(db, workspace_project_id: int, txn_id: int, *, actor: Any = None) -> None:
    ensure_schema(db)
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT id,voided FROM {LEDGER_TABLE} WHERE id=? AND workspace_project_id=? LIMIT 1",
            (int(txn_id), int(workspace_project_id)),
        ).fetchone()
        if not row: raise ValueError("Không tìm thấy giao dịch.")
        if _int(_rowdict(row).get("voided")): return
        connection.execute(
            f"UPDATE {LEDGER_TABLE} SET voided=1,voided_by=?,voided_at=? WHERE id=?",
            (_actor_name(actor), _now(), int(txn_id)),
        )


def _material_catalog(db, pid: int, plans: list[dict[str, Any]], ledger: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    try:
        for r in db.materials(pid) or []:
            row = _rowdict(r)
            if _text(row.get("supply_type")) != "CĐT cung cấp": continue
            code = _text(row.get("material_code")).upper()
            if code: out[code] = {"name": _text(row.get("material_name")), "unit": ""}
    except Exception:
        pass
    for source in (plans, ledger):
        for row in source:
            code = _text(row.get("material_code")).upper()
            if not code: continue
            rec = out.setdefault(code, {"name": "", "unit": ""})
            rec["name"] = rec["name"] or _text(row.get("material_name"))
            rec["unit"] = rec["unit"] or _text(row.get("unit"))
    return out


def _fmt_qty(value: Any) -> str:
    return f"{_float(value):,.3f}".rstrip("0").rstrip(".")


def _render_files(st, *, db, pid: int, gateway: Any, token: str, transaction: dict[str, Any], can_update: bool) -> None:
    if gateway is None or not token:
        st.caption("Chưa có phiên lưu trữ VPS để đính kèm chứng từ.")
        return
    project = db.project(int(pid)); record_code = _text(transaction.get("txn_code"))
    subtype = _text(transaction.get("txn_type")) or "OWNER_MATERIAL"
    if not project or not record_code: return
    key = f"owner_mat_files_{pid}_{transaction.get('id')}"
    st.markdown("##### 📎 Chứng từ đính kèm trên VPS")
    files = st.file_uploader(
        "Biên bản bàn giao, phiếu cấp/trả, hình ảnh hoặc chứng từ liên quan",
        accept_multiple_files=True, key=key + "_up", disabled=not can_update,
    )
    if st.button("⬆️ Tải file lên VPS", key=key + "_btn", disabled=(not can_update or not files), use_container_width=True):
        uploaded, errors = 0, []
        for f in files or []:
            try:
                gateway.upload_bytes(
                    token, project_code=project["code"], kind="owner_material", subtype=subtype,
                    record_code=record_code, name=f.name, content=f.getvalue(),
                    mime_type=getattr(f, "type", "") or "application/octet-stream",
                )
                uploaded += 1
            except Exception as exc:
                errors.append(f"{f.name}: {exc}")
        if uploaded: st.success(f"Đã tải {uploaded} file lên VPS.")
        if errors: st.error("Một số file chưa tải được: " + " | ".join(errors[:3]))
        if uploaded: st.rerun()
    try:
        try:
            data = gateway.list_record_files(
                token, project_code=project["code"], kind="owner_material", subtype=subtype,
                record_code=record_code, include_history=False,
            ) or {}
        except TypeError:
            data = gateway.list_record_files(
                token, project_code=project["code"], kind="owner_material", subtype=subtype,
                record_code=record_code,
            ) or {}
        items = data.get("files") or []
    except Exception as exc:
        st.warning(f"Chưa đọc được file đính kèm: {exc}"); items = []
    if not items:
        st.caption("Chưa có file đính kèm."); return
    for item in items:
        name = _text(item.get("name")) or "File"
        size = _float(item.get("size")); suffix = f" · {size/1024:,.1f} KB" if size else ""
        st.write(f"📄 **{name}**{suffix}")
        c1, c2 = st.columns(2)
        open_url = _text(item.get("open_url") or item.get("web_url"))
        download_url = _text(item.get("download_url"))
        if open_url: c1.link_button("👁 Xem", open_url, use_container_width=True)
        if download_url: c2.link_button("⬇️ Tải", download_url, use_container_width=True)


def render_owner_supplied_materials(st, db, workspace_project_id: int, *, identity: Any = None,
                                    can_update: bool = False, is_admin: bool = False,
                                    gateway: Any = None, session_token: str = "") -> None:
    import pandas as pd
    pid = int(workspace_project_id); ensure_schema(db); scope = _resolve_scope(db, pid)
    contractor_label = " - ".join(x for x in (
        _text(scope.get("contractor_code")), _text(scope.get("contractor_name"))) if x) or "Workspace mặc định"
    with st.expander("🏢 ERP vật tư CĐT cấp", expanded=True):
        st.caption(f"Workspace: {contractor_label}. Tồn kho được tính tự động từ giao dịch và không được sửa tay.")
        tabs = st.tabs(["Tổng quan", "Kế hoạch cấp", "Giao nhận & sử dụng", "Kho", "Đối chiếu / quyết toán"])
        plans = list_plans(db, pid); ledger = list_ledger(db, pid)
        summary = material_summary(db, pid); warehouses = list_warehouses(db, pid)
        catalog = _material_catalog(db, pid, plans, ledger)

        with tabs[0]:
            totals = {k: sum(_float(r.get(k)) for r in summary) for k in ("received", "issued", "installed", "holding", "stock")}
            c1,c2,c3,c4,c5 = st.columns(5)
            c1.metric("CĐT đã giao", _fmt_qty(totals["received"])); c2.metric("Đã cấp nhà thầu", _fmt_qty(totals["issued"]))
            c3.metric("Đã lắp đặt", _fmt_qty(totals["installed"])); c4.metric("Nhà thầu đang giữ", _fmt_qty(totals["holding"]))
            c5.metric("Tồn kho", _fmt_qty(totals["stock"]))
            alerts=[]
            for r in summary:
                if _float(r.get("over_limit")) > 1e-9: alerts.append(f"{r['material_code']}: cấp vượt BOQ/VO {_fmt_qty(r['over_limit'])} {r.get('unit','')}")
                if _float(r.get("stock")) < -1e-9: alerts.append(f"{r['material_code']}: tồn kho âm {_fmt_qty(r['stock'])}")
                if abs(_float(r.get("reconciliation"))) > 1e-6: alerts.append(f"{r['material_code']}: lệch đối chiếu {_fmt_qty(r['reconciliation'])}")
            if alerts: st.warning("⚠️ Cảnh báo ERP\n\n" + "\n\n".join(f"• {x}" for x in alerts[:20]))
            elif summary: st.success("Không phát hiện chênh lệch hoặc cấp vượt BOQ/VO trong dữ liệu hiện hành.")
            if summary:
                st.dataframe(pd.DataFrame([{
                    "Mã vật tư":r["material_code"],"Tên":r["material_name"],"ĐVT":r["unit"],"BOQ+VO/KH":r["allowed_qty"],
                    "CĐT đã giao":r["received"],"Đã cấp NT":r["issued"],"Đã lắp":r["installed"],"NT đang giữ":r["holding"],
                    "Tồn kho":r["stock"],"Hao hụt/mất":r["loss"],"Trả CĐT":r["returned_owner"],"Cấp vượt":r["over_limit"]
                } for r in summary]), hide_index=True, use_container_width=True)

        with tabs[1]:
            st.markdown("#### Kế hoạch vật tư CĐT cấp theo BOQ / VO")
            ids=[0]+[int(p["id"]) for p in plans]
            selected=st.selectbox("Chọn kế hoạch để sửa", ids,
                format_func=lambda x:"➕ Thêm mới" if x==0 else next((f"#{x} · {p['material_code']} · {p.get('location','')}" for p in plans if int(p['id'])==x),str(x)),
                key=f"owner_mat_plan_sel_{pid}")
            rec=next((p for p in plans if int(p["id"])==int(selected)),None); codes=[""]+sorted(catalog)
            with st.form(f"owner_mat_plan_form_{pid}_{selected}"):
                c1,c2=st.columns(2); default_code=_text(rec.get("material_code")) if rec else ""
                choice=c1.selectbox("Mã vật tư trong danh mục CĐT cấp",codes,index=codes.index(default_code) if default_code in codes else 0)
                manual=c2.text_input("Hoặc nhập mã vật tư",value=default_code if default_code not in codes else ""); code=_text(choice or manual).upper()
                c3,c4=st.columns(2); name=c3.text_input("Tên vật tư *",value=_text(rec.get("material_name")) if rec else _text(catalog.get(code,{}).get("name")))
                unit=c4.text_input("Đơn vị",value=_text(rec.get("unit")) if rec else _text(catalog.get(code,{}).get("unit")))
                c5,c6,c7=st.columns(3); boq=c5.number_input("Khối lượng BOQ",min_value=0.0,value=_float(rec.get("boq_qty")) if rec else 0.0,step=1.0)
                vo=c6.number_input("VO điều chỉnh (+/-)",value=_float(rec.get("vo_qty")) if rec else 0.0,step=1.0)
                planned=c7.number_input("Kế hoạch CĐT cấp",min_value=0.0,value=_float(rec.get("planned_qty")) if rec else 0.0,step=1.0)
                location=st.text_input("Khu vực / hạng mục sử dụng",value=_text(rec.get("location")) if rec else "")
                note=st.text_area("Ghi chú",value=_text(rec.get("note")) if rec else "")
                save=st.form_submit_button("💾 Lưu kế hoạch",type="primary",disabled=not can_update,use_container_width=True)
            if save:
                try:
                    save_plan(db,pid,{"material_code":code,"material_name":name,"unit":unit,"boq_qty":boq,"vo_qty":vo,"planned_qty":planned,"location":location,"note":note},int(selected) if selected else None)
                    st.success("Đã lưu kế hoạch vật tư CĐT cấp."); st.rerun()
                except Exception as exc: st.error(str(exc))
            if plans:
                st.dataframe(pd.DataFrame([{"Mã":p["material_code"],"Tên":p["material_name"],"ĐVT":p["unit"],"BOQ":p["boq_qty"],"VO":p["vo_qty"],"Kế hoạch cấp":p["planned_qty"],"Khu vực":p["location"],"Ghi chú":p["note"]} for p in plans]),hide_index=True,use_container_width=True)

        with tabs[2]:
            st.markdown("#### Giao nhận → cấp nhà thầu → lắp đặt → trả / hao hụt")
            wh_codes=[w["warehouse_code"] for w in warehouses]; material_codes=sorted(catalog)
            with st.form(f"owner_mat_txn_form_{pid}",clear_on_submit=True):
                c1,c2,c3=st.columns(3); txn_type=c1.selectbox("Loại giao dịch",list(TXN_TYPES),format_func=lambda x:TXN_LABELS[x])
                txn_date=c2.date_input("Ngày giao dịch",value=date.today()); quantity=c3.number_input("Số lượng",min_value=0.0,value=0.0,step=1.0)
                c4,c5=st.columns(2); choice=c4.selectbox("Mã vật tư",[""]+material_codes); manual=c5.text_input("Hoặc nhập mã mới"); code=_text(choice or manual).upper()
                c6,c7=st.columns(2); material_name=c6.text_input("Tên vật tư *",value=_text(catalog.get(code,{}).get("name"))); unit=c7.text_input("Đơn vị",value=_text(catalog.get(code,{}).get("unit")))
                c8,c9=st.columns(2); warehouse_from=c8.selectbox("Kho xuất",[""]+wh_codes); warehouse_to=c9.selectbox("Kho nhận",[""]+wh_codes)
                c10,c11=st.columns(2); document_no=c10.text_input("Số biên bản / phiếu"); task_ref=c11.text_input("Mã Task / BOQ / WBS liên quan")
                location=st.text_input("Vị trí / hạng mục thi công"); note=st.text_area("Ghi chú / lý do")
                submit=st.form_submit_button("➕ Ghi nhận giao dịch",type="primary",disabled=not can_update,use_container_width=True)
            if submit:
                try:
                    created=add_transaction(db,pid,{"txn_type":txn_type,"txn_date":txn_date.isoformat(),"material_code":code,"material_name":material_name,"unit":unit,"quantity":quantity,"warehouse_from":warehouse_from,"warehouse_to":warehouse_to,"document_no":document_no,"task_ref":task_ref,"location":location,"note":note},actor=identity)
                    st.session_state[f"owner_mat_txn_selected_{pid}"]=int(created.get("id") or 0); st.success(f"Đã ghi nhận {created.get('txn_code')}."); st.rerun()
                except Exception as exc: st.error(str(exc))
            active=list_ledger(db,pid)
            if active:
                st.dataframe(pd.DataFrame([{"Mã GD":r["txn_code"],"Ngày":r["txn_date"],"Loại":TXN_LABELS.get(r["txn_type"],r["txn_type"]),"Mã VT":r["material_code"],"Tên":r["material_name"],"SL":r["quantity"],"ĐVT":r["unit"],"Kho đi":r["warehouse_from"],"Kho đến":r["warehouse_to"],"Chứng từ":r["document_no"],"Vị trí":r["location"],"Người ghi":r["created_by"]} for r in active]),hide_index=True,use_container_width=True)
                by_id={int(r["id"]):r for r in active}; ids=list(by_id); current=_int(st.session_state.get(f"owner_mat_txn_selected_{pid}"),ids[0]); current=current if current in by_id else ids[0]
                selected_id=st.selectbox("Mở giao dịch / chứng từ",ids,index=ids.index(current),format_func=lambda rid:f"{by_id[rid]['txn_code']} · {TXN_LABELS.get(by_id[rid]['txn_type'],by_id[rid]['txn_type'])} · {by_id[rid]['material_code']}",key=f"owner_mat_txn_open_{pid}")
                selected_txn=by_id[int(selected_id)]; _render_files(st,db=db,pid=pid,gateway=gateway,token=session_token,transaction=selected_txn,can_update=can_update)
                if is_admin:
                    with st.expander("🧾 Điều chỉnh sai sót",expanded=False):
                        st.warning("ERP không xóa lịch sử. Giao dịch sai được hủy và tự loại khỏi tồn/đối chiếu.")
                        confirm=st.checkbox(f"Xác nhận hủy {selected_txn['txn_code']}",key=f"owner_mat_void_c_{pid}_{selected_id}")
                        if st.button("🚫 Hủy giao dịch",disabled=not confirm,key=f"owner_mat_void_{pid}_{selected_id}",use_container_width=True):
                            try: void_transaction(db,pid,int(selected_id),actor=identity); st.success("Đã hủy giao dịch."); st.rerun()
                            except Exception as exc: st.error(str(exc))

        with tabs[3]:
            st.markdown("#### Danh mục kho")
            with st.form(f"owner_mat_wh_form_{pid}",clear_on_submit=True):
                c1,c2=st.columns(2); wh_code=c1.text_input("Mã kho *",placeholder="VD: KHO-E3-TONG"); wh_name=c2.text_input("Tên kho *",placeholder="Kho tổng dự án")
                wh_location=st.text_input("Vị trí kho"); save_wh=st.form_submit_button("➕ Thêm / cập nhật kho",type="primary",disabled=not can_update,use_container_width=True)
            if save_wh:
                try: save_warehouse(db,pid,wh_code,wh_name,wh_location); st.success("Đã lưu kho."); st.rerun()
                except Exception as exc: st.error(str(exc))
            if warehouses: st.dataframe(pd.DataFrame([{"Mã kho":w["warehouse_code"],"Tên kho":w["warehouse_name"],"Vị trí":w["location"]} for w in warehouses]),hide_index=True,use_container_width=True)
            wh_stock=stock_by_warehouse(ledger)
            if wh_stock:
                names={s["material_code"]:s["material_name"] for s in summary}; units={s["material_code"]:s["unit"] for s in summary}
                st.markdown("#### Tồn theo kho")
                st.dataframe(pd.DataFrame([{"Kho":wh,"Mã vật tư":code,"Tên":names.get(code,""),"ĐVT":units.get(code,""),"Tồn":qty} for (wh,code),qty in sorted(wh_stock.items())]),hide_index=True,use_container_width=True)

        with tabs[4]:
            st.markdown("#### Đối chiếu & quyết toán vật tư CĐT cấp")
            st.caption("CĐT đã giao = Tồn kho + Nhà thầu đang giữ + Đã lắp đặt + Hao hụt/mất + Đã trả CĐT.")
            if not summary: st.info("Chưa có dữ liệu để đối chiếu.")
            else:
                recon=pd.DataFrame([{"Mã vật tư":r["material_code"],"Tên":r["material_name"],"ĐVT":r["unit"],"CĐT đã giao":r["received"],"Tồn kho":r["stock"],"NT đang giữ":r["holding"],"Đã lắp đặt":r["installed"],"Hao hụt/mất":r["loss"],"Đã trả CĐT":r["returned_owner"],"Chênh lệch":r["reconciliation"],"BOQ+VO/KH":r["allowed_qty"],"Cấp vượt":r["over_limit"]} for r in summary])
                st.dataframe(recon,hide_index=True,use_container_width=True)
                st.download_button("⬇️ Xuất CSV đối chiếu",data=recon.to_csv(index=False).encode("utf-8-sig"),file_name=f"doi_chieu_vat_tu_CDT_{pid}_{date.today():%Y%m%d}.csv",mime="text/csv",use_container_width=True)


def install_owner_supplied_material_erp() -> None:
    import streamlit as st
    if getattr(st,"_qlda_owner_material_erp_installed",False): return
    original_subheader=st.subheader
    def subheader_with_erp(body,*args,**kwargs):
        result=original_subheader(body,*args,**kwargs)
        if _text(body)!="📦 Vật tư & thiết bị": return result
        frame=inspect.currentframe(); caller=frame.f_back if frame else None
        try:
            if caller is None or caller.f_code.co_name!="render_material_management": return result
            glb,loc=caller.f_globals,caller.f_locals; db=glb.get("db"); pid=_int(loc.get("pid"))
            if db is None or pid<=0: return result
            can_update_fn=glb.get("_can_update"); is_admin_fn=glb.get("_is_admin"); identity_fn=glb.get("_cloud_identity")
            gateway_fn=glb.get("_drive_gateway"); token_fn=glb.get("_gateway_session_token")
            render_owner_supplied_materials(
                st,db,pid,
                identity=identity_fn() if callable(identity_fn) else None,
                can_update=bool(can_update_fn()) if callable(can_update_fn) else False,
                is_admin=bool(is_admin_fn()) if callable(is_admin_fn) else False,
                gateway=gateway_fn() if callable(gateway_fn) else None,
                session_token=_text(token_fn()) if callable(token_fn) else "",
            )
        except Exception as exc:
            st.warning(f"ERP vật tư CĐT cấp chưa khởi tạo được: {exc}")
        finally:
            del caller; del frame
        return result
    st.subheader=subheader_with_erp
    st._qlda_owner_material_erp_installed=True
    st._qlda_owner_material_erp_marker=PATCH_MARKER
    try:
        import qlda.runtime_core.project_database as pg
        order=list(pg.TABLE_ORDER)
        for table in (WAREHOUSE_TABLE,PLAN_TABLE,LEDGER_TABLE):
            if table not in order: order.append(table)
        pg.TABLE_ORDER=tuple(order); pg._ID_TABLES=set(pg.TABLE_ORDER)
    except Exception:
        pass


__all__=["ensure_schema","list_warehouses","save_warehouse","list_plans","save_plan","list_ledger","add_transaction","void_transaction","material_summary","render_owner_supplied_materials","install_owner_supplied_material_erp"]
