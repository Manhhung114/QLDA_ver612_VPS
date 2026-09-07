from v615_runtime_patch import install_db_patch as _db15, patch_streamlit_source as _ui15


def install_db_patch():
    _db15()


def _one(s, a, b, name):
    if a not in s:
        raise RuntimeError('V6.16 patch anchor missing: ' + name)
    return s.replace(a, b, 1)


def patch_streamlit_source(source):
    s = _ui15(source)
    s = s.replace('Approval UI / Workflow engine: V6.15', 'Approval UI / Workflow engine: V6.16')
    s = s.replace('Workflow engine: **V6.15**', 'Workflow engine: **V6.16**')
    s = s.replace(
        'identity = _cloud_identity()\n    approval_role = _user_approval_role(identity)\n    is_contractor = approval_role == "CONTRACTOR"',
        'identity = _cloud_identity(refresh=True)\n    approval_role = _user_approval_role(identity)\n    is_contractor = approval_role == "CONTRACTOR"')
    s = _one(s,
        'identity = _cloud_identity()\n    email = str(identity.get("email") or "").lower()\n    display_name = str(identity.get("name") or "")\n    approval_role = _user_approval_role(identity)',
        'identity = _cloud_identity(refresh=True)\n    email = str(identity.get("email") or "").lower()\n    display_name = str(identity.get("name") or "")\n    approval_role = _user_approval_role(identity)', 'online identity')

    for kind, subtype_var in [('document','doc_type'), ('drawing','drawing_type')]:
        old = f'''    record_wf = db.approval_workflow(pid, "{kind}", {subtype_var}, int(selected)) if selected else None\n    contractor_edit_locked = bool(\n        is_contractor and record_wf and str(record_wf["current_stage"] or "") not in {{"CONTRACTOR"}}\n    )\n    can_edit_current = bool(can_edit_submission and not contractor_edit_locked)'''
        new = f'''    record_wf = db.approval_workflow(pid, "{kind}", {subtype_var}, int(selected)) if selected else None\n    current_email = str(identity.get("email") or "").strip().lower()\n    own_returned_workflow = bool(\n        record_wf\n        and str(record_wf["current_stage"] or "").strip().upper() == "CONTRACTOR"\n        and current_email\n        and current_email == str(record_wf["submitted_by"] or "").strip().lower()\n    )\n    effective_contractor = bool(is_contractor or own_returned_workflow)\n    contractor_edit_locked = bool(\n        effective_contractor and record_wf and str(record_wf["current_stage"] or "").strip().upper() not in {{"CONTRACTOR"}}\n    )\n    can_edit_current = bool(_can_update() and effective_contractor and not contractor_edit_locked)'''
        s = _one(s, old, new, kind + ' contractor ownership')

    s = s.replace('if is_contractor and can_edit_current:\n                attachment_count = contractor_attachment_count',
                  'if can_edit_current:\n                attachment_count = contractor_attachment_count')
    s = _one(s,
        '    reviewer_role = approval_role in {"SITE_MANAGEMENT", "CONSULTANT", "PROJECT_MANAGEMENT"}\n    if reviewer_role:',
        '''    current_step_for_identity = next((x for x in steps if str(x["stage_code"] or "").strip().upper() == current_stage.strip().upper()), None)\n    assigned_current_email = str(current_step_for_identity["approver_email"] or "").strip().lower() if current_step_for_identity else ""\n    reviewer_role = bool(\n        approval_role in {"SITE_MANAGEMENT", "CONSULTANT", "PROJECT_MANAGEMENT"}\n        or (current_stage not in {"DONE", "CONTRACTOR"} and email and assigned_current_email == email)\n    )\n    if reviewer_role:''', 'reviewer identity')
    s = _one(s,
        '''    reviewer_can_claim = bool(\n        current_step\n        and current_stage not in {"DONE", "CONTRACTOR"}\n        and approval_role == current_stage\n    )''',
        '''    reviewer_can_claim = bool(\n        current_step\n        and current_stage not in {"DONE", "CONTRACTOR"}\n        and (approval_role == current_stage or (email and assigned_email == email))\n    )''', 'review button')
    s = s.replace('actor_name=display_name, actor_role=approval_role\n            )',
                  'actor_name=display_name, actor_role=(current_stage if (email and assigned_email == email) else approval_role)\n            )', 2)
    s = _one(s,
        'elif current_stage == "CONTRACTOR" and approval_role == "CONTRACTOR":',
        'elif current_stage == "CONTRACTOR" and (approval_role == "CONTRACTOR" or (email and email == str(wf["submitted_by"] or "").strip().lower())):', 'contractor panel')
    return s
