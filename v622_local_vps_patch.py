from __future__ import annotations

import ast
import os


PATCH_MARKER = "V6.22 LOCAL VPS UI V1"
RUNTIME_MARKER = "V6.22 LOCAL VPS RUNTIME INSTALL V1"


def _patch_preview(source: str) -> str:
    tree = ast.parse(source)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_drive_preview_url"), None)
    if fn is None or not fn.body:
        raise RuntimeError("V6.22 local VPS patch: _drive_preview_url not found")
    lines = source.splitlines(keepends=True)
    start = fn.body[0].lineno - 1
    end = fn.body[-1].end_lineno
    indent = " " * (fn.col_offset + 4)
    body = (
        f"{indent}# {PATCH_MARKER}: use the signed URL supplied by the active storage backend.\n"
        f"{indent}direct = str(open_url or '').strip()\n"
        f"{indent}if direct:\n"
        f"{indent}    return direct\n"
        f"{indent}fid = str(file_id or '').strip()\n"
        f"{indent}if not fid:\n"
        f"{indent}    return ''\n"
        f"{indent}name = str(file_name or '').lower()\n"
        f"{indent}ext = Path(name).suffix.lower()\n"
        f"{indent}if ext in {{'.pdf', '.jpg', '.jpeg', '.png', '.webp', '.gif', '.tif', '.tiff'}}:\n"
        f"{indent}    return f'https://drive.google.com/file/d/{{fid}}/preview'\n"
        f"{indent}return f'https://drive.google.com/file/d/{{fid}}/view'\n"
    )
    lines[start:end] = [body]
    return "".join(lines)


def _install_local_runtime_after_webopt(source: str) -> str:
    if RUNTIME_MARKER in source:
        return source
    anchor = (
        "from v621_webopt_runtime import install_runtime as _install_v621_webopt_runtime\n"
        "_install_v621_webopt_runtime()\n"
    )
    if anchor not in source:
        raise RuntimeError("V6.22 local VPS patch: WebOpt runtime anchor missing")
    replacement = anchor + (
        f"# {RUNTIME_MARKER}\n"
        "from local_vps_runtime_fix_v622 import install_local_vps_runtime as _install_local_vps_runtime\n"
        "_install_local_vps_runtime()\n"
    )
    return source.replace(anchor, replacement, 1)


def patch_local_vps(source: str) -> str:
    if PATCH_MARKER in source and RUNTIME_MARKER in source:
        return source
    patched = _install_local_runtime_after_webopt(source)
    patched = _patch_preview(patched)

    if str(os.environ.get("QLDA_STORAGE_BACKEND", "drive")).strip().lower() == "local":
        replacements = (
            ("Google Drive Gateway", "VPS Local Storage"),
            ("Drive Gateway", "VPS Local Storage"),
            ("Google Drive", "VPS"),
            ("Streamlit Cloud • Drive 2GB", "VPS Local • PostgreSQL + SSD"),
            ("PostgreSQL Cloud AI", "PostgreSQL VPS AI"),
            ("PostgreSQL Cloud", "PostgreSQL VPS"),
            ("☁ Google Drive & quyền", "💾 VPS Storage & quyền"),
            ("☁ Drive", "💾 VPS"),
            ("☁ Mở", "💾 Mở"),
            ("BOOTSTRAP_CODE trong Code.gs", "Mã khởi tạo Admin trên VPS"),
            ("Code.gs", "cấu hình VPS"),
            ("Google Apps Script", "VPS Local Storage"),
            ("Apps Script", "VPS Local Storage"),
            ("Thùng rác Drive", "Thùng rác VPS"),
            ("Phiên Drive", "Phiên VPS"),
            ("Drive hiện", "VPS hiện"),
        )
        for old, new in replacements:
            patched = patched.replace(old, new)

    compile(patched, "streamlit_app_v622_local_vps.py", "exec")
    return patched
