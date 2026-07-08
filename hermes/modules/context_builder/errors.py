"""Context Builder-specific exception types.

The Context Builder's failure modes are narrow: an empty seed set
under a "fail loud" policy, a misconfigured collaborator, or an
assembled context with zero results after filtering.
"""
from __future__ import annotations


class ContextBuilderError(Exception):
    """Base class for all Context Builder failures."""


class ContextBuilderConfigError(ContextBuilderError):
    """The Builder was constructed without a required collaborator
    (Memory Reader or Knowledge Graph) or received invalid request
    parameters (e.g. `k <= 0`)."""


class EmptyContextError(ContextBuilderError):
    """The assembled context is empty -- no entry survived the
    request filters. Distinct from "no seeds supplied," which is a
    `ContextBuilderConfigError`: this error fires AFTER the Builder
    tried and got nothing useful back.
    """
