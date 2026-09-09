from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any


PATCH_MARKER = "V6.22 AI IPC CLAIM CONTEXT V2 PAYMENT DELAY"
MAX_CLAIMS = 80
MAX_MATCH_ITEMS = 250


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


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _money(value: Any) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except Exception:
        return "0"


def _qty(value: Any) -> str:
    try:
        number = float(value or 0)
        if abs(number - round(number)) < 1e-9:
            return f"{number:,.0f}"
        return f"{number:,.4f}".rstrip("0").rstrip(".")
    except Exception:
        return str(value or "0")


def _parse_date(value: Any):
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            pass
    return None


def _payment_delay_days(payment_due_date: Any, disbursement_date: Any):
    """Late days for one Claim. Missing either date means not yet determinable."""
    due = _parse_date(payment_due_date)
    paid = _parse_date(disbursement_date)
    if due is None or paid is None:
        return None
    return max(0, (paid - due).days)


def _column_names(connection, table: str) -> set[str]:
    try:
        cursor = connection.execute(f"SELECT * FROM {table} LIMIT 0")
    except Exception:
        return set()
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


def _delay_text(days: Any) -> str:
    if days is None:
        return "chưa xác định"
    try:
        return f"{int(days)} ngày"
    except Exception:
        return str(days)


def _claim_appendix(builder, project_id: int, question: str) -> str:
    lines = ["", "## IPC / CLAIM THANH TOÁN LIVE"]
    with builder.connect() as c:
        try:
            if not builder.table_exists(c, "payment_claims"):
                lines.append("Dự án chưa có Claim IPC được lưu.")
                return "\n".join(lines)
        except Exception:
            lines.append("Dự án chưa có Claim IPC được lưu.")
            return "\n".join(lines)

        claim_columns = _column_names(c, "payment_claims")
        due_select = "payment_due_date" if "payment_due_date" in claim_columns else "'' AS payment_due_date"

        try:
            rows = c.execute(
                f"""SELECT claim_id,claim_no,claim_code,filename,contractor,contract_no,from_date,to_date,
                           contract_value,requested_amount,approved_amount,disbursed_amount,certified_cumulative,
                           retention_cumulative,advance_amount,advance_recovery,current_deductions,payment_status,
                           {due_select},disbursement_date,latest_revision,updated_at
                    FROM payment_claims WHERE project_id=? ORDER BY claim_no LIMIT ?""",
                (int(project_id), MAX_CLAIMS),
            ).fetchall()
            claims = [_rowdict(row) for row in rows]
        except Exception:
            claims = []

        for row in claims:
            row["late_payment_days"] = _payment_delay_days(
                row.get("payment_due_date"), row.get("disbursement_date")
            )

        requested = sum(float(row.get("requested_amount") or 0) for row in claims)
        approved = sum(float(row.get("approved_amount") or 0) for row in claims)
        disbursed = sum(float(row.get("disbursed_amount") or 0) for row in claims)
        contract_value = max([float(row.get("contract_value") or 0) for row in claims] + [0])
        late_claims = [row for row in claims if (row.get("late_payment_days") or 0) > 0]
        known_delay_claims = [row for row in claims if row.get("late_payment_days") is not None]
        lines.append(
            f"LIVE Claim: {len(claims):,} Claim | giá trị HĐ {_money(contract_value)} VND | "
            f"đề nghị {_money(requested)} VND | duyệt {_money(approved)} VND | giải ngân {_money(disbursed)} VND."
        )
        lines.append(
            f"Dữ liệu hạn thanh toán: {len(known_delay_claims):,}/{len(claims):,} Claim đủ cả ngày tới hạn và ngày giải ngân; "
            f"{len(late_claims):,} Claim trễ hạn."
        )
        lines.append(
            "QUY TẮC BẮT BUỘC CHO AI: late_payment_days của từng Claim là số ngày trễ đã tính trực tiếp từ "
            "max(0, Ngày giải ngân - Ngày tới hạn thanh toán). Nếu dòng Claim có late_payment_days thì KHÔNG được kết luận thiếu dữ liệu."
        )
        lines.append("Mỗi Claim và workbook Excel của Claim được lưu cấp dự án, dùng chung giữa các tài khoản.")

        for row in claims:
            late_days = row.get("late_payment_days")
            lines.append(
                f"[CLAIM:{row.get('claim_code','')}] kỳ={row.get('claim_no','')} | "
                f"{row.get('from_date','')}→{row.get('to_date','')} | file={row.get('filename','')} | "
                f"đề nghị={_money(row.get('requested_amount'))} | duyệt={_money(row.get('approved_amount'))} | "
                f"giải ngân={_money(row.get('disbursed_amount'))} | lũy kế NT={_money(row.get('certified_cumulative'))} | "
                f"tới_hạn={row.get('payment_due_date','') or 'chưa có'} | "
                f"ngày_giải_ngân={row.get('disbursement_date','') or 'chưa có'} | "
                f"late_payment_days={late_days if late_days is not None else 'chưa xác định'} | "
                f"trễ_thanh_toán={_delay_text(late_days)} | trạng thái={row.get('payment_status','')} | "
                f"rev={row.get('latest_revision',0)} | cập nhật={row.get('updated_at','')}"
            )

        qnorm = _norm(question)
        claim_intent = any(x in qnorm for x in (
            "claim", "ipc", "thanh toan", "giai ngan", "nghiem thu", "khoi luong", "vat tu", "lap dat",
            "tre han", "tre thanh toan", "cham thanh toan", "toi han", "qua han",
        ))
        if not claim_intent or not claims:
            return "\n".join(lines)

        selected = claims
        m = re.search(r"(?:claim|ipc)\s*#?\s*0*([0-9]+)", qnorm)
        if m:
            wanted = str(int(m.group(1)))
            selected = [row for row in claims if str(row.get("claim_no") or "").lstrip("0") == wanted]

        delay_intent = any(x in qnorm for x in (
            "tre han", "tre thanh toan", "cham thanh toan", "so ngay tre", "ngay tre", "qua han", "toi han",
        ))
        if delay_intent:
            lines.append("### KẾT QUẢ SỐ NGÀY TRỄ THANH TOÁN THEO TỪNG CLAIM")
            for claim in selected:
                lines.append(
                    f"[CLAIM-DELAY:{claim.get('claim_code','')}] "
                    f"tới hạn={claim.get('payment_due_date','') or 'chưa có'} | "
                    f"giải ngân={claim.get('disbursement_date','') or 'chưa có'} | "
                    f"số ngày trễ={_delay_text(claim.get('late_payment_days'))}"
                )
            return "\n".join(lines)

        tokens = [w for w in qnorm.split() if len(w) >= 3 and w not in {
            "claim", "ipc", "thanh", "toan", "giai", "ngan", "khoi", "luong", "nghiem", "thu", "trong", "toan", "boq",
        }]
        for claim in selected[:8]:
            claim_id = str(claim.get("claim_id") or "")
            if not claim_id:
                continue
            try:
                item_rows = c.execute(
                    """SELECT row_no,boq_item,contract_qty,unit,material_current_qty,material_cumulative_qty,
                              installation_current_pct,installation_cumulative_pct,current_value,cumulative_value,
                              item_code,cost_code,system,note
                       FROM payment_claim_items WHERE claim_id=? ORDER BY row_no""",
                    (claim_id,),
                ).fetchall()
                items = [_rowdict(row) for row in item_rows]
            except Exception:
                items = []
            if not items:
                continue

            scored = []
            for item in items:
                haystack = _norm(" ".join(str(item.get(k) or "") for k in ("boq_item", "item_code", "cost_code", "system", "note")))
                score = sum(1 for token in tokens if token in haystack)
                if not tokens or score > 0:
                    scored.append((score, item))
            scored.sort(key=lambda pair: (pair[0], int(pair[1].get("row_no") or 0)), reverse=True)
            matched = [row for _, row in scored[:MAX_MATCH_ITEMS]]
            lines.append(
                f"### CHI TIẾT {claim.get('claim_code','')} — kho {len(items):,} dòng GTHT; "
                f"đang đưa {len(matched):,} dòng phù hợp câu hỏi vào ngữ cảnh."
            )
            for item in matched:
                lines.append(
                    f"[CLAIM-ITEM:{claim.get('claim_code','')}:{item.get('row_no','')}] "
                    f"{item.get('boq_item','')} | ĐVT={item.get('unit','')} | KL HĐ={_qty(item.get('contract_qty'))} | "
                    f"VT kỳ={_qty(item.get('material_current_qty'))} | VT lũy kế={_qty(item.get('material_cumulative_qty'))} | "
                    f"LĐ kỳ={_qty(item.get('installation_current_pct'))}% | LĐ lũy kế={_qty(item.get('installation_cumulative_pct'))}% | "
                    f"GT kỳ={_money(item.get('current_value'))} | GT lũy kế={_money(item.get('cumulative_value'))}"
                )

    return "\n".join(lines)


def install_ai_claim_context() -> None:
    import ai_service

    cls = ai_service.ProjectContextBuilder
    if getattr(cls, "_qlda_ai_claim_context_installed", False):
        return
    original_build = cls.build

    def build_with_claims(self, project_id: int, question: str = "", status_date=None,
                          max_tasks: int = 80, max_docs: int = 70,
                          max_drawings: int = 60, max_legal: int = 40) -> str:
        snapshot = original_build(
            self, project_id, question, status_date,
            max_tasks=max_tasks, max_docs=max_docs,
            max_drawings=max_drawings, max_legal=max_legal,
        )
        try:
            appendix = _claim_appendix(self, int(project_id), str(question or ""))
        except Exception as exc:
            appendix = f"\n## IPC / CLAIM THANH TOÁN LIVE\nKhông đọc được Claim live: {exc}"
        return snapshot.rstrip() + "\n" + appendix + "\n"

    cls.build = build_with_claims
    cls._qlda_ai_claim_context_installed = True
    cls._qlda_ai_claim_context_marker = PATCH_MARKER