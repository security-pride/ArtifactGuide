from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .specs import ToolContext, ToolError, ToolHandler, ToolResult, ToolSpec


class GlobalToolRegistry:
    def __init__(self, context: ToolContext) -> None:
        self.context = context
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler

    def describe(self) -> str:
        rows: list[str] = []
        for name in sorted(self._specs):
            spec = self._specs[name]
            lines = [
                f"tool: {spec.name}",
                f"purpose: {spec.description}",
            ]
            if spec.usage:
                lines.append(f"usage: {spec.usage}")
            if spec.when_to_use:
                lines.append("use_when:")
                lines.extend(f"- {item}" for item in spec.when_to_use)
            if spec.required_args or spec.optional_args:
                lines.append("args:")
                for arg_name in spec.required_args:
                    doc = _arg_doc(spec, arg_name)
                    lines.append(f"- required `{arg_name}`" + (f": {doc}" if doc else ""))
                for arg_name in spec.optional_args:
                    doc = _arg_doc(spec, arg_name)
                    lines.append(f"- optional `{arg_name}`" + (f": {doc}" if doc else ""))
            if spec.path_rules:
                lines.append("path_rules:")
                lines.extend(f"- {item}" for item in spec.path_rules)
            if spec.return_docs:
                lines.append("returns:")
                lines.extend(f"- {item}" for item in spec.return_docs)
            if spec.notes:
                lines.append("notes:")
                lines.extend(f"- {item}" for item in spec.notes)
            rows.append("\n".join(lines))
        return "\n\n".join(rows)

    def invoke(self, tool_name: str, args: dict[str, Any]) -> ToolResult:
        spec = self._specs.get(tool_name)
        handler = self._handlers.get(tool_name)
        if spec is None or handler is None:
            raise ToolError(f"Tool `{tool_name}` is not registered.")
        if not isinstance(args, dict):
            raise ToolError("Tool args must be a JSON object.")
        return handler(self.context, args)

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._specs and tool_name in self._handlers

    def registered_tool_specs(self) -> list[dict[str, Any]]:
        return [asdict(spec) for spec in self._specs.values()]


def _arg_doc(spec: ToolSpec, arg_name: str) -> str:
    for key, value in spec.arg_docs:
        if key == arg_name:
            return value
    return ""
