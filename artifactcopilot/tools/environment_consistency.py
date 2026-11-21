from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


PYTHON_VERSION_RE = re.compile(r"\bPython\s+(\d+\.\d+(?:\.\d+)?)\b", re.IGNORECASE)
PYTHON_PATH_RE = re.compile(r"(?P<path>/(?:[^\s\"'`:$]+/)*(?:python(?:3(?:\.\d+)?)?))\b")
POST_SETUP_MARKERS = (
    "post-setup",
    "post setup",
    "confirmed",
    "verified",
    "environment check",
    "environment checks",
    "environment setup commands",
    "environment ready",
)
NEGATED_CLAIM_MARKERS = (
    "not confirmed",
    "not fully confirmed",
    "not directly confirmed",
    "not verified",
    "not fully verified",
    "unconfirmed",
    "instead of claiming",
    "rather than claiming",
    "cannot claim",
    "does not claim",
    "do not claim",
)
PYTHON_MISMATCH_MARKERS = (
    "wrong interpreter",
    "different venv",
    "different virtualenv",
    "not the conda python",
    "not the virtualenv python",
    "not the venv python",
    "need to verify",
    "environment mismatch",
    "execution-context propagation",
    "activation may not fully override path",
)
PYTHON_MISMATCH_CARRY_FORWARD_MARKERS = PYTHON_MISMATCH_MARKERS + (
    "environment/interpreter mismatch",
    "interpreter mismatch",
    "runtime-context mismatch",
    "runtime context mismatch",
    "uv-managed venv",
    "pip absent",
    "partial_failure",
)
PYTHON_MISMATCH_ACK_MARKERS = (
    "carried forward",
    "limitation",
    "limitations",
    "human_review_sensitive_points",
    "review-sensitive",
    "no stronger than",
    "blocking_or_limiting_items",
)


def environment_consistency_findings(
    run_root: Path,
    *,
    report: dict[str, Any] | None = None,
    final_scoring_packet: dict[str, Any] | None = None,
    d2_stage_packet: dict[str, Any] | None = None,
    command_ledger: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    run_root = Path(run_root)
    final_scoring_packet = final_scoring_packet if isinstance(final_scoring_packet, dict) else _read_json(run_root / "artifacts" / "final_scoring" / "final_scoring_packet.json")
    d2_stage_packet = d2_stage_packet if isinstance(d2_stage_packet, dict) else _read_json(run_root / "artifacts" / "D2_environment" / "stage_packet.json")
    command_ledger = command_ledger if isinstance(command_ledger, dict) else _read_json(run_root / "artifacts" / "command_ledger" / "commands.json")
    report = report if isinstance(report, dict) else {}

    observations = _python_environment_observations(run_root, command_ledger)
    if not observations:
        return []

    findings: list[dict[str, str]] = []
    final_text = _json_text(final_scoring_packet)
    report_text = _json_text(report)
    d2_text = _json_text(d2_stage_packet)
    decision_text = "\n".join(text for text in (final_text, report_text) if text)

    claimed_versions = _claimed_confirmed_python_versions(decision_text)
    observed_versions = {
        version
        for observation in observations
        if observation["activation_or_env_check"]
        for version in observation["versions"]
    }
    if claimed_versions and observed_versions:
        for claimed in sorted(claimed_versions):
            if not any(_same_major_minor(claimed, observed) for observed in observed_versions):
                findings.append(
                    _finding(
                        "error",
                        "python_environment_claim_contradicts_command_log",
                        "Final/report text claims a confirmed Python "
                        f"{claimed} environment, but D2 activated environment checks observed "
                        f"{', '.join(sorted(observed_versions))}.",
                    )
                )

    claimed_paths = _claimed_python_paths(decision_text)
    observed_paths = {
        path
        for observation in observations
        if observation["activation_or_env_check"]
        for path in observation["paths"]
    }
    for claimed_path in sorted(claimed_paths):
        if observed_paths and claimed_path not in observed_paths and _path_claim_is_specific_environment(claimed_path):
            findings.append(
                _finding(
                    "error",
                    "python_environment_path_claim_contradicts_command_log",
                    "Final/report text claims Python executable "
                    f"`{claimed_path}`, but D2 activated environment checks observed "
                    f"{', '.join(f'`{path}`' for path in sorted(observed_paths))}.",
                )
            )

    if _has_unresolved_python_mismatch(d2_text) and not _final_carries_python_mismatch(decision_text):
        findings.append(
            _finding(
                "error",
                "unresolved_python_environment_mismatch_not_carried_forward",
                "D2 evidence records an unresolved Python environment/interpreter mismatch, "
                "but final/report text does not carry that limitation or review-sensitive point forward.",
            )
        )

    return _dedupe_findings(findings)


def _python_environment_observations(run_root: Path, command_ledger: dict[str, Any]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for command in _commands(command_ledger):
        phase = str(command.get("phase", command.get("phase_name", ""))).strip()
        if phase != "D2_environment":
            continue
        command_text = str(command.get("command", ""))
        if not _looks_like_python_env_check(command_text):
            continue
        output = _command_output(run_root, command)
        versions = _python_versions(output)
        paths = _python_paths(output)
        if not versions and not paths:
            continue
        lowered = command_text.lower()
        purpose = str(command.get("purpose", "")).lower()
        command_id = str(command.get("command_id", command.get("id", ""))).strip()
        observations.append(
            {
                "command_id": command_id,
                "versions": versions,
                "paths": paths,
                "activation_or_env_check": (
                    "conda activate" in lowered
                    or "source " in lowered and "activate" in lowered
                    or "post" in lowered
                    or "verify" in purpose
                    or "check" in purpose
                    or "environment" in purpose
                    or "env" in command_id.lower()
                ),
            }
        )
    return observations


def _looks_like_python_env_check(command: str) -> bool:
    lowered = str(command or "").lower()
    return bool(
        re.search(r"\bpython(?:3(?:\.\d+)?)?\s+(?:--version|-v)\b", lowered)
        or "sys.executable" in lowered
        or re.search(r"\bwhich\s+python(?:3(?:\.\d+)?)?\b", lowered)
        or re.search(r"\bpip\s+-v\b", lowered)
        or re.search(r"\bpip\s+--version\b", lowered)
    )


def _command_output(run_root: Path, command: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("stdout_ref", "stderr_ref", "stdout_path", "stderr_path", "stdout_log_path", "stderr_log_path"):
        value = str(command.get(key, "")).strip()
        if not value:
            continue
        path = _resolve_run_path(run_root, value)
        if path and path.exists():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    for key in ("stdout", "stderr", "stdout_tail", "stderr_tail"):
        value = str(command.get(key, "")).strip()
        if value:
            parts.append(value)
    return "\n".join(parts)


def _resolve_run_path(run_root: Path, value: str) -> Path | None:
    path = Path(value)
    if path.is_absolute():
        return path
    candidate = run_root / value
    if candidate.exists():
        return candidate
    marker = "logs/"
    raw = path.as_posix()
    if marker in raw:
        suffix = raw[raw.rfind(marker) :]
        return run_root / suffix
    return candidate


def _claimed_confirmed_python_versions(text: str) -> set[str]:
    versions: set[str] = set()
    for match in PYTHON_VERSION_RE.finditer(text):
        start = max(0, match.start() - 180)
        end = min(len(text), match.end() + 180)
        context = text[start:end].lower()
        if any(marker in context for marker in POST_SETUP_MARKERS) and not any(marker in context for marker in NEGATED_CLAIM_MARKERS):
            versions.add(match.group(1))
    return versions


def _claimed_python_paths(text: str) -> set[str]:
    paths: set[str] = set()
    for match in PYTHON_PATH_RE.finditer(text):
        path = match.group("path")
        start = max(0, match.start() - 180)
        end = min(len(text), match.end() + 180)
        context = text[start:end].lower()
        if any(marker in context for marker in POST_SETUP_MARKERS) or any(token in path for token in ("/envs/", "/.venv/", "/venv/")):
            paths.add(path)
    return paths


def _python_versions(text: str) -> set[str]:
    return {match.group(1) for match in PYTHON_VERSION_RE.finditer(text or "")}


def _python_paths(text: str) -> set[str]:
    return {match.group("path") for match in PYTHON_PATH_RE.finditer(text or "")}


def _same_major_minor(left: str, right: str) -> bool:
    return ".".join(left.split(".")[:2]) == ".".join(right.split(".")[:2])


def _path_claim_is_specific_environment(path: str) -> bool:
    return any(token in path for token in ("/envs/", "/.venv/", "/venv/", "/virtualenv/"))


def _has_unresolved_python_mismatch(text: str) -> bool:
    lowered = text.lower()
    return "python" in lowered and any(marker in lowered for marker in PYTHON_MISMATCH_MARKERS)


def _final_carries_python_mismatch(text: str) -> bool:
    lowered = text.lower()
    if "python" not in lowered:
        return False
    if any(marker in lowered for marker in PYTHON_MISMATCH_MARKERS):
        return True
    has_ack = any(marker in lowered for marker in PYTHON_MISMATCH_ACK_MARKERS)
    has_equivalent_marker = any(marker in lowered for marker in PYTHON_MISMATCH_CARRY_FORWARD_MARKERS)
    return has_ack and has_equivalent_marker


def _commands(command_ledger: dict[str, Any]) -> list[dict[str, Any]]:
    raw = command_ledger.get("commands", []) if isinstance(command_ledger, dict) else []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _json_text(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict) or not payload:
        return ""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _finding(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _dedupe_findings(findings: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, str]] = []
    for finding in findings:
        key = (finding.get("severity", ""), finding.get("code", ""), finding.get("message", ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result
