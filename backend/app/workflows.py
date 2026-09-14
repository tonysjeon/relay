"""In-memory workflow definitions and validation, independent of persistence."""

from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Workflow and step names must be non-empty strings")


@dataclass(frozen=True)
class StepDefinition:
    name: str
    handler: Callable[[dict[str, Any]], Any]
    depends_on: tuple[str, ...] = ()
    max_attempts: int = 1
    requires_approval: bool = False

    def __post_init__(self) -> None:
        _validate_name(self.name)
        if type(self.requires_approval) is not bool:
            raise TypeError("requires_approval must be a boolean")
        if not callable(self.handler):
            raise TypeError(f"Handler for step {self.name!r} must be callable")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if isinstance(self.depends_on, str):
            raise TypeError("depends_on must be a sequence of step names")
        dependencies = tuple(self.depends_on)
        for name in dependencies:
            _validate_name(name)
        if len(set(dependencies)) != len(dependencies):
            raise ValueError(f"Step {self.name!r} has duplicate dependencies")
        object.__setattr__(self, "depends_on", dependencies)


class Workflow:
    def __init__(self, name: str):
        _validate_name(name)
        self.name = name
        self._steps: dict[str, StepDefinition] = {}

    @property
    def steps(self) -> Mapping[str, StepDefinition]:
        return MappingProxyType(self._steps)

    def step(
        self,
        name: str,
        handler: Callable[[dict[str, Any]], Any],
        *,
        depends_on: Sequence[str] | None = None,
        retries: int = 0,
        requires_approval: bool = False,
    ) -> StepDefinition:
        _validate_name(name)
        if name in self._steps:
            raise ValueError(f"Duplicate step name: {name!r}")
        if type(retries) is not int or retries < 0:
            raise ValueError("retries must be a non-negative integer")
        if isinstance(depends_on, str):
            raise TypeError("depends_on must be a sequence of step names")
        step = StepDefinition(
            name=name,
            handler=handler,
            depends_on=tuple(depends_on) if depends_on is not None else (),
            max_attempts=retries + 1,
            requires_approval=requires_approval,
        )
        self._steps[name] = step
        return step

    def validate(self) -> None:
        """Raise ValueError for an empty graph, missing dependencies, or cycles."""
        if not self._steps:
            raise ValueError(f"Workflow {self.name!r} must contain at least one step")

        remaining = {name: len(step.depends_on) for name, step in self._steps.items()}
        dependents: dict[str, list[str]] = {name: [] for name in self._steps}
        for name, step in self._steps.items():
            for dependency in step.depends_on:
                if dependency not in self._steps:
                    raise ValueError(
                        f"Step {name!r} depends on missing step {dependency!r}"
                    )
                dependents[dependency].append(name)

        ready = deque(name for name, count in remaining.items() if count == 0)
        visited = 0
        while ready:
            name = ready.popleft()
            visited += 1
            for dependent in dependents[name]:
                remaining[dependent] -= 1
                if remaining[dependent] == 0:
                    ready.append(dependent)

        if visited != len(self._steps):
            blocked = ", ".join(name for name, count in remaining.items() if count)
            raise ValueError(f"Circular dependency detected; blocked steps: {blocked}")
