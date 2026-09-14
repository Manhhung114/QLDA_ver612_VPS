from __future__ import annotations

from qlda.shared.legacy import resolve

IMPLEMENTATION = "vo_background_v624"


def __getattr__(name: str):
    return resolve(IMPLEMENTATION, name)
