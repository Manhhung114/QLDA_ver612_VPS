from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


PATCH_MARKER = "V6.22 BOQ AI FULLSCAN V1"
AUTO_NOTE_PREFIX = "[QLDA_BOQ_EXCEL]"


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


def _num(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except Exception:
        text = str(value).strip().replace(" ", "")
        if not text:
            return None
        text = re.sub(r"[^0-9,\.\-]", "", text)
        if not text:
            return None
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            parts = text.split(",")
            if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
                text = "".join(parts)
            else:
                text = text.replace(",", ".")
        try:
            return float(text)
        except Exception:
            return None


def _detect_split_price_cols_full(snapshot: dict[str, Any]) -> dict[str, int]:
    """Find Đơn giá -> Vật tư/Vật liệu + Nhân công in the saved sheet snapshot.

    Unlike the older recovery path, this scans the first 120 Excel rows so a
    detail sheet whose real header starts below row 30 is still included in the
    project-wide material/labor total.
    """
    import boq_claim_price_recovery_v622 as recovery

    rows = recovery._snapshot_rows(snapshot)
    if not rows:
        return {}

    scan_limit = 120
    scan = sorted(row_no for row_no in rows if row_no <= scan_limit)
    if not scan:
        return {}

    material: int | None = None
    labor: int | None = None

    # Direct one-cell headings: "Đơn giá vật tư", "Đơn giá nhân công".
    for row_no in scan:
        values = rows.get(row_no) or []
        for index, value in enumerate(values):
            text = recovery._norm(value)
            if "don gia" not in text:
                continue
            if "vat tu" in text or "vat lieu" in text or "material" in text:
                material = index
            if "nhan cong" in text or "labor" in text or "labour" in text:
                labor = index
        if material is not None and labor is not None:
            return {"material_unit_price": material, "labor_unit_price": labor}

    # Grouped multi-row headings, including merged parent cells.
    last_scanned = max(scan)
    for row_no in scan:
        parent = rows.get(row_no) or []
        for start, value in enumerate(parent):
            if "don gia" not in recovery._norm(value):
                continue
            end = recovery._next_nonempty(parent, start)
            end = max(start + 2, end)
            for child_row_no in range(row_no + 1, min(row_no + 5, last_scanned + 1)):
                child = rows.get(child_row_no) or []
                for index in range(start, min(end, len(child))):
                    text = recovery._norm(child[index])
                    if text in {"vat tu", "vat lieu", "material"} or "vat tu" in text or "vat lieu" in text:
                        material = index
                    if text in {"nhan cong", "labor", "labour"} or "nhan cong" in text:
                        labor = index
            if material is not None and labor is not None:
                return {"material_unit_price": material, "labor_unit_price": labor}

    return {}


def _load_saved_workbook(connection, project_id: int) -> dict[str, Any] | None:
    try:
        row = connection.execute(
            "SELECT payload FROM boq_excel_workbooks WHERE project_id=?",
            (int(project_id),),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    data = _rowdict(row)
    try:
        payload = data.get("payload") if data else row[0]
        import boq_persistence_v622 as persistence
        return persistence._decode_result(str(payload or ""))
    except Exception:
        return None


def fullscan_boq_component_totals(connection, project_id: int) -> dict[str, Any]:
    """Aggregate BOQ material/labor directly from every saved Excel detail row.

    The LLM context row cap is deliberately not involved in this calculation.
    Database rows are used only as the authoritative list of imported detail
    lines and their quantities; unit prices are read again from the saved Excel
    snapshot by sheet + original row number.
    """
    import boq_claim_price_recovery_v622 as recovery

    saved = _load_saved_workbook(connection, int(project_id))
    if not saved:
        return {"ok": False, "reason": "no_saved_workbook"}

    workbook_sheets = saved.get("workbook_sheets")
    if not isinstance(workbook_sheets, dict):
        return {"ok": False, "reason": "no_workbook_sheets"}

    sources: dict[str, tuple[dict[str, int], dict[int, list[Any]]]] = {}
    truncated_sheets: list[str] = []
    for name, snapshot in workbook_sheets.items():
        if not isinstance(snapshot, dict):
            continue
        mapping = _detect_split_price_cols_full(snapshot)
        if mapping:
            sources[str(name)] = (mapping, recovery._snapshot_rows(snapshot))
        if bool(snapshot.get("truncated")):
            truncated_sheets.append(str(name))

    try:
        raw_rows = connection.execute(
            """SELECT id,quantity,note
               FROM cost_budgets
               WHERE project_id=? AND note LIKE ?
               ORDER BY id""",
            (int(project_id), AUTO_NOTE_PREFIX + "%"),
        ).fetchall()
    except Exception:
        return {"ok": False, "reason": "boq_rows_unavailable"}

    total_rows = len(raw_rows)
    scanned_rows = 0
    missing_source_rows = 0
    empty_component_rows = 0
    material_numeric_rows = 0
    labor_numeric_rows = 0
    material_total = 0.0
    labor_total = 0.0
    by_sheet: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"rows": 0, "material": 0.0, "labor": 0.0, "missing": 0}
    )

    for raw in raw_rows:
        row = _rowdict(raw)
        if not row:
            try:
                row = {"id": raw[0], "quantity": raw[1], "note": raw[2]}
            except Exception:
                missing_source_rows += 1
                continue

        parsed = recovery._parse_boq_note(row.get("note"))
        if not parsed:
            missing_source_rows += 1
            continue
        sheet_name, row_no = parsed
        bucket = by_sheet[sheet_name]
        bucket["rows"] += 1

        source = sources.get(sheet_name)
        if source is None:
            missing_source_rows += 1
            bucket["missing"] += 1
            continue
        mapping, source_rows = source
        cells = source_rows.get(int(row_no))
        if cells is None:
            missing_source_rows += 1
            bucket["missing"] += 1
            continue

        scanned_rows += 1
        qty = _num(row.get("quantity")) or 0.0
        material_price = _num(recovery._cell(cells, mapping.get("material_unit_price")))
        labor_price = _num(recovery._cell(cells, mapping.get("labor_unit_price")))

        # A blank component cell in an otherwise mapped Excel detail row is zero,
        # not a reason to discard the row from the project-wide scan.
        if material_price is not None:
            material_numeric_rows += 1
        if labor_price is not None:
            labor_numeric_rows += 1
        if material_price is None and labor_price is None:
            empty_component_rows += 1

        material_cost = qty * float(material_price or 0.0)
        labor_cost = qty * float(labor_price or 0.0)
        material_total += material_cost
        labor_total += labor_cost
        bucket["material"] += material_cost
        bucket["labor"] += labor_cost

    sheet_rows = [
        {
            "sheet": name,
            "rows": int(values["rows"]),
            "material_total": float(values["material"]),
            "labor_total": float(values["labor"]),
            "missing_rows": int(values["missing"]),
        }
        for name, values in by_sheet.items()
    ]
    sheet_rows.sort(key=lambda item: item["sheet"])

    complete = total_rows > 0 and scanned_rows == total_rows and not truncated_sheets
    return {
        "ok": total_rows > 0 and scanned_rows > 0,
        "complete": complete,
        "total_rows": total_rows,
        "scanned_rows": scanned_rows,
        "missing_source_rows": missing_source_rows,
        "empty_component_rows": empty_component_rows,
        "material_numeric_rows": material_numeric_rows,
        "labor_numeric_rows": labor_numeric_rows,
        "material_total": float(material_total),
        "labor_total": float(labor_total),
        "component_sheet_count": len(sources),
        "saved_sheet_count": len(workbook_sheets),
        "truncated_sheets": truncated_sheets,
        "by_sheet": sheet_rows,
    }


def _component_total_intent(ai, question: str) -> bool:
    q = ai._norm(question)
    has_component = any(word in q for word in ("vat tu", "vat lieu", "nhan cong", "material", "labor", "labour"))
    has_total = any(word in q for word in ("tong", "chi phi", "gia tri", "du toan", "boq"))
    return has_component and has_total


def install_boq_ai_fullscan() -> None:
    """Make full-workbook BOQ component totals authoritative for AI answers."""
    import ai_live_context_v622 as ai

    if getattr(ai, "_qlda_boq_fullscan_installed", False):
        return

    original = ai._boq_query_appendix

    def boq_query_with_fullscan(connection, project_id: int, question: str, total_rows: int) -> list[str]:
        lines = original(connection, project_id, question, total_rows)
        if not _component_total_intent(ai, question):
            return lines

        stats = fullscan_boq_component_totals(connection, int(project_id))
        lines += ["", "#### BOQ FULL-SCAN VẬT TƯ / NHÂN CÔNG — NGUỒN TỔNG HỢP ƯU TIÊN"]
        if not stats.get("ok"):
            lines.append(
                "Không tạo được tổng full-scan từ workbook BOQ đã lưu. Không được gọi subtotal từ một phần dòng là 'Tổng BOQ'."
            )
            return lines

        lines += [
            "QUY TẮC BẮT BUỘC: phần này được tính bằng chương trình trên toàn bộ dòng BOQ Excel theo sheet + dòng gốc, "
            "không phụ thuộc giới hạn số dòng được đưa nguyên văn vào prompt. Nếu tổng ở phần phía trên khác phần FULL-SCAN này, "
            "phải bỏ qua tổng phía trên và dùng FULL-SCAN.",
            f"Đã quét {stats['scanned_rows']:,}/{stats['total_rows']:,} dòng BOQ Excel; "
            f"{stats['component_sheet_count']:,}/{stats['saved_sheet_count']:,} sheet có cấu trúc đơn giá vật tư/nhân công.",
            f"TỔNG CHI PHÍ VẬT TƯ BOQ (FULL-SCAN): {ai._fmt_money(stats['material_total'])} VND.",
            f"TỔNG CHI PHÍ NHÂN CÔNG BOQ (FULL-SCAN): {ai._fmt_money(stats['labor_total'])} VND.",
            f"TỔNG VẬT TƯ + NHÂN CÔNG (FULL-SCAN): {ai._fmt_money(stats['material_total'] + stats['labor_total'])} VND.",
        ]

        if stats.get("complete"):
            lines.append("Trạng thái FULL-SCAN: HOÀN TẤT — tất cả dòng BOQ Excel đã nhập đều đã tham gia phép tổng hợp.")
        else:
            lines.append(
                f"Trạng thái FULL-SCAN: CHƯA ĐỦ — còn {stats['missing_source_rows']:,} dòng chưa đối chiếu được với snapshot Excel"
                + (f"; snapshot bị giới hạn ở sheet: {', '.join(stats['truncated_sheets'])}." if stats.get("truncated_sheets") else ".")
                + " Không được trình bày các số trên như tổng cuối cùng nếu trạng thái chưa đủ."
            )

        lines += ["", "##### ĐỐI CHIẾU FULL-SCAN THEO SHEET"]
        for item in stats.get("by_sheet") or []:
            lines.append(
                f"[BOQ-FULLSCAN:{item['sheet']}] {item['rows']:,} dòng | "
                f"vật tư={ai._fmt_money(item['material_total'])} VND | "
                f"nhân công={ai._fmt_money(item['labor_total'])} VND | "
                f"chưa đối chiếu={item['missing_rows']:,}"
            )
        return lines

    ai._boq_query_appendix = boq_query_with_fullscan
    ai._qlda_boq_fullscan_installed = True
    ai._qlda_boq_fullscan_marker = PATCH_MARKER
