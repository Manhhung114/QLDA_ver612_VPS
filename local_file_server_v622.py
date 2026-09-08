from __future__ import annotations

import argparse
import html
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from local_vps_backend_v622 import (
    LocalVPSError,
    direct_max_bytes,
    health,
    local_file_path,
    save_stream_from_ticket,
    verify_file_signature,
    verify_upload_ticket,
)


SERVER_VERSION = "QLDA-Local-File-Server/6.22"


def _json_bytes(data) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class QLDAFileHandler(BaseHTTPRequestHandler):
    server_version = SERVER_VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args):
        path = urlparse(self.path).path
        print(f"{self.client_address[0]} {self.command} {path} - " + (fmt % args), flush=True)

    def _send_json(self, status: int, payload: dict):
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, message: str):
        self._send_json(status, {"ok": False, "error": str(message)})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            try:
                data = health()
                self._send_json(200, {"ok": True, "service": SERVER_VERSION, "initialized": bool(data.get("initialized"))})
            except Exception as exc:
                self._error(503, str(exc))
            return
        if parsed.path == "/upload":
            self._render_upload_page(parsed)
            return
        if parsed.path.startswith("/file/"):
            self._serve_file(parsed, head_only=False)
            return
        self._error(404, "Not found")

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/file/"):
            self._serve_file(parsed, head_only=True)
            return
        if parsed.path == "/health":
            self._send_json(200, {"ok": True})
            return
        self._error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/upload":
            self._error(404, "Not found")
            return
        qs = parse_qs(parsed.query)
        ticket = str((qs.get("ticket") or [""])[0])
        try:
            meta = verify_upload_ticket(ticket)
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0:
                raise LocalVPSError("Thiếu Content-Length hoặc file rỗng.")
            if length > min(int(meta.get("max_bytes") or 0), direct_max_bytes()):
                raise LocalVPSError("File vượt giới hạn upload của VPS.")
            name_header = self.headers.get("X-QLDA-Filename", "attachment")
            mime = self.headers.get("X-QLDA-Mime", "application/octet-stream")
            name = unquote(str(name_header or "attachment"))
            item = save_stream_from_ticket(
                ticket,
                name=name,
                mime_type=str(mime or "application/octet-stream"),
                stream=self.rfile,
                content_length=length,
            )
            self._send_json(200, {"ok": True, "file": item})
        except LocalVPSError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, f"Upload VPS thất bại: {exc}")

    def _render_upload_page(self, parsed):
        qs = parse_qs(parsed.query)
        ticket = str((qs.get("ticket") or [""])[0])
        try:
            meta = verify_upload_ticket(ticket)
        except Exception as exc:
            body = f"<h2>Link upload không hợp lệ</h2><p>{html.escape(str(exc))}</p>".encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        max_mb = int(meta.get("max_bytes") or 0) / 1024 / 1024
        title = f"QLDA VPS • {meta.get('record_code') or 'Hồ sơ'}"
        safe_ticket = json.dumps(ticket)
        body = f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;background:#f5f7fb;margin:0;padding:24px;color:#172033}}
.card{{max-width:760px;margin:auto;background:white;border:1px solid #dde3ee;border-radius:16px;padding:24px;box-shadow:0 6px 24px #0001}}
button{{background:#2563eb;color:white;border:0;border-radius:10px;padding:12px 18px;font-weight:700;cursor:pointer}}
input{{display:block;width:100%;margin:16px 0;padding:12px;border:1px solid #cbd5e1;border-radius:10px;box-sizing:border-box}}
.row{{margin:10px 0;padding:10px;background:#f8fafc;border-radius:10px}} .ok{{color:#15803d}} .err{{color:#b91c1c}}
progress{{width:100%;height:16px}}
</style></head><body><div class="card">
<h2>⬆️ Tải file trực tiếp lên VPS</h2>
<p><b>Dự án:</b> {html.escape(str(meta.get('project_code') or ''))}<br>
<b>Hồ sơ:</b> {html.escape(str(meta.get('record_code') or ''))}<br>
<b>Giới hạn:</b> {max_mb:,.0f} MB/file</p>
<input id="files" type="file" multiple>
<button id="upload">Tải file lên VPS</button>
<div id="status"></div>
<script>
const ticket={safe_ticket};
const status=document.getElementById('status');
function row(name){{const d=document.createElement('div');d.className='row';d.innerHTML='<b></b><br><progress max="100" value="0"></progress><div></div>';d.querySelector('b').textContent=name;status.appendChild(d);return d;}}
function uploadOne(file){{return new Promise((resolve)=>{{
 const box=row(file.name), p=box.querySelector('progress'), msg=box.querySelector('div');
 const xhr=new XMLHttpRequest();
 xhr.open('POST','api/upload?ticket='+encodeURIComponent(ticket));
 xhr.setRequestHeader('Content-Type','application/octet-stream');
 xhr.setRequestHeader('X-QLDA-Filename',encodeURIComponent(file.name));
 xhr.setRequestHeader('X-QLDA-Mime',file.type||'application/octet-stream');
 xhr.upload.onprogress=(e)=>{{if(e.lengthComputable)p.value=Math.round(e.loaded*100/e.total)}};
 xhr.onload=()=>{{let data={{}};try{{data=JSON.parse(xhr.responseText)}}catch(e){{}};if(xhr.status>=200&&xhr.status<300&&data.ok){{p.value=100;msg.className='ok';msg.textContent='✓ Đã lưu trên VPS';}}else{{msg.className='err';msg.textContent='✗ '+(data.error||('HTTP '+xhr.status));}}resolve();}};
 xhr.onerror=()=>{{msg.className='err';msg.textContent='✗ Mất kết nối';resolve();}};
 xhr.send(file);
}})}}
document.getElementById('upload').onclick=async()=>{{const fs=[...document.getElementById('files').files];if(!fs.length)return;document.getElementById('upload').disabled=true;for(const f of fs)await uploadOne(f);document.getElementById('upload').disabled=false;}};
</script></div></body></html>""".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, parsed, *, head_only: bool):
        file_id = unquote(parsed.path[len("/file/"):]).strip()
        qs = parse_qs(parsed.query)
        mode = str((qs.get("mode") or ["inline"])[0])
        exp = str((qs.get("exp") or ["0"])[0])
        sig = str((qs.get("sig") or [""])[0])
        if not verify_file_signature(file_id, int(exp or 0), mode, sig):
            self._error(403, "Link file không hợp lệ hoặc đã hết hạn.")
            return
        try:
            row, path = local_file_path(file_id)
        except Exception as exc:
            self._error(404, str(exc))
            return
        total = int(path.stat().st_size)
        start, end = 0, max(0, total - 1)
        range_header = str(self.headers.get("Range") or "")
        partial = False
        if range_header:
            m = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
            if m:
                a, b = m.groups()
                if a:
                    start = min(int(a), max(0, total - 1))
                if b:
                    end = min(int(b), max(0, total - 1))
                elif a:
                    end = max(0, total - 1)
                if not a and b:
                    suffix_len = min(int(b), total)
                    start = total - suffix_len
                    end = total - 1
                if start <= end:
                    partial = True
        length = max(0, end - start + 1)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", str(row.get("mime_type") or "application/octet-stream"))
        disposition = "attachment" if mode == "download" else "inline"
        safe_ascii = re.sub(r"[^A-Za-z0-9._-]+", "_", str(row.get("name") or "attachment"))[:120] or "attachment"
        encoded = quote(str(row.get("name") or "attachment"))
        self.send_header("Content-Disposition", f"{disposition}; filename=\"{safe_ascii}\"; filename*=UTF-8''{encoded}")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.send_header("Cache-Control", "private, max-age=60")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if head_only or length <= 0:
            return
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def main():
    parser = argparse.ArgumentParser(description="QLDA VPS local file/upload server")
    parser.add_argument("--host", default=os.environ.get("QLDA_LOCAL_FILE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("QLDA_LOCAL_FILE_PORT", "8502")))
    args = parser.parse_args()
    health()
    server = ThreadingHTTPServer((args.host, args.port), QLDAFileHandler)
    print(f"{SERVER_VERSION} listening on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
