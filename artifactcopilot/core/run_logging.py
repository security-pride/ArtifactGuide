from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..shared.fs import ensure_dir, now_iso, write_json


class RunLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.pretty = os.getenv("AE_AGENT_PRETTY_TERMINAL", "1").strip().lower() not in {"0", "false", "no"}
        self.color = _terminal_colors_enabled()

    def log(self, message: str) -> None:
        timestamp = now_iso()
        line = f"[{timestamp}] {message}"
        print(_format_terminal_log(message, timestamp=timestamp, color=self.color) if self.pretty else line, flush=True)
        ensure_dir(self.path.parent)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


@dataclass
class RunStats:
    run_id: str
    repo_root: str
    started_at: str = field(default_factory=now_iso)
    ended_at: str = ""
    total_runtime_sec: float = 0.0
    llm_calls: int = 0
    agent_steps: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_prompt_cache_hit_tokens: int = 0
    total_prompt_cache_miss_tokens: int = 0
    tool_calls: int = 0
    tool_errors: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "repo_root": self.repo_root,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "total_runtime_sec": self.total_runtime_sec,
            "llm_calls": self.llm_calls,
            "agent_steps": self.agent_steps,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_prompt_cache_hit_tokens": self.total_prompt_cache_hit_tokens,
            "total_prompt_cache_miss_tokens": self.total_prompt_cache_miss_tokens,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
        }


class RunStatsTracker:
    def __init__(self, path: Path, *, run_id: str, repo_root: str) -> None:
        self.path = path
        self._started_monotonic = time.monotonic()
        self.stats = RunStats(run_id=run_id, repo_root=repo_root)
        self.save()

    def record_step(self) -> None:
        self.stats.agent_steps += 1
        self.save()

    def record_llm_usage(self, usage: dict[str, Any]) -> None:
        self.stats.llm_calls += 1
        self.stats.total_prompt_tokens += _as_int(usage.get("prompt_tokens"))
        self.stats.total_completion_tokens += _as_int(usage.get("completion_tokens"))
        self.stats.total_tokens += _as_int(usage.get("total_tokens"))
        self.stats.total_prompt_cache_hit_tokens += _as_int(usage.get("prompt_cache_hit_tokens"))
        self.stats.total_prompt_cache_miss_tokens += _as_int(usage.get("prompt_cache_miss_tokens"))
        self.save()

    def record_tool_call(self) -> None:
        self.stats.tool_calls += 1
        self.save()

    def record_tool_error(self) -> None:
        self.stats.tool_errors += 1
        self.save()

    def finalize(self) -> None:
        self.stats.ended_at = now_iso()
        self.stats.total_runtime_sec = round(time.monotonic() - self._started_monotonic, 3)
        self.save()

    def save(self) -> None:
        write_json(self.path, self.stats.to_dict())


def format_json_compact(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _format_terminal_log(message: str, *, timestamp: str, color: bool = False) -> str:
    width = _terminal_box_width()
    title = _log_title(message)
    body = _log_body(message)
    header = f" {title} | {timestamp} "
    top = "+" + _fit_header(header, width - 2) + "+"
    bottom = "+" + "-" * (width - 2) + "+"
    border_style = _box_style(message)
    lines = [_ansi(top, border_style, enabled=color)]
    for raw_line in body.splitlines() or [""]:
        for wrapped in _wrap_box_line(raw_line, width - 4):
            content = _colorize_box_content(wrapped.ljust(width - 4), enabled=color)
            border = _ansi("|", border_style, enabled=color)
            lines.append(f"{border} {content} {border}")
    lines.append(_ansi(bottom, border_style, enabled=color))
    return "\n".join(lines)


def _terminal_colors_enabled() -> bool:
    mode = os.getenv("AE_AGENT_TERMINAL_COLOR", "auto").strip().lower()
    if "NO_COLOR" in os.environ or mode in {"0", "false", "no", "never", "off"}:
        return False
    if mode in {"1", "true", "yes", "always", "on"}:
        return True
    return sys.stdout.isatty() and os.getenv("TERM", "").strip().lower() != "dumb"


def _box_style(message: str) -> str:
    if "observation=tool_error" in message or "observation=error" in message:
        return "bright_red"
    if "observation=protocol_error" in message or "rejected:" in message:
        return "bright_yellow"
    if " Result" in _log_title(message) or message.startswith("Run finished"):
        return "bright_green"
    if " Prompt" in _log_title(message):
        return "bright_cyan"
    if message.startswith("Phase preflight"):
        return "bright_blue"
    return "cyan"


def _colorize_box_content(text: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    match = re.match(r"(?P<label>[A-Za-z][A-Za-z _-]*:)(?P<rest>.*)", text)
    if not match:
        return text
    return _ansi(match.group("label"), "bold", enabled=True) + match.group("rest")


def _ansi(text: str, style: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    codes = {
        "bold": "1",
        "cyan": "36",
        "bright_red": "91",
        "bright_green": "92",
        "bright_yellow": "93",
        "bright_blue": "94",
        "bright_cyan": "96",
    }
    code = codes.get(style)
    return f"\033[{code}m{text}\033[0m" if code else text


def _terminal_box_width() -> int:
    columns = shutil.get_terminal_size(fallback=(100, 24)).columns
    return max(72, min(columns, 120))


def _fit_header(header: str, width: int) -> str:
    if len(header) > width:
        header = header[: max(0, width - 3)] + "..."
    left = max(0, (width - len(header)) // 2)
    right = max(0, width - len(header) - left)
    return "-" * left + header + "-" * right


def _log_title(message: str) -> str:
    if message.startswith("Run started"):
        return "Run Started"
    if message.startswith("Run finished"):
        return "Run Finished"
    if message.startswith("Phase preflight"):
        return "Phase Preflight"
    if message.startswith("Final report already exists"):
        return "Run Stop"
    if message.startswith("Stopping after requested phase"):
        return "Requested Stop"
    match = re.match(r"(?P<step>[A-Za-z0-9_]+-step-\d+)(?:\s+(?P<kind>prompt_sections|end|result_detail))?", message)
    if match:
        step = match.group("step").replace("_", " ")
        kind = match.group("kind") or "step"
        if kind == "prompt_sections":
            return f"{step} Prompt"
        if kind in {"end", "result_detail"}:
            return f"{step} Result"
        return step
    return "Agent Log"


def _log_body(message: str) -> str:
    prompt_match = re.match(r"(?P<step>[A-Za-z0-9_]+-step-\d+) prompt_sections: (?P<payload>\{.*\})\s*$", message, re.S)
    if prompt_match:
        return "Prompt sections:\n" + prompt_match.group("payload")
    detail_match = re.match(r"(?P<step>[A-Za-z0-9_]+-step-\d+) result_detail: (?P<payload>\{.*\})\s*$", message, re.S)
    if detail_match:
        try:
            payload = json.loads(detail_match.group("payload"))
        except Exception:
            payload = {"raw": detail_match.group("payload")}
        return _format_result_detail_body(payload)
    end_match = re.match(
        r"(?P<step>[A-Za-z0-9_]+-step-\d+) end action=(?P<action>\S+) observation=(?P<observation>\S+) "
        r"elapsed_sec=(?P<elapsed>\S+) summary=(?P<summary>.*)",
        message,
        re.S,
    )
    if end_match:
        return "\n".join(
            [
                f"Action: {end_match.group('action')}",
                f"Observation: {end_match.group('observation')}",
                f"Elapsed: {end_match.group('elapsed')} sec",
                f"Summary: {end_match.group('summary')}",
            ]
        )
    if message.startswith("Run started. "):
        return message.replace("Run started. ", "")
    if message.startswith("Run finished. "):
        return message.replace("Run finished. ", "")
    if message.startswith("Phase preflight: "):
        return "Phase preflight:\n" + message.removeprefix("Phase preflight: ")
    return message


def _format_result_detail_body(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    _append_kv(lines, "Action", payload.get("action"))
    _append_kv(lines, "Observation", payload.get("observation"))
    _append_kv(lines, "Elapsed", f"{payload.get('elapsed_sec')} sec" if payload.get("elapsed_sec") not in (None, "") else "")
    _append_kv(lines, "Decision reason", payload.get("decision_reason"))
    _append_kv(lines, "Memory note", payload.get("memory_note"))
    tools = payload.get("planned_tools")
    if isinstance(tools, list) and tools:
        lines.append("Planned tools:")
        for index, tool in enumerate(tools, start=1):
            lines.extend(_format_tool_plan(tool, prefix=f"  {index}. "))
    result = payload.get("result")
    if isinstance(result, dict):
        lines.append("Result facts:")
        lines.extend(_format_result_facts(result, prefix="  "))
    _append_kv(lines, "Summary", payload.get("summary"))
    return "\n".join(line for line in lines if line is not None)


def _format_tool_plan(tool: Any, *, prefix: str) -> list[str]:
    if not isinstance(tool, dict):
        return [prefix + _compact_display(tool, 600)]
    lines = [prefix + f"Tool: {tool.get('tool', '')}"]
    ordered = ["purpose", "phase_name", "cwd", "timeout_sec", "command", "paths", "relative_path", "subpath", "paper_source", "finish_reason", "handoff_summary", "evidence_refs"]
    for key in ordered:
        if key in tool and tool.get(key) not in (None, "", [], {}):
            lines.append("     " + _label_for_key(key) + ": " + _compact_display(tool.get(key), 2200))
    if tool.get("intervention"):
        lines.append("     Intervention: " + _compact_display(tool.get("intervention"), 1200))
    if tool.get("content_preview") is not None:
        lines.append("     Content preview: " + _compact_display(tool.get("content_preview"), 1200))
    if tool.get("payload_preview") is not None:
        lines.append("     Payload preview: " + _compact_display(tool.get("payload_preview"), 1200))
    return lines


def _format_result_facts(result: dict[str, Any], *, prefix: str) -> list[str]:
    if "tool_results" in result and isinstance(result.get("tool_results"), list):
        lines: list[str] = []
        for index, item in enumerate(result.get("tool_results", []), start=1):
            lines.append(prefix + f"{index}. Tool result")
            lines.extend(_format_result_facts(item if isinstance(item, dict) else {"summary": item}, prefix=prefix + "   "))
        if result.get("omitted_results"):
            lines.append(prefix + f"... omitted {result.get('omitted_results')} result(s)")
        return lines
    lines = []
    for key in [
        "tool",
        "kind",
        "ok",
        "command_id",
        "purpose",
        "command",
        "cwd",
        "final_status",
        "returncode",
        "strict_shell",
        "agent_action_required",
        "stdout_ref",
        "stderr_ref",
        "phase_command_ledger_ref",
        "duration_sec",
        "wrote_ref",
        "size_bytes",
        "paper_loaded",
        "paper_text_chars",
        "paper_text_ref",
        "paper_profile_ref",
        "next_phase",
        "step_trace_ref",
        "base",
        "subpath",
        "entry_count",
        "omitted_entries",
        "omitted_files",
        "repo_mutation_review_required",
        "error",
        "summary",
    ]:
        if key in result and result.get(key) not in (None, "", [], {}):
            lines.append(prefix + _label_for_key(key) + ": " + _compact_display(result.get(key), 2200))
    if result.get("files"):
        lines.append(prefix + "Files:")
        for item in result.get("files", [])[:8]:
            if not isinstance(item, dict):
                continue
            main = f"{item.get('path', '')} exists={item.get('exists', '')} status={item.get('read_status', '')} chars={item.get('content_chars', '')}"
            lines.append(prefix + "  - " + main)
            if item.get("evidence_ref"):
                lines.append(prefix + "    Ref: " + str(item.get("evidence_ref")))
            if item.get("excerpt"):
                lines.append(prefix + "    Excerpt: " + _compact_display(item.get("excerpt"), 1000))
            if item.get("error"):
                lines.append(prefix + "    Error: " + _compact_display(item.get("error"), 600))
    if result.get("entries_preview"):
        lines.append(prefix + "Entries preview:")
        for item in result.get("entries_preview", [])[:30]:
            lines.append(prefix + "  - " + str(item))
    for key in ["fatal_patterns", "warning_patterns", "stdout_excerpt", "stderr_excerpt", "phase_finish_signal", "data_excerpt"]:
        if result.get(key) not in (None, "", [], {}):
            lines.append(prefix + _label_for_key(key) + ":")
            lines.extend(prefix + "  " + line for line in _compact_display(result.get(key), 2600).splitlines())
    return lines


def _append_kv(lines: list[str], label: str, value: Any) -> None:
    if value in (None, "", [], {}):
        return
    text = _compact_display(value, 2200)
    if "\n" in text:
        lines.append(f"{label}:")
        lines.extend("  " + line for line in text.splitlines())
    else:
        lines.append(f"{label}: {text}")


def _label_for_key(key: str) -> str:
    labels = {
        "phase_name": "Phase",
        "timeout_sec": "Timeout",
        "relative_path": "Path",
        "finish_reason": "Finish reason",
        "handoff_summary": "Handoff",
        "evidence_refs": "Evidence refs",
        "command_id": "Command id",
        "final_status": "Final status",
        "returncode": "Return code",
        "strict_shell": "Strict shell",
        "agent_action_required": "Agent action required",
        "stdout_ref": "Stdout ref",
        "stderr_ref": "Stderr ref",
        "phase_command_ledger_ref": "Command ledger ref",
        "duration_sec": "Command duration",
        "wrote_ref": "Wrote ref",
        "size_bytes": "Size bytes",
        "paper_loaded": "Paper loaded",
        "paper_text_chars": "Paper text chars",
        "paper_text_ref": "Paper text ref",
        "paper_profile_ref": "Paper profile ref",
        "next_phase": "Next phase",
        "step_trace_ref": "Step trace ref",
        "entry_count": "Entry count",
        "omitted_entries": "Omitted entries",
        "omitted_files": "Omitted files",
        "repo_mutation_review_required": "Repo mutation review required",
        "fatal_patterns": "Fatal patterns",
        "warning_patterns": "Warning patterns",
        "stdout_excerpt": "Stdout excerpt",
        "stderr_excerpt": "Stderr excerpt",
        "phase_finish_signal": "Phase finish signal",
        "data_excerpt": "Data excerpt",
    }
    return labels.get(key, key.replace("_", " ").capitalize())


def _compact_display(value: Any, limit: int) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, indent=2)
    else:
        text = str(value)
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[: max(0, limit // 2)].rstrip()
    tail = text[-max(0, limit // 2) :].lstrip()
    return f"{head}\n...[truncated {len(text) - limit} chars]...\n{tail}"


def _wrap_box_line(line: str, width: int) -> list[str]:
    if len(line) <= width:
        return [line]
    chunks = []
    current = line
    while len(current) > width:
        chunks.append(current[:width])
        current = current[width:]
    chunks.append(current)
    return chunks


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0
