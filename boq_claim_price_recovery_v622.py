from __future__ import annotations

import re
import threading
import unicodedata
from typing import Any


PATCH_MARKER = "V6.22 BOQ CLAIM PRICE RECOVERY V1"
_LOCK = threading.RLock()
_BOQ_RECOVERED: set[tuple[int, str]] = set()
_CLAIM_RECOVERED: set[tuple[str, str]] = set()


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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


def _payload_value(row: Any, key: str, index: int) -> Any:
    data = _rowdict(row)
    if data:
        return data.get(key)
    try:
        return row[index]
    except Exception:
        return None


def _snapshot_rows(snapshot: dict[str, Any]) -> dict[int, list[Any]]:
    out: dict[int, list[Any]] = {}
    for raw in list(snapshot.get("rows") or []):
        if not isinstance(raw, (list, tuple)) or not raw:
            continue
        try:
            row_no = int(raw[0])
        except Exception:
            continue
        out[row_no] = list(raw[1:])
    return out


def _cell(cells: list[Any], column_index: int | None) -> Any:
    if column_index is None or column_index < 0 or column_index >= len(cells):
        return None
    return cells[column_index]


def _next_nonempty(values: list[Any], start: int) -> int:
    for index in range(start + 1, len(values)):
        if _norm(values[index]):
            return index
    return len(values)


def _detect_split_price_cols(snapshot: dict[str, Any]) -> dict[str, int]:
    """Detect Excel columns for grouped `Đơn giá -> Vật tư/Vật liệu | Nhân công`."""
    rows = _snapshot_rows(snapshot)
    if not rows:
        return {}
    scan = sorted(row for row in rows if row <= 30)
    material: int | None = None
    labor: int | None = None

    # Direct headings such as "Đơn giá vật tư" / "Đơn giá nhân công".
    for row_no in scan:
        values = rows[row_no]
        for index, value in enumerate(values):
            text = _norm(value)
            if "don gia" not in text:
                continue
            if "vat tu" in text or "vat lieu" in text or "material" in text:
                material = index
            if "nhan cong" in text or "labor" in text or "labour" in text:
                labor = index

    if material is not None and labor is not None:
        return {"material_unit_price": material, "labor_unit_price": labor}

    # Grouped multi-row heading. Works for both:
    #   BOQ:  I2=Đơn giá; I3=Vật tư; J3=Nhân công
    #   Claim: I12=Đơn giá (VNĐ); I13=Vật tư; J13=Nhân công
    for row_no in scan:
        parent = rows[row_no]
        for start, value in enumerate(parent):
            if "don gia" not in _norm(value):
                continue
            end = _next_nonempty(parent, start)
            end = max(start + 1, end)
            for child_row_no in range(row_no + 1, min(row_no + 4, max(scan) + 1)):
                child = rows.get(child_row_no) or []
                for index in range(start, min(end, len(child))):
                    text = _norm(child[index])
                    if text in {"vat tu", "vat lieu", "material"} or "vat tu" in text or "vat lieu" in text:
                        material = index
                    if text in {"nhan cong", "labor", "labour"} or "nhan cong" in text:
                        labor = index
            if material is not None and labor is not None:
                return {"material_unit_price": material, "labor_unit_price": labor}
    return {}


def _best_price_snapshot(workbook_sheets: Any, prefer_gtht: bool = False):
    if not isinstance(workbook_sheets, dict):
        return None, {}, {}
    candidates = []
    for name, snapshot in workbook_sheets.items():
        if not isinstance(snapshot, dict):
            continue
        mapping = _detect_split_price_cols(snapshot)
        if not mapping:
            continue
        rows = _snapshot_rows(snapshot)
        score = len(rows)
        nname = _norm(name)
        if prefer_gtht and ("gtht" in nname or "gia tri hoan thanh" in nname):
            score += 100000
        candidates.append((score, str(name), snapshot, mapping, rows))
    if not candidates:
        return None, {}, {}
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, name, _snapshot, mapping, rows = candidates[0]
    return name, mapping, rows


def _boq_snapshot_maps(workbook_sheets: Any) -> dict[str, tuple[dict[str, int], dict[int, list[Any]]]]:
    out: dict[str, tuple[dict[str, int], dict[int, list[Any]]]] = {}
    if not isinstance(workbook_sheets, dict):
        return out
    for name, snapshot in workbook_sheets.items():
        if not isinstance(snapshot, dict):
            continue
        mapping = _detect_split_price_cols(snapshot)
        if mapping:
            out[str(name)] = (mapping, _snapshot_rows(snapshot))
    return out


def _parse_boq_note(note: Any) -> tuple[str, int] | None:
    match = re.search(r"(?:^|\|)sheet=(.*?)\|row=(\d+)(?:\||$)", str(note or ""))
    if not match:
        return None
    try:
        return match.group(1), int(match.group(2))
    except Exception:
        return None


def _recover_boq(connection, project_id: int) -> int:
    try:
        columns_cursor = connection.execute("SELECT * FROM cost_budgets LIMIT 0")
        columns = {str(getattr(x, "name", None) or x[0]) for x in (getattr(columns_cursor, "description", None) or [])}
    except Exception:
        return 0
    required = {"material_unit_price", "labor_unit_price", "material_cost", "labor_cost"}
    if not required.issubset(columns):
        return 0

    try:
        saved_row = connection.execute(
            "SELECT batch_id,payload FROM boq_excel_workbooks WHERE project_id=?",
            (int(project_id),),
        ).fetchone()
    except Exception:
        return 0
    if saved_row is None:
        return 0

    batch_id = str(_payload_value(saved_row, "batch_id", 0) or "")
    cache_key = (int(project_id), batch_id)
    if cache_key in _BOQ_RECOVERED:
        return 0

    try:
        import boq_persistence_v622 as persistence
        saved = persistence._decode_result(str(_payload_value(saved_row, "payload", 1) or ""))
    except Exception:
        return 0

    sources = _boq_snapshot_maps(saved.get("workbook_sheets"))
    if not sources:
        return 0

    try:
        db_rows = connection.execute(
            """SELECT id,quantity,note,material_unit_price,labor_unit_price
               FROM cost_budgets
               WHERE project_id=? AND note LIKE ?""",
            (int(project_id), "[QLDA_BOQ_EXCEL]%"),
        ).fetchall()
    except Exception:
        return 0

    updates = []
    for raw in db_rows:
        row = _rowdict(raw)
        if not row:
            try:
                row = {
                    "id": raw[0], "quantity": raw[1], "note": raw[2],
                    "material_unit_price": raw[3], "labor_unit_price": raw[4],
                }
            except Exception:
                continue
        parsed = _parse_boq_note(row.get("note"))
        if not parsed:
            continue
        sheet_name, row_no = parsed
        source = sources.get(sheet_name)
        if source is None:
            continue
        mapping, source_rows = source
        cells = source_rows.get(int(row_no))
        if cells is None:
            continue
        material_price = _num(_cell(cells, mapping.get("material_unit_price")))
        labor_price = _num(_cell(cells, mapping.get("labor_unit_price")))
        if material_price is None and labor_price is None:
            continue
        if material_price is None:
            material_price = _num(row.get("material_unit_price"))
        if labor_price is None:
            labor_price = _num(row.get("labor_unit_price"))
        qty = _num(row.get("quantity")) or 0.0
        material_cost = qty * material_price if material_price is not None else None
        labor_cost = qty * labor_price if labor_price is not None else None
        updates.append((material_price, labor_price, material_cost, labor_cost, int(row.get("id"))))

    if updates:
        connection.executemany(
            """UPDATE cost_budgets
               SET material_unit_price=?,labor_unit_price=?,material_cost=?,labor_cost=?
               WHERE id=?""",
            updates,
        )
    _BOQ_RECOVERED.add(cache_key)
    return len(updates)


def _claim_number_from_question(question: str) -> str:
    match = re.search(r"(?:claim|ipc)\s*#?\s*0*([0-9]+)", _norm(question))
    return str(int(match.group(1))) if match else ""


def _recover_claims(connection, project_id: int, question: str = "") -> int:
    wanted = _claim_number_from_question(question)
    try:
        rows = connection.execute(
            """SELECT c.claim_id,c.claim_no,c.claim_code,w.batch_id,w.payload
               FROM payment_claims c
               JOIN payment_claim_workbooks w ON w.claim_id=c.claim_id
               WHERE c.project_id=? ORDER BY c.claim_no""",
            (int(project_id),),
        ).fetchall()
    except Exception:
        return 0

    claims = []
    for raw in rows:
        row = _rowdict(raw)
        if not row:
            try:
                row = {
                    "claim_id": raw[0], "claim_no": raw[1], "claim_code": raw[2],
                    "batch_id": raw[3], "payload": raw[4],
                }
            except Exception:
                continue
        if wanted and str(row.get("claim_no") or "").lstrip("0") != wanted:
            continue
        claims.append(row)

    # Without an explicit Claim number, only self-heal Claims whose stored item
    # prices are completely absent. This avoids decoding every large Claim on
    # unrelated questions while still repairing the old LIVE data state.
    if not wanted:
        selected = []
        for row in claims:
            try:
                stat = connection.execute(
                    """SELECT COUNT(*) AS total,
                              SUM(CASE WHEN ABS(COALESCE(material_unit_price,0)) + ABS(COALESCE(labor_unit_price,0)) > 0 THEN 1 ELSE 0 END) AS known
                       FROM payment_claim_items WHERE claim_id=?""",
                    (str(row.get("claim_id") or ""),),
                ).fetchone()
                data = _rowdict(stat)
                total = int((data.get("total") if data else stat[0]) or 0)
                known = int((data.get("known") if data else stat[1]) or 0)
            except Exception:
                total = known = 0
            if total > 0 and known == 0:
                selected.append(row)
        claims = selected

    recovered = 0
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        batch_id = str(claim.get("batch_id") or "")
        cache_key = (claim_id, batch_id)
        if not claim_id or cache_key in _CLAIM_RECOVERED:
            continue
        try:
            import ipc_claim_v622 as ipc
            saved = ipc._decode_result(str(claim.get("payload") or ""))
        except Exception:
            continue
        _sheet_name, mapping, source_rows = _best_price_snapshot(saved.get("workbook_sheets"), prefer_gtht=True)
        if not mapping or not source_rows:
            continue
        try:
            item_rows = connection.execute(
                "SELECT row_no,material_unit_price,labor_unit_price FROM payment_claim_items WHERE claim_id=?",
                (claim_id,),
            ).fetchall()
        except Exception:
            continue
        updates = []
        for raw in item_rows:
            row = _rowdict(raw)
            if not row:
                try:
                    row = {"row_no": raw[0], "material_unit_price": raw[1], "labor_unit_price": raw[2]}
                except Exception:
                    continue
            try:
                row_no = int(row.get("row_no") or 0)
            except Exception:
                continue
            cells = source_rows.get(row_no)
            if cells is None:
                continue
            material_price = _num(_cell(cells, mapping.get("material_unit_price")))
            labor_price = _num(_cell(cells, mapping.get("labor_unit_price")))
            if material_price is None and labor_price is None:
                continue
            if material_price is None:
                material_price = _num(row.get("material_unit_price")) or 0.0
            if labor_price is None:
                labor_price = _num(row.get("labor_unit_price")) or 0.0
            updates.append((float(material_price), float(labor_price), claim_id, row_no))
        if updates:
            connection.executemany(
                """UPDATE payment_claim_items
                   SET material_unit_price=?,labor_unit_price=?
                   WHERE claim_id=? AND row_no=?""",
                updates,
            )
            recovered += len(updates)
        _CLAIM_RECOVERED.add(cache_key)
    return recovered


def _install_boq_material_alias() -> None:
    """Accept both 'Vật tư' and 'Vật liệu' under the grouped Đơn giá header."""
    import boq_cost_components_v622 as components
    if getattr(components, "_qlda_price_recovery_alias_installed", False):
        return
    original = components._classify_component_header

    def classify_with_vat_lieu(text: Any, parent: str, norm):
        kind, score = original(text, parent, norm)
        if kind:
            return kind, score
        current = norm(text)
        combined = norm(f"{parent} {text or ''}")
        price_words = ("don gia", "unit price", "rate", "gia don vi")
        amount_words = ("thanh tien", "chi phi", "gia tri", "amount", "cost")
        has_price = any(word in combined for word in price_words)
        has_amount = any(word in combined for word in amount_words)
        if "vat lieu" in combined and has_price:
            return "material_unit_price", 20
        if "vat lieu" in combined and has_amount:
            return "material_cost", 18
        if current == "vat lieu":
            if any(word in parent for word in price_words):
                return "material_unit_price", 16
            if any(word in parent for word in amount_words):
                return "material_cost", 16
        return None, 0

    components._classify_component_header = classify_with_vat_lieu
    components._qlda_price_recovery_alias_installed = True


def _install_ipc_multirow_header_fix() -> None:
    """Parse the real GTHT layout: Tên công tác, Khối lượng, Đơn giá -> Vật tư/Nhân công."""
    import ipc_adaptive_parser_v622 as adaptive
    if getattr(adaptive, "_qlda_price_recovery_header_installed", False):
        return

    original_header_row = adaptive._header_row_for_new_gtht
    original_find = adaptive._find_header_col

    def header_row_with_ten_cong_tac(ipc, ws):
        row_no, grid = original_header_row(ipc, ws)
        if row_no is not None:
            return row_no, grid
        grid = adaptive._top_grid(ws, 35, 32)
        for r, row in enumerate(grid):
            for value in row[:10]:
                text = ipc._norm(value)
                if "ten cong tac" in text or "dien giai khoi luong" in text:
                    return r + 1, grid
        return None, grid

    def composite_with_carried_levels(ipc, grid, header_row: int, max_cols: int = 32):
        levels = []
        for offset in (0, 1, 2):
            row = list(grid[header_row - 1 + offset]) if header_row - 1 + offset < len(grid) else []
            row += [None] * (max_cols - len(row))
            carried = ""
            expanded = []
            for c in range(max_cols):
                if row[c] not in (None, ""):
                    carried = str(row[c])
                expanded.append(carried)
            levels.append(expanded)
        return [ipc._norm(" | ".join(level[c] for level in levels)) for c in range(max_cols)]

    def find_header_col_with_real_claim(headers, include, exclude=()):
        found = original_find(headers, include, exclude)
        if found is not None:
            return found
        wanted = tuple(include or ())
        aliases: tuple[tuple[str, ...], ...] = ()
        if wanted == ("stt",):
            aliases = (("tt",),)
        elif wanted == ("hang muc cong viec",):
            aliases = (("ten cong tac",), ("dien giai khoi luong",), ("noi dung cong viec",))
        elif wanted == ("dvt",):
            aliases = (("don vi",),)
        elif wanted == ("don gia lap dat",):
            aliases = (("don gia nhan cong",),)
        for alias in aliases:
            found = original_find(headers, alias, exclude)
            if found is not None:
                return found
        if wanted == ("hop dong", "khoi luong"):
            for index, text in enumerate(headers):
                if "khoi luong" in text and "nghiem thu" not in text and "vat tu" not in text and "lap dat" not in text:
                    return index
        return None

    adaptive._header_row_for_new_gtht = header_row_with_ten_cong_tac
    adaptive._composite_headers = composite_with_carried_levels
    adaptive._find_header_col = find_header_col_with_real_claim
    adaptive._qlda_price_recovery_header_installed = True


def _install_ai_recovery() -> None:
    import ai_live_context_v622 as ai_boq
    import ai_claim_context_v622 as ai_claim

    if not getattr(ai_boq, "_qlda_price_recovery_installed", False):
        original_boq = ai_boq._boq_query_appendix

        def boq_query_after_recovery(connection, project_id: int, question: str, total_rows: int):
            try:
                _recover_boq(connection, int(project_id))
            except Exception:
                pass
            return original_boq(connection, project_id, question, total_rows)

        ai_boq._boq_query_appendix = boq_query_after_recovery
        ai_boq._qlda_price_recovery_installed = True

    if not getattr(ai_claim, "_qlda_price_recovery_installed", False):
        original_claim = ai_claim._claim_appendix

        def claim_appendix_after_recovery(builder, project_id: int, question: str):
            try:
                with builder.connect() as connection:
                    _recover_claims(connection, int(project_id), str(question or ""))
            except Exception:
                pass
            return original_claim(builder, project_id, question)

        ai_claim._claim_appendix = claim_appendix_after_recovery
        ai_claim._qlda_price_recovery_installed = True


def install_boq_claim_price_recovery() -> None:
    """Self-heal old BOQ/Claim data from project-scoped workbook snapshots."""
    with _LOCK:
        _install_boq_material_alias()
        _install_ipc_multirow_header_fix()
        _install_ai_recovery()
