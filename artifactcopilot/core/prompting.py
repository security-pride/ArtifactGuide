from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from ..shared.fs import safe_read_text
from .state import AgentState


def load_global_prompt(prompt_path: Path) -> str:
    return safe_read_text(prompt_path).strip()


def load_evaluation_rules(rules_path: Path) -> str:
    return safe_read_text(rules_path).strip()


def load_phase_skill(skill_path: Path) -> str:
    return safe_read_text(skill_path).strip()


def render_user_prompt(
    *,
    state: AgentState,
    tools_text: str,
    evaluation_rules_text: str,
    state_summary_text: str,
    phase_skill: str,
    memory_text: str,
) -> str:
    sections = [
        "<tool_list>",
        tools_text.strip(),
        "</tool_list>",
        "",
        "<evaluation_rules>",
        evaluation_rules_text.strip(),
        "</evaluation_rules>",
        "",
        "<current_phase_skill>",
        phase_skill.strip(),
        "</current_phase_skill>",
        "",
        memory_text.strip(),
        "",
        "<state_summary>",
        state_summary_text.strip(),
        "</state_summary>",
    ]
    return "\n".join(sections).strip()


def render_state_summary(*, state: AgentState, last_observation: str, compact: bool = False) -> str:
    if compact:
        payload = _render_compact_state_summary(state=state, last_observation=last_observation)
    else:
        payload = {
            "run_id": state.run_id,
            "repo_root": state.repo_root,
            "run_root": state.run_root,
            "current_phase": state.current_phase,
            "phase_history": state.phase_history,
            "shared_context": state.shared_context,
            "phase_artifacts": state.phase_artifacts,
            "interventions": state.interventions,
            "blockers": state.blockers,
            "last_observation": last_observation.strip() or "(none)",
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def estimate_text_tokens(text: str) -> int:
    compact = str(text or "")
    if not compact:
        return 0
    return max(1, (len(compact) + 3) // 4)


def _render_compact_state_summary(*, state: AgentState, last_observation: str) -> dict[str, Any]:
    last_observation_payload: Any = None
    try:
        last_observation_payload = json.loads(last_observation) if last_observation.strip() else None
    except Exception:
        last_observation_payload = None
    return {
        "summary_mode": "compact_state_summary_v1",
        "run_id": state.run_id,
        "repo_root": state.repo_root,
        "run_root": state.run_root,
        "current_phase": state.current_phase,
        "phase_history": _compact_phase_history(state.phase_history),
        "shared_context": _compact_value(state.shared_context, depth=2, item_limit=14, text_limit=500),
        "phase_artifacts": _compact_phase_artifacts(state.phase_artifacts),
        "current_stage_progress": _current_stage_progress_index(state, last_observation=last_observation_payload),
        "evidence_store_keys": sorted(str(key) for key in state.evidence_store.keys()),
        "interventions": _compact_value(state.interventions, depth=3, item_limit=10, text_limit=500),
        "blockers": _compact_value(state.blockers, depth=3, item_limit=10, text_limit=500),
        "last_observation": _compact_json_text(last_observation.strip() or "(none)", char_limit=12_000),
        "retrieval_policy": (
            "This compact summary intentionally omits large artifact bodies. "
            "When exact evidence is needed, read the referenced run artifact or log file instead of relying on summary prose."
        ),
    }


def _current_stage_progress_index(state: AgentState, last_observation: Any | None = None) -> dict[str, Any]:
    phase = str(state.current_phase or "")
    commands = _phase_commands_from_global_ledger(state, phase)
    artifacts = state.phase_artifacts.get(phase, {})
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    missing_required = _missing_required_artifacts_from_state(state, phase)
    required_diagnostics = _required_artifact_diagnostics_from_state(state, phase)
    phase_finished = _phase_finished(state, phase)
    progress: dict[str, Any] = {
        "phase": phase,
        "phase_finished": phase_finished,
        "missing_required_artifacts": missing_required,
        "required_artifact_diagnostics": required_diagnostics,
        "required_stage_files_present": not missing_required,
        "stage_completion_status": _stage_completion_status(phase=phase, missing=missing_required, phase_finished=phase_finished),
        "recommended_next_action": _stage_recommended_next_action(
            phase=phase,
            missing=missing_required,
            phase_finished=phase_finished,
            last_observation=last_observation,
        ),
        "phase_command_count": len(commands),
        "recent_phase_commands": [
            {
                "command_id": str(command.get("command_id", "")),
                "final_status": str(command.get("final_status", command.get("outcome", ""))),
                "purpose": _trim_text(str(command.get("purpose", "")), 180),
                "command": _trim_text(str(command.get("command", "")), 260),
                "stdout_ref": str(command.get("stdout_ref", "")),
                "stderr_ref": str(command.get("stderr_ref", "")),
            }
            for command in commands[-8:]
        ],
    }
    if phase == "D3_executability":
        route_evidence = _artifact_payload(artifacts.get("route_evidence"))
        attempts = route_evidence.get("route_attempts", []) if isinstance(route_evidence, dict) else []
        attempts = [item for item in attempts if isinstance(item, dict)] if isinstance(attempts, list) else []
        progress["d3_route_attempts_recorded"] = [
            {
                "candidate_id": str(item.get("candidate_id", "")),
                "attempt_status": str(item.get("attempt_status", "")),
                "route_result_interpretation": str(item.get("route_result_interpretation", "")),
                "evidence_refs": _compact_value(item.get("evidence_refs", []), depth=1, item_limit=5, text_limit=140),
            }
            for item in attempts[-8:]
        ]
        progress["d3_protocol_hint"] = _d3_protocol_hint(commands, attempts, progress["missing_required_artifacts"])
    if phase == "D4_claim_support":
        progress["d4_protocol_hint"] = _d4_protocol_hint(state, artifacts, progress["missing_required_artifacts"])
    if progress["missing_required_artifacts"]:
        progress["missing_artifact_hint"] = _missing_artifact_hint(phase, progress["missing_required_artifacts"])
    return progress


def _phase_finished(state: AgentState, phase: str) -> bool:
    for item in state.phase_history:
        if isinstance(item, dict) and str(item.get("phase", "")).strip() == phase:
            return True
    return False


def _stage_completion_status(*, phase: str, missing: list[str], phase_finished: bool) -> str:
    if phase_finished:
        return f"{phase} is already finished in phase_history."
    if phase == "run_initialization":
        return (
            "run_initialization has no stage packet, so required-file presence alone does not mean it is complete. "
            "Follow the initialization skill until configuration, repository inspection, paper extraction, and memory writes are done."
        )
    if not missing:
        return (
            f"All required stage files for {phase} are present, but phase_history does not show this stage as finished."
        )
    return f"{phase} is still collecting required stage files: {', '.join(missing)}."


def _stage_recommended_next_action(
    *,
    phase: str,
    missing: list[str],
    phase_finished: bool,
    last_observation: Any | None = None,
) -> str:
    if phase_finished:
        return "Do not continue work for this finished stage; follow the runtime current_phase."
    guardrail_hint = _phase_finish_guardrail_repair_hint(last_observation)
    if guardrail_hint:
        return guardrail_hint
    if phase == "run_initialization":
        return (
            "Do not call phase_finish just because no stage packet is required. "
            "First run load_run_config, list_repo_tree, get_paper_info when a paper source exists, and write initialization memory files."
        )
    if not missing:
        return (
            f"Call phase_finish for {phase} now. Do not start the next stage, reread evidence, or rewrite memory before phase_finish succeeds."
        )
    return "Continue the current stage only to produce the missing required files, then call phase_finish."


def _phase_finish_guardrail_repair_hint(last_observation: Any | None) -> str:
    if not isinstance(last_observation, dict):
        return ""
    summary = str(last_observation.get("summary", "")).strip()
    if "phase_finish rejected" not in summary:
        return ""
    payload = last_observation.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    route_errors = payload.get("route_surface_finish_errors")
    if isinstance(route_errors, list) and route_errors:
        first = route_errors[0] if isinstance(route_errors[0], dict) else {}
        required_action = str(first.get("required_action", "")).strip()
        return (
            "Do not call phase_finish again yet. The previous phase_finish was rejected by route-surface "
            "guardrail. Update the current stage packet/reuse plan with explicit route evidence or skip "
            "arbitration for the named candidates, then call phase_finish. Required action: "
            + (required_action or "record attempt/skip status, failure_or_skip_cause, and decisiveness/sufficiency effect.")
        )
    if "final_scoring_finish_errors" in payload:
        errors = payload.get("final_scoring_finish_errors")
        first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
        required_action = str(first.get("required_action", "")).strip()
        target = first.get("deterministic_repair_target", {}) if isinstance(first, dict) else {}
        target_badge = str(target.get("expected_badge", "")).strip() if isinstance(target, dict) else ""
        failed = target.get("failed_predicates", {}) if isinstance(target, dict) else {}
        failed_text = ""
        if isinstance(failed, dict) and failed:
            failed_text = " Failed predicates: " + json.dumps(failed, ensure_ascii=False)[:1200]
        target_text = f" Expected deterministic badge: {target_badge}." if target_badge else ""
        return (
            "Do not call phase_finish again yet. Rewrite final_scoring_packet.json to fix the deterministic "
            "final-scoring errors first."
            + target_text
            + failed_text
            + (" Required action: " + required_action if required_action else "")
        )
    if "available_gate_finish_errors" in payload:
        return "Do not call phase_finish again yet. Rewrite the Available gate packet with artifact-authored reviewer entry evidence first."
    if "d4_functional_gate_finish_errors" in payload:
        errors = payload.get("d4_functional_gate_finish_errors")
        first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
        required_action = str(first.get("required_action", "")).strip()
        return (
            "Do not call phase_finish again yet. Rewrite the D4 stage_packet.json with the required "
            "functional_predicate and d5_entry_gate. D4 must apply the rubric Functional predicate "
            "before D5 can run."
            + (" Required action: " + required_action if required_action else "")
        )
    return ""


def _phase_commands_from_global_ledger(state: AgentState, phase: str) -> list[dict[str, Any]]:
    ledger = state.shared_context.get("command_ledger", {})
    raw_commands = ledger.get("commands", []) if isinstance(ledger, dict) else []
    if not raw_commands:
        ledger_path = Path(str(state.run_root)) / "artifacts" / "command_ledger" / "commands.json"
        if ledger_path.exists():
            try:
                payload = json.loads(ledger_path.read_text(encoding="utf-8"))
                raw_commands = payload.get("commands", []) if isinstance(payload, dict) else []
            except Exception:
                raw_commands = []
    if not isinstance(raw_commands, list):
        return []
    return [item for item in raw_commands if isinstance(item, dict) and str(item.get("phase", "")).strip() == phase]


def _artifact_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("payload"), dict):
        return value["payload"]
    return value if isinstance(value, dict) else {}


def _missing_required_artifacts_from_state(state: AgentState, phase: str) -> list[str]:
    try:
        from .state import PHASE_REQUIRED_ARTIFACTS, phase_artifact_validation_error
    except Exception:
        return []
    required = PHASE_REQUIRED_ARTIFACTS.get(phase, ())
    artifacts = state.phase_artifacts.get(phase, {})
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    run_root = Path(str(state.run_root))
    return [key for key in required if phase_artifact_validation_error(run_root, phase, key, artifacts)]


def _required_artifact_diagnostics_from_state(state: AgentState, phase: str) -> list[dict[str, str]]:
    try:
        from .state import PHASE_REQUIRED_ARTIFACTS, phase_artifact_path, phase_artifact_validation_error
    except Exception:
        return []
    required = PHASE_REQUIRED_ARTIFACTS.get(phase, ())
    artifacts = state.phase_artifacts.get(phase, {})
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    run_root = Path(str(state.run_root))
    diagnostics: list[dict[str, str]] = []
    for key in required:
        error = phase_artifact_validation_error(run_root, phase, key, artifacts)
        if not error:
            continue
        path = phase_artifact_path(run_root, phase, key)
        diagnostics.append(
            {
                "artifact_key": key,
                "path": str(path.relative_to(run_root)) if path.is_relative_to(run_root) else str(path),
                "status": "invalid_json" if error.startswith("invalid_json:") else "missing",
                "detail": error,
            }
        )
    return diagnostics


def _d3_protocol_hint(commands: list[dict[str, Any]], attempts: list[dict[str, Any]], missing: list[str]) -> str:
    if _d3_packaging_started(missing):
        return (
            "D3 packaging has started. Fill only missing required deliverables in the required order. "
            "Do not repeat log reads, help commands, route selection, or route attempts unless the missing artifact itself requires it."
        )
    repeated_attempt_ids = _repeated_candidate_ids(attempts)
    if commands and not attempts:
        return (
            "D3 commands already exist but route_evidence.json has no route_attempts. "
            "Do not restart discovery or repeat help/README reads; write route_evidence.json with each meaningful command outcome, then package D3 evidence."
        )
    if repeated_attempt_ids:
        return (
            "D3 has repeated workflow attempt ids "
            f"{', '.join(repeated_attempt_ids[:5])}. Do not rewrite equivalent attempts or re-read the same logs. "
            "If no bounded representative workflow is grounded by D1/README evidence, record no_runnable_workflow or limited evidence in route_evidence/evidence_semantics and finish the phase."
        )
    if attempts and missing:
        return (
            "Continue from recorded route attempts. Fill only missing D3 deliverables; do not repeat equivalent "
            "commands, unsupported flags, or the same log reads without a new concrete purpose."
        )
    if attempts:
        return "Route attempts are recorded. Prefer packaging route_evidence/evidence_semantics/stage_packet and closing the phase over further broad exploration."
    return "No D3 route attempts recorded yet. Write route_selection_checkpoint, execute one selected documentation-grounded route, then record route evidence."


def _d4_protocol_hint(state: AgentState, artifacts: dict[str, Any], missing: list[str]) -> str:
    if _d4_packaging_started(missing):
        return (
            "D4 packaging has started. Fill only missing required deliverables. Do not reread the same D3/paper files unless a named field is missing."
        )
    d3_artifacts = state.phase_artifacts.get("D3_executability", {})
    d3_artifacts = d3_artifacts if isinstance(d3_artifacts, dict) else {}
    stage_packet = _artifact_payload(d3_artifacts.get("stage_packet"))
    evidence_semantics = _artifact_payload(d3_artifacts.get("evidence_semantics"))
    route_evidence = _artifact_payload(d3_artifacts.get("route_evidence"))
    combined = json.dumps([stage_packet, evidence_semantics, route_evidence], ensure_ascii=False, default=str).lower()
    if "no fresh representative" in combined or "smoke" in combined and "representative" in combined and "failed" in combined:
        return (
            "D3 appears to have no fresh representative output. Build not_evaluable claim mappings from D3 blockers/limitations and inventory only packaged refs already identified by D3."
        )
    return "Read D3 evidence and paper targets once, then build claim_support_evidence instead of repeatedly rereading broad artifacts."


def _d3_packaging_started(missing: list[str]) -> bool:
    if not missing:
        return True
    core = {"route_selection_checkpoint", "route_evidence", "evidence_semantics", "stage_packet"}
    return not any(item in core for item in missing)


def _d4_packaging_started(missing: list[str]) -> bool:
    if not missing:
        return True
    return "claim_support_evidence" not in missing


def _missing_artifact_hint(phase: str, missing: list[str]) -> str:
    missing_text = ", ".join(missing)
    canonical = {
        "available_gate_llm_check",
        "documentation_llm_check",
        "environment_llm_check",
        "execution_llm_check",
        "claim_support_llm_check",
        "reusability_llm_check",
        "final_scoring_packet",
    }
    if any(item in canonical for item in missing):
        return f"Missing required artifact(s): {missing_text}. Use the canonical tool for protected artifacts; otherwise store only the missing artifact(s), then phase_finish."
    return f"Missing required artifact(s): {missing_text}. Store only these missing artifact(s), then phase_finish."


def _repeated_candidate_ids(attempts: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    repeated: list[str] = []
    for attempt in attempts:
        candidate_id = str(attempt.get("candidate_id", "")).strip()
        if not candidate_id:
            continue
        if candidate_id in seen and candidate_id not in repeated:
            repeated.append(candidate_id)
        seen.add(candidate_id)
    return repeated


def _compact_phase_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in history[-8:]:
        if not isinstance(item, dict):
            continue
        finish_payload = item.get("finish_payload", {})
        if not isinstance(finish_payload, dict):
            finish_payload = {}
        compact.append(
            {
                "phase": str(item.get("phase", "")),
                "finished_at": str(item.get("finished_at", "")),
                "finish_reason": _trim_text(str(finish_payload.get("finish_reason", "")), 360),
                "completion_signals": _compact_value(finish_payload.get("completion_signals", []), depth=1, item_limit=6, text_limit=220),
                "remaining_gaps": _compact_value(finish_payload.get("remaining_gaps", []), depth=1, item_limit=6, text_limit=180),
                "evidence_refs": _compact_value(finish_payload.get("evidence_refs", []), depth=1, item_limit=8, text_limit=180),
            }
        )
    return compact


def _compact_phase_artifacts(phase_artifacts: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for phase_name, artifacts in phase_artifacts.items():
        if not isinstance(artifacts, dict):
            compact[str(phase_name)] = _compact_value(artifacts, depth=2, item_limit=10, text_limit=400)
            continue
        phase_compact: dict[str, Any] = {}
        for artifact_key, artifact_value in artifacts.items():
            phase_compact[str(artifact_key)] = _summarize_artifact_payload(artifact_value)
        compact[str(phase_name)] = phase_compact
    return compact


def _summarize_artifact_payload(value: Any) -> Any:
    if not isinstance(value, dict):
        return _compact_value(value, depth=2, item_limit=8, text_limit=360)
    summary_keys = (
        "schema",
        "status",
        "overall",
        "score",
        "d1_score",
        "d2_score",
        "d3_score",
        "d4_score",
        "d5_score",
        "evidence_tier",
        "highest_intervention",
        "badge_recommendation",
        "confidence",
        "artifact_path",
        "profile_path",
        "text_path",
        "command_ledger_path",
        "validation_path",
    )
    summary: dict[str, Any] = {"keys": sorted(str(key) for key in value.keys())[:40]}
    for key in summary_keys:
        if key in value:
            summary[key] = _compact_value(value.get(key), depth=2, item_limit=8, text_limit=300)
    for key in ("dimension_scores", "available_gate", "checklist", "rw_checks", "badge_derivation"):
        if key in value:
            summary[key] = _compact_value(value.get(key), depth=3, item_limit=12, text_limit=220)
    for key in ("limitations", "blockers", "evidence_refs", "output_refs", "command_refs", "audit_findings", "corrections"):
        if key in value:
            summary[key] = _compact_value(value.get(key), depth=2, item_limit=10, text_limit=220)
    for key in ("handoff_summary", "summary", "notes", "rationale", "d4_evidence", "d5_evidence"):
        if key in value:
            summary[key] = _trim_text(str(value.get(key, "")), 500)
    return summary


def _compact_json_text(text: str, *, char_limit: int) -> str:
    if len(text) <= char_limit:
        return text
    head = text[: char_limit // 2]
    tail = text[-char_limit // 2 :]
    return f"{head}\n...[truncated {len(text) - char_limit} chars from compact state summary field]...\n{tail}"


def _compact_value(value: Any, *, depth: int, item_limit: int, text_limit: int) -> Any:
    if depth <= 0:
        return _trim_text(str(value), text_limit)
    if isinstance(value, str):
        return _trim_text(value, text_limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        items = [_compact_value(item, depth=depth - 1, item_limit=item_limit, text_limit=text_limit) for item in value[:item_limit]]
        if len(value) > item_limit:
            items.append({"_truncated": len(value) - item_limit})
        return items
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= item_limit:
                compact["_truncated"] = len(value) - item_limit
                break
            compact[str(key)] = _compact_value(item, depth=depth - 1, item_limit=item_limit, text_limit=text_limit)
        return compact
    return _trim_text(str(value), text_limit)


def _trim_text(text: str, limit: int) -> str:
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    if limit <= 20:
        return raw[:limit]
    head = max(1, limit // 2 - 20)
    tail = max(1, limit - head - 40)
    return raw[:head] + f"...[truncated {len(raw) - head - tail} chars]..." + raw[-tail:]
