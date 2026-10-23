"""A provider that replays a fixed script of turns, for tests and demos.

Each entry is a ready ``ModelTurn`` or a function that builds one from the
request, so a script can react to tool results, for example by obeying text
found in a document the way a careless model might.
"""

import time
from collections.abc import Callable, Sequence

from app.application.agent_protocol import ModelRequest, ModelTurn

type ScriptStep = ModelTurn | Callable[[ModelRequest], ModelTurn]


class ScriptedProvider:
    """Return scripted turns in order; an exhausted script raises."""

    def __init__(
        self, name: str, script: Sequence[ScriptStep], *, delay_seconds: float = 0.0
    ) -> None:
        """Keep the script; delay_seconds simulates a slow provider."""
        self._name = name
        self._script = list(script)
        self._delay = delay_seconds
        self.requests: list[ModelRequest] = []

    @property
    def name(self) -> str:
        """The label recorded in traces."""
        return self._name

    def complete(self, request: ModelRequest) -> ModelTurn:
        """Record the request and return the next scripted turn."""
        self.requests.append(request)
        if self._delay:
            time.sleep(self._delay)
        if len(self.requests) > len(self._script):
            raise RuntimeError("The script has no more turns.")
        step = self._script[len(self.requests) - 1]
        return step(request) if callable(step) else step
