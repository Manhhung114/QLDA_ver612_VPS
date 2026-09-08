from __future__ import annotations

from typing import Any


PATCH_VERSION = "V6.22 IPC CLAIM DELETE V1"


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


def _replace_claim_items(connection, ipc, project_id: int, claim_id: str, result: dict[str, Any]) -> None:
    connection.execute(f"DELETE FROM {ipc.ITEMS_TABLE} WHERE claim_id=?", (str(claim_id),))
    item_fields = [
        "claim_id","project_id","sheet_name","row_no","seq","boq_item","contract_qty","unit","spec","item_code","brand","origin",
        "material_unit_price","labor_unit_price","contract_amount","material_previous_qty","material_current_qty","material_cumulative_qty",
        "installation_previous_pct","installation_current_pct","installation_cumulative_pct","material_previous_value","material_current_value",
        "material_cumulative_value","installation_previous_value","installation_current_value","installation_cumulative_value",
        "deduction_previous","deduction_current","deduction_cumulative","current_value","cumulative_value","completion_ratio","note","cost_code","system",
    ]
    params = []
    for item in result.get("detail_items") or []:
        row = {**item, "claim_id": str(claim_id), "project_id": int(project_id)}
        params.append(tuple(row.get(field, "") for field in item_fields))
    if params:
        sql = f"INSERT INTO {ipc.ITEMS_TABLE}({','.join(item_fields)}) VALUES({','.join('?' for _ in item_fields)})"
        connection.executemany(sql, params)


def delete_ipc_revision(db, claim_id: str, revision_no: int) -> dict[str, Any]:
    """Delete one uploaded revision.

    If the deleted revision is the current/latest workbook, the immediately
    preceding revision becomes active again. Revision 0 is protected here;
    delete the whole Claim when the original file itself must be removed.
    """
    import ipc_claim_v622 as ipc

    cid = str(claim_id)
    rev_no = int(revision_no)
    if rev_no <= 0:
        raise ValueError("Revision 0 là file gốc. Hãy dùng 'Xóa toàn bộ Claim' nếu cần xóa file gốc.")

    now = ipc._now()
    with db.connect() as connection:
        ipc._ensure_tables(connection)
        claim_row = connection.execute(
            f"SELECT * FROM {ipc.CLAIMS_TABLE} WHERE claim_id=?", (cid,)
        ).fetchone()
        if claim_row is None:
            raise ValueError("Không tìm thấy Claim cần xóa.")
        claim = _rowdict(claim_row)
        pid = int(claim.get("project_id") or 0)

        target_row = connection.execute(
            f"SELECT revision_id,revision_no,filename,batch_id,payload,created_at FROM {ipc.REVISIONS_TABLE} "
            "WHERE claim_id=? AND revision_no=?",
            (cid, rev_no),
        ).fetchone()
        if target_row is None:
            raise ValueError(f"Không tìm thấy Revision {rev_no}.")

        connection.execute(
            f"DELETE FROM {ipc.REVISIONS_TABLE} WHERE claim_id=? AND revision_no=?",
            (cid, rev_no),
        )

        latest = int(claim.get("latest_revision") or 0)
        restored_to = latest
        if rev_no == latest:
            previous_row = connection.execute(
                f"SELECT revision_id,revision_no,filename,batch_id,payload,created_at FROM {ipc.REVISIONS_TABLE} "
                "WHERE claim_id=? ORDER BY revision_no DESC LIMIT 1",
                (cid,),
            ).fetchone()
            if previous_row is None:
                raise ValueError("Không còn revision trước để khôi phục. Hãy xóa toàn bộ Claim thay vì xóa file gốc.")
            previous = _rowdict(previous_row)
            restored_to = int(previous.get("revision_no") or 0)
            payload = str(previous.get("payload") or "")
            result = ipc._decode_result(payload)
            metadata = dict(result.get("metadata") or {})
            summary = dict(result.get("summary") or {})
            filename = str(previous.get("filename") or result.get("filename") or "IPC.xlsx")
            batch_id = str(previous.get("batch_id") or result.get("batch_id") or "")

            connection.execute(
                f"""UPDATE {ipc.CLAIMS_TABLE}
                       SET filename=?,batch_id=?,contractor=?,contract_no=?,package_name=?,from_date=?,to_date=?,
                           contract_value=?,requested_amount=?,certified_cumulative=?,previous_approved=?,retention_cumulative=?,
                           advance_amount=?,advance_recovery=?,current_deductions=?,latest_revision=?,updated_at=?
                       WHERE claim_id=?""",
                (
                    filename,
                    batch_id,
                    str(metadata.get("contractor") or ""),
                    str(metadata.get("contract_no") or ""),
                    str(metadata.get("package") or ""),
                    str(metadata.get("from_date") or ""),
                    str(metadata.get("to_date") or ""),
                    float(summary.get("contract_value") or 0),
                    float(summary.get("requested_amount") or 0),
                    float(summary.get("cumulative_completed") or summary.get("cumulative_acceptance") or 0),
                    float(summary.get("previous_approved") or 0),
                    float(summary.get("cumulative_retention") or 0),
                    float(summary.get("contract_advance") or 0),
                    float(summary.get("cumulative_advance_recovery") or 0),
                    float(summary.get("current_deductions") or 0),
                    restored_to,
                    now,
                    cid,
                ),
            )
            connection.execute(f"DELETE FROM {ipc.WORKBOOK_TABLE} WHERE claim_id=?", (cid,))
            connection.execute(
                f"INSERT INTO {ipc.WORKBOOK_TABLE}(claim_id,project_id,filename,batch_id,payload,updated_at) VALUES(?,?,?,?,?,?)",
                (cid, pid, filename, batch_id, payload, now),
            )
            _replace_claim_items(connection, ipc, pid, cid, result)
            refreshed = connection.execute(
                f"SELECT * FROM {ipc.CLAIMS_TABLE} WHERE claim_id=?", (cid,)
            ).fetchone()
            if refreshed is not None:
                ipc._sync_payment_tracking(connection, _rowdict(refreshed))

    return {
        "claim_id": cid,
        "deleted_revision": rev_no,
        "restored_revision": restored_to,
        "restored_previous": bool(rev_no == latest),
    }


def delete_ipc_claim(db, claim_id: str) -> dict[str, Any]:
    """Permanently remove one Claim and all its Excel revisions/details."""
    import ipc_claim_v622 as ipc

    cid = str(claim_id)
    with db.connect() as connection:
        ipc._ensure_tables(connection)
        row = connection.execute(
            f"SELECT * FROM {ipc.CLAIMS_TABLE} WHERE claim_id=?", (cid,)
        ).fetchone()
        if row is None:
            raise ValueError("Không tìm thấy Claim cần xóa.")
        claim = _rowdict(row)
        pid = int(claim.get("project_id") or 0)
        code = str(claim.get("claim_code") or "")

        connection.execute(f"DELETE FROM {ipc.ITEMS_TABLE} WHERE claim_id=?", (cid,))
        connection.execute(f"DELETE FROM {ipc.WORKBOOK_TABLE} WHERE claim_id=?", (cid,))
        connection.execute(f"DELETE FROM {ipc.REVISIONS_TABLE} WHERE claim_id=?", (cid,))
        connection.execute(f"DELETE FROM {ipc.CLAIMS_TABLE} WHERE claim_id=?", (cid,))
        connection.execute(
            "DELETE FROM payment_tracking WHERE project_id=? AND payment_code=?",
            (pid, code),
        )

    return {"claim_id": cid, "claim_code": code, "project_id": pid}


def render_ipc_claim_delete_ui(db, project_id: int, *, can_update: bool = True) -> None:
    import pandas as pd
    import streamlit as st
    import ipc_claim_v622 as ipc

    pid = int(project_id)
    claims = ipc.list_ipc_claims(db, pid)
    if not claims:
        return

    with st.expander("🗑️ Quản lý file cập nhật / xóa Claim", expanded=False):
        st.caption(
            "Xóa Revision chỉ xóa file cập nhật đã chọn. Nếu xóa revision mới nhất, hệ thống tự khôi phục file ngay trước đó. "
            "Revision 0 là file gốc và chỉ được xóa bằng chức năng Xóa toàn bộ Claim."
        )
        claim_map = {str(c.get("claim_code") or c.get("claim_no") or c.get("claim_id")): c for c in claims}
        selected_label = st.selectbox(
            "Chọn Claim",
            list(claim_map.keys()),
            key=f"ipc_delete_claim_select_{pid}",
        )
        claim = claim_map[selected_label]
        claim_id = str(claim.get("claim_id") or "")
        revisions = ipc.ipc_claim_revisions(db, claim_id)

        if revisions:
            rev_df = pd.DataFrame(revisions).rename(columns={
                "revision_no": "Revision", "filename": "File Excel", "batch_id": "Batch", "created_at": "Ngày cập nhật"
            })
            cols = [c for c in ["Revision", "File Excel", "Batch", "Ngày cập nhật"] if c in rev_df.columns]
            st.dataframe(rev_df[cols], hide_index=True, width="stretch")

            update_revs = [r for r in revisions if int(r.get("revision_no") or 0) > 0]
            if update_revs:
                rev_map = {
                    f"Revision {int(r.get('revision_no') or 0)} · {str(r.get('filename') or '')} · {str(r.get('created_at') or '')}": r
                    for r in update_revs
                }
                rev_label = st.selectbox(
                    "File cập nhật cần xóa",
                    list(rev_map.keys()),
                    key=f"ipc_delete_revision_select_{claim_id}",
                )
                selected_rev = rev_map[rev_label]
                confirm_rev = st.checkbox(
                    "Tôi xác nhận xóa file revision đã chọn",
                    key=f"ipc_delete_revision_confirm_{claim_id}_{selected_rev.get('revision_no')}",
                )
                if st.button(
                    "🗑️ Xóa file cập nhật đã chọn",
                    disabled=not bool(can_update and confirm_rev),
                    key=f"ipc_delete_revision_button_{claim_id}_{selected_rev.get('revision_no')}",
                    width="stretch",
                ):
                    try:
                        result = delete_ipc_revision(db, claim_id, int(selected_rev.get("revision_no") or 0))
                        if result.get("restored_previous"):
                            st.success(
                                f"Đã xóa Revision {result['deleted_revision']} và khôi phục Revision {result['restored_revision']} làm file hiện hành."
                            )
                        else:
                            st.success(f"Đã xóa Revision {result['deleted_revision']} khỏi lịch sử Claim.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Không thể xóa file cập nhật: {exc}")
            else:
                st.info("Claim này chưa có file cập nhật (chỉ có Revision 0/file gốc).")

        st.markdown("#### Xóa toàn bộ Claim")
        st.warning(
            "Thao tác này xóa Claim, workbook hiện hành, toàn bộ revision, dòng GTHT và bản ghi đồng bộ thanh toán. Không thể hoàn tác trên giao diện."
        )
        claim_code = str(claim.get("claim_code") or "")
        confirm_code = st.text_input(
            f"Nhập chính xác {claim_code} để xác nhận",
            key=f"ipc_delete_claim_confirm_{claim_id}",
        )
        if st.button(
            f"🗑️ Xóa toàn bộ {claim_code}",
            disabled=not bool(can_update and confirm_code.strip() == claim_code),
            key=f"ipc_delete_claim_button_{claim_id}",
            width="stretch",
        ):
            try:
                result = delete_ipc_claim(db, claim_id)
                st.success(f"Đã xóa toàn bộ {result['claim_code']} khỏi dự án.")
                st.rerun()
            except Exception as exc:
                st.error(f"Không thể xóa Claim: {exc}")
