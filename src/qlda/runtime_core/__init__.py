"""Legacy compatibility implementation for QLDA V7.6+.

Importing ``qlda.runtime_core`` is intentionally side-effect free. Runtime wiring
lives in :mod:`qlda.composition.runtime_features` and is activated explicitly by
:mod:`qlda.runtime_core.bootstrap`.

New business rules belong in ``qlda.domain`` / ``qlda.application``; new UI belongs
in ``qlda.presentation``.  This package is a compatibility boundary and must shrink
rather than grow.
"""
