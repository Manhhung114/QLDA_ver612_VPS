from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Iterable

from .models import EvidenceRef, IntegrityReport


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def checksum(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def evidence_id(project_id: int, source_kind: str, source_name: str, source_ref: str, digest: str = "") -> str:
    raw = f"{int(project_id)}|{source_kind}|{source_name}|{source_ref}|{digest}"
    return "EV-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20].upper()


def make_evidence(project_id: int, *, source_kind: str, source_name: str, source_ref: str, value: Any = None, metadata: dict[str, Any] | None = None) -> EvidenceRef:
    digest = checksum(value) if value is not None else ""
    return EvidenceRef(
        evidence_id=evidence_id(project_id, source_kind, source_name, source_ref, digest),
        source_kind=str(source_kind),
        source_name=str(source_name),
        source_ref=str(source_ref),
        checksum=digest,
        metadata=dict(metadata or {}),
    )


class DataIntegrityGate:
    """V7.7 reconciliation gate.

    AI/action execution must not rely on a dataset that has lost source rows or
    contains duplicate logical records.  The gate deliberately separates raw
    source count from normalized count: normalization may legitimately produce
    more/fewer points than rows, but raw persistence must reconcile exactly.
    """

    def reconcile(
        self,
        *,
        project_id: int,
        source_name: str,
        source_rows: Iterable[dict[str, Any]],
        stored_rows: Iterable[dict[str, Any]],
        normalized_rows: Iterable[dict[str, Any]] = (),
        source_key: str = "source_row",
    ) -> IntegrityReport:
        source = [dict(x) for x in source_rows]
        stored = [dict(x) for x in stored_rows]
        normalized = [dict(x) for x in normalized_rows]

        src_keys = [str(x.get(source_key) or "") for x in source if str(x.get(source_key) or "")]
        db_keys = [str(x.get(source_key) or "") for x in stored if str(x.get(source_key) or "")]
        src_counter = Counter(src_keys)
        db_counter = Counter(db_keys)
        missing = sum(max(0, count - db_counter.get(key, 0)) for key, count in src_counter.items())
        duplicates = sum(max(0, count - 1) for count in db_counter.values())

        warnings: list[str] = []
        if len(source) != len(stored):
            warnings.append(f"Số dòng nguồn {len(source)} khác số dòng đã lưu {len(stored)}.")
        if missing:
            warnings.append(f"Thiếu {missing} dòng nguồn trong kho dữ liệu.")
        if duplicates:
            warnings.append(f"Có {duplicates} dòng nguồn bị lặp trong kho dữ liệu.")

        valid = len(source) == len(stored) and missing == 0 and duplicates == 0
        penalties = 0.0
        if source:
            penalties += min(60.0, 60.0 * missing / max(1, len(source)))
            penalties += min(25.0, 25.0 * duplicates / max(1, len(source)))
            penalties += min(15.0, 15.0 * abs(len(source) - len(stored)) / max(1, len(source)))
        score = max(0.0, 100.0 - penalties)

        evidence = (
            make_evidence(
                project_id,
                source_kind="RECONCILIATION",
                source_name=source_name,
                source_ref="raw-row-count",
                value={"source": len(source), "stored": len(stored), "missing": missing, "duplicates": duplicates},
            ),
        )
        return IntegrityReport(
            valid=valid,
            score=round(score, 2),
            source_count=len(source),
            stored_count=len(stored),
            normalized_count=len(normalized),
            missing_count=missing,
            duplicate_count=duplicates,
            warnings=tuple(warnings),
            evidence=evidence,
        )

    @staticmethod
    def require_valid(report: IntegrityReport, *, minimum_score: float = 100.0) -> None:
        if not report.valid or float(report.score) < float(minimum_score):
            detail = "; ".join(report.warnings) or "Dữ liệu chưa qua kiểm tra toàn vẹn."
            raise ValueError(f"AI_DATA_VALID=False: {detail}")
