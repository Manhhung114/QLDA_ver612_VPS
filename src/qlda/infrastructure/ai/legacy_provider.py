from __future__ import annotations

"""Deprecated import shim for pre-migration callers.

The implementation is fully native and has no dependency on ``runtime_core``.
New code must import ``NativeProviderGateway`` from ``provider_gateway``.
"""

from qlda.infrastructure.ai.provider_gateway import AIProviderError, NativeProviderGateway


LegacyAIProvider = NativeProviderGateway


__all__ = ["LegacyAIProvider", "NativeProviderGateway", "AIProviderError"]
