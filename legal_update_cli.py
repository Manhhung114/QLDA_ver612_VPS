from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from legal_documents import LegalRepository, sync_all, sync_source, search_vsqi, search_online_all, search_online_sites


def main() -> int:
    ap = argparse.ArgumentParser(description="QLDA legal online update worker")
    ap.add_argument("--db", required=True, help="SQLite database path")
    ap.add_argument("--source", default="all", choices=["all", "vbpl", "vsqi", "moc_drafts", "tvpl", "search_vsqi", "search_web", "search_sites"])
    ap.add_argument("--query", default="")
    args = ap.parse_args()

    repo = LegalRepository(Path(args.db))
    try:
        if args.source == "all":
            results = sync_all(repo)
        elif args.source == "search_vsqi":
            docs = search_vsqi(args.query)
            stats = repo.upsert_many(docs, "VSQI - CSDL Tiêu chuẩn quốc gia")
            repo.log_sync("VSQI tra cứu", "OK", stats)
            results = [{"source": "VSQI tra cứu", **stats, "error": ""}]
        elif args.source == "search_web":
            docs = search_online_all(args.query)
            stats = repo.upsert_many(docs, "Tìm kiếm online tổng hợp")
            repo.log_sync("Tìm kiếm online tổng hợp", "OK", stats, f"Query: {args.query}")
            results = [{"source": "Tìm kiếm online tổng hợp", **stats, "error": ""}]
        elif args.source == "search_sites":
            docs = search_online_sites(args.query)
            stats = repo.upsert_many(docs, "Tìm kiếm các trang chỉ định")
            repo.log_sync("Tìm kiếm các trang chỉ định", "OK", stats, f"Query: {args.query}")
            results = [{"source": "Tìm kiếm các trang chỉ định", **stats, "error": ""}]
        else:
            results = [sync_source(repo, args.source)]
        print(json.dumps({"ok": True, "results": results, "query": args.query, "documents": docs[:60] if args.source in {"search_vsqi", "search_web", "search_sites"} else []}, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "results": []}, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
