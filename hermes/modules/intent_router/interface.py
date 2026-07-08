"""Public entry point for the Intent Router.

Everything outside this package imports from here, never from
service.py directly -- mirrors every other module's interface.py
convention.
"""
from __future__ import annotations

from hermes.modules.intent_router.errors import UnknownIntentError
from hermes.modules.intent_router.models import WorkflowRoute
from hermes.modules.intent_router.service import IntentRouter

__all__ = ["IntentRouter", "WorkflowRoute", "UnknownIntentError", "build_intent_router"]


def build_intent_router(*, default_workflow_id: str | None = None) -> IntentRouter:
    return IntentRouter(default_workflow_id=default_workflow_id)
