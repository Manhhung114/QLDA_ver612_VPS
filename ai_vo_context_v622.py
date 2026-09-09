from __future__ import annotations

from typing import Any

PATCH_MARKER = "V6.22 AI VO CONTEXT V1"
MAX_VOS = 100


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


def _money(value: Any) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except Exception:
        return "0"


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
            """SELECT vo_id,vo_no,vo_code,filename,vo_date,revision_label,
                      increase_amount,decrease_amount,latest_revision,updated_at
               FROM variation_orders WHERE project_id=? ORDER BY vo_no LIMIT ?""",
            (int(project_id), MAX_VOS),
        ).fetchall()
        vos = [_rowdict(r) for r in rows]
        increase = sum(float(r.get("increase_amount") or 0) for r in vos)
        decrease = sum(float(r.get("decrease_amount") or 0) for r in vos)
        net = increase + decrease

        lines.append(
            f"LIVE VO độc lập: {len(vos):,} VO | phát sinh tăng={_money(increase)} VND | "
            f"phát sinh giảm={_money(decrease)} VND | chênh lệch={_money(net)} VND."
        )
        lines.append(
            "Quy tắc nghiệp vụ: VO là sheet độc lập chỉ theo dõi phát sinh tăng (+) và giảm (-). "
            "VO không tự điều chỉnh ngân sách/hợp đồng trong hệ thống; khi được chấp thuận, "
            "giá trị hợp đồng chính được cập nhật riêng tại phần Hợp đồng."
        )
        for row in vos:
            inc = float(row.get("increase_amount") or 0)
            dec = float(row.get("decrease_amount") or 0)
            lines.append(
                f"[VO:{row.get('vo_code','')}] ngày={row.get('vo_date','')} | "
                f"tăng={_money(inc)} | giảm={_money(dec)} | chênh lệch={_money(inc + dec)} | "
                f"rev={row.get('latest_revision',0)} | file={row.get('filename','')} | "
                f"cập nhật={row.get('updated_at','')}"
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
