from __future__ import annotations

import difflib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from ..shared.fs import ensure_dir, now_iso, write_json, write_text
from .common import bounded_int, coerce_bool, dedupe_strings, normalize_string_list, resolve_repo_path, resolve_run_path, sha256_file
from .intervention_tools import append_intervention_record, normalize_command_intervention
from .specs import ToolContext, ToolError, ToolResult

INLINE_COMMAND_OUTPUT_THRESHOLD = 10_000
INLINE_COMMAND_OUTPUT_HEAD_CHARS = 300
INLINE_COMMAND_OUTPUT_TAIL_CHARS = 300
INLINE_REPO_MUTATION_FILE_LIMIT = 20
DEFAULT_COMMAND_TIMEOUT_SEC = 10_800
MAX_COMMAND_TIMEOUT_SEC = 10_800

FATAL_OUTPUT_PATTERNS = (
    "traceback (most recent call last)",
    "modulenotfounderror",
    "command not found",
    "error: failed to build",
    "failed to solve:",
    "build failure",
    "fatal error",
)

BENIGN_OUTPUT_PATTERNS = (
    "could not query file systems: could not open mount file (errno 2: no such file or directory)",
    "unable to list file systems to check whether they can be watched",
    "fromascasing:",
)


def run_host_command(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    original_command = str(args.get("command", "")).strip()
    command = original_command
    log_label = str(args.get("log_label", "")).strip() or "host_command"
    if not command:
        raise ToolError("`command` must be a non-empty string.")
    cwd_arg = str(args.get("cwd", ".")).strip() or "."
    cwd = _resolve_command_cwd(context, cwd_arg)
    if cwd is None or not cwd.exists():
        raise ToolError(f"Invalid cwd for host command: {cwd_arg}")
    phase_name = str(args.get("phase_name", "")).strip() or "unknown"
    raw_timeout = args.get("timeout_sec", DEFAULT_COMMAND_TIMEOUT_SEC)
    timeout_sec = _normalize_timeout(raw_timeout=raw_timeout, phase_name=phase_name, command=command)
    purpose = str(args.get("purpose", "")).strip()
    strict_shell_request = args.get("strict_shell")
    strict_shell_decision = _resolve_strict_shell(command=command, requested=strict_shell_request, override_reason=args.get("strict_shell_override_reason"))
    strict_shell = strict_shell_decision["effective"]
    pattern_config = _normalize_pattern_config(args)
    intervention_request = normalize_command_intervention(args.get("intervention"))
    logs_dir = context.run_root / "logs" / "host"
    ensure_dir(logs_dir)
    safe_label = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in log_label)[:120] or "host_command"
    command_id = _next_command_id(context.run_root, safe_label)
    stdout_path = logs_dir / f"{command_id}.stdout.log"
    stderr_path = logs_dir / f"{command_id}.stderr.log"
    repo_snapshot_before = _snapshot_repo_files(context.repo_root)

    started_at = now_iso()
    started_monotonic = time.monotonic()
    run_args: list[str] | str = ["bash", "-o", "pipefail", "-e", "-u", "-c", command] if strict_shell else command
    try:
        completed = subprocess.run(
            run_args,
            cwd=str(cwd),
            shell=not strict_shell,
            capture_output=True,
            timeout=timeout_sec,
        )
        return _command_result(
            context=context,
            command_id=command_id,
            command=command,
            cwd=cwd,
            phase_name=phase_name,
            purpose=purpose,
            timeout_sec=timeout_sec,
            strict_shell=strict_shell,
            started_at=started_at,
            started_monotonic=started_monotonic,
            returncode=completed.returncode,
            stdout=_decode_output(completed.stdout),
            stderr=_decode_output(completed.stderr),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            repo_snapshot_before=repo_snapshot_before,
            pattern_config=pattern_config,
            strict_shell_decision=strict_shell_decision,
            intervention_request=intervention_request,
            timed_out=False,
            output_decode_lossy=_decode_lossy(completed.stdout) or _decode_lossy(completed.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        return _command_result(
            context=context,
            command_id=command_id,
            command=command,
            cwd=cwd,
            phase_name=phase_name,
            purpose=purpose,
            timeout_sec=timeout_sec,
            strict_shell=strict_shell,
            started_at=started_at,
            started_monotonic=started_monotonic,
            returncode=None,
            stdout=_timeout_text(exc.stdout),
            stderr=_timeout_text(exc.stderr),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            repo_snapshot_before=repo_snapshot_before,
            pattern_config=pattern_config,
            strict_shell_decision=strict_shell_decision,
            intervention_request=intervention_request,
            timed_out=True,
            output_decode_lossy=_decode_lossy(exc.stdout) or _decode_lossy(exc.stderr),
    )


def _normalize_timeout(*, raw_timeout: Any, phase_name: str, command: str) -> int:
    timeout_sec = DEFAULT_COMMAND_TIMEOUT_SEC if raw_timeout is None else bounded_int(raw_timeout, lower=1, upper=MAX_COMMAND_TIMEOUT_SEC)
    if phase_name == "D3_executability" and 600 <= timeout_sec < 3600 and _looks_like_long_running_artifact_workflow(command):
        return 3600
    return timeout_sec


def _looks_like_long_running_artifact_workflow(command: str) -> bool:
    lowered = command.lower()
    indicators = (
        "generate_tests.py",
        "run_experiment",
        "running_simple",
        "benchmark",
        "train",
        "evaluation",
        "experiment",
        "search_budget",
        "time-budget",
        "time_budget",
        "docker run",
    )
    return any(indicator in lowered for indicator in indicators)


def _resolve_command_cwd(context: ToolContext, cwd_arg: str) -> Path | None:
    raw = Path(cwd_arg)
    if raw.is_absolute():
        repo_path = resolve_repo_path(context.repo_root, cwd_arg)
        if repo_path is not None:
            return repo_path
        return resolve_run_path(context.run_root, cwd_arg)
    repo_path = resolve_repo_path(context.repo_root, cwd_arg)
    if repo_path is not None and repo_path.exists():
        return repo_path
    return resolve_run_path(context.run_root, cwd_arg)


def _decode_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _decode_lossy(value: Any) -> bool:
    if not isinstance(value, bytes):
        return False
    return value.decode("utf-8", errors="replace").encode("utf-8", errors="replace") != value


def materialize_phase_command_ledger(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    phase_name = str(args.get("phase_name", "D3_executability")).strip() or "D3_executability"
    ledger_path = context.run_root / "artifacts" / "command_ledger" / "commands.json"
    commands: list[dict[str, Any]] = []
    if ledger_path.exists():
        try:
            payload = json.loads(ledger_path.read_text(encoding="utf-8"))
            raw_commands = payload.get("commands", []) if isinstance(payload, dict) else []
            if isinstance(raw_commands, list):
                commands = [item for item in raw_commands if isinstance(item, dict)]
        except Exception as exc:
            raise ToolError("Global command ledger exists but could not be parsed.", data={"ledger_path": str(ledger_path)}) from exc

    phase_commands = [_phase_command_entry(command) for command in commands if str(command.get("phase", "")).strip() == phase_name]
    outcome_counts: dict[str, int] = {}
    for command in phase_commands:
        outcome = str(command.get("outcome", "unknown")).strip() or "unknown"
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
    materialized = {
        "schema": "phase_command_ledger_v1",
        "phase_name": phase_name,
        "source_ledger_path": str(ledger_path),
        "command_count": len(phase_commands),
        "outcome_counts": outcome_counts,
        "commands": phase_commands,
        "evidence_refs": [str(ledger_path)] + [
            ref
            for command in phase_commands
            for ref in (str(command.get("stdout_ref", "")).strip(), str(command.get("stderr_ref", "")).strip())
            if ref
        ],
        "updated_at": now_iso(),
    }
    output_path = context.run_root / "artifacts" / phase_name / "command_ledger.json"
    write_json(output_path, materialized)
    return ToolResult(
        tool_name="materialize_phase_command_ledger",
        ok=True,
        summary=f"Materialized {len(phase_commands)} command ledger entrie(s) for `{phase_name}`.",
        data={
            "phase_name": phase_name,
            "artifact_key": "command_ledger",
            "payload": materialized,
            "artifact_path": str(output_path),
        },
    )


def _command_result(
    *,
    context: ToolContext,
    command_id: str,
    command: str,
    cwd: Path,
    phase_name: str,
    purpose: str,
    timeout_sec: int,
    strict_shell: bool,
    started_at: str,
    started_monotonic: float,
    returncode: int | None,
    stdout: str,
    stderr: str,
    stdout_path: Path,
    stderr_path: Path,
    repo_snapshot_before: dict[str, dict[str, Any]],
    pattern_config: dict[str, list[str]],
    strict_shell_decision: dict[str, Any],
    intervention_request: dict[str, Any],
    timed_out: bool,
    output_decode_lossy: bool = False,
) -> ToolResult:
    log_write_error: dict[str, Any] = {}
    try:
        stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
        stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
    except OSError as exc:
        log_write_error = {"error_type": type(exc).__name__, "error": str(exc), "stdout_path": str(stdout_path), "stderr_path": str(stderr_path)}
    try:
        repo_mutation = _detect_repo_mutation(context=context, before=repo_snapshot_before, command_id=command_id)
    except OSError as exc:
        repo_mutation = {
            "schema": "repo_mutation_v1",
            "changed_files": [],
            "change_count": 0,
            "mutation_detection_error": f"{type(exc).__name__}: {exc}",
            "evidence_refs": [],
        }
    detected_patterns = _detect_pattern_config(stdout=stdout, stderr=stderr, pattern_config=pattern_config) if not timed_out else {"fatal": [], "warning": [], "benign": []}
    status_summary = _classify_command_status(
        command=command,
        returncode=returncode,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        detected_patterns=detected_patterns,
    )
    stdout_preview = _inline_command_output_preview(stdout)
    stderr_preview = _inline_command_output_preview(stderr)
    ended_at = now_iso()
    duration_sec = round(time.monotonic() - started_monotonic, 3)
    outcome = str(status_summary["final_status"])
    missing_mutation_intervention = bool(repo_mutation.get("changed_files")) and not intervention_request
    repo_mutation_review_required = bool(missing_mutation_intervention)
    ledger_write_error: dict[str, Any] = {}
    try:
        ledger_entry = _append_command_ledger(
            context=context,
            command_id=command_id,
            phase_name=phase_name,
            log_label=command_id.split("_", 2)[-1],
            purpose=purpose,
            command=command,
            cwd=cwd,
            timeout_sec=timeout_sec,
            started_at=started_at,
            ended_at=ended_at,
            duration_sec=duration_sec,
            returncode=returncode,
            outcome=outcome,
            timed_out=timed_out,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            stdout_chars=len(stdout),
            stderr_chars=len(stderr),
            strict_shell=strict_shell,
            detected_failure_patterns=status_summary["detected_failure_patterns"],
            command_status_summary=status_summary,
            repo_mutation=repo_mutation,
            strict_shell_decision=strict_shell_decision,
            output_decode_lossy=output_decode_lossy,
        )
    except OSError as exc:
        ledger_write_error = {"error_type": type(exc).__name__, "error": str(exc)}
        ledger_entry = {"command_ledger_path": "", "command_ledger_jsonl_path": ""}
    intervention_payload = None
    intervention_path = ""
    if intervention_request:
        try:
            intervention_payload, intervention_path = append_intervention_record(
                context,
                {
                    **intervention_request,
                    "details": {
                        **intervention_request.get("details", {}),
                        "tool": "run_host_command",
                        "command_id": command_id,
                        "command": command,
                        "cwd": str(cwd),
                        "returncode": returncode,
                        "outcome": outcome,
                        "command_status_summary": status_summary,
                        "strict_shell": strict_shell,
                        "strict_shell_decision": strict_shell_decision,
                        "stdout_path": str(stdout_path),
                        "stderr_path": str(stderr_path),
                        "detected_failure_patterns": status_summary["detected_failure_patterns"],
                        "repo_mutation": repo_mutation,
                    },
                    "evidence_refs": dedupe_strings(
                        intervention_request.get("evidence_refs", [])
                        + [command_id, str(stdout_path), str(stderr_path)]
                        + _repo_mutation_evidence_refs(repo_mutation)
                    ),
                },
            )
        except OSError as exc:
            intervention_payload = {"record_error": f"{type(exc).__name__}: {exc}"}
            intervention_path = ""
    ok = True
    error = ""
    if timed_out:
        error = f"Timeout after {timeout_sec} seconds."
    elif returncode != 0:
        error = f"Non-zero exit code: {returncode}"
    summary = _command_result_summary(status_summary=status_summary, returncode=returncode, timeout_sec=timeout_sec)
    if repo_mutation_review_required:
        summary += " Repository file changes were detected and recorded as mutation evidence; classify them from the command context."
    phase_ledger = _refresh_phase_command_ledger(context, phase_name)
    return ToolResult(
        tool_name="run_host_command",
        ok=ok,
        summary=summary,
        data={
            "command_id": command_id,
            "command": command,
            "cwd": str(cwd),
            "phase_name": phase_name,
            "purpose": purpose,
            "timeout_sec": timeout_sec,
            "returncode": returncode,
            "exit_code": returncode,
            "outcome": outcome,
            "final_status": status_summary["final_status"],
            "exit_code_status": status_summary["exit_code_status"],
            "pattern_status": status_summary["pattern_status"],
            "agent_action_required": status_summary["agent_action_required"],
            "command_status_summary": status_summary,
            "strict_shell": strict_shell,
            "strict_shell_decision": strict_shell_decision,
            "strict_shell_auto_enabled": strict_shell_decision["auto_enabled"],
            "strict_shell_reason": strict_shell_decision["reason"],
            "strict_shell_override_reason": strict_shell_decision["override_reason"],
            "detected_failure_patterns": status_summary["detected_failure_patterns"],
            "fatal_patterns": status_summary["fatal_patterns"],
            "warning_patterns": status_summary["warning_patterns"],
            "benign_patterns": status_summary["benign_patterns"],
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "stdout_log_path": str(stdout_path),
            "stderr_log_path": str(stderr_path),
            "command_ledger_path": ledger_entry["command_ledger_path"],
            "command_ledger_jsonl_path": ledger_entry["command_ledger_jsonl_path"],
            "phase_command_ledger_path": phase_ledger.get("path", ""),
            "phase_command_ledger_updated": phase_ledger.get("updated", False),
            "log_write_error": log_write_error,
            "ledger_write_error": ledger_write_error,
            "phase_command_ledger_error": phase_ledger.get("error", ""),
            "repo_mutation": repo_mutation,
            "repo_mutation_review_required": repo_mutation_review_required,
            "repo_mutation_intervention_supplied": bool(intervention_request),
            "intervention": intervention_payload or {},
            "interventions_path": intervention_path,
            "stdout": stdout_preview["text"],
            "stderr": stderr_preview["text"],
            "stdout_tail": stdout_preview["tail"],
            "stderr_tail": stderr_preview["tail"],
            "stdout_chars": stdout_preview["original_chars"],
            "stderr_chars": stderr_preview["original_chars"],
            "stdout_truncated": stdout_preview["truncated"],
            "stderr_truncated": stderr_preview["truncated"],
            "output_decode_lossy": output_decode_lossy,
            "timed_out": timed_out,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_sec": duration_sec,
        },
        error=error,
    )


def _refresh_phase_command_ledger(context: ToolContext, phase_name: str) -> dict[str, Any]:
    try:
        result = materialize_phase_command_ledger(context, {"phase_name": phase_name})
    except Exception as exc:
        return {"updated": False, "error": str(exc)}
    return {
        "updated": bool(result.ok),
        "path": str(result.data.get("artifact_path", "")),
    }


def _timeout_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _inline_command_output_preview(text: str) -> dict[str, Any]:
    raw = str(text or "")
    original_chars = len(raw)
    if original_chars <= INLINE_COMMAND_OUTPUT_THRESHOLD:
        return {"text": raw, "tail": raw, "original_chars": original_chars, "truncated": False}
    head = raw[:INLINE_COMMAND_OUTPUT_HEAD_CHARS]
    tail = raw[-INLINE_COMMAND_OUTPUT_TAIL_CHARS:]
    return {
        "text": f"{head}\n\n...[truncated {original_chars - INLINE_COMMAND_OUTPUT_HEAD_CHARS - INLINE_COMMAND_OUTPUT_TAIL_CHARS} chars]...\n\n{tail}",
        "tail": tail,
        "original_chars": original_chars,
        "truncated": True,
    }


def _normalize_failure_patterns(value: Any) -> list[str]:
    defaults = ["duplicate session:", "command not found", "modulenotfounderror", "traceback (most recent call last)"]
    unsafe_broad_patterns = {"error", "error:", "errors", "failed", "failure", "fail", "warning", "warn"}
    if value is None:
        return defaults
    raw_patterns = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    patterns = [
        pattern
        for pattern in (str(item).strip().lower() for item in raw_patterns)
        if pattern and pattern not in unsafe_broad_patterns
    ]
    return patterns or defaults


def _normalize_pattern_config(args: dict[str, Any]) -> dict[str, list[str]]:
    fatal_input = args.get("fatal_patterns", args.get("failure_patterns"))
    return {
        "fatal": _normalize_failure_patterns(fatal_input),
        "warning": _normalize_optional_patterns(args.get("warning_patterns")),
        "benign": _normalize_optional_patterns(args.get("benign_patterns")),
    }


def _normalize_optional_patterns(value: Any) -> list[str]:
    unsafe_broad_patterns = {"error", "error:", "errors", "failed", "failure", "fail", "warning", "warn"}
    return [
        pattern
        for pattern in (item.lower() for item in normalize_string_list(value))
        if pattern and pattern not in unsafe_broad_patterns
    ]


def _detect_failure_patterns(*, stdout: str, stderr: str, patterns: list[str]) -> list[str]:
    combined = f"{stdout}\n{stderr}".lower()
    detected: list[str] = []
    for pattern in patterns:
        if pattern and pattern in combined and pattern not in detected:
            detected.append(pattern)
    return detected


def _detect_pattern_config(*, stdout: str, stderr: str, pattern_config: dict[str, list[str]]) -> dict[str, list[str]]:
    return {
        "fatal": _detect_failure_patterns(stdout=stdout, stderr=stderr, patterns=pattern_config.get("fatal", [])),
        "warning": _detect_failure_patterns(stdout=stdout, stderr=stderr, patterns=pattern_config.get("warning", [])),
        "benign": _detect_failure_patterns(stdout=stdout, stderr=stderr, patterns=pattern_config.get("benign", [])),
    }


def _classify_command_status(
    *,
    command: str,
    returncode: int | None,
    timed_out: bool,
    stdout: str,
    stderr: str,
    detected_patterns: dict[str, list[str]],
) -> dict[str, Any]:
    combined = f"{stdout}\n{stderr}".lower()
    masked_failure = _detect_masked_or_fallback(command=command, stdout=stdout, stderr=stderr)
    failure_suppression = _detect_failure_suppression(command)
    fatal_patterns = dedupe_strings(_detect_known_patterns(combined, FATAL_OUTPUT_PATTERNS) + detected_patterns.get("fatal", []))
    if masked_failure["detected"]:
        fatal_patterns = dedupe_strings(fatal_patterns + [f"masked fallback marker: {marker}" for marker in masked_failure["markers"]])
    if failure_suppression["detected"]:
        fatal_patterns = dedupe_strings(fatal_patterns + [f"failure-suppression operator: {marker}" for marker in failure_suppression["markers"]])
    warning_patterns = dedupe_strings(detected_patterns.get("warning", []))
    benign_patterns = dedupe_strings(_detect_known_patterns(combined, BENIGN_OUTPUT_PATTERNS) + detected_patterns.get("benign", []))
    detected_failure_patterns = dedupe_strings(fatal_patterns + warning_patterns + benign_patterns)
    if timed_out:
        exit_code_status = "timeout"
        final_status = "timeout"
        pattern_status = "not_evaluated"
        action = "record_timeout_or_retry_with_budget_rationale"
    elif returncode != 0:
        exit_code_status = "nonzero"
        final_status = "failure"
        pattern_status = "fatal" if fatal_patterns else "warning" if warning_patterns else "none"
        action = "diagnose_or_repair"
    elif fatal_patterns:
        exit_code_status = "zero"
        final_status = "partial_failure"
        pattern_status = "fatal"
        action = "inspect_outputs_then_repair_or_record_blocker"
    elif warning_patterns:
        exit_code_status = "zero"
        final_status = "success_with_warnings"
        pattern_status = "warning"
        action = "proceed_with_caution"
    else:
        exit_code_status = "zero"
        final_status = "success"
        pattern_status = "none"
        action = "proceed"
    return {
        "schema": "command_status_summary_v1",
        "exit_code_status": exit_code_status,
        "pattern_status": pattern_status,
        "final_status": final_status,
        "agent_action_required": action,
        "returncode": returncode,
        "detected_failure_patterns": detected_failure_patterns,
        "fatal_patterns": fatal_patterns,
        "warning_patterns": warning_patterns,
        "benign_patterns": benign_patterns,
        "masked_failure_detected": masked_failure["detected"],
        "masked_failure_markers": masked_failure["markers"],
        "failure_suppression_detected": failure_suppression["detected"],
        "failure_suppression_markers": failure_suppression["markers"],
        "interpretation_rule": "Nonzero exit code is failure. Timeout is timeout. Zero exit with fatal patterns or detected OR-fallback failure markers is partial_failure. Zero exit with warning patterns is success_with_warnings. Zero exit with benign-only patterns is success.",
    }


def _detect_known_patterns(text: str, patterns: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        if pattern in text and pattern not in found:
            found.append(pattern)
    return found


def _detect_masked_or_fallback(*, command: str, stdout: str, stderr: str) -> dict[str, Any]:
    if "||" not in command:
        return {"detected": False, "markers": []}
    combined = f"{stdout}\n{stderr}".lower()
    markers = [
        marker
        for marker in _extract_or_fallback_markers(command)
        if marker and marker.lower() in combined
    ]
    return {"detected": bool(markers), "markers": dedupe_strings(markers)}


def _detect_failure_suppression(command: str) -> dict[str, Any]:
    text = str(command or "")
    markers: list[str] = []
    suppressors: tuple[tuple[str, str], ...] = (
        (r"\|\|\s*true(?:\s|$|;|&&|\|\|)", "|| true"),
        (r"\|\|\s*:(?:\s|$|;|&&|\|\|)", "|| :"),
        (r"\|\|\s*exit\s+0(?:\s|$|;|&&|\|\|)", "|| exit 0"),
    )
    for pattern, label in suppressors:
        if re.search(pattern, text):
            markers.append(label)
    return {"detected": bool(markers), "markers": dedupe_strings(markers)}


def _extract_or_fallback_markers(command: str) -> list[str]:
    markers: list[str] = []
    for segment in str(command or "").split("||")[1:]:
        segment = re.split(r"\s*(?:&&|\|\||;|\n)\s*", segment, maxsplit=1)[0]
        quoted = re.search(r"\b(?:echo|printf)\s+(?:-[A-Za-z]+\s+)?(?P<quote>['\"])(?P<msg>[^'\"]{2,200})(?P=quote)", segment)
        if quoted:
            markers.append(_normalize_fallback_marker(quoted.group("msg")))
            continue
        bare = re.search(r"\b(?:echo|printf)\s+(?:-[^\s]+\s+)*(?P<msg>[A-Za-z0-9_.:-]*(?:FAILED|FAILURE|ERROR|DENIED|UNAVAILABLE)[A-Za-z0-9_.:-]*)\b", segment)
        if bare:
            markers.append(_normalize_fallback_marker(bare.group("msg")))
    return [marker for marker in dedupe_strings(markers) if marker]


def _normalize_fallback_marker(value: str) -> str:
    return str(value or "").replace("\\n", "\n").strip()


def _command_result_summary(*, status_summary: dict[str, Any], returncode: int | None, timeout_sec: int) -> str:
    final_status = str(status_summary.get("final_status", "unknown"))
    if final_status == "timeout":
        return f"Host command timed out after {timeout_sec} seconds."
    base = f"Host command exited with code {returncode}; final_status={final_status}."
    fatal = status_summary.get("fatal_patterns", [])
    warnings = status_summary.get("warning_patterns", [])
    if fatal:
        base += " Fatal output pattern(s): " + ", ".join(str(item) for item in fatal[:5]) + "."
    elif warnings:
        base += " Warning output pattern(s): " + ", ".join(str(item) for item in warnings[:5]) + "."
    base += f" Agent action: {status_summary.get('agent_action_required', 'proceed')}."
    return base


def _resolve_strict_shell(*, command: str, requested: Any, override_reason: Any) -> dict[str, Any]:
    requested_bool = coerce_bool(requested, default=False) if requested is not None else None
    override_text = str(override_reason or "").strip()
    auto_required, reason = _strict_shell_auto_required(command)
    if requested_bool is False and auto_required and override_text:
        effective = False
        source = "explicit_override"
    elif requested_bool is True:
        effective = True
        source = "explicit_true"
        reason = "requested_by_agent"
    elif auto_required:
        effective = True
        source = "auto"
    else:
        effective = False
        source = "default"
        reason = "simple_command"
    return {
        "effective": effective,
        "requested": requested_bool,
        "auto_enabled": auto_required,
        "source": source,
        "reason": reason,
        "override_reason": override_text,
    }


def _strict_shell_auto_required(command: str) -> tuple[bool, str]:
    text = str(command or "")
    checks: tuple[tuple[str, str], ...] = (
        (";", "compound_separator"),
        ("&&", "compound_and"),
        ("||", "compound_or"),
        ("|", "pipe"),
        ("$(", "subshell"),
        ("`", "subshell_backticks"),
        ("tmux", "tmux_session_command"),
        ("docker exec", "docker_exec"),
        ("docker run -d", "detached_docker_run"),
        ("bash -c", "nested_bash_c"),
        ("sh -c", "nested_sh_c"),
    )
    for needle, reason in checks:
        if needle in text:
            return True, reason
    if re.search(r"(^|[^\w])&(\s|$)", text):
        return True, "background_job"
    if re.search(r"\b(service|systemctl)\b", text) and re.search(r"\b(curl|wget|nc|grep|tail|sleep)\b", text):
        return True, "service_startup_readiness_combo"
    return False, ""


def _next_command_id(run_root: Path, safe_label: str) -> str:
    ledger_path = run_root / "artifacts" / "command_ledger" / "commands.json"
    next_index = 1
    if ledger_path.exists():
        try:
            payload = json.loads(ledger_path.read_text(encoding="utf-8"))
            entries = payload.get("commands", []) if isinstance(payload, dict) else []
            if isinstance(entries, list):
                next_index = len(entries) + 1
        except Exception:
            next_index = 1
    return f"cmd_{next_index:04d}_{safe_label}"


def _snapshot_repo_files(repo_root: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    root = repo_root.resolve()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(root)
        except Exception:
            continue
        if _skip_repo_mutation_snapshot(relative, path):
            continue
        try:
            stat = path.stat()
            item = {"sha256": sha256_file(path), "size_bytes": stat.st_size}
            if stat.st_size <= 200_000 and _is_probably_text_file(path):
                item["text"] = path.read_text(encoding="utf-8", errors="replace")
            snapshot[relative.as_posix()] = item
        except OSError:
            continue
    return snapshot


def _skip_repo_mutation_snapshot(relative: Path, path: Path) -> bool:
    parts = set(relative.parts)
    if parts.intersection({".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".venv", "venv", "env", "node_modules", ".mvp_run", ".artifactcopilot_run"}):
        return True
    if relative.name.endswith((".pyc", ".pyo", ".so", ".dll", ".dylib", ".class", ".jar", ".zip", ".tar", ".gz")):
        return True
    try:
        return path.stat().st_size > 2_000_000
    except OSError:
        return True


def _is_probably_text_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            chunk = handle.read(4096)
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _detect_repo_mutation(*, context: ToolContext, before: dict[str, dict[str, Any]], command_id: str) -> dict[str, Any]:
    after = _snapshot_repo_files(context.repo_root)
    changed_paths = sorted(set(before) | set(after))
    changes: list[dict[str, Any]] = []
    patch_parts: list[str] = []
    for relative in changed_paths:
        before_meta = before.get(relative)
        after_meta = after.get(relative)
        if before_meta == after_meta:
            continue
        status = "modified" if before_meta is not None and after_meta is not None else "added" if before_meta is None else "deleted"
        changes.append(
            {
                "path": relative,
                "status": status,
                "before_sha256": before_meta.get("sha256") if before_meta else None,
                "after_sha256": after_meta.get("sha256") if after_meta else None,
                "before_size_bytes": before_meta.get("size_bytes") if before_meta else None,
                "after_size_bytes": after_meta.get("size_bytes") if after_meta else None,
            }
        )
        diff_text = _repo_file_diff(context.repo_root, relative, status=status, before_meta=before_meta, after_meta=after_meta)
        if diff_text:
            patch_parts.append(diff_text)
    mutation: dict[str, Any] = {"schema": "repo_mutation_v1", "changed_files": changes, "change_count": len(changes), "patch_path": ""}
    if patch_parts:
        patch_dir = context.run_root / "interventions" / "command_repo_mutations"
        ensure_dir(patch_dir)
        patch_path = patch_dir / f"{command_id}.patch"
        write_text(patch_path, "\n".join(patch_parts))
        mutation["patch_path"] = str(patch_path)
        mutation["evidence_refs"] = [str(patch_path)]
    else:
        mutation["evidence_refs"] = []
    if changes:
        mutation_dir = context.run_root / "artifacts" / "repo_mutations"
        ensure_dir(mutation_dir)
        mutation_path = mutation_dir / f"{command_id}.json"
        write_json(mutation_path, mutation)
        mutation["mutation_path"] = str(mutation_path)
        mutation["evidence_refs"] = dedupe_strings(list(mutation.get("evidence_refs", [])) + [str(mutation_path)])
    return _summarize_repo_mutation(mutation)


def _summarize_repo_mutation(mutation: dict[str, Any]) -> dict[str, Any]:
    changes = mutation.get("changed_files", [])
    if not isinstance(changes, list):
        changes = []
    status_counts: dict[str, int] = {}
    top_level_counts: dict[str, int] = {}
    for item in changes:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "unknown")).strip() or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        path = str(item.get("path", "")).strip()
        top = path.split("/", 1)[0] if path else "(unknown)"
        top_level_counts[top] = top_level_counts.get(top, 0) + 1
    sample = [item for item in changes[:INLINE_REPO_MUTATION_FILE_LIMIT] if isinstance(item, dict)]
    omitted = max(0, len(changes) - len(sample))
    summary = dict(mutation)
    summary["changed_files"] = sample
    summary["changed_files_omitted"] = omitted
    summary["status_counts"] = status_counts
    summary["top_level_counts"] = dict(sorted(top_level_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:12])
    summary["summary_mode"] = "repo_mutation_summary_v1"
    if omitted:
        summary["note"] = "Full changed file list is stored in mutation_path; prompt/state only carries a bounded sample."
    return summary


def _repo_file_diff(repo_root: Path, relative: str, *, status: str, before_meta: dict[str, Any] | None, after_meta: dict[str, Any] | None) -> str:
    before_text = str((before_meta or {}).get("text", ""))
    after_text = str((after_meta or {}).get("text", ""))
    if status == "modified" and not after_text:
        return ""
    from_label = f"a/{relative}" if status != "added" else "/dev/null"
    to_label = f"b/{relative}" if status != "deleted" else "/dev/null"
    return "".join(difflib.unified_diff(before_text.splitlines(keepends=True), after_text.splitlines(keepends=True), fromfile=from_label, tofile=to_label))


def _repo_mutation_evidence_refs(repo_mutation: dict[str, Any]) -> list[str]:
    refs = []
    if isinstance(repo_mutation, dict):
        refs.extend(str(ref).strip() for ref in repo_mutation.get("evidence_refs", []) if str(ref).strip())
        patch_path = str(repo_mutation.get("patch_path", "")).strip()
        if patch_path:
            refs.append(patch_path)
    return dedupe_strings(refs)


def _append_command_ledger(
    *,
    context: ToolContext,
    command_id: str,
    phase_name: str,
    log_label: str,
    purpose: str,
    command: str,
    cwd: Path,
    timeout_sec: int,
    started_at: str,
    ended_at: str,
    duration_sec: float,
    returncode: int | None,
    outcome: str,
    timed_out: bool,
    stdout_path: Path,
    stderr_path: Path,
    stdout_chars: int,
    stderr_chars: int,
    strict_shell: bool = False,
    detected_failure_patterns: list[str] | None = None,
    command_status_summary: dict[str, Any] | None = None,
    repo_mutation: dict[str, Any] | None = None,
    strict_shell_decision: dict[str, Any] | None = None,
    output_decode_lossy: bool = False,
) -> dict[str, str]:
    ledger_dir = context.run_root / "artifacts" / "command_ledger"
    ensure_dir(ledger_dir)
    ledger_path = ledger_dir / "commands.json"
    jsonl_path = context.run_root / "command_ledger.jsonl"
    entry = {
        "schema": "command_ledger_entry_v1",
        "command_id": command_id,
        "run_id": context.run_id,
        "phase": phase_name,
        "log_label": log_label,
        "purpose": purpose,
        "command": command,
        "cwd": str(cwd),
        "returncode": returncode,
        "outcome": outcome,
        "final_status": (command_status_summary or {}).get("final_status", outcome),
        "exit_code_status": (command_status_summary or {}).get("exit_code_status", ""),
        "pattern_status": (command_status_summary or {}).get("pattern_status", ""),
        "agent_action_required": (command_status_summary or {}).get("agent_action_required", ""),
        "command_status_summary": command_status_summary or {},
        "strict_shell": strict_shell,
        "strict_shell_decision": strict_shell_decision or {},
        "detected_failure_patterns": detected_failure_patterns or [],
        "timeout_sec": timeout_sec,
        "timed_out": timed_out,
        "stdout_ref": str(stdout_path),
        "stderr_ref": str(stderr_path),
        "stdout_chars": stdout_chars,
        "stderr_chars": stderr_chars,
        "output_decode_lossy": output_decode_lossy,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_sec": duration_sec,
        "repo_mutation": _summarize_repo_mutation(repo_mutation or {"changed_files": []}),
        "repo_mutation_review_required": bool((repo_mutation or {}).get("changed_files")),
        "repo_mutation_intervention_supplied": False,
    }
    payload: dict[str, Any] = {"schema": "command_ledger_v1", "commands": [], "updated_at": ended_at}
    if ledger_path.exists():
        try:
            existing = json.loads(ledger_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload.update(existing)
                if not isinstance(payload.get("commands"), list):
                    payload["commands"] = []
        except Exception:
            payload["commands"] = []
    payload["schema"] = "command_ledger_v1"
    payload["updated_at"] = ended_at
    payload.setdefault("commands", []).append(entry)
    write_json(ledger_path, payload)
    ensure_dir(jsonl_path.parent)
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"command_ledger_path": str(ledger_path), "command_ledger_jsonl_path": str(jsonl_path)}


def _phase_command_entry(command: dict[str, Any]) -> dict[str, Any]:
    return {
        "command_id": command.get("command_id"),
        "command": command.get("command", ""),
        "cwd": command.get("cwd", ""),
        "purpose": command.get("purpose", ""),
        "returncode": command.get("returncode"),
        "outcome": command.get("outcome", "unknown"),
        "final_status": command.get("final_status", command.get("outcome", "unknown")),
        "exit_code_status": command.get("exit_code_status", ""),
        "pattern_status": command.get("pattern_status", ""),
        "agent_action_required": command.get("agent_action_required", ""),
        "command_status_summary": command.get("command_status_summary", {}),
        "strict_shell": bool(command.get("strict_shell", False)),
        "strict_shell_decision": command.get("strict_shell_decision", {}),
        "detected_failure_patterns": command.get("detected_failure_patterns", []),
        "timeout_sec": command.get("timeout_sec"),
        "timed_out": bool(command.get("timed_out", False)),
        "stdout_ref": command.get("stdout_ref", ""),
        "stderr_ref": command.get("stderr_ref", ""),
        "stdout_chars": command.get("stdout_chars", 0),
        "stderr_chars": command.get("stderr_chars", 0),
        "started_at": command.get("started_at", ""),
        "ended_at": command.get("ended_at", ""),
        "duration_sec": command.get("duration_sec"),
        "repo_mutation": _summarize_repo_mutation(command.get("repo_mutation", {}) if isinstance(command.get("repo_mutation"), dict) else {}),
    }
