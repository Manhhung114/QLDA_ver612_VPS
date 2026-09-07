import json
import sys
from pathlib import Path
from mpp_reader import MppComReader

if len(sys.argv) < 2:
    print('Cách dùng: python check_mpp.py "D:\\duong_dan\\tien_do.mpp"')
    raise SystemExit(2)

path = Path(sys.argv[1])
data = MppComReader().read(path)
print(f"Project: {data['project_name']}")
print(f"Start/Finish: {data['project_start']} -> {data['project_finish']}")
print(f"Tasks: {len(data['tasks'])}")
out = path.with_suffix('.qlda_preview.json')
out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"Đã xuất kiểm tra: {out}")
