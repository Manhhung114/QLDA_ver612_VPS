from pathlib import Path

ROOT = Path(__file__).resolve().parent
app = (ROOT / 'streamlit_app.py').read_text(encoding='utf-8')
gs = (ROOT / 'google_drive_appscript' / 'Code.gs').read_text(encoding='utf-8')
req = (ROOT / 'requirements.txt').read_text(encoding='utf-8')

assert 'QLDA_DRIVE_WEBAPP_URL' in app
assert 'QLDA_DRIVE_API_TOKEN' in app
assert 'GOOGLE_OAUTH_CLIENT_ID' not in app
assert 'GOOGLE_DRIVE_ROOT_FOLDER_ID = ' not in app
assert 'google-api-python-client' not in req
assert "case 'upload'" in gs
assert "case 'set_user'" in gs
assert "case 'login'" in gs
assert "_Lich_su" in gs
assert "02_Ho_so" in gs and "03_Ban_ve" in gs and "04_Phat_sinh_VO" in gs
print('V4.1.1 DRIVE ONLY TEST: OK')
