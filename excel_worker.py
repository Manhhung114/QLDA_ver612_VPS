from __future__ import annotations

"""Compatibility entrypoint for the superseded V6.24 prototype worker.

The production implementation is ``excel_worker_v624.py``. Keeping this file as
an alias prevents an old service/command from falling back to the former
``Path.read_bytes()`` BOQ pipeline.
"""

from excel_worker_v624 import main


if __name__ == "__main__":
    main()
