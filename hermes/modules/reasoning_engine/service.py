"""Reasoning Engine -- prepare structured `ReasoningContext` for Commander.

This is **not** an AI reasoning engine. Per the Sprint-3 directive:

> The Reasoning Engine must not call AI models or perform provider
> reasoning yet. For this sprint, Reasoning Engine only prepares
> structured ReasoningContext payloads for Commander and future
> provider execution.

The Engine's job is to take a `ReasoningRequest` (intent + seed
set + mission), hand it to the Context Builder for assembly, and
freeze the result into a `ReasoningContext` payload. A future
Provider Ecosystem layer (out of scope) would consume the same
payload to perform the actual model reasoning.

Guard rail: if a caller asks the Engine to perform provider
reasoning (e.g. via `mode != "assemble"`), it raises
`ProviderReasoningUnavailableError`. This is deliberate -- the
Engine is not silent about its current scope.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from hermes.core.event_bus.interface import EventBus
from hermes.core.event_bus.models import Event
from hermes.modules.context_builder.errors import EmptyContextError
from hermes.modules.context_builder.models import ContextRequest
from hermes.modules.reasoning_engine import events as re_events
from hermes.modules.reasoning_engine.contracts import ContextSource
from hermes.modules.reasoning_engine.errors import (
    EmptyReasoningContextError,
    ProviderReasoningUnavailableError,
    ReasoningConfigError,
)
from hermes.modules.reasoning_engine.models import (
    ReasoningContext,
    ReasoningRequest,
    ReasoningTrace,
)

SOURCE_MODULE = "reasoning_engine"


class ReasoningEngine:
    """The Reasoning Engine. Read-only over the Context Builder in
    Sprint-3.

    Construction mirrors every other module: `context_builder` is
    required (the engine has no useful default), `event_bus` and
    `agent_id` are optional.
    """

    def __init__(
        self,
        *,
        context_builder: ContextSource,
        event_bus: EventBus | None = None,
        agent_id: str = "reasoning_engine",
    ) -> None:
        self._cb = context_builder
        self._bus = event_bus
        self._agent_id = agent_id

    # ====================================================================== #
    # prepare
    # ====================================================================== #
    async def prepare(self, request: ReasoningRequest) -> ReasoningContext:
        """Prepare a structured `ReasoningContext` for Commander.

        Steps:

        1. Validate request parameters.
        2. Reject non-`assemble` modes with `ProviderReasoningUnavailableError`
           (a guard rail against the Engine doing provider reasoning
           in Sprint-3).
        3. Delegate assembly to the Context Builder.
        4. Freeze the assembled entries + scoring trace into a
           `ReasoningContext`.
        5. Publish `REASONING_PREPARED` and return.
        """
        self._validate_request(request)
        if request.mode != "assemble":
            raise ProviderReasoningUnavailableError(
                f"ReasoningEngine.prepare() only supports mode='assemble' in Sprint-3; "
                f"got mode={request.mode!r}. Provider reasoning belongs to the Provider "
                f"Ecosystem layer, which is out of scope for this sprint."
            )
        requester = request.requesting_agent_id or self._agent_id

        # Step 3 -- delegate assembly. EmptyContextError from the
        # Builder is translated into our domain exception so callers
        # don't have to import Context Builder's errors.
        try:
            assembled = await self._cb.assemble(
                ContextRequest(
                    requesting_agent_id=requester,
                    seed_ids=list(request.seed_ids),
                    mission_id=request.mission_id,
                    k=request.max_entries,
                    min_confidence=request.min_confidence,
                    max_hops=request.max_hops,
                )
            )
        except EmptyContextError as exc:
            await self._publish_fail(requester, request, "empty_assembled_context")
            raise EmptyReasoningContextError(str(exc)) from exc
        except Exception:
            await self._publish_fail(requester, request, "context_builder_error")
            raise

        if not assembled.entries:
            await self._publish_fail(requester, request, "empty_assembled_context")
            raise EmptyReasoningContextError(
                "Context Builder returned an empty AssembledContext for the request"
            )

        # Step 4 -- freeze into ReasoningContext.
        request_id = uuid.uuid4()
        scores = [t.score for t in assembled.scoring_trace]
        trace = ReasoningTrace(
            request_id=request_id,
            request=request,
            context_entry_ids=[e.id for e in assembled.entries],
            context_scores=scores,
            assembled_at=assembled.assembled_at,
            metadata={
                "requester": requester,
                "intent": request.intent,
                "mission_id": str(request.mission_id) if request.mission_id else "",
                "mode": request.mode,
            },
        )
        context = ReasoningContext(
            request_id=request_id,
            requesting_agent_id=requester,
            intent=request.intent,
            mode=request.mode,
            mission_id=request.mission_id,
            entries=list(assembled.entries),
            context_scores=list(scores),
            trace=trace,
            prepared_at=datetime.now(timezone.utc),
        )

        await self._publish_prepared(requester, request, context)
        return context

    # ====================================================================== #
    # Validation + event publication
    # ====================================================================== #
    def _validate_request(self, request: ReasoningRequest) -> None:
        if not request.seed_ids:
            raise ReasoningConfigError("seed_ids must be non-empty")
        if request.max_entries <= 0:
            raise ReasoningConfigError(f"max_entries must be >= 1; got {request.max_entries}")
        if not request.intent.strip():
            raise ReasoningConfigError("intent must be a non-empty string")
        if not 0.0 <= request.min_confidence <= 1.0:
            raise ReasoningConfigError(
                f"min_confidence must be in [0.0, 1.0]; got {request.min_confidence}"
            )

    async def _publish_prepared(
        self, requester: str, request: ReasoningRequest, context: ReasoningContext
    ) -> None:
        await self._publish(
            re_events.REASONING_PREPARED,
            {
                "requester": requester,
                "intent": request.intent,
                "mission_id": str(request.mission_id) if request.mission_id else "",
                "entry_count": str(len(context.entries)),
            },
        )

    async def _publish_fail(
        self, requester: str, request: ReasoningRequest, reason: str
    ) -> None:
        await self._publish(
            re_events.REASONING_PREPARATION_FAILED,
            {
                "requester": requester,
                "reason": reason,
                "intent": request.intent,
            },
        )

    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.publish(
                Event(
                    event_type=event_type,
                    source_module=SOURCE_MODULE,
                    correlation_id=uuid.uuid4(),
                    payload=payload,
                )
            )
        except Exception:
            return