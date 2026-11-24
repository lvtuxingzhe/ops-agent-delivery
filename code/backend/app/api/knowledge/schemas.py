"""知识库请求模型；组件名继续沿用 `app.api.knowledge`。"""

from __future__ import annotations

from pydantic import BaseModel


class ManualBody(BaseModel):
    title: str
    summary: str = ""
    body: str = ""
    tech_domain: str = "other"
    tags: list[str] = []
    target_hosts: list[str] | str = []
    target_services: list[str] | str = []


class RagBody(BaseModel):
    query: str
    limit: int = 8
    kind: str = ""
    tech_domain: str = ""
    days: int = 0
    host: str = ""
    service: str = ""
    tags: list[str] = []


class OverrideBody(BaseModel):
    source_kind: str
    decision: str
    reason: str = ""


class JudgeBody(BaseModel):
    source_kind: str


for _model in (ManualBody, RagBody, OverrideBody, JudgeBody):
    _model.__module__ = "app.api.knowledge"
    _model.model_rebuild(force=True)
