"""Data contracts for the Intent Router."""
from __future__ import annotations

from pydantic import BaseModel, Field


class WorkflowRoute(BaseModel):
    """One routing rule: match a request to `workflow_id` by an explicit
    intent hint, a leading command token, or a keyword substring. See
    `IntentRouter._match` (service.py) for the exact matching order --
    `priority` only breaks ties between routes matched by the SAME
    mechanism, it does not let a low-priority keyword route beat a
    higher-confidence command/intent match on another route."""

    workflow_id: str
    intent_names: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    command: str | None = None
    priority: int = Field(default=100, ge=0)
