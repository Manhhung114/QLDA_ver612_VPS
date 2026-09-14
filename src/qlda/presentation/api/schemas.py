from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class JobEnqueueRequest(BaseModel):
    upload_purpose: str = Field(min_length=1, max_length=4000)
    file_id: str = Field(min_length=1, max_length=200)


class FileTicketRequest(BaseModel):
    project_code: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=100)
    subtype: str = Field(default="Khac", max_length=100)
    record_code: str = Field(default="Chung", max_length=200)
    upload_purpose: str = Field(default="", max_length=4000)
    max_bytes: int | None = Field(default=None, ge=1)


class FileCountRequest(BaseModel):
    project_code: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=100)
    subtype: str = Field(default="Khac", max_length=100)
    record_codes: list[str] = Field(default_factory=list, max_length=500)


class AIAskRequest(BaseModel):
    project_id: int = Field(gt=0)
    question: str = Field(min_length=1, max_length=20000)
    provider: str = Field(default="openai", max_length=30)
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=30)
    status_date: date | None = None
    use_web: bool | None = None


class AIProjectRequest(BaseModel):
    project_id: int = Field(gt=0)
    provider: str = Field(default="openai", max_length=30)
    status_date: date | None = None


class AIReportRequest(AIProjectRequest):
    period: str = Field(default="tuần", max_length=100)


class AILegalRequest(AIProjectRequest):
    question: str = Field(min_length=1, max_length=20000)
    use_web: bool = True


class AITestRequest(BaseModel):
    provider: str = Field(default="openai", max_length=30)


class SearchRequest(BaseModel):
    project_id: int = Field(gt=0)
    query: str = Field(min_length=2, max_length=500)
    kinds: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=50, ge=1, le=200)
