"""Explicit application composition root.

Composition is the only place allowed to wire legacy compatibility installers into
native QLDA entrypoints. Importing a package must not mutate runtime behavior.
"""

from qlda.composition.runtime_features import RuntimeStage, feature_status, install_stage

__all__ = ["RuntimeStage", "feature_status", "install_stage"]
