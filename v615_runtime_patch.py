from __future__ import annotations


def _norm_stage(value: object) -> str:
    return str(value or "").strip().upper()


def _infer_return_stage(c, wf) -> str:
    valid = {"SITE_MANAGEMENT", "CONSULTANT", "PROJECT_MANAGEMENT"}
    aliases = {
        "SITE_MANAGEMENT": "SITE_MANAGEMENT",
        "BAN DIEU HANH": "SITE_MANAGEMENT",
        "BAN ĐIỀU HÀNH": "SITE_MANAGEMENT",
        "TVGS": "CONSULTANT",
        "CONSULTANT": "CONSULTANT",
        "TU VAN GIAM SAT": "CONSULTANT",
        "TƯ VẤN GIÁM SÁT": "CONSULTANT",
        "BQLDA": "PROJECT_MANAGEMENT",
        "PROJECT_MANAGEMENT": "PROJECT_MANAGEMENT",
        "BAN QLDA": "PROJECT_MANAGEMENT",
        "BAN QUAN LY DU AN": "PROJECT_MANAGEMENT",
        "BAN QUẢN LÝ DỰ ÁN": "PROJECT_MANAGEMENT",
    }
    raw = _norm_stage(wf["return_stage"] if "return_stage" in wf.keys() else "")
    if raw in valid:
        return raw
    if raw in aliases:
        return aliases[raw]

    try:
        h = c.execute(
            """SELECT stage_code,stage_label FROM approval_history
               WHERE workflow_id=? AND UPPER(TRIM(action)) IN ('REQUEST_REVISION','REJECT','RETURN')
               ORDER BY id DESC LIMIT 1""",
            (int(wf["id"]),),
        ).fetchone()
        if h:
            code = _norm_stage(h["stage_code"])
            if code in valid:
                return code
            label = _norm_stage(h["stage_label"])
            if label in aliases:
                return aliases[label]
    except Exception:
        pass

    status = _norm_stage(wf["overall_status"] if "overall_status" in wf.keys() else "")
    if "BAN ĐIỀU HÀNH" in status or "BAN DIEU HANH" in status:
        return "SITE_MANAGEMENT"
    if "TƯ VẤN GIÁM SÁT" in status or "TU VAN GIAM SAT" in status or "TVGS" in status:
        return "CONSULTANT"
    if "BAN QLDA" in status or "BAN QUẢN LÝ DỰ ÁN" in status or "BAN QUAN LY DU AN" in status:
        return "PROJECT_MANAGEMENT"
    return "SITE_MANAGEMENT"


def install_db_patch() -> None:
    """V6.15: Save of a returned record MUST resubmit it in the same transaction."""
    from cloud_db import CloudDatabase, _now

    if getattr(CloudDatabase, "_v615_revision_resubmit", False):
        return

    def _find_workflow(c, project_id: int, record_kind: str, record_id: int):
        return c.execute(
            """SELECT * FROM approval_workflows
               WHERE project_id=? AND LOWER(TRIM(record_kind))=? AND record_id=?
               ORDER BY CASE WHEN UPPER(TRIM(current_stage))='CONTRACTOR' THEN 0 ELSE 1 END, id DESC
               LIMIT 1""",
            (int(project_id), str(record_kind).strip().lower(), int(record_id)),
        ).fetchone()

    def _is_waiting(wf) -> bool:
        if not wf:
            return False
        current = _norm_stage(wf["current_stage"])
        status = _norm_stage(wf["overall_status"])
        return current == "CONTRACTOR" or "CHỜ NHÀ THẦU CHỈNH SỬA" in status or "CHO NHA THAU CHINH SUA" in status

    def _force_resubmit(self, c, wf, actor_email: str = "", actor_name: str = "") -> dict:
        if not wf:
            return {"resubmitted": False, "reason": "no_workflow"}
        if not _is_waiting(wf):
            return {"resubmitted": False, "reason": "not_waiting_contractor", "current_stage": _norm_stage(wf["current_stage"])}

        wid = int(wf["id"])
        target_stage = _infer_return_stage(c, wf)
        target = c.execute(
            "SELECT * FROM approval_steps WHERE workflow_id=? AND UPPER(TRIM(stage_code))=? LIMIT 1",
            (wid, target_stage),
        ).fetchone()
        if not target:
            target = c.execute(
                "SELECT * FROM approval_steps WHERE workflow_id=? AND stage_order>=1 ORDER BY stage_order LIMIT 1",
                (wid,),
            ).fetchone()
        if not target:
            raise RuntimeError("Workflow không có bước duyệt để trình lại.")

        target_stage = _norm_stage(target["stage_code"])
        target_label = str(target["stage_label"] or target_stage)
        revision_no = int(wf["revision_no"] or 0) + 1 if "revision_no" in wf.keys() else 1
        now = _now()
        email = str(actor_email or wf["submitted_by"] or "").strip().lower()

        c.execute(
            """UPDATE approval_steps SET status='Đã trình lại',acted_by=?,acted_at=?
               WHERE workflow_id=? AND UPPER(TRIM(stage_code))='CONTRACTOR'""",
            (email, now, wid),
        )
        c.execute(
            """UPDATE approval_steps SET status='Đang chờ duyệt',comment='',acted_by='',acted_at=''
               WHERE id=?""",
            (int(target["id"]),),
        )
        c.execute(
            "UPDATE approval_steps SET status='Chờ' WHERE workflow_id=? AND stage_order>?",
            (wid, int(target["stage_order"])),
        )
        overall = "Trình lại - Đang duyệt - " + target_label
        c.execute(
            """UPDATE approval_workflows
               SET overall_status=?,current_stage=?,submitted_by=?,submitted_at=?,updated_at=?,revision_no=?,return_stage=''
               WHERE id=?""",
            (overall, target_stage, email, now, now, revision_no, wid),
        )
        table = "documents" if str(wf["record_kind"] or "").strip().lower() == "document" else "drawings"
        c.execute(f"UPDATE {table} SET status=?,updated_at=? WHERE id=?", (overall, now, int(wf["record_id"])))
        try:
            self._approval_log(
                c, wid, revision_no, "CONTRACTOR", "Nhà thầu", "RESUBMIT", overall,
                actor_email=email, actor_name=str(actor_name or ""),
            )
        except Exception:
            pass
        return {
            "resubmitted": True,
            "workflow_id": wid,
            "status": overall,
            "current_stage": target_stage,
            "revision_no": revision_no,
        }

    def force_revision_resubmit(self, project_id: int, record_kind: str, record_id: int, submitted_by: str = "", submitted_name: str = "") -> dict:
        with self.connect() as c:
            wf = _find_workflow(c, project_id, record_kind, record_id)
            return _force_resubmit(self, c, wf, submitted_by, submitted_name)

    def save_document(self, project_id: int, doc_type: str, data: dict, doc_id: int | None = None) -> int:
        fields = ["code","subject","discipline","contractor","issuer","assignee","issue_date","due_date","closed_date",
                  "status","priority","related_wbs","description","response","note","cost_impact","time_impact_days"]
        vals = [data.get(f, "") for f in fields]
        with self.connect() as c:
            if doc_id:
                c.execute(
                    f"UPDATE documents SET {','.join(f'{f}=?' for f in fields)},updated_at=? WHERE id=?",
                    vals + [_now(), int(doc_id)],
                )
                wf = _find_workflow(c, project_id, "document", int(doc_id))
                if _is_waiting(wf):
                    _force_resubmit(self, c, wf, str(wf["submitted_by"] or ""), "")
                    after = _find_workflow(c, project_id, "document", int(doc_id))
                    if after and _norm_stage(after["current_stage"]) == "CONTRACTOR":
                        raise RuntimeError("Đã lưu hồ sơ nhưng workflow vẫn ở CONTRACTOR.")
                return int(doc_id)
            cur = c.execute(
                f"INSERT INTO documents(project_id,doc_type,{','.join(fields)},created_at,updated_at) VALUES(?,?,{','.join('?' for _ in fields)},?,?)",
                [project_id, doc_type] + vals + [_now(), _now()],
            )
            return int(cur.lastrowid)

    def save_drawing(self, project_id: int, drawing_type: str, data: dict, drawing_id: int | None = None) -> int:
        fields = ["drawing_no","title","discipline","revision","issuer","receiver","received_date","issue_date","due_date",
                  "priority","description","status","related_wbs","reference_no","note"]
        vals = [data.get(f, "") for f in fields]
        with self.connect() as c:
            if drawing_id:
                c.execute(
                    f"UPDATE drawings SET {','.join(f'{f}=?' for f in fields)},updated_at=? WHERE id=?",
                    vals + [_now(), int(drawing_id)],
                )
                wf = _find_workflow(c, project_id, "drawing", int(drawing_id))
                if _is_waiting(wf):
                    _force_resubmit(self, c, wf, str(wf["submitted_by"] or ""), "")
                    after = _find_workflow(c, project_id, "drawing", int(drawing_id))
                    if after and _norm_stage(after["current_stage"]) == "CONTRACTOR":
                        raise RuntimeError("Đã lưu bản vẽ nhưng workflow vẫn ở CONTRACTOR.")
                return int(drawing_id)
            cur = c.execute(
                f"INSERT INTO drawings(project_id,drawing_type,{','.join(fields)},created_at,updated_at) VALUES(?,?,{','.join('?' for _ in fields)},?,?)",
                [project_id, drawing_type] + vals + [_now(), _now()],
            )
            return int(cur.lastrowid)

    CloudDatabase.force_revision_resubmit = force_revision_resubmit
    CloudDatabase.save_document = save_document
    CloudDatabase.save_drawing = save_drawing
    CloudDatabase._v615_revision_resubmit = True


def patch_streamlit_source(source: str) -> str:
    source = source.replace("Approval UI / Workflow engine: V6.12", "Approval UI / Workflow engine: V6.15")
    source = source.replace("Workflow engine: **V6.9**", "Workflow engine: **V6.15**")

    # Explicit post-save second check for the two role-based approval forms.
    doc_anchor = '''                    route = _ensure_approval_workflow_started(\n                        pid, "document", doc_type, int(doc_id), normalized_code, subject,\n                        submitted_email=str(identity.get("email") or ""),\n                        submitted_name=str(identity.get("name") or issuer or ""),\n                        current_identity=identity,\n                        notify=True,\n                    )\n'''
    doc_extra = doc_anchor + '''                    if is_revision_return:\n                        forced = db.force_revision_resubmit(\n                            pid, "document", int(doc_id),\n                            submitted_by=str(identity.get("email") or ""),\n                            submitted_name=str(identity.get("name") or issuer or ""),\n                        )\n                        post_save_wf = db.approval_workflow(pid, "document", doc_type, int(doc_id))\n                        if post_save_wf and str(post_save_wf["current_stage"] or "").strip().upper() == "CONTRACTOR":\n                            st.error("❌ Lưu hồ sơ chưa chuyển được về cấp duyệt. Hệ thống không ghi nhận trạng thái sai.")\n                            st.stop()\n                        if forced.get("resubmitted") or (post_save_wf and str(post_save_wf["current_stage"] or "").strip().upper() != "CONTRACTOR"):\n                            route = {**route, "ok": True, "resubmitted": True}\n'''
    if doc_anchor not in source:
        raise RuntimeError("V6.15 patch failed: document save anchor not found")
    source = source.replace(doc_anchor, doc_extra, 1)

    drawing_anchor = '''                    route = _ensure_approval_workflow_started(\n                        pid, "drawing", drawing_type, int(drawing_id), normalized_number, title,\n                        submitted_email=str(identity.get("email") or ""),\n                        submitted_name=str(identity.get("name") or submitter or ""),\n                        current_identity=identity,\n                        notify=True,\n                    )\n'''
    drawing_extra = drawing_anchor + '''                    if is_revision_return:\n                        forced = db.force_revision_resubmit(\n                            pid, "drawing", int(drawing_id),\n                            submitted_by=str(identity.get("email") or ""),\n                            submitted_name=str(identity.get("name") or submitter or ""),\n                        )\n                        post_save_wf = db.approval_workflow(pid, "drawing", drawing_type, int(drawing_id))\n                        if post_save_wf and str(post_save_wf["current_stage"] or "").strip().upper() == "CONTRACTOR":\n                            st.error("❌ Lưu bản vẽ chưa chuyển được về cấp duyệt. Hệ thống không ghi nhận trạng thái sai.")\n                            st.stop()\n                        if forced.get("resubmitted") or (post_save_wf and str(post_save_wf["current_stage"] or "").strip().upper() != "CONTRACTOR"):\n                            route = {**route, "ok": True, "resubmitted": True}\n'''
    if drawing_anchor not in source:
        raise RuntimeError("V6.15 patch failed: drawing save anchor not found")
    source = source.replace(drawing_anchor, drawing_extra, 1)
    return source
