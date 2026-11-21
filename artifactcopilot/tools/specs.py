from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ToolContext:
    repo_root: Path
    run_root: Path
    run_id: str
    shared_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    required_args: tuple[str, ...] = ()
    optional_args: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    usage: str = ""
    when_to_use: tuple[str, ...] = ()
    arg_docs: tuple[tuple[str, str], ...] = ()
    return_docs: tuple[str, ...] = ()
    path_rules: tuple[str, ...] = ()


@dataclass
class ToolResult:
    tool_name: str
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class ToolError(RuntimeError):
    def __init__(self, message: str, *, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.data = data or {}


ToolHandler = Callable[[ToolContext, dict[str, Any]], ToolResult]
