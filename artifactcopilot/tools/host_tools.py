from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from ..shared.fs import ensure_dir, relpath_str, safe_read_text, write_json, write_text
from .command_tools import run_host_command
from .common import bounded_int, dedupe_strings, resolve_repo_path, resolve_run_path, safe_artifact_filename, sha256_file
from .paper_source_tools import extract_paper_source
from .rubric_evidence_engine import normalize_final_scoring_packet
from .specs import ToolContext, ToolError, ToolResult, ToolSpec

INLINE_FILE_PREVIEW_THRESHOLD = 12_000
INLINE_FILE_PREVIEW_HEAD_CHARS = 600
INLINE_FILE_PREVIEW_TAIL_CHARS = 600
MAX_INLINE_FILE_TOKENS = 300_000
MAX_INLINE_FILE_CHARS = MAX_INLINE_FILE_TOKENS * 4

SKIP_TREE_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".artifactcopilot_run",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "node_modules",
}


def get_host_tools() -> list[tuple[ToolSpec, Any]]:
    return [
        (
            ToolSpec(
                name="load_run_config",
                description="Load the AE run inputs and write the run configuration under the run root.",
                optional_args=("config_path", "paper_source", "manual_paper_targets", "official_badge", "artifact_url"),
                usage='{"name":"load_run_config","args":{"paper_source":"/path/to/paper.pdf"}}',
                when_to_use=("Use once during run_initialization.",),
                return_docs=("Returns run_config and the run-root config path.",),
            ),
            _load_run_config,
        ),
        (
            ToolSpec(
                name="list_repo_tree",
                description="List artifact repository files and directories.",
                optional_args=("subpath", "max_depth", "max_entries"),
                usage='{"name":"list_repo_tree","args":{"subpath":".","max_depth":3,"max_entries":500}}',
                when_to_use=("Use before choosing which artifact files to read.",),
                return_docs=("Returns repository-relative paths, absolute paths, and file/dir kinds.",),
                path_rules=("subpath is repository-relative.",),
            ),
            _list_repo_tree,
        ),
        (
            ToolSpec(
                name="read_file",
                description="Read repository files or run-root files.",
                required_args=("paths",),
                optional_args=("base", "max_chars"),
                usage='{"name":"read_file","args":{"base":"repo","paths":["README.md"]}}',
                when_to_use=(
                    "Use for semantic reading of artifact docs, configs, generated stage packets, logs, and paper text refs.",
                    "By default this returns full file content. Use max_chars only when an intentional preview is enough.",
                ),
                arg_docs=(
                    ("base", "`repo` for artifact repository files, or `run` for files under the run root. Defaults to repo."),
                    ("paths", "List of paths relative to the selected base, or absolute paths inside that base."),
                    ("max_chars", "Optional explicit preview size per file. If omitted, full file content is returned."),
                ),
                return_docs=("Returns file metadata, excerpt text, truncation status, and evidence refs.",),
            ),
            _read_file,
        ),
        (
            ToolSpec(
                name="write_file",
                description="Write text or JSON under the run root.",
                required_args=("relative_path",),
                optional_args=("text", "json_payload"),
                usage='{"name":"write_file","args":{"relative_path":"artifacts/D1_documentation/stage_packet.json","json_payload":{}}}',
                when_to_use=(
                    "Use to write stage packets, evidence files, memory files, generated inputs, and final reports.",
                    "Do not use this to modify artifact repository files.",
                ),
                return_docs=("Returns the written file path and run-root relative ref.",),
                path_rules=("relative_path must stay inside the run root.",),
            ),
            _write_file,
        ),
        (
            ToolSpec(
                name="get_paper_info",
                description="Extract paper text and metadata from the configured or supplied paper source.",
                optional_args=("paper_source", "max_chars", "max_pages"),
                usage='{"name":"get_paper_info","args":{"max_chars":20000}}',
                when_to_use=(
                    "Use during run_initialization to ensure paper text exists.",
                    "Use during D4 when full paper text is needed. Omit max_pages unless the user explicitly limits extraction.",
                ),
                return_docs=("Writes paper_source_profile.json and paper_text.txt under artifacts/paper_source.",),
            ),
            _get_paper_info,
        ),
        (
            ToolSpec(
                name="run_command",
                description="Run a literal host command and record command facts/log refs.",
                required_args=("command", "log_label"),
                optional_args=(
                    "cwd",
                    "timeout_sec",
                    "phase_name",
                    "purpose",
                    "strict_shell",
                    "fatal_patterns",
                    "warning_patterns",
                    "benign_patterns",
                    "intervention",
                ),
                usage='{"name":"run_command","args":{"command":"docker build -t ae-artifact .","log_label":"docker_build","cwd":".","phase_name":"D2_environment","purpose":"build provided Docker environment","timeout_sec":10800}}',
                when_to_use=(
                    "Use when a command is required by the current stage workflow.",
                    "D1 must not use this for workflow execution.",
                ),
                return_docs=(
                    "Returns command_id, final_status, exit code, stdout/stderr previews, and full log refs.",
                    "The command result is factual evidence only. Final rubric scoring happens only in final_scoring.",
                ),
                arg_docs=(
                    ("intervention", 'Optional object only when the command is a reviewer intervention, e.g. {"kind":"I2","summary":"Patch documented path to run under the staged review directory","details":{"semantic_preserving":true}}. Do not use true/false or a bare string.'),
                ),
            ),
            _run_command,
        ),
        (
            ToolSpec(
                name="phase_finish",
                description="Signal that the current stage is complete and move to the next stage.",
                required_args=("phase_name", "finish_reason"),
                optional_args=("handoff_summary", "residual_risks", "evidence_refs"),
                usage='{"name":"phase_finish","args":{"phase_name":"D1_documentation","finish_reason":"Stage packet and workflow extraction were written.","handoff_summary":"...","evidence_refs":["artifacts/D1_documentation/stage_packet.json"]}}',
                when_to_use=("Use only after the current stage skill's required files have been written.",),
                return_docs=("Advances to the next stage when accepted by the agent runtime.",),
            ),
            _phase_finish,
        ),
    ]


def _load_run_config(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    config: dict[str, Any] = {
        "inputs": {
            "artifact_repo": str(context.repo_root),
            "paper_pdf": None,
            "artifact_url": None,
            "official_badge": None,
            "manual_paper_targets": None,
        },
        "budget": {
            "max_wall_time_hours": 24,
            "command_silent_timeout_minutes": 180,
            "disk_limit_gb": 1000,
            "allow_network": True,
            "allow_gpu": True,
            "allow_paid_api": True,
            "allow_external_services": True,
            "max_intervention_level": "I4",
            "allow_repo_modification": True,
        },
        "policy": {
            "rubric": {
                "name": "artifact_evaluation_rubric",
                "version": "2026-05",
                "profile": "icse2027_main",
            },
            "reviewer_mode": "full_execution_allowed",
        },
    }
    config_path = _optional_string(args.get("config_path"))
    if config_path:
        candidate = Path(config_path).expanduser()
        if not candidate.is_absolute():
            resolved = resolve_repo_path(context.repo_root, config_path)
            candidate = resolved if resolved is not None else candidate
        if candidate.exists() and candidate.is_file():
            loaded = yaml.safe_load(candidate.read_text(encoding="utf-8", errors="replace")) or {}
            if isinstance(loaded, dict):
                _deep_merge(config, loaded)
            config["loaded_config_path"] = str(candidate.resolve())
        else:
            config["config_load_warning"] = f"Config path does not exist: {config_path}"
    for arg_name, target_key in [
        ("paper_source", "paper_pdf"),
        ("manual_paper_targets", "manual_paper_targets"),
        ("official_badge", "official_badge"),
        ("artifact_url", "artifact_url"),
    ]:
        value = _optional_string(args.get(arg_name))
        if value is not None:
            config["inputs"][target_key] = value
    if not isinstance(config.get("inputs"), dict):
        config["inputs"] = {}
    config["inputs"]["artifact_repo"] = str(context.repo_root)
    mirrored_inputs = _mirror_config_input_files(context, config)
    artifact_universe = _build_artifact_universe(context)
    config["artifact_universe"] = {
        "root_scope": artifact_universe.get("root_scope"),
        "component_count": len(artifact_universe.get("components", [])),
        "boundary_warning_count": len(artifact_universe.get("boundary_warnings", [])),
        "ref": "artifacts/run_initialization/artifact_universe.json",
    }
    write_json(context.run_root / "artifacts" / "run_initialization" / "artifact_universe.json", artifact_universe)
    path = context.run_root / "ae_config.yaml"
    ensure_dir(path.parent)
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return ToolResult(
        tool_name="load_run_config",
        ok=True,
        summary="Loaded AE run configuration.",
        data={
            "run_config": config,
            "config_path": str(path),
            "mirrored_inputs": mirrored_inputs,
            "artifact_universe": artifact_universe,
            "artifact_universe_ref": "artifacts/run_initialization/artifact_universe.json",
        },
    )


def _list_repo_tree(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    max_depth = bounded_int(args.get("max_depth", 3), lower=1, upper=5)
    max_entries = bounded_int(args.get("max_entries", 1000), lower=20, upper=8000)
    subpath = str(args.get("subpath", ".")).strip() or "."
    root = resolve_repo_path(context.repo_root, subpath)
    if root is None or not root.exists():
        return ToolResult(
            tool_name="list_repo_tree",
            ok=True,
            summary=f"Repository subpath does not exist: {subpath}",
            data={"subpath": subpath, "entries": [], "status": "not_found"},
        )
    if not root.is_dir():
        return ToolResult(
            tool_name="list_repo_tree",
            ok=True,
            summary=f"Repository subpath is not a directory: {subpath}",
            data={"subpath": subpath, "entries": [], "status": "not_directory"},
        )
    entries: list[dict[str, str]] = []
    start_depth = len(root.relative_to(context.repo_root).parts) if root != context.repo_root else 0
    for path in sorted(root.rglob("*")):
        try:
            relative = path.relative_to(context.repo_root)
        except Exception:
            continue
        if _skip_tree_path(relative):
            continue
        depth = len(relative.parts) - start_depth
        if depth > max_depth:
            continue
        entries.append(
            {
                "path": relative.as_posix(),
                "abs_path": str(path.resolve()),
                "kind": "dir" if path.is_dir() else "file",
            }
        )
        if len(entries) >= max_entries:
            break
    return ToolResult(
        tool_name="list_repo_tree",
        ok=True,
        summary=f"Listed {len(entries)} repository entries.",
        data={"subpath": subpath, "max_depth": max_depth, "entries": entries, "status": "exists_nonempty" if entries else "exists_empty"},
    )


def _read_file(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    raw_paths = args.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ToolError("`paths` must be a non-empty list.")
    base = str(args.get("base", "repo")).strip().lower() or "repo"
    if base not in {"repo", "run"}:
        raise ToolError("`base` must be `repo` or `run`.")
    explicit_max_chars = "max_chars" in args and args.get("max_chars") is not None
    max_chars = bounded_int(args.get("max_chars"), lower=1000, upper=250_000) if explicit_max_chars else None
    root = context.repo_root if base == "repo" else context.run_root
    files: list[dict[str, Any]] = []
    for item in raw_paths:
        requested = str(item).strip()
        if not requested:
            continue
        path = _resolve_base_path(root, requested)
        if path is None:
            files.append({"path": requested, "exists": False, "error": f"Path escapes {base} root.", "excerpt": ""})
            continue
        if not path.exists() or not path.is_file():
            files.append({"path": requested, "exists": False, "error": "File does not exist or is not a file.", "excerpt": ""})
            continue
        try:
            stat = path.stat()
            text = safe_read_text(path)
        except OSError as exc:
            files.append(
                {
                    "path": requested,
                    "exists": True,
                    "error": f"Could not read file metadata/content: {type(exc).__name__}: {exc}",
                    "excerpt": "",
                    "read_status": "read_error",
                    "full_content_on_disk": True,
                }
            )
            continue
        effective_max_chars = max_chars if max_chars is not None else _auto_inline_file_chars(text)
        preview = _file_preview(text, max_chars=effective_max_chars)
        auto_truncated = not explicit_max_chars and preview["truncated"]
        files.append(
            {
                "path": relpath_str(path, root),
                "abs_path": str(path.resolve()),
                "base": base,
                "exists": True,
                "size_bytes": stat.st_size,
                "content_chars": len(text),
                "estimated_tokens": max(1, len(text) // 4),
                "max_chars_used": effective_max_chars,
                "explicit_max_chars": explicit_max_chars,
                "read_status": "preview" if preview["truncated"] else "full",
                "was_truncated": preview["truncated"],
                "auto_truncated_for_prompt_budget": auto_truncated,
                "truncation_reason": "estimated_tokens_over_300k" if auto_truncated else "",
                "excerpt": preview["text"],
                "evidence_ref": str(path.resolve()) if base == "repo" else relpath_str(path, context.run_root),
                "full_content_on_disk": True,
            }
        )
    return ToolResult(
        tool_name="read_file",
        ok=True,
        summary=f"Read {sum(1 for item in files if item.get('exists'))} file(s) from {base}.",
        data={"base": base, "files": files},
    )


def _write_file(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    relative_path = str(args.get("relative_path", "")).strip()
    if not relative_path:
        raise ToolError("`relative_path` must be a non-empty run-root-relative path.")
    _enforce_write_scope(context, relative_path)
    target = resolve_run_path(context.run_root, relative_path)
    if target is None:
        raise ToolError("Target path escapes run root.")
    normalized = Path(relative_path).as_posix().lstrip("./")
    try:
        if "json_payload" in args and isinstance(args.get("json_payload"), (dict, list)):
            payload = args["json_payload"]
            if isinstance(payload, dict):
                payload = _normalize_json_payload(context, normalized, payload)
            write_json(target, payload)
        else:
            text = str(args.get("text", ""))
            normalized_payload = _try_normalize_text_json_payload(context, normalized, text)
            if normalized_payload is not None:
                write_json(target, normalized_payload)
            elif normalized.endswith(".json"):
                try:
                    json.loads(text)
                except Exception as exc:
                    raise ToolError(
                        f"Refusing to write invalid JSON to `{normalized}`: {exc}. "
                        "Use `json_payload` or rewrite `text` as one valid JSON object/array."
                    )
                write_text(target, text)
            else:
                write_text(target, text)
    except PermissionError as exc:
        return _filesystem_write_failure_result(context, target, normalized, exc)
    except OSError as exc:
        return _filesystem_write_failure_result(context, target, normalized, exc)
    return ToolResult(
        tool_name="write_file",
        ok=True,
        summary=f"Wrote run file `{relpath_str(target, context.run_root)}`.",
        data={"path": str(target), "relative_path": relpath_str(target, context.run_root), "size_bytes": target.stat().st_size},
    )


def _normalize_json_payload(context: ToolContext, normalized_relative_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    payload = _normalize_final_scoring_json_payload(context, normalized_relative_path, payload)
    payload = _normalize_report_json_payload(context, normalized_relative_path, payload)
    return payload


def _try_normalize_text_json_payload(context: ToolContext, normalized_relative_path: str, text: str) -> dict[str, Any] | None:
    if normalized_relative_path not in {
        "artifacts/final_scoring/final_scoring_packet.json",
        "ae_report.json",
        "artifacts/report_generation/ae_report.json",
    }:
        return None
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return _normalize_json_payload(context, normalized_relative_path, payload)


def _normalize_final_scoring_json_payload(context: ToolContext, normalized_relative_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if normalized_relative_path != "artifacts/final_scoring/final_scoring_packet.json":
        return payload
    if _current_phase_from_state(context.run_root) != "final_scoring":
        return payload
    ledger = _read_intervention_ledger(context.run_root)
    records = ledger.get("interventions", []) if isinstance(ledger, dict) else []
    if not isinstance(records, list):
        records = []
    ledger_highest = _highest_intervention_from_records(records)
    command_floor = _highest_intervention_from_command_ledger(context.run_root)
    if _intervention_value(command_floor) > _intervention_value(ledger_highest):
        ledger_highest = command_floor
    normalized = dict(payload)
    current = _intervention_level(normalized.get("highest_intervention")) or "I0"
    if _intervention_value(ledger_highest) <= _intervention_value(current):
        if not isinstance(normalized.get("highest_intervention"), dict):
            normalized["highest_intervention"] = _highest_intervention_object(records, fallback_level=current, fallback_payload=normalized.get("highest_intervention"))
    else:
        normalized["highest_intervention"] = _highest_intervention_object(records, fallback_level=ledger_highest)
        sensitive = normalized.get("human_review_sensitive_points")
        if not isinstance(sensitive, list):
            sensitive = []
        sensitive.append(
            {
                "type": "intervention_consistency_normalized",
                "description": f"final_scoring_packet.highest_intervention was normalized from {current} to {ledger_highest} based on artifacts/interventions/intervention_ledger.json.",
                "refs": ["artifacts/interventions/intervention_ledger.json"],
            }
        )
        normalized["human_review_sensitive_points"] = sensitive
        evidence_refs = normalized.get("evidence_refs", [])
        if not isinstance(evidence_refs, list):
            evidence_refs = [evidence_refs]
        normalized["evidence_refs"] = dedupe_strings(evidence_refs + ["artifacts/interventions/intervention_ledger.json"])
    return normalize_final_scoring_packet(normalized)


def _normalize_report_json_payload(context: ToolContext, normalized_relative_path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if normalized_relative_path not in {"ae_report.json", "artifacts/report_generation/ae_report.json"}:
        return payload
    if _current_phase_from_state(context.run_root) != "report_generation":
        return payload
    final_packet_path = context.run_root / "artifacts" / "final_scoring" / "final_scoring_packet.json"
    try:
        final_packet = json.loads(final_packet_path.read_text(encoding="utf-8"))
    except Exception:
        return payload
    if not isinstance(final_packet, dict) or not final_packet:
        return payload
    normalized = dict(payload)
    normalized["schema"] = "ae_report_json_v1"
    normalized["source_final_scoring_packet"] = "artifacts/final_scoring/final_scoring_packet.json"
    for key in (
        "available_gate",
        "dimension_scores",
        "evidence_tier",
        "highest_intervention",
        "functional_decision",
        "reusable_decision",
        "badge_recommendation",
        "confidence",
        "limitations",
        "author_clarification_requests",
        "human_review_sensitive_points",
        "evidence_refs",
    ):
        if key in final_packet:
            normalized[key] = final_packet[key]
    return normalized


INTERVENTION_ORDER = {"I0": 0, "I1": 1, "I2": 2, "I3": 3, "I4": 4}


def _read_intervention_ledger(run_root: Path) -> dict[str, Any]:
    path = run_root / "artifacts" / "interventions" / "intervention_ledger.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _intervention_level(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("level", value.get("kind", ""))
    text = str(value or "").strip().upper().replace("LEVEL_", "")
    if text and not text.startswith("I") and text in {"0", "1", "2", "3", "4"}:
        text = f"I{text}"
    return text if text in INTERVENTION_ORDER else ""


def _intervention_value(value: str) -> int:
    return INTERVENTION_ORDER.get(str(value).strip(), -1)


def _highest_intervention_from_records(records: list[Any]) -> str:
    highest = "I0"
    for item in records:
        if not isinstance(item, dict):
            continue
        level = _intervention_level(item)
        if _intervention_value(level) > _intervention_value(highest):
            highest = level
    return highest


def _highest_intervention_from_command_ledger(run_root: Path) -> str:
    path = run_root / "artifacts" / "command_ledger" / "commands.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "I0"
    commands = payload.get("commands", []) if isinstance(payload, dict) else []
    if not isinstance(commands, list):
        return "I0"
    highest = "I0"
    for command in commands:
        if not isinstance(command, dict):
            continue
        mutation = command.get("repo_mutation", {})
        changed = mutation.get("changed_files", []) if isinstance(mutation, dict) else []
        if not isinstance(changed, list) or not changed:
            continue
        level = "I2" if any(_repo_mutation_path_requires_intervention(item) for item in changed if isinstance(item, dict)) else "I1"
        if _intervention_value(level) > _intervention_value(highest):
            highest = level
    return highest


def _repo_mutation_path_requires_intervention(item: dict[str, Any]) -> bool:
    path = str(item.get("path", "")).strip()
    status = str(item.get("status", "")).strip()
    if not path:
        return False
    if status in {"deleted", "modified"}:
        return True
    if path.endswith((".py", ".sh", ".R", ".jl", ".ipynb", ".toml", ".yaml", ".yml", ".json", ".lock", "requirements.txt", "environment.yml", "Dockerfile")):
        return True
    return False


def _highest_intervention_object(records: list[Any], *, fallback_level: str, fallback_payload: Any = None) -> dict[str, Any]:
    level = _intervention_level(fallback_level) or "I0"
    selected = [item for item in records if isinstance(item, dict) and _intervention_level(item) == level]
    affected_stages: list[Any] = []
    refs: list[Any] = ["artifacts/interventions/intervention_ledger.json"] if records else []
    summaries: list[str] = []
    depends_on_i4 = level == "I4"
    for item in selected:
        summary = str(item.get("summary", "")).strip()
        if summary:
            summaries.append(summary)
        refs.extend(item.get("evidence_refs", []) if isinstance(item.get("evidence_refs"), list) else [])
        details = item.get("details", {})
        if isinstance(details, dict):
            command_phase = str(details.get("phase_name", "")).strip()
            if command_phase:
                affected_stages.append(command_phase)
            command_id = str(details.get("command_id", "")).strip()
            if command_id:
                refs.append(command_id)
    if isinstance(fallback_payload, dict):
        affected_stages.extend(fallback_payload.get("affected_stages", []) if isinstance(fallback_payload.get("affected_stages"), list) else [])
        refs.extend(fallback_payload.get("refs", []) if isinstance(fallback_payload.get("refs"), list) else [])
        depends_on_i4 = bool(fallback_payload.get("depends_on_i4", depends_on_i4))
        fallback_summary = str(fallback_payload.get("summary", "")).strip()
    else:
        fallback_summary = str(fallback_payload or "").strip()
    if not summaries and fallback_summary:
        summaries.append(fallback_summary)
    if not summaries:
        summaries.append("No interventions recorded." if level == "I0" else f"Highest recorded intervention is {level} according to the intervention ledger.")
    return {
        "level": level,
        "summary": " | ".join(dedupe_strings(summaries))[:2000],
        "affected_stages": dedupe_strings(affected_stages),
        "depends_on_i4": depends_on_i4,
        "refs": dedupe_strings(refs),
    }


def _enforce_write_scope(context: ToolContext, relative_path: str) -> None:
    current_phase = _current_phase_from_state(context.run_root)
    normalized = Path(relative_path).as_posix().lstrip("./")
    report_outputs = {
        "ae_report.md",
        "ae_report.json",
        "artifacts/report_generation/ae_report.md",
        "artifacts/report_generation/ae_report.json",
    }
    if normalized in report_outputs and current_phase != "report_generation":
        raise ToolError(
            "Final report files may be written only during report_generation. "
            "During final_scoring, write artifacts/final_scoring/final_scoring_packet.json and call phase_finish."
        )


def _current_phase_from_state(run_root: Path) -> str:
    state_path = run_root / "agent_state.json"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(data.get("current_phase", "")).strip()


def _get_paper_info(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    source = _optional_string(args.get("paper_source"))
    if source is None:
        source = _configured_paper_source(context)
    if not source:
        raise ToolError("No paper_source provided and no configured paper source found in ae_config.yaml.")
    tool_args = dict(args)
    tool_args["paper_source"] = source
    return extract_paper_source(context, tool_args)


def _run_command(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    command_args = dict(args)
    if "phase_name" not in command_args or not str(command_args.get("phase_name", "")).strip():
        command_args["phase_name"] = "unknown"
    result = run_host_command(context, command_args)
    data = dict(result.data or {})
    data.pop("repo_mutation", None)
    summary = result.summary
    return ToolResult(
        tool_name="run_command",
        ok=result.ok,
        summary=summary,
        data=data,
        error=result.error,
    )


def _phase_finish(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    phase_name = str(args.get("phase_name", "")).strip()
    finish_reason = str(args.get("finish_reason", "")).strip()
    if not phase_name or not finish_reason:
        raise ToolError("`phase_name` and `finish_reason` are required.")
    payload = {
        "phase_name": phase_name,
        "finish_reason": finish_reason,
        "handoff_summary": str(args.get("handoff_summary", "")).strip(),
        "residual_risks": args.get("residual_risks", []),
        "evidence_refs": args.get("evidence_refs", []),
    }
    path = context.run_root / "artifacts" / phase_name / "finish_signal.json"
    try:
        write_json(path, payload)
    except PermissionError as exc:
        return _filesystem_write_failure_result(context, path, relpath_str(path, context.run_root), exc, tool_name="phase_finish")
    except OSError as exc:
        return _filesystem_write_failure_result(context, path, relpath_str(path, context.run_root), exc, tool_name="phase_finish")
    return ToolResult(
        tool_name="phase_finish",
        ok=True,
        summary=f"Stage `{phase_name}` finish signal recorded.",
        data={"phase_finish_signal": payload, "path": str(path), "relative_path": relpath_str(path, context.run_root)},
    )


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    return text


def _auto_inline_file_chars(text: str) -> int:
    if len(text) <= MAX_INLINE_FILE_CHARS:
        return len(text)
    return MAX_INLINE_FILE_CHARS


def _filesystem_write_failure_result(
    context: ToolContext,
    target: Path,
    normalized: str,
    exc: OSError,
    *,
    tool_name: str = "write_file",
) -> ToolResult:
    parent = target.parent
    owner_uid = ""
    owner_gid = ""
    mode = ""
    try:
        stat = parent.stat()
        owner_uid = str(stat.st_uid)
        owner_gid = str(stat.st_gid)
        mode = oct(stat.st_mode & 0o777)
    except OSError:
        pass
    current_uid = os.getuid() if hasattr(os, "getuid") else None
    return ToolResult(
        tool_name=tool_name,
        ok=False,
        summary=(
            f"{tool_name} could not write `{normalized}` due to filesystem permissions. "
            "This is a runtime/tool observation, not a reason to silently stop."
        ),
        data={
            "path": str(target),
            "relative_path": normalized,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "parent_path": str(parent),
            "parent_owner_uid": owner_uid,
            "parent_owner_gid": owner_gid,
            "parent_mode": mode,
            "current_uid": current_uid,
            "requires_root_fix": bool(owner_uid and current_uid is not None and owner_uid != str(current_uid)),
            "agent_action_required": "retry_in_writable_run_path_or_record_permission_blocker",
        },
        error=f"{type(exc).__name__}: {exc}",
    )


def _build_artifact_universe(context: ToolContext) -> dict[str, Any]:
    repo_root = context.repo_root.resolve()
    parent = repo_root.parent.resolve()
    components: list[dict[str, Any]] = []
    boundary_warnings: list[str] = []

    def add_component(path: Path, *, relation: str) -> None:
        resolved = path.resolve()
        if any(item.get("path") == str(resolved) for item in components):
            return
        signals = _artifact_component_signals(resolved)
        if not signals and relation != "configured_root":
            return
        component_type = _artifact_component_type(resolved, signals)
        components.append(
            {
                "path": str(resolved),
                "relation": relation,
                "component_type": component_type,
                "signals": signals,
                "is_configured_repo_root": resolved == repo_root,
            }
        )

    add_component(repo_root, relation="configured_root")
    for child in _bounded_child_component_candidates(repo_root):
        add_component(child, relation="child")

    sibling_scan_root = parent if _should_scan_sibling_components(repo_root, parent) else None
    if sibling_scan_root is not None:
        for child in sorted(sibling_scan_root.iterdir()):
            if child.name in SKIP_TREE_NAMES:
                continue
            if child.resolve() == repo_root:
                continue
            if child.is_dir() or _is_archive_path(child):
                add_component(child, relation="sibling")

    configured = next((item for item in components if item.get("is_configured_repo_root")), None)
    sibling_components = [item for item in components if item.get("relation") == "sibling"]
    strong_siblings = [
        item
        for item in sibling_components
        if item.get("component_type")
        in {
            "replication_package",
            "dataset_package",
            "result_package",
            "notebook_package",
            "archive",
            "tool_repo",
        }
    ]
    root_scope = "complete_root"
    if strong_siblings:
        root_scope = "subdir_possible"
        boundary_warnings.append(
            "Configured artifact root has sibling artifact-like components; route conclusions may be repo-local unless those components are covered."
        )
    if len(strong_siblings) > 1:
        root_scope = "ambiguous"
        boundary_warnings.append(
            "Multiple sibling artifact-like components were detected; the review may need a combined artifact root or explicit component coverage."
        )
    if configured and configured.get("component_type") == "tool_repo" and any(
        item.get("component_type") in {"replication_package", "dataset_package", "result_package", "notebook_package"}
        for item in strong_siblings
    ):
        root_scope = "subdir_possible" if root_scope == "complete_root" else root_scope
        boundary_warnings.append(
            "Configured root looks like a tool repository while sibling data/result/notebook package components exist."
        )

    return {
        "schema": "artifact_universe_v1",
        "configured_repo_root": str(repo_root),
        "scan_root": str(parent),
        "root_scope": root_scope,
        "components": components,
        "boundary_warnings": boundary_warnings,
        "coverage_instruction": (
            "D1/D3/D5 route portfolios should record route_scope. Final scoring should not claim all routes failed "
            "when root_scope is subdir_possible or ambiguous unless artifact-level component coverage is explicit."
        ),
    }


def _should_scan_sibling_components(repo_root: Path, parent: Path) -> bool:
    if not parent.exists() or not parent.is_dir():
        return False
    parent_name = parent.name.lower()
    parent_text = str(parent)
    if parent_name == "work" or "gold_suite_review" in parent_text:
        return True
    try:
        sibling_names = {child.name.lower() for child in list(parent.iterdir())[:80]}
    except OSError:
        return False
    if "paper.txt" in sibling_names and any("zenodo" in name for name in sibling_names):
        return True
    # Avoid scanning broad temp/cache parents such as /tmp, which may contain
    # unrelated artifact roots from other validation runs.
    return False


def _bounded_child_component_candidates(repo_root: Path) -> list[Path]:
    if not repo_root.exists() or not repo_root.is_dir():
        return []
    try:
        children = list(repo_root.iterdir())[:120]
    except OSError:
        return []
    candidates: list[Path] = []
    for child in children:
        if child.name in SKIP_TREE_NAMES or child.name.startswith("."):
            continue
        if child.is_dir() or _is_archive_path(child):
            candidates.append(child)
    return candidates


def _artifact_component_signals(path: Path) -> list[str]:
    signals: list[str] = []
    name = path.name.lower()
    if _is_archive_path(path):
        return [f"archive:{path.suffix.lower() or name}"]
    if not path.is_dir():
        return signals

    direct_names: set[str] = set()
    try:
        children = list(path.iterdir())
    except OSError:
        return signals
    for child in children[:500]:
        direct_names.add(child.name.lower())
    if any(name == "readme" or name.startswith("readme.") for name in direct_names):
        signals.append("README")
    if ".git" in direct_names:
        signals.append("git_repo")
    if any(name in direct_names for name in {"pyproject.toml", "setup.py", "package.json", "build.gradle", "pom.xml", "cargo.toml"}):
        signals.append("build_or_package_manifest")
    if any(name.endswith(".ipynb") for name in direct_names):
        signals.append("notebook")
    if any(name in direct_names for name in {"csv", "data", "dataset", "datasets", "results", "result"}):
        signals.append("data_or_result_dir")
    if name.startswith("zenodo") or "zenodo" in name:
        signals.append("zenodo_named")
    if any(name.endswith((".csv", ".jsonl", ".parquet")) for name in direct_names):
        signals.append("tabular_or_result_files")

    # One-level bounded scan for common package signals when the top level is a wrapper directory.
    for child in children[:80]:
        if not child.is_dir() or child.name in SKIP_TREE_NAMES:
            continue
        try:
            nested_names = {nested.name.lower() for nested in list(child.iterdir())[:120]}
        except OSError:
            continue
        if any(name.endswith(".ipynb") for name in nested_names):
            signals.append(f"nested_notebook:{child.name}")
        if any(name in nested_names for name in {"csv", "data", "dataset", "datasets", "results", "result"}):
            signals.append(f"nested_data_or_result_dir:{child.name}")
        if any(name == "readme" or name.startswith("readme.") for name in nested_names):
            signals.append(f"nested_README:{child.name}")
    return sorted(set(signals))


def _artifact_component_type(path: Path, signals: list[str]) -> str:
    signal_set = set(signals)
    if any(signal.startswith("archive:") for signal in signals):
        return "archive"
    if "notebook" in signal_set or any(signal.startswith("nested_notebook:") for signal in signals):
        if "data_or_result_dir" in signal_set or any(signal.startswith("nested_data_or_result_dir:") for signal in signals):
            return "replication_package"
        return "notebook_package"
    if "zenodo_named" in signal_set and (
        "data_or_result_dir" in signal_set
        or "tabular_or_result_files" in signal_set
        or any(signal.startswith("nested_data_or_result_dir:") for signal in signals)
    ):
        return "replication_package"
    if "data_or_result_dir" in signal_set or "tabular_or_result_files" in signal_set:
        name = path.name.lower()
        if "result" in name:
            return "result_package"
        return "dataset_package"
    if "git_repo" in signal_set or "build_or_package_manifest" in signal_set:
        return "tool_repo"
    if "README" in signal_set or any(signal.startswith("nested_README:") for signal in signals):
        return "documented_component"
    return "unknown"


def _is_archive_path(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith((".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".7z"))


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _mirror_config_input_files(context: ToolContext, config: dict[str, Any]) -> dict[str, Any]:
    inputs = config.setdefault("inputs", {})
    if not isinstance(inputs, dict):
        return {}
    mirrored: dict[str, Any] = {}
    for input_key, default_name in [("paper_pdf", "paper_source"), ("manual_paper_targets", "manual_paper_targets")]:
        source_text = str(inputs.get(input_key) or "").strip()
        if not source_text:
            continue
        source_path = Path(source_text).expanduser()
        if not source_path.is_absolute():
            resolved = resolve_repo_path(context.repo_root, source_text)
            source_path = resolved if resolved is not None else source_path
        source_path = source_path.resolve()
        if not source_path.exists() or not source_path.is_file():
            continue
        target_dir = context.run_root / "artifacts" / "config_inputs"
        ensure_dir(target_dir)
        suffix = source_path.suffix
        target = target_dir / (f"{default_name}{suffix}" if suffix else default_name)
        if target.exists() and target.resolve() != source_path:
            target = target_dir / safe_artifact_filename(source_path.name, suffix=suffix or ".input")
        if target.resolve() != source_path:
            shutil.copy2(source_path, target)
        record = {
            "source_path": str(source_path),
            "run_copy": str(target),
            "run_relative_path": relpath_str(target, context.run_root),
            "size_bytes": target.stat().st_size,
            "sha256": sha256_file(target),
        }
        mirrored[input_key] = record
        inputs[f"{input_key}_run_copy"] = str(target)
        inputs[f"{input_key}_run_relative_path"] = record["run_relative_path"]
    if mirrored:
        config["mirrored_inputs"] = mirrored
    return mirrored


def _configured_paper_source(context: ToolContext) -> str:
    config_path = context.run_root / "ae_config.yaml"
    if config_path.exists():
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8", errors="replace")) or {}
        except Exception:
            config = {}
        inputs = config.get("inputs") if isinstance(config, dict) else {}
        if isinstance(inputs, dict):
            configured = str(inputs.get("paper_pdf_run_copy") or inputs.get("paper_pdf") or "").strip()
            if configured:
                return configured

    state_path = context.run_root / "agent_state.json"
    if not state_path.exists():
        return ""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return ""
    shared_context = state.get("shared_context") if isinstance(state, dict) else {}
    if not isinstance(shared_context, dict):
        return ""
    return str(shared_context.get("paper_source") or "").strip()


def _resolve_base_path(root: Path, requested: str) -> Path | None:
    candidate = Path(requested)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (root / requested).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return None
    return resolved


def _skip_tree_path(relative: Path) -> bool:
    return any(part in SKIP_TREE_NAMES for part in relative.parts)


def _is_readme_path(path: Path) -> bool:
    name = path.name.lower()
    return name == "readme" or name.startswith("readme.")


def _file_preview(text: str, *, max_chars: int) -> dict[str, Any]:
    raw = str(text or "")
    if len(raw) <= max_chars:
        return {"text": raw, "truncated": False, "original_chars": len(raw)}
    head_chars = min(INLINE_FILE_PREVIEW_HEAD_CHARS, max_chars // 2)
    tail_chars = min(INLINE_FILE_PREVIEW_TAIL_CHARS, max_chars - head_chars)
    omitted = max(0, len(raw) - head_chars - tail_chars)
    return {
        "text": f"{raw[:head_chars]}\n\n...[truncated {omitted} chars; full content remains on disk]...\n\n{raw[-tail_chars:]}",
        "truncated": True,
        "original_chars": len(raw),
    }
