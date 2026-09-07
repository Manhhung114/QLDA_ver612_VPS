from pathlib import Path
from tempfile import TemporaryDirectory
import pytest
from cloud_db import CloudDatabase


def approvers():
    return {
        'CONTRACTOR': {'email':'contractor@example.com','name':'Nhà thầu'},
        'SITE_MANAGEMENT': {'email':'site@example.com','name':'Ban điều hành'},
        'CONSULTANT': {'email':'tvgs@example.com','name':'TVGS'},
        'PROJECT_MANAGEMENT': {'email':'pm@example.com','name':'Ban QLDA'},
    }

@pytest.mark.parametrize('doc_type', ['RFA','RFI'])
def test_document_save_atomically_resubmits(doc_type):
    with TemporaryDirectory() as td:
        db=CloudDatabase(Path(td)/'db.sqlite')
        pid=db.add_project('P01','Dự án')
        rid=db.save_document(pid,doc_type,{'code':'S2-MEP-001','subject':'Test','status':'Soạn thảo'})
        wid=db.start_approval_workflow(pid,'document',doc_type,rid,'S2-MEP-001','contractor@example.com',approvers(),'Nhà thầu')
        db.approval_action(wid,'SITE_MANAGEMENT','site@example.com','REQUEST_REVISION','Sửa lại','Ban điều hành',actor_role='SITE_MANAGEMENT')
        assert db.approval_workflow(pid,'document',doc_type,rid)['current_stage']=='CONTRACTOR'
        # Save immediately; no timestamp manipulation / no sleep.
        db.save_document(pid,doc_type,{'code':'S2-MEP-001','subject':'Test sửa','status':'Soạn thảo'},rid)
        wf=db.approval_workflow(pid,'document',doc_type,rid)
        assert wf['current_stage']=='SITE_MANAGEMENT'
        assert int(wf['revision_no'])==1
        # Reviewer can act immediately after contractor save.
        out=db.approval_action(wid,'SITE_MANAGEMENT','site@example.com','APPROVE','OK','Ban điều hành',actor_role='SITE_MANAGEMENT')
        assert out['current_stage']=='CONSULTANT'

@pytest.mark.parametrize('drawing_type', ['SHOPDRAWING','AS_BUILT'])
def test_drawing_save_atomically_resubmits(drawing_type):
    with TemporaryDirectory() as td:
        db=CloudDatabase(Path(td)/'db.sqlite')
        pid=db.add_project('P01','Dự án')
        rid=db.save_drawing(pid,drawing_type,{'drawing_no':'S2-MEP-002','title':'Test','status':'Mới nhận'})
        wid=db.start_approval_workflow(pid,'drawing',drawing_type,rid,'S2-MEP-002','contractor@example.com',approvers(),'Nhà thầu')
        db.approval_action(wid,'SITE_MANAGEMENT','site@example.com','REQUEST_REVISION','Sửa lại','Ban điều hành',actor_role='SITE_MANAGEMENT')
        assert db.approval_workflow(pid,'drawing',drawing_type,rid)['current_stage']=='CONTRACTOR'
        db.save_drawing(pid,drawing_type,{'drawing_no':'S2-MEP-002','title':'Test sửa','status':'Mới nhận'},rid)
        wf=db.approval_workflow(pid,'drawing',drawing_type,rid)
        assert wf['current_stage']=='SITE_MANAGEMENT'
        assert int(wf['revision_no'])==1
        out=db.approval_action(wid,'SITE_MANAGEMENT','site@example.com','APPROVE','OK','Ban điều hành',actor_role='SITE_MANAGEMENT')
        assert out['current_stage']=='CONSULTANT'


def test_v614_markers_and_all_online_types():
    app=Path(__file__).with_name('streamlit_app.py').read_text(encoding='utf-8')
    dbs=Path(__file__).with_name('cloud_db.py').read_text(encoding='utf-8')
    assert 'Workflow engine: **V6.14**' in app
    assert 'Approval UI / Workflow engine: V6.14' in app
    assert "record_kind='document'" in dbs
    assert "record_kind='drawing'" in dbs
    assert 'current_stage"] or "") == "CONTRACTOR"' in dbs


@pytest.mark.parametrize('kind, subtype', [
    ('document','RFA'), ('document','RFI'),
    ('drawing','SHOPDRAWING'), ('drawing','AS_BUILT'),
])
@pytest.mark.parametrize('return_stage', ['SITE_MANAGEMENT','CONSULTANT','PROJECT_MANAGEMENT'])
def test_save_returns_to_exact_stage_for_every_online_sheet(kind, subtype, return_stage):
    with TemporaryDirectory() as td:
        db=CloudDatabase(Path(td)/'db.sqlite')
        pid=db.add_project('P02','Dự án 2')
        if kind == 'document':
            rid=db.save_document(pid,subtype,{'code':'S2-MEP-010','subject':'Hồ sơ','status':'Soạn thảo'})
            code='S2-MEP-010'
        else:
            rid=db.save_drawing(pid,subtype,{'drawing_no':'S2-MEP-010','title':'Bản vẽ','status':'Mới nhận'})
            code='S2-MEP-010'
        wid=db.start_approval_workflow(pid,kind,subtype,rid,code,'contractor@example.com',approvers(),'Nhà thầu')
        if return_stage in {'CONSULTANT','PROJECT_MANAGEMENT'}:
            db.approval_action(wid,'SITE_MANAGEMENT','site@example.com','APPROVE','OK','Ban điều hành',actor_role='SITE_MANAGEMENT')
        if return_stage == 'PROJECT_MANAGEMENT':
            db.approval_action(wid,'CONSULTANT','tvgs@example.com','APPROVE','OK','TVGS',actor_role='CONSULTANT')
        actor={'SITE_MANAGEMENT':('site@example.com','Ban điều hành'), 'CONSULTANT':('tvgs@example.com','TVGS'), 'PROJECT_MANAGEMENT':('pm@example.com','Ban QLDA')}[return_stage]
        db.approval_action(wid,return_stage,actor[0],'REQUEST_REVISION','Cập nhật lại',actor[1],actor_role=return_stage)
        assert db.approval_workflow(pid,kind,subtype,rid)['current_stage']=='CONTRACTOR'
        if kind == 'document':
            db.save_document(pid,subtype,{'code':code,'subject':'Hồ sơ đã sửa','status':'Soạn thảo'},rid)
        else:
            db.save_drawing(pid,subtype,{'drawing_no':code,'title':'Bản vẽ đã sửa','status':'Mới nhận'},rid)
        wf=db.approval_workflow(pid,kind,subtype,rid)
        assert wf['current_stage']==return_stage
        assert int(wf['revision_no'])==1
        steps={x['stage_code']:x for x in db.approval_steps(wid)}
        assert steps[return_stage]['status']=='Đang chờ duyệt'
        # The exact reviewer can now approve without any extra contractor action.
        out=db.approval_action(wid,return_stage,actor[0],'APPROVE','Đạt',actor[1],actor_role=return_stage)
        assert out['current_stage'] != 'CONTRACTOR'
