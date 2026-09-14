"""Excel background-worker entrypoint for the V7 clean architecture.

Queue operations live in the native Job application port.  The only public
module retained here is ``worker`` because systemd launches
``python -m qlda.modules.excel.worker``.
"""

__all__ = ["worker"]
