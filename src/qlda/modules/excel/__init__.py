"""Excel queue/worker public boundary.

Submodules stay lazy so importing the queue facade does not eagerly import the
production worker.  This keeps the V6.26 service layer acyclic:
services.jobs -> modules.excel.jobs, while the worker may depend on services.
"""

__all__ = ["jobs", "worker"]
