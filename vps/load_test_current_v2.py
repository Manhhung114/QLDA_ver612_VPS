#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import math
import os
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

DEFAULT_LEVELS = [1, 2, 5, 10, 20, 30, 40, 50, 75, 100]
DB_LEVELS = [1, 2, 5, 8, 10]
PDF_LEVELS = [1, 2, 3]


@dataclass
class Result:
    ok: bool
    wall_s: float
    connect_s: float = 0.0
    run_latencies_s: list[float] = field(default_factory=list)
    detail: str = ""


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
        vals = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        return sum(vals), vals[3] + (vals[4] if len(vals) > 4 else 0)
    except Exception:
        return 0, 0


def qlda_pid() -> int:
    try:
        return int(
            subprocess.check_output(
                ["systemctl", "show", "qlda", "--property", "MainPID", "--value"],
                text=True,
                timeout=3,
            ).strip()
            or "0"
        )
    except Exception:
        return 0


def _rss_mb(pid: int) -> float:
    if not pid:
        return 0.0
    try:
        for line in Path(f"/proc/{pid}/status").read_text(errors="ignore").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return 0.0


class Sampler:
    def __init__(self, interval: float = 0.20):
        self.interval = interval
        self.stop_event = threading.Event()
        self.samples: list[dict[str, float]] = []
        self.pid = qlda_pid()
        self.thread: threading.Thread | None = None

    def __enter__(self):
        total, avail = _meminfo()
        self.samples.append(
            {
                "cpu_pct": 0.0,
                "mem_used_pct": 0.0 if not total else 100.0 * (total - avail) / total,
                "qlda_rss_mb": _rss_mb(self.pid),
            }
        )
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def _run(self):
        pt, pi = _cpu_times()
        while not self.stop_event.wait(self.interval):
            total_ticks, idle_ticks = _cpu_times()
            dt = total_ticks - pt
            di = idle_ticks - pi
            pt, pi = total_ticks, idle_ticks
            cpu = 0.0 if dt <= 0 else max(0.0, min(100.0, 100.0 * (dt - di) / dt))
            total, avail = _meminfo()
            self.samples.append(
                {
                    "cpu_pct": cpu,
                    "mem_used_pct": 0.0 if not total else 100.0 * (total - avail) / total,
                    "qlda_rss_mb": _rss_mb(self.pid),
                }
            )

    def summary(self) -> dict[str, float]:
        cpus = [x["cpu_pct"] for x in self.samples]
        return {
            "cpu_avg": round(statistics.mean(cpus), 2) if cpus else 0.0,
            "cpu_p95": round(percentile(cpus, 0.95), 2) if cpus else 0.0,
            "mem_peak": round(max((x["mem_used_pct"] for x in self.samples), default=0.0), 2),
            "qlda_rss_peak_mb": round(max((x["qlda_rss_mb"] for x in self.samples), default=0.0), 2),
        }


def health_ok(base_url: str, timeout: float = 4.0) -> bool:
    try:
        with urlopen(
            Request(base_url.rstrip("/") + "/_stcore/health", headers={"User-Agent": "QLDA-loadtest-v2"}),
            timeout=timeout,
        ) as r:
            return int(r.status) == 200 and r.read(64).strip().lower() == b"ok"
    except Exception:
        return False


def ws_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc or parsed.path}/_stcore/stream"


def _connect(base_url: str, timeout: float):
    from websockets.sync.client import connect

    return connect(
        ws_url(base_url),
        subprotocols=["streamlit"],
        origin=base_url.rstrip("/"),
        open_timeout=timeout,
        close_timeout=2,
        max_size=None,
    )


def ws_hold_user(base_url: str, barrier: threading.Barrier, hold: float, timeout: float) -> Result:
    try:
        barrier.wait(timeout=max(10.0, timeout))
        started = time.perf_counter()
        c0 = time.perf_counter()
        with _connect(base_url, timeout):
            connect_s = time.perf_counter() - c0
            time.sleep(max(0.0, hold))
        return Result(True, time.perf_counter() - started, connect_s=connect_s)
    except Exception as exc:
        return Result(False, 0.0, detail=f"{type(exc).__name__}: {exc}")


def ws_rerun_user(
    base_url: str,
    barrier: threading.Barrier,
    reruns: int,
    think_s: float,
    timeout: float,
) -> Result:
    try:
        from streamlit.proto.BackMsg_pb2 import BackMsg
        from streamlit.proto.ForwardMsg_pb2 import ForwardMsg

        barrier.wait(timeout=max(10.0, timeout))
        started = time.perf_counter()
        c0 = time.perf_counter()
        latencies: list[float] = []
        origin = base_url.rstrip("/")
        with _connect(base_url, timeout) as ws:
            connect_s = time.perf_counter() - c0
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

                r0 = time.perf_counter()
                ws.send(back.SerializeToString())
                deadline = time.monotonic() + timeout
                finished = False
                while time.monotonic() < deadline:
                    raw = ws.recv(timeout=max(0.1, deadline - time.monotonic()))
                    if raw is None:
                        continue
                    if isinstance(raw, str):
                        raw = raw.encode("utf-8", errors="ignore")
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
                latencies.append(time.perf_counter() - r0)
                if run_no + 1 < reruns and think_s > 0:
                    time.sleep(think_s)
        return Result(
            True,
            time.perf_counter() - started,
            connect_s=connect_s,
            run_latencies_s=latencies,
        )
    except Exception as exc:
        return Result(False, 0.0, detail=f"{type(exc).__name__}: {exc}")


def _sla_label(success: float, p95: float, health: bool, mem_peak: float) -> str:
    if success < 98.0 or not health or mem_peak >= 92.0:
        return "FAIL"
    if p95 <= 5.0:
        return "FAST"
    if p95 <= 10.0:
        return "GOOD"
    if p95 <= 15.0:
        return "ACCEPTABLE"
    if p95 <= 20.0:
        return "SLOW"
    return "FAIL"


def _run_level(level: int, worker, *args) -> tuple[list[Result], dict[str, float]]:
    barrier = threading.Barrier(level)
    with Sampler() as sampler:
        with cf.ThreadPoolExecutor(max_workers=level) as ex:
            futures = [ex.submit(worker, *args, barrier) for _ in range(level)]
            results = [f.result() for f in futures]
    return results, sampler.summary()


def run_hold_series(base_url: str, levels: list[int], hold: float, timeout: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for level in levels:
        print(f"\n[IDLE WS] connections={level}, hold={hold}s")
        barrier = threading.Barrier(level)
        with Sampler() as sampler:
            with cf.ThreadPoolExecutor(max_workers=level) as ex:
                results = [
                    f.result()
                    for f in [
                        ex.submit(ws_hold_user, base_url, barrier, hold, timeout)
                        for _ in range(level)
                    ]
                ]
        oks = [r for r in results if r.ok]
        connects = [r.connect_s for r in oks]
        success = 100.0 * len(oks) / len(results)
        resources = sampler.summary()
        row = {
            "concurrency": level,
            "success_rate": round(success, 2),
            "connect_p50_s": round(percentile(connects, 0.50), 3),
            "connect_p95_s": round(percentile(connects, 0.95), 3),
            "health_after": health_ok(base_url),
            **resources,
        }
        row["pass"] = bool(success >= 99.0 and row["health_after"] and row["connect_p95_s"] <= 5.0 and row["mem_peak"] < 92.0)
        errs = [r.detail for r in results if not r.ok]
        if errs:
            row["errors"] = errs[:5]
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
        if not row["pass"] and level >= 20:
            break
        time.sleep(0.5)
    return rows


def run_rerun_series(
    label: str,
    base_url: str,
    levels: list[int],
    reruns: int,
    think_s: float,
    timeout: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    hard_fail = 0
    for level in levels:
        print(f"\n[{label}] concurrency={level}, reruns={reruns}")
        barrier = threading.Barrier(level)
        with Sampler() as sampler:
            with cf.ThreadPoolExecutor(max_workers=level) as ex:
                futures = [
                    ex.submit(ws_rerun_user, base_url, barrier, reruns, think_s, timeout)
                    for _ in range(level)
                ]
                results = [f.result() for f in futures]
        oks = [r for r in results if r.ok]
        runs = [x for r in oks for x in r.run_latencies_s]
        connects = [r.connect_s for r in oks]
        success = 100.0 * len(oks) / len(results)
        resources = sampler.summary()
        health = health_ok(base_url)
        p95 = percentile(runs, 0.95)
        row = {
            "concurrency": level,
            "users": len(results),
            "successful_users": len(oks),
            "success_rate": round(success, 2),
            "reruns_measured": len(runs),
            "run_p50_s": round(percentile(runs, 0.50), 3),
            "run_p95_s": round(p95, 3),
            "run_max_s": round(max(runs), 3) if runs else 0.0,
            "connect_p95_s": round(percentile(connects, 0.95), 3),
            "health_after": health,
            **resources,
        }
        row["sla"] = _sla_label(success, p95, health, resources["mem_peak"])
        row["pass"] = row["sla"] in {"FAST", "GOOD", "ACCEPTABLE"}
        errs = [r.detail for r in results if not r.ok]
        if errs:
            row["errors"] = errs[:5]
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
        if row["sla"] == "FAIL":
            hard_fail += 1
        else:
            hard_fail = 0
        if hard_fail >= 2:
            print("Stopping after 2 consecutive hard failures.")
            break
        time.sleep(0.75)
    return rows


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _db_worker(database_url: str, loops: int = 80) -> Result:
    started = time.perf_counter()
    try:
        import psycopg

        with psycopg.connect(database_url, connect_timeout=5, autocommit=True) as conn:
            with conn.cursor() as cur:
                for _ in range(loops):
                    cur.execute("SELECT id, name FROM projects ORDER BY id DESC LIMIT 20")
                    projects = cur.fetchall()
                    if projects:
                        pid = int(projects[0][0])
                        cur.execute("SELECT id, status FROM tasks WHERE project_id=%s ORDER BY id DESC LIMIT 100", (pid,))
                        cur.fetchall()
                        cur.execute("SELECT id, doc_type FROM documents WHERE project_id=%s ORDER BY id DESC LIMIT 100", (pid,))
                        cur.fetchall()
        return Result(True, time.perf_counter() - started)
    except Exception as exc:
        return Result(False, time.perf_counter() - started, detail=f"{type(exc).__name__}: {exc}")


def run_db_series(database_url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for level in DB_LEVELS:
        print(f"\n[DB READ] workers={level}")
        with Sampler() as sampler:
            with cf.ThreadPoolExecutor(max_workers=level) as ex:
                results = list(ex.map(lambda _: _db_worker(database_url), range(level)))
        oks = [r for r in results if r.ok]
        times = [r.wall_s for r in oks]
        success = 100.0 * len(oks) / len(results)
        row = {
            "workers": level,
            "success_rate": round(success, 2),
            "p95_s": round(percentile(times, 0.95), 3),
            **sampler.summary(),
        }
        row["pass"] = bool(success >= 99.0 and row["p95_s"] <= 8.0 and row["mem_peak"] < 92.0)
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
        for p in root.rglob("*"):
            if seen >= max_files:
                break
            seen += 1
            try:
                if p.is_file() and p.suffix.lower() == ".pdf":
                    size = p.stat().st_size
                    if 0 < size <= 300 * 1024 * 1024 and (best is None or size > best[0]):
                        best = (size, p)
            except Exception:
                continue
    return best[1] if best else None


def _pdf_worker(path: str) -> Result:
    started = time.perf_counter()
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
        return Result(True, time.perf_counter() - started, detail=detail)
    except Exception as exc:
        return Result(False, time.perf_counter() - started, detail=f"{type(exc).__name__}: {exc}")


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
                results = [f.result(timeout=240) for f in futures]
        oks = [r for r in results if r.ok]
        times = [r.wall_s for r in oks]
        success = 100.0 * len(oks) / len(results)
        row = {
            "jobs": level,
            "file_mb": round(size_mb, 2),
            "success_rate": round(success, 2),
            "p95_s": round(percentile(times, 0.95), 3),
            **sampler.summary(),
        }
        row["pass"] = bool(success >= 99.0 and row["p95_s"] <= 120.0 and row["mem_peak"] < 92.0)
        errs = [r.detail for r in results if not r.ok]
        if errs:
            row["errors"] = errs[:5]
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
        if not row["pass"]:
            break
        time.sleep(0.75)
    return rows


def max_pass(rows: list[dict[str, Any]], key: str) -> int:
    values = [int(row[key]) for row in rows if row.get("pass")]
    return max(values) if values else 0


def safe_headroom(value: int) -> int:
    return max(0, int(math.floor(value * 0.8)))


def write_report(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "load_test_v2.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    idle = max_pass(payload["idle_connections"], "concurrency")
    burst = max_pass(payload["burst_render"], "concurrency")
    active = max_pass(payload["active_reruns"], "concurrency")
    dbw = max_pass(payload.get("database_read", []), "workers")
    pdfj = max_pass(payload.get("pdf_preprocess", []), "jobs")

    lines = [
        "# QLDA V6.22 - Load test V2 report",
        "",
        f"- Timestamp UTC: {payload['timestamp_utc']}",
        f"- CPU: {payload['host']['cpu_count']} vCPU",
        f"- RAM: {payload['host']['ram_gb']:.2f} GB",
        "",
        "## Capacity summary",
        "",
        "| Scenario | Highest operational PASS | Recommended (~20% headroom) |",
        "|---|---:|---:|",
        f"| Idle WebSocket connections | {idle} | {safe_headroom(idle)} |",
        f"| Simultaneous first-page/login renders | {burst} | {safe_headroom(burst)} |",
        f"| Simultaneously active users (3 reruns/user) | {active} | {safe_headroom(active)} |",
        f"| PostgreSQL concurrent read workers | {dbw or 'N/A'} | {safe_headroom(dbw) if dbw else 'N/A'} |",
        f"| Concurrent large-PDF preprocessing jobs | {pdfj or 'N/A'} | {max(1, safe_headroom(pdfj)) if pdfj else 'N/A'} |",
        "",
        "Operational PASS for interactive reruns = success >=98%, app healthy, RAM <92%, per-rerun p95 <=15s.",
        "SLA labels: FAST <=5s; GOOD <=10s; ACCEPTABLE <=15s; SLOW <=20s; otherwise FAIL.",
        "Idle connections are not the same as simultaneously active users.",
        "",
        "## Raw results",
        "",
        "```json",
        json.dumps({k: payload[k] for k in ("idle_connections", "burst_render", "active_reruns", "database_read", "pdf_preprocess")}, ensure_ascii=False, indent=2),
        "```",
    ]
    (out_dir / "load_test_report_v2.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="QLDA VPS corrected multi-scenario load benchmark")
    parser.add_argument("--base-url", default="http://127.0.0.1:8501")
    parser.add_argument("--levels", default=",".join(map(str, DEFAULT_LEVELS)))
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--hold", type=float, default=5.0)
    parser.add_argument("--out", default="")
    parser.add_argument("--skip-db", action="store_true")
    parser.add_argument("--skip-pdf", action="store_true")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    if not health_ok(base_url):
        raise SystemExit(f"QLDA health check failed before test: {base_url}/_stcore/health")

    _load_env_file(Path("/opt/qlda/shared/qlda.env"))
    levels = sorted({int(x.strip()) for x in args.levels.split(",") if x.strip() and 1 <= int(x.strip()) <= 200})
    idle_levels = levels
    burst_levels = [x for x in levels if x <= 30]
    active_levels = [x for x in levels if x <= 20]

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

    print("=== QLDA CURRENT LOAD TEST V2 (CORRECTED) ===")
    print(json.dumps(payload["host"], ensure_ascii=False))

    payload["idle_connections"] = run_hold_series(base_url, idle_levels, args.hold, args.timeout)
    payload["burst_render"] = run_rerun_series("BURST RENDER", base_url, burst_levels, 1, 0.0, args.timeout)
    payload["active_reruns"] = run_rerun_series("ACTIVE", base_url, active_levels, 3, 0.5, args.timeout)

    database_url = "" if args.skip_db else str(
        os.environ.get("DATABASE_URL") or os.environ.get("QLDA_DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
    ).strip()
    payload["database_read"] = run_db_series(database_url) if database_url else []

    pdf = None if args.skip_pdf else find_pdf([Path("/opt/qlda/data"), Path("/opt/qlda/shared")])
    payload["pdf_file"] = str(pdf) if pdf else ""
    payload["pdf_preprocess"] = run_pdf_series(pdf)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else Path(f"/opt/qlda/shared/loadtest_v2_{stamp}")
    write_report(out_dir, payload)

    idle = max_pass(payload["idle_connections"], "concurrency")
    burst = max_pass(payload["burst_render"], "concurrency")
    active = max_pass(payload["active_reruns"], "concurrency")
    print("\n=== CORRECTED SUMMARY ===")
    print(f"Idle connected sessions PASS up to: {idle}; recommended <= {safe_headroom(idle)}")
    print(f"Simultaneous first-render users PASS up to: {burst}; recommended <= {safe_headroom(burst)}")
    print(f"Simultaneously active users PASS up to: {active}; recommended <= {safe_headroom(active)}")
    print(f"Report: {out_dir / 'load_test_report_v2.md'}")
    print(f"JSON  : {out_dir / 'load_test_v2.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
