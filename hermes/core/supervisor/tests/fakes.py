"""Test double for the Supervisable contract.

Not a specialist-agent or a real module -- a scripted stand-in used only
to exercise the Supervisor's lifecycle/health/restart logic in isolation.
"""
from __future__ import annotations


class ScriptedUnit:
    """A Supervisable whose `start`/`health_check` outcomes are scripted
    in advance. Each outcome list is consumed in order; once exhausted,
    the last scripted outcome repeats indefinitely.

    Outcomes: "ok" (succeeds / healthy), "raise" (raises), "unhealthy"
    (health_check only -- returns False without raising).
    """

    def __init__(
        self,
        *,
        start_outcomes: list[str] | None = None,
        health_outcomes: list[str] | None = None,
    ) -> None:
        self._start_outcomes = list(start_outcomes) if start_outcomes is not None else ["ok"]
        self._health_outcomes = list(health_outcomes) if health_outcomes is not None else ["ok"]
        self.start_calls = 0
        self.stop_calls = 0
        self.health_calls = 0

    async def start(self) -> None:
        self.start_calls += 1
        if self._next(self._start_outcomes) == "raise":
            raise RuntimeError("scripted start failure")

    async def stop(self) -> None:
        self.stop_calls += 1

    async def health_check(self) -> bool:
        self.health_calls += 1
        outcome = self._next(self._health_outcomes)
        if outcome == "raise":
            raise RuntimeError("scripted health check failure")
        return outcome != "unhealthy"

    @staticmethod
    def _next(outcomes: list[str]) -> str:
        if len(outcomes) > 1:
            return outcomes.pop(0)
        return outcomes[0]
