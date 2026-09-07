from __future__ import annotations

import base64
import gzip
import os
from functools import lru_cache
from pathlib import Path

# Keep the WebOpt resource limits before pandas/numpy/BLAS are imported.
for _name, _value in {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "MALLOC_ARENA_MAX": "2",
}.items():
    os.environ.setdefault(_name, _value)

# VPS PostgreSQL startup: install connection-pool resilience before any
# CloudDatabase instance can create/use a pooled SSL connection.
import postgres_backend_v622 as _postgres_backend
from vps_postgres_resilience import install_vps_postgres_resilience

install_vps_postgres_resilience(_postgres_backend)
_postgres_backend.install_postgres_backend()

from build_v621_webopt import _finalize_source
from v622_auth_refresh_v4 import patch_auth_refresh_v4


# VPS entrypoint. The historical V6.21 source bundle is rebuilt in memory and
# finalized as V6.22 PostgreSQL, then executed behind Nginx/systemd.
_ROOT = Path(__file__).resolve().parent
_PARTS_DIR = _ROOT / "v621_webopt_source"
_PARTS = tuple(sorted(_PARTS_DIR.glob("part_*.b64")))

if len(_PARTS) != 9:
    raise RuntimeError(
        f"QLDA V6.22 source is incomplete: expected 9 parts, found {len(_PARTS)}."
    )

_SIGNATURE = tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in _PARTS)


@lru_cache(maxsize=1)
def _compiled_vps_app(signature):
    del signature
    try:
        encoded = "".join(p.read_text(encoding="ascii").strip() for p in _PARTS)
        source = gzip.decompress(base64.b64decode(encoded)).decode("utf-8")
    except Exception as exc:
        raise RuntimeError(f"Invalid QLDA V6.22 source bundle: {exc}") from exc

    source = _finalize_source(source)
    source = patch_auth_refresh_v4(source)
    return compile(source, str(_ROOT / "streamlit_app_v622_postgresql.py"), "exec")


exec(_compiled_vps_app(_SIGNATURE), globals(), globals())
