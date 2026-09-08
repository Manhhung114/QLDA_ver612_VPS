from __future__ import annotations

import re
import unicodedata
from typing import Any

PATCH_MARKER = "V6.22 AI VO CONTEXT V1"
MAX_VOS = 100
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
        n = float(value or 0)
        if abs(n - round(n)) < 1e-9:
            return f"{n:,.0f}"
        return f"{n:,.4f}".rstrip("0").rstrip(".")
    except Exception:
        return str(value or "0")


def _vo_appendix(builder, project_id: int, question: str) -> str:
    lines = ["", "## VO / PHÁT SINH LIVE"]
    with builder.connect() as c:
        try:
            if not builder.table_exists(c, "variation_orders"):
                lines.append("Dự án chưa có VO Excel được lưu.")
                return "\n".join(lines)
        except Exception:
            lines.append("Dự án chưa có VO Excel được lưu.")
            return "\n".join(lines)

        rows = c.execute(
            """SELECT vo_id,vo_no,vo_code,filename,vo_date,revision_label,subtotal_before_vat,vat_amount,
                      total_after_vat,increase_amount,decrease_amount,proposed_amount,approved_amount,
                      funding_source,status,latest_revision,updated_at
               FROM variation_orders WHERE project_id=? ORDER BY vo_no LIMIT ?""",
            (int(project_id), MAX_VOS),
        ).fetchall()
        vos = [_rowdict(r) for r in rows]
        proposed = sum(float(r.get("proposed_amount") or 0) for r in vos)
        approved = sum(float(r.get("approved_amount") or 0) for r in vos)
        increase = sum(float(r.get("increase_amount") or 0) for r in vos)
        decrease = sum(float(r.get("decrease_amount") or 0) for r in vos)
        lines.append(
            f"LIVE VO: {len(vos):,} VO | tăng={_money(increase)} VND | giảm={_money(decrease)} VND | "
            f"ròng đề xuất={_money(proposed)} VND | ròng duyệt={_money(approved)} VND."
        )
        lines.append("Quy ước dấu: VO tăng là số dương; VO giảm là số âm và làm giảm ngân sách dự án.")
        for row in vos:
            lines.append(
                f"[VO:{row.get('vo_code','')}] ngày={row.get('vo_date','')} | file={row.get('filename','')} | "
                f"trước VAT={_money(row.get('subtotal_before_vat'))} | VAT={_money(row.get('vat_amount'))} | "
                f"sau VAT={_money(row.get('total_after_vat'))} | tăng={_money(row.get('increase_amount'))} | "
                f"giảm={_money(row.get('decrease_amount'))} | duyệt={_money(row.get('approved_amount'))} | "
                f"trạng thái={row.get('status','')} | rev={row.get('latest_revision',0)} | cập nhật={row.get('updated_at','')}"
            )

        qnorm = _norm(question)
        vo_intent = any(x in qnorm for x in ("vo", "phat sinh", "tang giam", "variation", "chi phi"))
        if not vo_intent or not vos:
            return "\n".join(lines)
        selected = vos
        m = re.search(r"\bvo\s*0*([0-9]+)", qnorm)
        if m:
            wanted = int(m.group(1))
            selected = [r for r in vos if int(r.get("vo_no") or -1) == wanted]

        tokens = [w for w in qnorm.split() if len(w) >= 3 and w not in {
            "phat", "sinh", "tang", "giam", "variation", "chi", "phi", "trong", "vo", "boq",
        }]
        for vo in selected[:8]:
            vo_id = str(vo.get("vo_id") or "")
            item_rows = c.execute(
                """SELECT sheet_name,row_no,description,unit,contract_qty,actual_qty,variation_qty,
                          material_unit_price,labor_unit_price,variation_amount,variation_kind,item_code,note
                   FROM variation_order_items WHERE vo_id=? ORDER BY sheet_name,row_no""",
                (vo_id,),
            ).fetchall()
            items = [_rowdict(r) for r in item_rows]
            scored = []
            for item in items:
                hay = _norm(" ".join(str(item.get(k) or "") for k in ("description","item_code","sheet_name","note")))
                score = sum(1 for token in tokens if token in hay)
                if not tokens or score > 0:
                    scored.append((score, item))
            scored.sort(key=lambda p: (p[0], abs(float(p[1].get("variation_amount") or 0))), reverse=True)
            matched = [x for _, x in scored[:MAX_MATCH_ITEMS]]
            lines.append(
                f"### CHI TIẾT {vo.get('vo_code','')} — {len(items):,} dòng; "
                f"đưa {len(matched):,} dòng phù hợp câu hỏi vào ngữ cảnh."
            )
            for item in matched:
                lines.append(
                    f"[VO-ITEM:{vo.get('vo_code','')}:{item.get('sheet_name','')}:{item.get('row_no','')}] "
                    f"{item.get('description','')} | ĐVT={item.get('unit','')} | KL HĐ={_qty(item.get('contract_qty'))} | "
                    f"KL TT={_qty(item.get('actual_qty'))} | KL +/-={_qty(item.get('variation_qty'))} | "
                    f"GT VO={_money(item.get('variation_amount'))} | loại={item.get('variation_kind','')}"
                )
    return "\n".join(lines)


def install_ai_vo_context() -> None:
    import ai_service

    cls = ai_service.ProjectContextBuilder
    if getattr(cls, "_qlda_ai_vo_context_installed", False):
        return
    original_build = cls.build

    def build_with_vo(self, project_id: int, question: str = "", status_date=None,
                      max_tasks: int = 80, max_docs: int = 70,
                      max_drawings: int = 60, max_legal: int = 40) -> str:
        snapshot = original_build(
            self, project_id, question, status_date,
            max_tasks=max_tasks, max_docs=max_docs,
            max_drawings=max_drawings, max_legal=max_legal,
        )
        try:
            appendix = _vo_appendix(self, int(project_id), str(question or ""))
        except Exception as exc:
            appendix = f"\n## VO / PHÁT SINH LIVE\nKhông đọc được VO live: {exc}"
        return snapshot.rstrip() + "\n" + appendix + "\n"

    cls.build = build_with_vo
    cls._qlda_ai_vo_context_installed = True
    cls._qlda_ai_vo_context_marker = PATCH_MARKER
