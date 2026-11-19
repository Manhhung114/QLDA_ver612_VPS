from __future__ import annotations

"""Infrastructure CLI wiring for the official legal catalog.

Only this adapter imports the legacy legal search provider. Domain/application
catalog rules stay independent from qlda.runtime_core.
"""

from typing import Any

from qlda.application.legal_sources import main as build_catalog


def _standards_search(query: str) -> list[dict[str, Any]]:
    from qlda.runtime_core import legal_documents
    return list(legal_documents.search_vsqi(query, limit=18))


def _laws_search(query: str) -> list[dict[str, Any]]:
    from qlda.runtime_core import legal_documents
    return list(legal_documents.search_online_sites(
        query, domains=[
            "vbpl.vn", "congbao.chinhphu.vn",
            "bxd.gov.vn", "moc.gov.vn",
        ], limit=18,
    ))


def main(argv: list[str] | None = None) -> int:
    return build_catalog(
        argv,
        standards_search=_standards_search,
        laws_search=_laws_search,
    )


if __name__ == "__main__":
    raise SystemExit(main())
