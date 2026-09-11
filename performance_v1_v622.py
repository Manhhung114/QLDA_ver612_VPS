from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from types import CodeType
from typing import Any, Callable, Hashable


PATCH_MARKER = "V6.22 PERFORMANCE V1"
PERFORMANCE_V1_VERSION = "1.0.0"

# Streamlit re-executes the entrypoint for every session/rerun. Module globals in
# imported modules survive those reruns, so this module is the process-level home
# for safe immutable caches and lightweight timing counters.
_LOCK = threading.RLock()
_COMPILED_APP_CACHE: dict[Hashable, CodeType] = {}
_COUNTERS: dict[str, int] = defaultdict(int)
_TIMINGS: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))


def enabled() -> bool:
    value = str(os.environ.get("QLDA_PERFORMANCE_V1", "1") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def get_compiled_app(key: Hashable, builder: Callable[[], CodeType]) -> CodeType:
    """Return one compiled generated-app code object per Streamlit process.

    The generated QLDA source is immutable for the lifetime of a production
    process. Deployments restart systemd, therefore a fresh process always
    rebuilds from the new source while normal Streamlit reruns reuse the already
    compiled code object.
    """
    if not enabled():
        started = time.perf_counter()
        code = builder()
        record_timing("compile_uncached", time.perf_counter() - started)
        return code

    cache_key = (PERFORMANCE_V1_VERSION, key)
    with _LOCK:
        cached = _COMPILED_APP_CACHE.get(cache_key)
        if cached is not None:
            _COUNTERS["compiled_cache_hit"] += 1
            return cached

        started = time.perf_counter()
        code = builder()
        if not isinstance(code, CodeType):
            raise TypeError("Performance V1 compiled-app builder must return a code object")
        _COMPILED_APP_CACHE[cache_key] = code
        _COUNTERS["compiled_cache_miss"] += 1
        _TIMINGS["compile_build"].append(time.perf_counter() - started)
        return code


def record_timing(name: str, seconds: float) -> None:
    with _LOCK:
        _COUNTERS[f"timing_{name}_count"] += 1
        _TIMINGS[str(name)].append(max(0.0, float(seconds)))


@contextmanager
def phase(name: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        record_timing(name, time.perf_counter() - started)


def snapshot() -> dict[str, Any]:
    """Small diagnostic snapshot for VPS troubleshooting/load-test comparison."""
    with _LOCK:
        timing = {}
        for name, values in _TIMINGS.items():
            xs = list(values)
            if not xs:
                continue
            xs_sorted = sorted(xs)
            p95_index = min(len(xs_sorted) - 1, max(0, int(round((len(xs_sorted) - 1) * 0.95))))
            timing[name] = {
                "count": len(xs),
                "avg_ms": round(sum(xs) * 1000.0 / len(xs), 2),
                "p95_ms": round(xs_sorted[p95_index] * 1000.0, 2),
                "max_ms": round(max(xs) * 1000.0, 2),
            }
        return {
            "marker": PATCH_MARKER,
            "version": PERFORMANCE_V1_VERSION,
            "enabled": enabled(),
            "compiled_entries": len(_COMPILED_APP_CACHE),
            "counters": dict(_COUNTERS),
            "timings": timing,
        }


def clear_process_cache_for_tests() -> None:
    """Test-only reset; production code never calls this."""
    with _LOCK:
        _COMPILED_APP_CACHE.clear()
        _COUNTERS.clear()
        _TIMINGS.clear()
