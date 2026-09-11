#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import math
import os
import statistics
import subprocess
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_LEVELS = [5, 10, 20, 30, 40, 50, 75, 100]
ACTIVE_LEVELS = [5, 10, 20, 30, 40, 50]
DB_LEVELS = [1, 2, 5, 8, 10]
PDF_LEVELS = [1, 2, 3]


@dataclass
class Result:
    ok: bool
    seconds: float
    detail: str = ""
    bytes_received: int = 0
    messages: int = 0


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(float(x) for x in values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


def _proc_status_value(pid: int, key: str) -> int:
    try:
        text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 0
    for line in text.splitlines():
        if line.startswith(key + ":"):
            parts = line.split()
            try:
                return int(parts[1]) * 1024
            except Exception:
                return 0
    return 0


def _meminfo() -> tuple[int, int]:
    data: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            parts = v.split()
            if parts:
                data[k] = int(parts[0]) * 1024
    except Exception:
        return 0, 0
    return data.get("MemTotal", 0), data.get("MemAvailable", 0)


def _cpu_times() -> tuple[int, int]:
    try:
        line = Path("/proc/stat").read_text().splitlines()[0]
        vals = [int(x) for x in line.split()[1:]]
        total = sum(vals)
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return total, idle
    except Exception:
        return 0, 0


def qlda_pid() -> int:
    try:
        out = subprocess.check_output(
            ["systemctl", "show", "qlda", "--property", "MainPID", "--value"],
            text=True,
            timeout=3,
        ).strip()
        return int(out or "0")
    except Exception:
        return 0


class Sampler:
    def __init__(self, interval: float = 0.25):
        self.interval = interval
        self.stop_event = threading.Event()
        self.samples: list[dict[str, float]] = []
        self.thread: threading.Thread | None = None
        self.pid = qlda_pid()

    def __enter__(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def _run(self):
        prev_total, prev_idle = _cpu_times()
        while not self.stop_event.wait(self.interval):
            total, idle = _cpu_times()
            dt = total - prev_total
            di = idle - prev_idle
            cpu = 0.0 if dt <= 0 else max(0.0, min(100.0, 100.0 * (dt - di) / dt))
            prev_total, prev_idle = total, idle
            mem_total, mem_avail = _meminfo()
            mem_used_pct = 0.0 if not mem_total else 100.0 * (mem_total - mem_avail) / mem_total
            rss = _proc_status_value(self.pid, "VmRSS") if self.pid else 0
            self.samples.append(
                {
                    "cpu_pct": cpu,
                    "mem_used_pct": mem_used_pct,
                    "qlda_rss_mb": rss / 1024 / 1024,
                }
            )

    def summary(self) -> dict[str, float]:
        if not self.samples:
            return {"cpu_avg": 0.0, "cpu_p95": 0.0, "mem_peak": 0.0, "qlda_rss_peak_mb": 0.0}
        cpus = [s["cpu_pct"] for s in self.samples]
        return {
            "cpu_avg": round(statistics.mean(cpus), 2),
            "cpu_p95": round(percentile(cpus, 0.95), 2),
            "mem_peak": round(max(s["mem_used_pct"] for s in self.samples), 2),
            "qlda_rss_peak_mb": round(max(s["qlda_rss_mb"] for s in self.samples), 2),
        }


def health_ok(base_url: str, timeout: float = 4.0) -> bool:
    url = base_url.rstrip("/") + "/_stcore/health"
    try:
        with urlopen(Request(url, headers={"User-Agent": "QLDA-loadtest/1"}), timeout=timeout) as r:
            return int(r.status) == 200 and r.read(64).strip().lower() == b"ok"
    except Exception:
        return False


def ws_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    host = parsed.netloc or parsed.path
    return f"{scheme}://{host}/_stcore/stream"


def ws_virtual_user(base_url: str, barrier: threading.Barrier, reruns: int, hold: float, timeout: float) -> Result:
    start = time.perf_counter()
    try:
        from websockets.sync.client import connect
        from streamlit.proto.BackMsg_pb2 import BackMsg
        from streamlit.proto.ForwardMsg_pb2 import ForwardMsg

        barrier.wait(timeout=max(10.0, timeout))
        uri = ws_url(base_url)
        total_bytes = 0
        total_msgs = 0
        origin = base_url.rstrip("/")
        with connect(
            uri,
            subprotocols=["streamlit"],
            origin=origin,
            open_timeout=timeout,
            close_timeout=2,
            max_size=None,
        ) as ws:
            for run_no in range(max(1, int(reruns))):
                back = BackMsg()
                state = back.rerun_script
                state.query_string = ""
                state.page_script_hash = ""
                state.page_name = ""
                try:
                    state.context_info.url = origin + "/"
                    state.context_info.timezone = "Asia/Ho_Chi_Minh"
                    state.context_info.locale = "vi-VN"
                except Exception:
                    pass
                ws.send(back.SerializeToString())

                deadline = time.monotonic() + timeout
                finished = False
                while time.monotonic() < deadline:
                    remaining = max(0.1, deadline - time.monotonic())
                    raw = ws.recv(timeout=remaining)
                    if raw is None:
                        continue
                    if isinstance(raw, str):
                        raw = raw.encode("utf-8", errors="ignore")
                    total_bytes += len(raw)
                    total_msgs += 1
                    msg = ForwardMsg()
                    try:
                        msg.ParseFromString(raw)
                    except Exception:
                        continue
                    if msg.WhichOneof("type") == "script_finished":
                        finished = True
                        break
                if not finished:
                    raise TimeoutError(f"rerun {run_no + 1} did not finish in {timeout:.0f}s")
                if run_no + 1 < reruns:
                    time.sleep(0.25)
            if hold > 0:
                time.sleep(hold)
        return Result(True, time.perf_counter() - start, bytes_received=total_bytes, messages=total_msgs)
    except Exception as exc:
        return Result(False, time.perf_counter() - start, detail=f"{type(exc).__name__}: {exc}")


def summarize_results(level: int, results: list[Result], resources: dict[str, float], base_url: str) -> dict[str, Any]:
    oks = [r for r in results if r.ok]
    lats = [r.seconds for r in oks]
    success = 0.0 if not results else len(oks) / len(results)
    out: dict[str, Any] = {
        "concurrency": int(level),
        "requests": len(results),
        "success_rate": round(success * 100, 2),
        "p50_s": round(percentile(lats, 0.50), 3),
        "p95_s": round(percentile(lats, 0.95), 3),
        "max_s": round(max(lats), 3) if lats else 0.0,
        "health_after": health_ok(base_url),
        **resources,
    }
    errors = [r.detail for r in results if not r.ok]
    if errors:
        out["errors"] = errors[:5]
    out["pass"] = bool(
        out["success_rate"] >= 98.0
        and out["health_after"]
        and out["p95_s"] <= 10.0
        and out["mem_peak"] <= 92.0
    )
    return out


def run_ws_series(base_url: str, levels: list[int], reruns: int, hold: float, timeout: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    consecutive_fail = 0
    for level in levels:
        print(f"\n[WS] concurrency={level}, reruns={reruns}, hold={hold}s")
        barrier = threading.Barrier(level)
        with Sampler() as sampler:
            with cf.ThreadPoolExecutor(max_workers=level) as ex:
                futures = [ex.submit(ws_virtual_user, base_url, barrier, reruns, hold, timeout) for _ in range(level)]
                results = [f.result() for f in futures]
        row = summarize_results(level, results, sampler.summary(), base_url)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
        if row["pass"]:
            consecutive_fail = 0
        else:
            consecutive_fail += 1
            if consecutive_fail >= 2:
                print("Stopping this series after 2 consecutive failing levels.")
                break
        time.sleep(1.0)
    return rows


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _db_worker(database_url: str, loops: int = 12) -> Result:
    start = time.perf_counter()
    try:
        import psycopg
        with psycopg.connect(database_url, connect_timeout=5, autocommit=True) as conn:
            with conn.cursor() as cur:
                for _ in range(loops):
                    cur.execute("SELECT id, name FROM projects ORDER BY id DESC LIMIT 20")
                    rows = cur.fetchall()
                    if rows:
                        pid = int(rows[0][0])
                        cur.execute("SELECT id, status FROM tasks WHERE project_id=%s ORDER BY id DESC LIMIT 100", (pid,))
                        cur.fetchall()
                        cur.execute("SELECT id, doc_type FROM documents WHERE project_id=%s ORDER BY id DESC LIMIT 100", (pid,))
                        cur.fetchall()
        return Result(True, time.perf_counter() - start)
    except Exception as exc:
        return Result(False, time.perf_counter() - start, detail=f"{type(exc).__name__}: {exc}")


def run_db_series(database_url: str) -> list[dict[str, Any]]:
    if not database_url:
        return []
    rows: list[dict[str, Any]] = []
    for level in DB_LEVELS:
        print(f"\n[DB READ] workers={level}")
        with Sampler() as sampler:
            with cf.ThreadPoolExecutor(max_workers=level) as ex:
                results = list(ex.map(lambda _: _db_worker(database_url), range(level)))
        oks = [r for r in results if r.ok]
        lats = [r.seconds for r in oks]
        success = 100.0 * len(oks) / len(results)
        row = {
            "workers": level,
            "success_rate": round(success, 2),
            "p95_s": round(percentile(lats, 0.95), 3),
            **sampler.summary(),
        }
        row["pass"] = bool(success >= 99.0 and row["p95_s"] <= 5.0 and row["mem_peak"] <= 92.0)
        errs = [r.detail for r in results if not r.ok]
        if errs:
            row["errors"] = errs[:5]
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
        time.sleep(0.5)
    return rows


def find_pdf(roots: list[Path], max_files: int = 6000) -> Path | None:
    best: tuple[int, Path] | None = None
    seen = 0
    for root in roots:
        if not root.exists():
            continue
        try:
            iterator = root.rglob("*")
            for p in iterator:
                if seen >= max_files:
                    break
                seen += 1
                try:
                    if not p.is_file() or p.suffix.lower() != ".pdf":
                        continue
                    size = p.stat().st_size
                    if size <= 0 or size > 300 * 1024 * 1024:
                        continue
                    if best is None or size > best[0]:
                        best = (size, p)
                except Exception:
                    continue
        except Exception:
            continue
    return best[1] if best else None


def _pdf_worker(path: str) -> Result:
    start = time.perf_counter()
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 25 * 1024 * 1024:
            from contract_ai_large_pdf_v622 import split_large_pdf
            chunks = split_large_pdf(Path(path).name, raw)
            detail = f"split_parts={len(chunks)}"
        else:
            import io
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw), strict=False)
            chars = 0
            for page in list(reader.pages)[:40]:
                try:
                    chars += len(page.extract_text() or "")
                except Exception:
                    pass
            detail = f"pages={min(40, len(reader.pages))},chars={chars}"
        return Result(True, time.perf_counter() - start, detail=detail)
    except Exception as exc:
        return Result(False, time.perf_counter() - start, detail=f"{type(exc).__name__}: {exc}")


def run_pdf_series(pdf: Path | None) -> list[dict[str, Any]]:
    if not pdf:
        return []
    rows: list[dict[str, Any]] = []
    size_mb = pdf.stat().st_size / 1024 / 1024
    print(f"\n[PDF] representative file: {pdf} ({size_mb:.1f} MB)")
    for level in PDF_LEVELS:
        print(f"[PDF] concurrent jobs={level}")
        with Sampler() as sampler:
            with cf.ProcessPoolExecutor(max_workers=level) as ex:
                futures = [ex.submit(_pdf_worker, str(pdf)) for _ in range(level)]
                results = [f.result(timeout=180) for f in futures]
        oks = [r for r in results if r.ok]
        lats = [r.seconds for r in oks]
        success = 100.0 * len(oks) / len(results)
        row = {
            "jobs": level,
            "file_mb": round(size_mb, 2),
            "success_rate": round(success, 2),
            "p95_s": round(percentile(lats, 0.95), 3),
            **sampler.summary(),
        }
        row["pass"] = bool(success >= 99.0 and row["mem_peak"] <= 92.0 and row["p95_s"] <= 90.0)
        errs = [r.detail for r in results if not r.ok]
        if errs:
            row["errors"] = errs[:5]
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
        if not row["pass"]:
            break
        time.sleep(1.0)
    return rows


def max_pass(rows: list[dict[str, Any]], key: str) -> int:
    vals = [int(r[key]) for r in rows if r.get("pass")]
    return max(vals) if vals else 0


def safe_headroom(value: int) -> int:
    if value <= 0:
        return 0
    return max(1, int(math.floor(value * 0.80)))


def write_report(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "load_test.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    idle = max_pass(payload["websocket_idle"], "concurrency")
    active = max_pass(payload["websocket_active"], "concurrency")
    dbw = max_pass(payload.get("database_read", []), "workers")
    pdfj = max_pass(payload.get("pdf_preprocess", []), "jobs")

    lines = [
        "# QLDA V6.22 - Load test report",
        "",
        f"- Timestamp UTC: {payload['timestamp_utc']}",
        f"- Base URL: {payload['base_url']}",
        f"- CPU: {payload['host']['cpu_count']} vCPU",
        f"- RAM: {payload['host']['ram_gb']:.2f} GB",
        f"- Streamlit PID: {payload['host']['qlda_pid']}",
        "",
        "## Capacity summary",
        "",
        "| Scenario | Highest tested PASS | Recommended with ~20% headroom |",
        "|---|---:|---:|",
        f"| Connected/login-page sessions, 1 rerun then hold | {idle} users | {safe_headroom(idle)} users |",
        f"| Active sessions, 3 reruns/user | {active} users | {safe_headroom(active)} users |",
        f"| Concurrent PostgreSQL read workers | {dbw if dbw else 'N/A'} | {safe_headroom(dbw) if dbw else 'N/A'} |",
        f"| Concurrent contract PDF preprocessing jobs | {pdfj if pdfj else 'N/A'} | {max(1, safe_headroom(pdfj)) if pdfj else 'N/A'} |",
        "",
        "## Interpretation",
        "",
        "- WebSocket tests are real Streamlit sessions and execute the current app entrypoint, but they stop at the unauthenticated/login path unless test credentials are supplied by a browser-level harness.",
        "- Database and PDF tests are read-only / preprocessing-only. They do not modify production records and do not call OpenAI/Gemini, so AI provider rate limits and API latency are excluded.",
        "- For production planning, use the lower number between the active-session capacity and the capacity observed while heavy PDF/Excel/AI work is occurring. Keep at least 20% headroom.",
        "",
        "## Raw results",
        "",
        "```json",
        json.dumps({k: payload[k] for k in ("websocket_idle", "websocket_active", "database_read", "pdf_preprocess")}, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    (out_dir / "load_test_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="QLDA current-production safe concurrency benchmark")
    parser.add_argument("--base-url", default="http://127.0.0.1:8501")
    parser.add_argument("--levels", default=",".join(str(x) for x in DEFAULT_LEVELS))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--hold", type=float, default=5.0)
    parser.add_argument("--skip-db", action="store_true")
    parser.add_argument("--skip-pdf", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    if not health_ok(base_url):
        raise SystemExit(f"QLDA health check failed before test: {base_url}/_stcore/health")

    _load_env_file(Path("/opt/qlda/shared/qlda.env"))
    levels = [int(x.strip()) for x in args.levels.split(",") if x.strip()]
    levels = sorted({x for x in levels if 1 <= x <= 200})
    active_levels = [x for x in ACTIVE_LEVELS if x <= max(levels or [50])]

    mem_total, _ = _meminfo()
    payload: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "host": {
            "cpu_count": int(os.cpu_count() or 1),
            "ram_gb": round(mem_total / 1024 / 1024 / 1024, 2),
            "qlda_pid": qlda_pid(),
        },
    }

    print("=== QLDA CURRENT LOAD TEST ===")
    print(json.dumps(payload["host"], ensure_ascii=False))

    payload["websocket_idle"] = run_ws_series(base_url, levels, reruns=1, hold=args.hold, timeout=args.timeout)
    payload["websocket_active"] = run_ws_series(base_url, active_levels, reruns=3, hold=1.0, timeout=args.timeout)

    database_url = "" if args.skip_db else str(
        os.environ.get("DATABASE_URL") or os.environ.get("QLDA_DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
    ).strip()
    payload["database_read"] = run_db_series(database_url) if database_url else []

    pdf = None if args.skip_pdf else find_pdf([Path("/opt/qlda/data"), Path("/opt/qlda/shared")])
    payload["pdf_file"] = str(pdf) if pdf else ""
    payload["pdf_preprocess"] = run_pdf_series(pdf)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else Path(f"/opt/qlda/shared/loadtest_{stamp}")
    write_report(out_dir, payload)

    idle = max_pass(payload["websocket_idle"], "concurrency")
    active = max_pass(payload["websocket_active"], "concurrency")
    pdfj = max_pass(payload["pdf_preprocess"], "jobs")
    print("\n=== SUMMARY ===")
    print(f"Connected/login-page sessions PASS up to: {idle}; recommended <= {safe_headroom(idle)}")
    print(f"Active 3-rerun sessions PASS up to: {active}; recommended <= {safe_headroom(active)}")
    if pdfj:
        print(f"Concurrent PDF preprocessing jobs PASS up to: {pdfj}")
    print(f"Report: {out_dir / 'load_test_report.md'}")
    print(f"JSON  : {out_dir / 'load_test.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
