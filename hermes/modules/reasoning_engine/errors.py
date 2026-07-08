"""Reasoning Engine-specific exception types.

The Reasoning Engine is read-only over Context Builder output in
Sprint-3. Its failure modes are narrow: unknown or invalid request,
empty context after the Builder's filters, and the "this is a
future-Provider-Ecosystem-layer concern" guard rail.
"""
from __future__ import annotations


class ReasoningEngineError(Exception):
    """Base class for all Reasoning Engine failures."""


class ReasoningConfigError(ReasoningEngineError):
    """The Engine was constructed without a required collaborator
    (Context Builder) or the request parameters are invalid (e.g.
    empty seed set, non-positive `max_entries`)."""


class EmptyReasoningContextError(ReasoningEngineError):
    """The Context Builder returned an empty `AssembledContext`.
    Distinct from "no seeds" (which is a `ReasoningConfigError`)
    -- this fires AFTER the Builder tried and got nothing useful.
    """


class ProviderReasoningUnavailableError(ReasoningEngineError):
    """Guard rail: a caller asked the Engine to perform model
    reasoning directly (e.g. via a non-`assemble` mode). Sprint-3
    does not ship provider reasoning -- it belongs to a future
    Provider Ecosystem layer. This error fires so a misuse is
    loud, not silent.
    """