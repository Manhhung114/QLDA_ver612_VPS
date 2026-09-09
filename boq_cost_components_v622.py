from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path
from typing import Any


PATCH_MARKER = "V6.22 BOQ COST COMPONENTS V1"
_COMPONENT_LOCK = threading.RLock()
_COMPONENT_COLUMNS = {
    "material_unit_price": "REAL",
    "labor_unit_price": "REAL",
    "material_cost": "REAL",
    "labor_cost": "REAL",
}


def _rowdict(row: Any) -> dict:
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


def _nullable_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _column_names(connection, table: str) -> set[str]:
    cursor = connection.execute(f"SELECT * FROM {table} LIMIT 0")
    description = getattr(cursor, "description", None) or []
    names: set[str] = set()
    for item in description:
        name = getattr(item, "name", None)
        if name is None:
            try:
                name = item[0]
            except Exception:
                name = None
        if name:
            names.add(str(name))
    return names


def ensure_cost_component_schema(db) -> None:
    """Add nullable BOQ material/labor columns without changing legacy totals."""
    with db.connect() as connection:
        columns = _column_names(connection, "cost_budgets")
        for name, decl in _COMPONENT_COLUMNS.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE cost_budgets ADD COLUMN {name} {decl}")
                columns.add(name)


def _nearest_parent(values: list[Any], index: int, norm) -> str:
    for pos in range(min(index, len(values) - 1), -1, -1):
        text = norm(values[pos])
        if text:
            return text
    return ""


def _classify_component_header(text: Any, parent: str, norm) -> tuple[str | None, int]:
    current = norm(text)
    combined = norm(f"{parent} {text or ''}")
    if not current and not combined:
        return None, 0

    material_words = ("vat tu", "material")
    labor_words = ("nhan cong", "labor", "labour")
    price_words = ("don gia", "unit price", "rate", "gia don vi")
    amount_words = ("thanh tien", "chi phi", "gia tri", "amount", "cost")

    def has_any(source: str, words: tuple[str, ...]) -> bool:
        return any(word in source for word in words)

    # Direct combined headings such as "Đơn giá vật tư" / "Chi phí nhân công".
    if has_any(combined, material_words) and has_any(combined, price_words):
        return "material_unit_price", 20
    if has_any(combined, labor_words) and has_any(combined, price_words):
        return "labor_unit_price", 20
    if has_any(combined, material_words) and has_any(combined, amount_words):
        return "material_cost", 18
    if has_any(combined, labor_words) and has_any(combined, amount_words):
        return "labor_cost", 18

    # Common abbreviations used in Vietnamese BOQ files.
    if current in {"dg vat tu", "d gia vat tu", "gia vt", "dg vt"}:
        return "material_unit_price", 22
    if current in {"dg nhan cong", "d gia nhan cong", "gia nc", "dg nc"}:
        return "labor_unit_price", 22
    if current in {"tt vat tu", "chi phi vt", "gia tri vt"}:
        return "material_cost", 22
    if current in {"tt nhan cong", "chi phi nc", "gia tri nc"}:
        return "labor_cost", 22

    # Two-row headers: parent is "Đơn giá" or "Thành tiền", child is
    # exactly "Vật tư" / "Nhân công". Exact matching prevents "Tên vật tư"
    # from being mistaken for a price column.
    if current in {"vat tu", "material"}:
        if has_any(parent, price_words):
            return "material_unit_price", 16
        if has_any(parent, amount_words):
            return "material_cost", 16
    if current in {"nhan cong", "labor", "labour", "nc"}:
        if has_any(parent, price_words):
            return "labor_unit_price", 16
        if has_any(parent, amount_words):
            return "labor_cost", 16

    return None, 0


def _detect_component_columns(ws, header_row: int, boq) -> dict[str, int]:
    """Detect split material/labor columns around a one- or multi-row BOQ header."""
    start = max(1, int(header_row) - 2)
    end = min(int(ws.max_row or header_row), int(header_row) + 2)
    rows: dict[int, list[Any]] = {}
    for row_no, values in enumerate(
        ws.iter_rows(min_row=start, max_row=end, values_only=True),
        start=start,
    ):
        rows[row_no] = list(values)

    candidates: dict[str, tuple[int, int]] = {}
    for row_no in range(start, end + 1):
        current = rows.get(row_no) or []
        previous = rows.get(row_no - 1) or []
        for idx, value in enumerate(current):
            parent = _nearest_parent(previous, idx, boq._norm) if previous else ""
            kind, score = _classify_component_header(value, parent, boq._norm)
            if not kind:
                continue
            old = candidates.get(kind)
            # Prefer stronger semantic matches and rows nearest the detected header.
            distance_penalty = abs(row_no - int(header_row))
            ranked_score = score * 10 - distance_penalty
            if old is None or ranked_score > old[0]:
                candidates[kind] = (ranked_score, idx)
    return {kind: idx for kind, (_, idx) in candidates.items()}


def _read_component_values(ws, item_rows: list[int], mapping: dict[str, int], boq) -> dict[int, dict[str, float | None]]:
    if not item_rows or not mapping:
        return {}
    wanted = set(int(x) for x in item_rows)
    first = min(wanted)
    last = max(wanted)
    out: dict[int, dict[str, float | None]] = {}
    for row_no, values in enumerate(
        ws.iter_rows(min_row=first, max_row=last, values_only=True),
        start=first,
    ):
        if row_no not in wanted:
            continue
        values = tuple(values)
        out[row_no] = {
            key: boq._to_number(boq._cell(values, col))
            for key, col in mapping.items()
        }
    return out


def _install_parser_patch() -> None:
    import boq_multisheet_v622 as boq

    if getattr(boq, "_qlda_cost_components_installed", False):
        return

    original_parse_sheet = boq._parse_sheet
    original_parse_workbook = boq.parse_boq_workbook
    original_save = boq.save_boq_summary_to_project

    def parse_sheet_with_components(ws):
        parsed = original_parse_sheet(ws)
        if not parsed:
            return parsed

        for item in parsed.get("items") or []:
            item.setdefault("material_unit_price", None)
            item.setdefault("labor_unit_price", None)
            item.setdefault("material_cost", None)
            item.setdefault("labor_cost", None)

        header_row = int(parsed.get("header_row") or 0)
        component_map = _detect_component_columns(ws, header_row, boq)
        if not component_map:
            parsed["component_columns"] = {}
            parsed["component_line_count"] = 0
            return parsed

        item_rows = [int(item.get("row_no") or 0) for item in parsed.get("items") or [] if int(item.get("row_no") or 0) > 0]
        component_values = _read_component_values(ws, item_rows, component_map, boq)
        original_header = boq._find_header(ws)
        original_mapping = original_header[1] if original_header else {}
        source_has_total_amount = "amount" in original_mapping
        component_lines = 0
        discrepancy_lines = 0

        for item in parsed.get("items") or []:
            row_no = int(item.get("row_no") or 0)
            values = component_values.get(row_no, {})
            qty = float(item.get("quantity") or 0)

            material_price = values.get("material_unit_price")
            labor_price = values.get("labor_unit_price")
            material_cost = values.get("material_cost")
            labor_cost = values.get("labor_cost")

            if material_price is None and material_cost is not None and qty:
                material_price = material_cost / qty
            if labor_price is None and labor_cost is not None and qty:
                labor_price = labor_cost / qty
            if material_cost is None and material_price is not None:
                material_cost = qty * material_price
            if labor_cost is None and labor_price is not None:
                labor_cost = qty * labor_price

            item["material_unit_price"] = float(material_price) if material_price is not None else None
            item["labor_unit_price"] = float(labor_price) if labor_price is not None else None
            item["material_cost"] = float(material_cost) if material_cost is not None else None
            item["labor_cost"] = float(labor_cost) if labor_cost is not None else None

            if any(value is not None for value in (material_price, labor_price, material_cost, labor_cost)):
                component_lines += 1

            # If the workbook has no all-in "Thành tiền" column, calculate the
            # existing legacy total from the split prices. If an all-in total is
            # present, preserve it exactly as before.
            if not source_has_total_amount and (material_price is not None or labor_price is not None):
                total_price = float(material_price or 0) + float(labor_price or 0)
                item["unit_price"] = total_price
                item["budget_total"] = qty * total_price

            if material_price is not None and labor_price is not None:
                split_total = float(material_price) + float(labor_price)
                legacy_price = float(item.get("unit_price") or 0)
                tolerance = max(1.0, abs(legacy_price) * 1e-8)
                if abs(split_total - legacy_price) > tolerance:
                    discrepancy_lines += 1

        parsed["component_columns"] = component_map
        parsed["component_line_count"] = component_lines
        parsed["component_discrepancy_count"] = discrepancy_lines
        parsed["budget_total"] = float(sum(float(item.get("budget_total") or 0) for item in parsed.get("items") or []))
        return parsed

    def parse_workbook_with_components(data: bytes, filename: str = "BOQ.xlsx"):
        result = original_parse_workbook(data, filename)
        detail = list(result.get("detail_items") or [])
        material_rows = [item for item in detail if item.get("material_unit_price") is not None or item.get("material_cost") is not None]
        labor_rows = [item for item in detail if item.get("labor_unit_price") is not None or item.get("labor_cost") is not None]
        full_rows = [item for item in detail if item.get("material_unit_price") is not None and item.get("labor_unit_price") is not None]
        result["material_component_line_count"] = len(material_rows)
        result["labor_component_line_count"] = len(labor_rows)
        result["full_component_line_count"] = len(full_rows)
        result["material_cost_total"] = float(sum(float(item.get("material_cost") or 0) for item in material_rows))
        result["labor_cost_total"] = float(sum(float(item.get("labor_cost") or 0) for item in labor_rows))

        discrepancy_count = 0
        for item in full_rows:
            split_price = float(item.get("material_unit_price") or 0) + float(item.get("labor_unit_price") or 0)
            all_in = float(item.get("unit_price") or 0)
            tolerance = max(1.0, abs(all_in) * 1e-8)
            if abs(split_price - all_in) > tolerance:
                discrepancy_count += 1
        result["component_discrepancy_count"] = discrepancy_count

        warnings = result.setdefault("warnings", [])
        if material_rows or labor_rows:
            warnings.append(
                f"Đã nhận diện thành phần đơn giá: vật tư {len(material_rows):,} dòng, "
                f"nhân công {len(labor_rows):,} dòng. Chi phí thành phần = Số lượng × Đơn giá thành phần."
            )
        if discrepancy_count:
            warnings.append(
                f"Có {discrepancy_count:,} dòng mà Đơn giá vật tư + Đơn giá nhân công lệch đơn giá tổng. "
                "Hệ thống giữ nguyên đơn giá/tổng tiền gốc của BOQ và chỉ cảnh báo, không tự ghi đè."
            )
        return result

    def save_with_components(db, project_id: int, result: dict[str, Any], *, replace_existing_excel: bool = True):
        ensure_cost_component_schema(db)
        stats = original_save(
            db,
            project_id,
            result,
            replace_existing_excel=replace_existing_excel,
        )

        detail_items = list(result.get("detail_items") or [])
        filename = Path(str(result.get("filename") or "BOQ.xlsx")).name.replace("|", "_")
        batch_id = re.sub(r"[^a-fA-F0-9]", "", str(result.get("batch_id") or ""))[:32]
        if not batch_id:
            batch_id = hashlib.sha256(repr(detail_items).encode("utf-8")).hexdigest()[:16]

        with db.connect() as connection:
            for item in detail_items:
                sheet = str(item.get("sheet") or "BOQ").replace("|", "_")
                row_no = int(item.get("row_no") or 0)
                note = f"{boq.AUTO_NOTE_PREFIX} file={filename}|sheet={sheet}|row={row_no}|batch={batch_id}"
                connection.execute(
                    """UPDATE cost_budgets
                       SET material_unit_price=?,labor_unit_price=?,material_cost=?,labor_cost=?
                       WHERE project_id=? AND note=?""",
                    (
                        _nullable_float(item.get("material_unit_price")),
                        _nullable_float(item.get("labor_unit_price")),
                        _nullable_float(item.get("material_cost")),
                        _nullable_float(item.get("labor_cost")),
                        int(project_id),
                        note,
                    ),
                )

        stats["material_cost_total"] = float(result.get("material_cost_total") or 0)
        stats["labor_cost_total"] = float(result.get("labor_cost_total") or 0)
        stats["material_component_line_count"] = int(result.get("material_component_line_count") or 0)
        stats["labor_component_line_count"] = int(result.get("labor_component_line_count") or 0)
        return stats

    boq._parse_sheet = parse_sheet_with_components
    boq.parse_boq_workbook = parse_workbook_with_components
    boq.save_boq_summary_to_project = save_with_components
    boq._qlda_cost_components_installed = True
    boq._qlda_cost_components_marker = PATCH_MARKER


def _patch_database_class(cls) -> None:
    if cls is None or getattr(cls, "_qlda_cost_components_installed", False):
        return

    original_init = cls.__init__
    original_save_cost_budget = getattr(cls, "save_cost_budget", None)

    def init_with_components(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        ensure_cost_component_schema(self)

    cls.__init__ = init_with_components

    if callable(original_save_cost_budget):
        def save_cost_budget_with_components(self, project_id: int, data: dict, row_id: int | None = None) -> int:
            ensure_cost_component_schema(self)
            saved_id = original_save_cost_budget(self, project_id, data, row_id)
            component_keys = set(_COMPONENT_COLUMNS)
            if component_keys.intersection(data or {}):
                qty = _nullable_float((data or {}).get("quantity")) or 0.0
                material_price = _nullable_float((data or {}).get("material_unit_price"))
                labor_price = _nullable_float((data or {}).get("labor_unit_price"))
                material_cost = _nullable_float((data or {}).get("material_cost"))
                labor_cost = _nullable_float((data or {}).get("labor_cost"))
                if material_cost is None and material_price is not None:
                    material_cost = qty * material_price
                if labor_cost is None and labor_price is not None:
                    labor_cost = qty * labor_price
                with self.connect() as connection:
                    connection.execute(
                        """UPDATE cost_budgets
                           SET material_unit_price=?,labor_unit_price=?,material_cost=?,labor_cost=?
                           WHERE id=?""",
                        (material_price, labor_price, material_cost, labor_cost, int(saved_id)),
                    )
            return int(saved_id)

        cls.save_cost_budget = save_cost_budget_with_components

    cls._qlda_cost_components_installed = True


def _fmt_nullable_money(ai, value: Any) -> str:
    if value is None:
        return "chưa có"
    return f"{ai._fmt_money(value)} VND"


def _install_ai_patch() -> None:
    import ai_live_context_v622 as ai

    if getattr(ai, "_qlda_cost_components_installed", False):
        return

    original_boq_appendix = ai._boq_query_appendix

    def boq_query_with_components(connection, project_id: int, question: str, total_rows: int) -> list[str]:
        lines = original_boq_appendix(connection, project_id, question, total_rows)
        qnorm = ai._norm(question)
        component_intent = any(term in qnorm for term in (
            "vat tu", "nhan cong", "don gia", "chi phi", "material", "labor", "labour",
        ))
        if not component_intent or total_rows <= 0:
            return lines

        try:
            columns = _column_names(connection, "cost_budgets")
        except Exception:
            return lines
        if not set(_COMPONENT_COLUMNS).issubset(columns):
            lines += [
                "",
                "#### PHÂN TÁCH CHI PHÍ VẬT TƯ / NHÂN CÔNG",
                "Database hiện chưa có các trường thành phần đơn giá của BOQ; không được tự suy đoán tỷ lệ vật tư/nhân công.",
            ]
            return lines

        try:
            rows = [_rowdict(row) for row in connection.execute(
                """SELECT id,task_ref,boq_item,quantity,unit,unit_price,budget_total,
                          material_unit_price,labor_unit_price,material_cost,labor_cost,
                          contract_type,contractor,note,updated_at
                   FROM cost_budgets WHERE project_id=? ORDER BY id""",
                (int(project_id),),
            ).fetchall()]
        except Exception:
            return lines

        tokens, _ = ai._question_terms(question)
        component_words = {"vat", "tu", "nhan", "cong", "don", "gia", "chi", "phi", "material", "labor", "labour"}
        filters = [token for token in tokens if token not in component_words]

        selected: list[dict] = []
        if filters:
            for row in rows:
                haystack = ai._norm(" ".join(str(row.get(k) or "") for k in ("boq_item", "task_ref", "unit", "note", "contractor")))
                if any(token in haystack for token in filters):
                    selected.append(row)
        else:
            selected = rows

        known_material = [row for row in selected if row.get("material_unit_price") is not None or row.get("material_cost") is not None]
        known_labor = [row for row in selected if row.get("labor_unit_price") is not None or row.get("labor_cost") is not None]
        material_total = sum(float(row.get("material_cost") or 0) for row in known_material)
        labor_total = sum(float(row.get("labor_cost") or 0) for row in known_labor)

        lines += [
            "",
            "#### PHÂN TÁCH CHI PHÍ VẬT TƯ / NHÂN CÔNG",
            "QUY TẮC DỮ LIỆU: chi phí vật tư = số lượng × đơn giá vật tư; chi phí nhân công = số lượng × đơn giá nhân công. "
            "Trường nào là 'chưa có' thì AI không được tự chia từ đơn giá tổng hoặc tự giả định tỷ lệ.",
            f"Phạm vi truy xuất thành phần: {len(selected):,} dòng BOQ; có dữ liệu vật tư {len(known_material):,} dòng, "
            f"nhân công {len(known_labor):,} dòng.",
        ]
        if known_material:
            lines.append(f"Tổng chi phí vật tư theo dữ liệu đã tách: {ai._fmt_money(material_total)} VND.")
        if known_labor:
            lines.append(f"Tổng chi phí nhân công theo dữ liệu đã tách: {ai._fmt_money(labor_total)} VND.")
        if not known_material and not known_labor:
            lines.append("Các dòng trong phạm vi này chưa có dữ liệu phân tách vật tư/nhân công.")
            return lines

        evidence = [
            row for row in selected
            if row.get("material_unit_price") is not None
            or row.get("labor_unit_price") is not None
            or row.get("material_cost") is not None
            or row.get("labor_cost") is not None
        ]
        for row in evidence[: ai.MAX_BOQ_MATCH_ROWS]:
            lines.append(
                f"[BOQ-COMPONENT:{row.get('id','')}] {row.get('boq_item','')} | "
                f"SL={ai._fmt_qty(row.get('quantity'))} {row.get('unit','')} | "
                f"đơn_giá_vật_tư={_fmt_nullable_money(ai, row.get('material_unit_price'))} | "
                f"chi_phí_vật_tư={_fmt_nullable_money(ai, row.get('material_cost'))} | "
                f"đơn_giá_nhân_công={_fmt_nullable_money(ai, row.get('labor_unit_price'))} | "
                f"chi_phí_nhân_công={_fmt_nullable_money(ai, row.get('labor_cost'))} | "
                f"đơn_giá_tổng={ai._fmt_money(row.get('unit_price'))} VND | "
                f"thành_tiền={ai._fmt_money(row.get('budget_total'))} VND"
            )
        if len(evidence) > ai.MAX_BOQ_MATCH_ROWS:
            lines.append(
                f"Còn {len(evidence) - ai.MAX_BOQ_MATCH_ROWS:,} dòng thành phần chưa đưa nguyên văn vào prompt do giới hạn context; "
                "các dòng đó vẫn đã tham gia tổng hợp ở trên."
            )
        return lines

    ai._boq_query_appendix = boq_query_with_components
    ai._qlda_cost_components_installed = True
    ai._qlda_cost_components_marker = PATCH_MARKER


def install_boq_cost_components() -> None:
    """Install non-destructive BOQ material/labor split for database, parser and AI."""
    import cloud_db

    if getattr(cloud_db, "_qlda_cost_components_global_installed", False):
        return

    with _COMPONENT_LOCK:
        if getattr(cloud_db, "_qlda_cost_components_global_installed", False):
            return

        classes = []
        for cls in (
            getattr(cloud_db, "CloudDatabase", None),
            getattr(cloud_db, "SQLiteCloudDatabase", None),
        ):
            if cls is not None and cls not in classes:
                classes.append(cls)
        try:
            import postgres_backend_v622 as pg
            cls = getattr(pg, "_SQLITE_CLOUD_DATABASE", None)
            if cls is not None and cls not in classes:
                classes.append(cls)
        except Exception:
            pass

        for cls in classes:
            _patch_database_class(cls)

        _install_parser_patch()
        _install_ai_patch()
        cloud_db._qlda_cost_components_global_installed = True
        cloud_db._qlda_cost_components_marker = PATCH_MARKER
