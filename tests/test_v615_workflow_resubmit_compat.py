from pathlib import Path
from tempfile import TemporaryDirectory
import pytest

from v615_runtime_patch import install_db_patch
install_db_patch()
from cloud_db import CloudDatabase


def approvers():
    return {
        'CONTRACTOR': {'email':'contractor@example.com','name':'Nhà thầu'},
        'SITE_MANAGEMENT': {'email':'site@example.com','name':'Ban điều hành'},
        'CONSULTANT': {'email':'tvgs@example.com','name':'TVGS'},
        'PROJECT_MANAGEMENT': {'email':'pm@example.com','name':'Ban QLDA'},
    }


@pytest.mark.parametrize('kind, subtype', [
    ('document','RFA'), ('document','RFI'),
    ('drawing','SHOPDRAWING'), ('drawing','AS_BUILT'),
])
@pytest.mark.parametrize('return_stage', ['SITE_MANAGEMENT','CONSULTANT','PROJECT_MANAGEMENT'])
def test_v615_save_resubmits_to_exact_return_stage(kind, subtype, return_stage):
    with TemporaryDirectory() as td:
        db=CloudDatabase(Path(td)/'db.sqlite')
        pid=db.add_project('P15','Dự án V6.15')
        if kind == 'document':
            rid=db.save_document(pid,subtype,{'code':'S2-MEP-015','subject':'Hồ sơ','status':'Soạn thảo'})
        else:
            rid=db.save_drawing(pid,subtype,{'drawing_no':'S2-MEP-015','title':'Bản vẽ','status':'Mới nhận'})
        wid=db.start_approval_workflow(pid,kind,subtype,rid,'S2-MEP-015','contractor@example.com',approvers(),'Nhà thầu')
        if return_stage in {'CONSULTANT','PROJECT_MANAGEMENT'}:
            db.approval_action(wid,'SITE_MANAGEMENT','site@example.com','APPROVE','OK','Ban điều hành',actor_role='SITE_MANAGEMENT')
        if return_stage == 'PROJECT_MANAGEMENT':
            db.approval_action(wid,'CONSULTANT','tvgs@example.com','APPROVE','OK','TVGS',actor_role='CONSULTANT')
        actor={
            'SITE_MANAGEMENT':('site@example.com','Ban điều hành'),
            'CONSULTANT':('tvgs@example.com','TVGS'),
            'PROJECT_MANAGEMENT':('pm@example.com','Ban QLDA'),
        }[return_stage]
        db.approval_action(wid,return_stage,actor[0],'REQUEST_REVISION','Cập nhật lại',actor[1],actor_role=return_stage)
        assert db.approval_workflow(pid,kind,subtype,rid)['current_stage']=='CONTRACTOR'

        # Simulate legacy subtype drift to prove V6.15 lookup no longer depends on subtype.
        with db.connect() as c:
            c.execute("UPDATE approval_workflows SET subtype=? WHERE id=?", ('legacy_'+subtype.lower(), wid))

        if kind == 'document':
            db.save_document(pid,subtype,{'code':'S2-MEP-015','subject':'Hồ sơ đã sửa','status':'Soạn thảo'},rid)
        else:
            db.save_drawing(pid,subtype,{'drawing_no':'S2-MEP-015','title':'Bản vẽ đã sửa','status':'Mới nhận'},rid)

        with db.connect() as c:
            wf=c.execute('SELECT * FROM approval_workflows WHERE id=?',(wid,)).fetchone()
        assert wf['current_stage']==return_stage
        assert int(wf['revision_no'])==1
        out=db.approval_action(wid,return_stage,actor[0],'APPROVE','Đạt',actor[1],actor_role=return_stage)
        assert out['current_stage'] != 'CONTRACTOR'


def test_v615_markers():
    root=Path(__file__).resolve().parent
    app=(root/'streamlit_app.py').read_text(encoding='utf-8')
    assert 'Workflow engine: **V6.15**' in app
    assert 'Approval UI / Workflow engine: V6.15' in app
    assert (root/'VERSION.txt').read_text(encoding='utf-8').strip()=='6.15'
