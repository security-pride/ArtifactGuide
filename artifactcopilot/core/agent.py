from __future__ import annotations

import json
import os
import re
import traceback
import time
from pathlib import Path
from typing import Any

import yaml

from ..llm.llm_client import DeepSeekLLMClient, PromptBundle
from ..shared.fs import ensure_dir, now_iso, relpath_str, safe_read_text, write_json, write_text
from ..tools import GlobalToolRegistry, ToolContext, ToolError, get_host_tools
from ..tools.available_gate_guardrail import available_gate_finish_errors
from ..tools.badge_guardrails import badge_guardrail_findings
from ..tools.consistency_checker import final_packet_internal_consistency_findings, representative_workflow_findings, resource_boundary_findings, stage_final_semantics_findings
from ..tools.environment_consistency import environment_consistency_findings
from ..tools.rubric_evidence_engine import normalize_final_scoring_packet, predicate_consistency_findings
from ..tools.route_surface_guardrail import route_surface_finish_errors
from .evidence_auditor import audit_context_for_prompt, run_evidence_audit_gate, update_state_with_audit_result
from .prompting import estimate_text_tokens, load_evaluation_rules, load_global_prompt, load_phase_skill, render_state_summary, render_user_prompt
from .run_logging import format_json_compact
from .runtime import AgentRuntime
from .state import PHASE_REQUIRED_ARTIFACTS, phase_artifact_path
from .types import AgentDecision, Observation, ToolCall, next_phase_name


STAGE_SKILLS = {
    "run_initialization": "orchestrator_global_skill.md",
    "available_gate": "available_gate_skill.md",
    "D1_documentation": "documentation_review_skill.md",
    "D2_environment": "environment_review_skill.md",
    "D3_executability": "execution_validation_skill.md",
    "D4_claim_support": "claim_support_skill.md",
    "D5_reusability": "reuse_validation_skill.md",
    "final_scoring": "final_scoring_skill.md",
    "report_generation": "report_synthesis_skill.md",
}


STAGE_GOALS = {
    "run_initialization": "Load run inputs, configuration, paper source, and repository overview. Do not emit a stage packet or assign scores.",
    "available_gate": "Confirm whether the artifact provides the minimum evidence required to enter full AE review. Record evidence only; do not assign D1-D5 scores.",
    "D1_documentation": "Read artifact-authored documentation, especially README-style entry material, extract the documented review route, and record documentation evidence. Do not execute commands or assign scores.",
    "D2_environment": "Reconstruct or prepare the review environment from artifact-provided instructions, and record environment evidence and interventions. Do not run the representative workflow or assign scores.",
    "D3_executability": "Execute the representative workflow documented in D1 using the environment prepared in D2, and record commands, outputs, interventions, and limitations. Do not assign scores.",
    "D4_claim_support": "Read the paper source and analyze how D3 outputs support the paper claims. Do not run new experiments or assign scores.",
    "D5_reusability": "When the Functional prerequisite holds, collect and validate reuse-related evidence, and record the reuse plan and validation outcome. Do not assign scores.",
    "final_scoring": "Only in this phase, derive Available, D1-D5, evidence tier, intervention level, and badge conclusions from persisted evidence using the rubric.",
    "report_generation": "Convert the final scoring result into a reviewer-facing Markdown report. Do not rescore the artifact.",
}

INTERVENTION_ORDER = {"I0": 0, "I1": 1, "I2": 2, "I3": 3, "I4": 4}
DEFAULT_AGENT_MAX_STEPS = 260
MAX_REPEATED_ERROR_OBSERVATIONS = 12
AVAILABLE_SHORT_CIRCUIT_SHARED_KEY = "available_gate_short_circuit"
D5_FUNCTIONAL_SKIP_SHARED_KEY = "d5_skipped_by_d4_functional_gate"
DEFAULT_EVIDENCE_AUDITOR_PHASES = {
    phase
    for phase in STAGE_SKILLS
    if phase != "report_generation"
}


EVIDENCE_COLLECTION_POLICY = """Evidence collection phase policy.

This phase must collect and preserve reviewer-checkable evidence only. Full badge scoring is intentionally withheld until final_scoring.

Allowed:
- record observed files, commands, outputs, logs, environment state, paper-output mappings, transfer attempts, failures, interventions, and limitations;
- use natural-language evidence observations and stable refs;
- record whether an attempted route succeeded, failed, was blocked, or was not attempted, with reasons.

Not allowed before final_scoring:
- do not assign R/WR/WA/A dimension scores;
- do not recommend or deny Available/Functional/Reusable badges;
- do not write "D1=...", "D2=...", "D3=...", "D4=...", "D5=...", "score would be...", "Reusable holds/does not hold", or "badge blocked/awarded";
- do not convert evidence labels such as supporting-only, component-only, or insufficient into final badge conclusions.

Required D4 exception:
- In `D4_claim_support`, you must apply the rubric Functional predicate and write `functional_predicate` plus `d5_entry_gate` in `artifacts/D4_claim_support/stage_packet.json`. This is the prerequisite gate for D5, not a final badge recommendation. D4 may write "Functional predicate holds" or "Functional predicate does not hold" only inside that predicate/gate evidence.

The final_scoring stage will receive the full rubric and will judge all checklist items, D1-D5 scores, Functional, Reusable, confidence, and final badge from the evidence you write now.
If a stage skill mentions rubric-shaped evidence questions, treat them only as evidence organization, except for the explicit D4 Functional predicate described above. Do not apply Reusable or final badge scoring until final_scoring.
"""


class AgentRunIncomplete(RuntimeError):
    pass


class ArtifactEvaluationAgent:
    def __init__(self, *, runtime: AgentRuntime) -> None:
        self.runtime = runtime
        self.state_store = runtime.state_store
        self.memory = runtime.memory
        self.trajectory = runtime.trajectory
        self.run_logger = runtime.run_logger
        self.run_stats = runtime.run_stats
        self.llm = DeepSeekLLMClient.from_env()
        self.llm.set_monitor_log_path(runtime.paths.llm_monitor_path)
        self.package_root = Path(__file__).resolve().parents[1]
        self.global_prompt_path = self.package_root / "prompts" / "global_prompt.md"
        self.skills_dir = self.package_root / "skills"
        self.evaluation_rules_path = Path(__file__).resolve().parents[2] / "artifactguide" / "rubric_overview.md"
        self.decision_max_tokens = _bounded_env_int("AE_AGENT_DECISION_MAX_TOKENS", default=12000, lower=1024, upper=384000)
        self.step_trace_token_limit = int(os.getenv("AE_AGENT_STEP_TRACE_TOKEN_LIMIT", "256000"))
        self.evidence_auditor_enabled = _env_truthy("AE_AGENT_EVIDENCE_AUDITOR", default=False)
        self.evidence_auditor_max_rounds = _bounded_env_int("AE_AGENT_EVIDENCE_AUDITOR_MAX_ROUNDS", default=3, lower=1, upper=10)
        self.evidence_auditor_max_steps = _bounded_env_int("AE_AGENT_EVIDENCE_AUDITOR_MAX_STEPS", default=5, lower=1, upper=5)
        self.evidence_auditor_max_tokens = _bounded_env_int("AE_AGENT_EVIDENCE_AUDITOR_MAX_TOKENS", default=6000, lower=512, upper=64000)
        self.evidence_auditor_phases = _env_phase_set("AE_AGENT_EVIDENCE_AUDITOR_PHASES", default=DEFAULT_EVIDENCE_AUDITOR_PHASES)
        self.allow_system_package_install = _env_truthy("AE_AGENT_ALLOW_SYSTEM_PACKAGE_INSTALL", default=False)
        self.evidence_auditor_skill_path = self.skills_dir / "evidence_auditor_skill.md"
        self.registry = self._build_registry()
        self.memory.set_mission("Single-agent AE review: collect stage evidence first, score only in final_scoring, and write a reviewer-facing report.")
        self.memory.working.update_mission(repo_root=runtime.repo_root)

    def _build_registry(self) -> GlobalToolRegistry:
        context = ToolContext(
            repo_root=self.runtime.repo_root,
            run_root=self.runtime.paths.run_root,
            run_id=self.runtime.run_id,
            shared_context=self.state_store.state.shared_context,
        )
        registry = GlobalToolRegistry(context)
        for spec, handler in get_host_tools():
            registry.register(spec, handler)
        return registry

    def run(self, *, max_steps: int = 0, stop_after_phase: str = "") -> None:
        effective_max_steps = max_steps if max_steps > 0 else int(os.getenv("AE_AGENT_DEFAULT_MAX_STEPS", str(DEFAULT_AGENT_MAX_STEPS)))
        if effective_max_steps <= 0:
            effective_max_steps = DEFAULT_AGENT_MAX_STEPS
        last_observation = Observation(kind="bootstrap", summary="Agent started.", payload={})
        self.run_logger.log(f"Run started. repo={self.runtime.repo_root} run_root={self.runtime.paths.run_root}")
        self.run_logger.log(f"LLM decision max_tokens={self.decision_max_tokens}")
        self.run_logger.log(f"Phase preflight: {format_json_compact(self.runtime.phase_preflight)}")
        self.run_logger.log(f"Step budget: effective_max_steps={effective_max_steps} requested_max_steps={max_steps}")
        step_index = 1
        stopped_after_requested_phase = False
        stopped_normally = False
        repeated_error_signature = ""
        repeated_error_count = 0
        fatal_error: BaseException | None = None
        try:
            while step_index <= effective_max_steps:
                state = self.state_store.state
                if stop_after_phase and self._phase_committed(stop_after_phase):
                    self.run_logger.log(f"Stopping after requested phase `{stop_after_phase}` already committed.")
                    stopped_after_requested_phase = True
                    stopped_normally = True
                    break
                if state.current_phase == "report_generation" and self._final_report_exists() and self._report_generation_finished():
                    self.run_logger.log("Final report already exists and report_generation is finished; stopping.")
                    stopped_normally = True
                    break
                phase_before = state.current_phase
                step_id = f"{phase_before}-step-{step_index:02d}"
                started = time.monotonic()
                self.run_stats.record_step()
                self.memory.set_current_phase(phase_before, phase_goal=STAGE_GOALS.get(phase_before, phase_before))
                decision = self._decide(step_id=step_id, last_observation=last_observation)
                self._record_decision(step_id, decision)
                if decision.memory_note:
                    self.memory.add_fact(decision.memory_note)
                if decision.next_action == "tool_call" and decision.tool_calls:
                    last_observation = self._execute_tool_queue(step_id, decision.tool_calls)
                elif decision.next_action == "phase_finish" and decision.phase_finish is not None:
                    last_observation = self._finish_phase(step_id, decision.phase_finish)
                elif decision.next_action == "stop":
                    if stop_after_phase and not self._phase_committed(stop_after_phase):
                        last_observation = Observation(
                            kind="protocol_error",
                            summary=(
                                f"stop is not accepted in --stop-after-phase `{stop_after_phase}` mode until "
                                f"`{stop_after_phase}` has phase_finish_committed."
                            ),
                            payload={
                                "stop_after_phase": stop_after_phase,
                                "phase_committed": False,
                                "required_stage_files_present": not self.state_store.missing_required_artifacts(stop_after_phase),
                            },
                        )
                        self.trajectory.append(
                            phase=phase_before,
                            event_type="protocol_error",
                            step_id=step_id,
                            summary=last_observation.summary,
                            payload=last_observation.payload,
                        )
                    elif stop_after_phase and self._phase_committed(stop_after_phase):
                        last_observation = Observation(kind="agent_stop", summary=f"Agent stopped after requested phase `{stop_after_phase}`.", payload={})
                        self.trajectory.append(
                            phase=phase_before,
                            event_type="agent_stop",
                            step_id=step_id,
                            summary=last_observation.summary,
                            payload={"thought": decision.thought, "stop_after_phase": stop_after_phase},
                        )
                        self._log_step_end(step_id, decision, last_observation, time.monotonic() - started)
                        stopped_after_requested_phase = True
                        stopped_normally = True
                        break
                    elif state.current_phase == "report_generation" and self._report_generation_finished():
                        last_observation = Observation(kind="agent_stop", summary="Agent stopped after final report.", payload={})
                        self.trajectory.append(
                            phase=phase_before,
                            event_type="agent_stop",
                            step_id=step_id,
                            summary=last_observation.summary,
                            payload={"thought": decision.thought},
                        )
                        self._log_step_end(step_id, decision, last_observation, time.monotonic() - started)
                        stopped_normally = True
                        break
                    else:
                        last_observation = Observation(
                            kind="protocol_error",
                            summary="stop is accepted only after the final report exists; continue with tool_call or phase_finish.",
                            payload={},
                        )
                        self.trajectory.append(
                            phase=phase_before,
                            event_type="protocol_error",
                            step_id=step_id,
                            summary=last_observation.summary,
                            payload=last_observation.payload,
                        )
                else:
                    last_observation = Observation(kind="protocol_error", summary="Decision did not contain an executable action.", payload={})
                    self.trajectory.append(
                        phase=phase_before,
                        event_type="protocol_error",
                        step_id=step_id,
                        summary=last_observation.summary,
                        payload=last_observation.payload,
                    )
                if last_observation.kind in {"tool_error", "protocol_error"}:
                    signature = self._error_observation_signature(phase_before, last_observation)
                    if signature == repeated_error_signature:
                        repeated_error_count += 1
                    else:
                        repeated_error_signature = signature
                        repeated_error_count = 1
                    if repeated_error_count >= MAX_REPEATED_ERROR_OBSERVATIONS:
                        raise AgentRunIncomplete(
                            "Run stopped to avoid repeated error-observation token waste: "
                            f"{last_observation.summary} ({repeated_error_count} repeats in {phase_before})."
                        )
                else:
                    repeated_error_signature = ""
                    repeated_error_count = 0
                self._log_step_end(step_id, decision, last_observation, time.monotonic() - started)
                if (
                    phase_before == stop_after_phase
                    and (last_observation.kind == "phase_finish" or self.state_store.state.current_phase != phase_before)
                    and self._phase_committed(stop_after_phase)
                ):
                    stopped_after_requested_phase = True
                    stopped_normally = True
                    self.run_logger.log(f"Stopping after requested phase `{stop_after_phase}`.")
                    break
                step_index += 1
                if last_observation.kind == "phase_finish" and self.state_store.state.current_phase == phase_before:
                    break
            if stop_after_phase and not stopped_after_requested_phase and not self._phase_committed(stop_after_phase):
                raise AgentRunIncomplete(
                    f"Run stopped before `{stop_after_phase}` was phase_finish_committed. "
                    "Treat this run as failed, even if stage files or finish_signal.json exist."
                )
            if not stopped_normally and step_index > effective_max_steps:
                raise AgentRunIncomplete(
                    f"Run exhausted max_steps={effective_max_steps} before reaching a terminal state. "
                    "Treat this run as failed/incomplete; inspect the current phase trace before increasing the budget."
                )
        except BaseException as exc:
            fatal_error = exc
            self._record_runtime_failure(exc, phase_name=self.state_store.state.current_phase, step_index=step_index)
            raise
        finally:
            self.run_stats.finalize()
            self.run_logger.log(
                "Run finished. "
                f"runtime_sec={self.run_stats.stats.total_runtime_sec} "
                f"steps={self.run_stats.stats.agent_steps} "
                f"llm_calls={self.run_stats.stats.llm_calls} "
                f"prompt_tokens={self.run_stats.stats.total_prompt_tokens} "
                f"completion_tokens={self.run_stats.stats.total_completion_tokens} "
                f"total_tokens={self.run_stats.stats.total_tokens} "
                f"cache_hit={self.run_stats.stats.total_prompt_cache_hit_tokens} "
                f"cache_miss={self.run_stats.stats.total_prompt_cache_miss_tokens}"
            )
            if fatal_error is not None:
                self.run_logger.log(f"Run ended with runtime_failure={type(fatal_error).__name__}: {fatal_error}")

    def _phase_committed(self, phase_name: str) -> bool:
        if not phase_name:
            return False
        for item in self.state_store.state.phase_history:
            if isinstance(item, dict) and str(item.get("phase", "")).strip() == phase_name:
                return True
        return False

    @staticmethod
    def _error_observation_signature(phase_name: str, observation: Observation) -> str:
        payload = observation.payload if isinstance(observation.payload, dict) else {}
        code = ""
        for key in (
            "final_scoring_finish_errors",
            "route_surface_finish_errors",
            "available_gate_finish_errors",
        ):
            items = payload.get(key)
            if isinstance(items, list) and items and isinstance(items[0], dict):
                code = str(items[0].get("code", "")).strip()
                break
        if not code:
            code = str(payload.get("code", "")).strip()
        return f"{phase_name}:{observation.kind}:{observation.summary}:{code}"

    def _decide(self, *, step_id: str, last_observation: Observation) -> AgentDecision:
        state = self.state_store.state
        phase_skill = load_phase_skill(self.skills_dir / STAGE_SKILLS.get(state.current_phase, "orchestrator_global_skill.md"))
        system_prompt = load_global_prompt(self.global_prompt_path)
        evaluation_rules_text = self._evaluation_rules_for_phase(state.current_phase)
        state_summary = render_state_summary(
            state=state,
            last_observation=json.dumps(_compact_value(last_observation.__dict__, char_limit=24_000), ensure_ascii=False, indent=2),
            compact=True,
        )
        memory_text = self._render_stage_memory(state.current_phase)
        user_prompt = render_user_prompt(
            state=state,
            tools_text=self.registry.describe(),
            evaluation_rules_text=evaluation_rules_text,
            state_summary_text=state_summary,
            phase_skill=phase_skill,
            memory_text=memory_text,
        )
        prompt_stats = {
            "system": estimate_text_tokens(system_prompt),
            "tools": estimate_text_tokens(self.registry.describe()),
            "rubric": estimate_text_tokens(evaluation_rules_text),
            "stage_skill": estimate_text_tokens(phase_skill),
            "memory": estimate_text_tokens(memory_text),
            "state_summary": estimate_text_tokens(state_summary),
        }
        prompt_stats["total_est_tokens"] = sum(prompt_stats.values())
        self.run_logger.log(f"{step_id} prompt_sections: {format_json_compact(prompt_stats)}")
        response = self.llm.chat_json(
            PromptBundle(
                system=system_prompt,
                user=user_prompt,
                meta={"task": "artifact_evaluation", "label": state.current_phase, "step_id": step_id, "prompt_section_stats": prompt_stats},
            ),
            max_tokens=self.decision_max_tokens,
        )
        self.run_stats.record_llm_usage(response.get("usage", {}) if isinstance(response, dict) else {})
        return self._parse_decision(response if isinstance(response, dict) else {})

    def _evaluation_rules_for_phase(self, phase_name: str) -> str:
        if phase_name == "final_scoring":
            return load_evaluation_rules(self.evaluation_rules_path)
        return EVIDENCE_COLLECTION_POLICY

    def _render_stage_memory(self, phase_name: str) -> str:
        memory_dir = self.runtime.paths.run_root / "artifacts" / "memory"
        global_memory = _read_optional(memory_dir / "global_memory.md")
        stage_summaries = _read_optional(memory_dir / "stage_summaries.md")
        evidence_index = _read_optional(memory_dir / "evidence_index.md")
        current_stage = _read_optional(memory_dir / "current_stage_memory.md")
        current_stage_steps = self._render_current_stage_steps(phase_name)
        sections = [
            "<memory_policy>",
            "Memory is operational continuity, not a scoring source. Evidence refs point to real files. If a ref is needed, read it with read_file before relying on details.",
            "During the current stage, current_stage_steps.md is the authoritative step-by-step working trace: decisions, tool calls, and tool results are appended there so earlier observations are not lost.",
            "At the end of each evidence stage, write a stage packet, append a concise stage summary, update the evidence index, and replace current_stage_memory.md with the new stage's short working memory. The runtime archives current_stage_steps.md to the finished stage and clears it for the next stage.",
            "</memory_policy>",
            "",
            "<global_review_memory>",
            global_memory or "(empty)",
            "</global_review_memory>",
            "",
            "<completed_stage_summaries>",
            stage_summaries or "(empty)",
            "</completed_stage_summaries>",
            "",
            "<evidence_index>",
            evidence_index or "(empty)",
            "</evidence_index>",
            "",
            f"<current_stage_memory phase=\"{phase_name}\">",
            current_stage or "(empty)",
            "</current_stage_memory>",
            "",
            f"<current_stage_step_trace phase=\"{phase_name}\">",
            current_stage_steps or "(empty)",
            "</current_stage_step_trace>",
        ]
        audit_context = audit_context_for_prompt(self.state_store.state, phase_name=phase_name)
        if audit_context:
            sections.extend(["", audit_context])
        return "\n".join(sections)

    def _parse_decision(self, obj: dict[str, Any]) -> AgentDecision:
        tool_calls: list[ToolCall] = []
        raw_calls = obj.get("tool_calls")
        if isinstance(raw_calls, list):
            for raw in raw_calls:
                call = _parse_tool_call(raw)
                if call is not None:
                    tool_calls.append(call)
        elif isinstance(obj.get("tool_call"), dict):
            call = _parse_tool_call(obj.get("tool_call"))
            if call is not None:
                tool_calls.append(call)
        next_action = str(obj.get("next_action", "")).strip()
        if next_action in {"tool_calls", "tools"} and tool_calls:
            next_action = "tool_call"
        if not next_action:
            next_action = "tool_call" if tool_calls else "protocol_error"
        return AgentDecision(
            thought=str(obj.get("thought", "")).strip(),
            next_action=next_action,
            tool_call=tool_calls[0] if tool_calls else None,
            tool_calls=tool_calls,
            phase_finish=obj.get("phase_finish") if isinstance(obj.get("phase_finish"), dict) else None,
            state_updates=obj.get("state_updates") if isinstance(obj.get("state_updates"), dict) else {},
            memory_note=str(obj.get("memory_note", "")).strip(),
            questions=obj.get("questions") if isinstance(obj.get("questions"), list) else [],
            usage=obj.get("usage") if isinstance(obj.get("usage"), dict) else {},
            raw_response={key: value for key, value in obj.items() if key != "usage"},
        )

    def _execute_tool_queue(self, step_id: str, tool_calls: list[ToolCall]) -> Observation:
        observations: list[Observation] = []
        for index, call in enumerate(tool_calls, start=1):
            observations.append(self._execute_tool(f"{step_id}-tool-{index:02d}", call))
            if observations[-1].kind == "phase_finish":
                break
        if len(observations) == 1:
            return observations[0]
        return Observation(
            kind="tool_queue",
            summary=f"Executed {len(observations)} tool call(s).",
            payload={"observations": [obs.__dict__ for obs in observations]},
        )

    def _execute_tool(self, step_id: str, tool_call: ToolCall) -> Observation:
        phase_name = self.state_store.state.current_phase
        tool_call = self._normalize_tool_call_args(tool_call)
        self.run_stats.record_tool_call()
        precheck = self._precheck_tool_call(tool_call)
        if precheck is not None:
            self.run_stats.record_tool_error()
            self.memory.record_tool_error(tool_name=tool_call.name, args=tool_call.args, error=precheck.summary, data=precheck.payload, summary=precheck.summary)
            self.trajectory.append(phase=phase_name, event_type="tool_error", step_id=step_id, summary=precheck.summary, payload=precheck.payload)
            self._append_step_trace_tool_observation(step_id, tool_call, precheck)
            return precheck
        try:
            result = self.registry.invoke(tool_call.name, tool_call.args)
        except ToolError as exc:
            self.run_stats.record_tool_error()
            observation = Observation(kind="tool_error", summary=f"{tool_call.name} failed: {exc}", payload={"error": str(exc), "data": exc.data})
            self.memory.record_tool_error(tool_name=tool_call.name, args=tool_call.args, error=str(exc), data=exc.data, summary=observation.summary)
            self.trajectory.append(phase=phase_name, event_type="tool_error", step_id=step_id, summary=observation.summary, payload=observation.payload)
            self._append_step_trace_tool_observation(step_id, tool_call, observation)
            return observation
        except Exception as exc:
            self.run_stats.record_tool_error()
            payload = {
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=8),
                "tool": tool_call.name,
                "args": tool_call.args,
                "agent_action_required": "treat_as_runtime_tool_failure_and_choose_recovery_or_record_blocker",
            }
            observation = Observation(kind="tool_error", summary=f"{tool_call.name} raised unexpected runtime error: {type(exc).__name__}: {exc}", payload=payload)
            self.memory.record_tool_error(tool_name=tool_call.name, args=tool_call.args, error=payload["error"], data=payload, summary=observation.summary)
            self.trajectory.append(phase=phase_name, event_type="tool_error", step_id=step_id, summary=observation.summary, payload=observation.payload)
            self._append_step_trace_tool_observation(step_id, tool_call, observation)
            return observation
        if not result.ok:
            self.run_stats.record_tool_error()
        self._sync_state_from_tool_result(result)
        self.memory.record_tool_result(tool_name=tool_call.name, args=tool_call.args, data=result.data, summary=result.summary)
        kind = "phase_finish" if tool_call.name == "phase_finish" and result.ok else "tool_result"
        observation = Observation(kind=kind, summary=result.summary, payload={"tool": tool_call.name, "ok": result.ok, "data": result.data, "error": result.error})
        self.trajectory.append(phase=phase_name, event_type=kind, step_id=step_id, summary=result.summary, payload=observation.payload)
        self._append_step_trace_tool_observation(step_id, tool_call, observation)
        if kind == "phase_finish":
            return self._commit_phase_finish(step_id, result)
        return observation

    def _normalize_tool_call_args(self, tool_call: ToolCall) -> ToolCall:
        if tool_call.name != "load_run_config":
            return tool_call
        args = dict(tool_call.args if isinstance(tool_call.args, dict) else {})
        shared = self.state_store.state.shared_context if isinstance(self.state_store.state.shared_context, dict) else {}
        for shared_key, arg_key in (("config_path", "config_path"), ("paper_source", "paper_source"), ("manual_paper_targets", "manual_paper_targets")):
            if not str(args.get(arg_key, "") or "").strip():
                value = str(shared.get(shared_key, "") or "").strip()
                if value:
                    args[arg_key] = value
        return ToolCall(name=tool_call.name, args=args)

    def _sync_state_from_tool_result(self, result: Any) -> None:
        data = result.data if hasattr(result, "data") and isinstance(result.data, dict) else {}
        if result.tool_name == "load_run_config":
            self.state_store.merge_shared_context({"run_config": data.get("run_config", {}), "config_path": data.get("config_path", "")})
        if result.tool_name == "get_paper_info":
            profile = data.get("paper_source_profile", {})
            self.state_store.merge_shared_context({"paper_source_profile": profile})
        if result.tool_name in {"load_run_config", "get_paper_info", "list_repo_tree", "read_file", "write_file", "run_command"}:
            self._record_tool_fact(result.tool_name, data, result.summary)
        if result.tool_name == "write_file":
            relative = str(data.get("relative_path", "")).strip()
            if relative.startswith("artifacts/"):
                self.state_store.sync_phase_artifacts_from_disk()
        if result.tool_name == "run_command":
            self.state_store.sync_phase_artifacts_from_disk()

    def _finish_phase(self, step_id: str, finish_payload: dict[str, Any]) -> Observation:
        call = ToolCall("phase_finish", finish_payload)
        return self._execute_tool(step_id + "-finish", call)

    def _precheck_tool_call(self, tool_call: ToolCall) -> Observation | None:
        if tool_call.name != "run_command":
            return None
        command = str(tool_call.args.get("command", "")).strip()
        system_install_rejection = self._system_install_rejection(
            command,
            allow_system_package_install=self.allow_system_package_install,
            has_intervention=bool(tool_call.args.get("intervention")),
        )
        if system_install_rejection:
            return Observation(
                kind="tool_error",
                summary=str(system_install_rejection["summary"]),
                payload={
                    "code": system_install_rejection["code"],
                    "command": command,
                    "required_action": system_install_rejection["required_action"],
                },
            )
        cwd = str(tool_call.args.get("cwd", ".")).strip() or "."
        if self._looks_like_helper_script_copy_into_repo(command, cwd):
            return Observation(
                kind="tool_error",
                summary="run_command rejected: reviewer helper scripts must stay under the run root and be executed by absolute path; do not copy them into the artifact repository.",
                payload={
                    "code": "helper_script_copy_into_repo",
                    "command": command,
                    "required_action": "Run the helper script from artifacts/D*/ by absolute path, or write outputs under the run root. Do not cp reviewer-created scripts into the artifact repo.",
                },
            )
        return None

    @staticmethod
    def _system_install_rejection(command: str, *, allow_system_package_install: bool, has_intervention: bool) -> dict[str, str] | None:
        text = command.strip()
        lowered = text.lower()
        if re.search(r"(^|[;&|]\s*)sudo(\s|$)", lowered):
            return {
                "code": "privileged_system_install",
                "summary": "run_command rejected: sudo is not allowed in artifact review runs.",
                "required_action": "Use a non-sudo command, a user/run-root environment, a containerized environment, or record the dependency as resource-gated.",
            }
        if re.search(r"\bcurl\b[^\n|;&]*\|\s*(?:sh|bash)\b", lowered):
            return {
                "code": "pipe_installer",
                "summary": "run_command rejected: curl-to-shell installers are not allowed in artifact review runs.",
                "required_action": "Use a package-manager or run-root install with explicit intervention evidence, or record the dependency as resource-gated.",
            }
        if re.search(r"\bwget\b[^\n|;&]*\|\s*(?:sh|bash)\b", lowered):
            return {
                "code": "pipe_installer",
                "summary": "run_command rejected: wget-to-shell installers are not allowed in artifact review runs.",
                "required_action": "Use a package-manager or run-root install with explicit intervention evidence, or record the dependency as resource-gated.",
            }
        package_actions = list(re.finditer(r"\b(?P<manager>apt|apt-get|dnf|yum|pacman|brew)\s+(?P<action>install|update|upgrade)\b", lowered))
        if not package_actions:
            return None
        if not allow_system_package_install:
            return {
                "code": "system_package_install_disabled",
                "summary": "run_command rejected: system package-manager commands are disabled unless explicitly enabled for the review run.",
                "required_action": "Set AE_AGENT_ALLOW_SYSTEM_PACKAGE_INSTALL=1 for controlled non-sudo package-manager installs, use a run-root/user environment, or record the dependency as resource-gated.",
            }
        if not has_intervention:
            return {
                "code": "system_package_install_missing_intervention",
                "summary": "run_command rejected: system package-manager commands require an explicit intervention object.",
                "required_action": "Retry with a structured intervention explaining the dependency being installed, semantic preservation, affected stage, and evidence refs.",
            }
        for match in package_actions:
            manager = match.group("manager")
            action = match.group("action")
            segment = ArtifactEvaluationAgent._shell_segment_from(lowered, match.start())
            if action == "upgrade":
                return {
                    "code": "system_package_upgrade_disallowed",
                    "summary": "run_command rejected: system package upgrades are not allowed in artifact review runs.",
                    "required_action": "Install only the missing declared/observed toolchain package, use a run-root/user environment, or record the dependency as resource-gated.",
                }
            if action == "update" and manager not in {"apt", "apt-get"}:
                return {
                    "code": "system_package_update_disallowed",
                    "summary": "run_command rejected: package update commands that may upgrade host packages are not allowed.",
                    "required_action": "Run only a metadata refresh that is known not to upgrade packages, install the missing package directly, or record the dependency as resource-gated.",
                }
            if action == "install" and not ArtifactEvaluationAgent._package_install_is_noninteractive(manager, segment):
                return {
                    "code": "system_package_install_not_noninteractive",
                    "summary": "run_command rejected: system package installs must be explicitly non-interactive.",
                    "required_action": "Retry with non-interactive install flags such as apt-get/yum/dnf -y or pacman --noconfirm, keep sudo disabled, and include intervention evidence.",
                }
        return None

    @staticmethod
    def _shell_segment_from(command: str, start: int) -> str:
        remainder = command[start:]
        stop = re.search(r"\s*(?:&&|\|\||;|\n)", remainder)
        return remainder[: stop.start()] if stop else remainder

    @staticmethod
    def _package_install_is_noninteractive(manager: str, command_segment: str) -> bool:
        if manager in {"apt", "apt-get", "dnf", "yum"}:
            return bool(re.search(r"(^|\s)(?:-y|--yes|--assume-yes)(\s|$)", command_segment))
        if manager == "pacman":
            return bool(re.search(r"(^|\s)--noconfirm(\s|$)", command_segment))
        if manager == "brew":
            return True
        return False

    def _looks_like_helper_script_copy_into_repo(self, command: str, cwd: str) -> bool:
        if not command:
            return False
        run_root = re.escape(str(self.runtime.paths.run_root.resolve()))
        repo_root = re.escape(str(self.runtime.repo_root.resolve()))
        helper_source = rf"(?:{run_root}/)?artifacts/[^\s;&|]+[.](?:py|sh|R|jl|ipynb)"
        cp_to_repo_abs = rf"\bcp\s+{helper_source}\s+{repo_root}(?:/|\b)"
        cp_to_repo_relative = rf"\bcp\s+{helper_source}\s+(?:[.]/)?(?:[A-Za-z0-9_.-]+[.](?:py|sh|R|jl)|[.])(?:\s|;|&&|\|\||$)"
        if re.search(cp_to_repo_abs, command):
            return True
        if re.search(cp_to_repo_relative, command) and self._command_cwd_is_repo(cwd):
            return True
        return False

    def _command_cwd_is_repo(self, cwd_text: str) -> bool:
        cwd = cwd_text.strip() or "."
        raw = Path(cwd)
        candidate = raw.resolve() if raw.is_absolute() else (self.runtime.repo_root / raw).resolve()
        try:
            candidate.relative_to(self.runtime.repo_root.resolve())
            return True
        except Exception:
            return False

    def _record_tool_fact(self, tool_name: str, data: dict[str, Any], summary: str) -> None:
        state = self.state_store.state
        facts = state.shared_context.get("tool_facts")
        if not isinstance(facts, dict):
            facts = {}
        current_phase = state.current_phase
        phase_facts = facts.get(current_phase)
        if not isinstance(phase_facts, list):
            phase_facts = []
        entry: dict[str, Any] = {"tool": tool_name, "summary": summary}
        if tool_name == "get_paper_info":
            profile = data.get("paper_source_profile", {})
            if isinstance(profile, dict):
                entry.update(
                    {
                        "paper_loaded": profile.get("paper_loaded"),
                        "text_chars": profile.get("text_chars"),
                        "text_path": profile.get("text_path"),
                        "profile_path": profile.get("profile_path"),
                    }
                )
        elif tool_name == "list_repo_tree":
            entry.update({"subpath": data.get("subpath"), "entry_count": len(data.get("entries", []) if isinstance(data.get("entries"), list) else [])})
        elif tool_name == "read_file":
            files = data.get("files", []) if isinstance(data.get("files"), list) else []
            entry.update({"base": data.get("base"), "paths": [item.get("path") for item in files if isinstance(item, dict)]})
        elif tool_name == "write_file":
            entry.update({"relative_path": data.get("relative_path")})
        elif tool_name == "run_command":
            entry.update({"command_id": data.get("command_id"), "final_status": data.get("final_status"), "stdout_path": data.get("stdout_path"), "stderr_path": data.get("stderr_path")})
        phase_facts.append(entry)
        facts[current_phase] = phase_facts[-12:]
        self.state_store.merge_shared_context({"tool_facts": facts})

    def _commit_phase_finish(self, step_id: str, result: Any) -> Observation:
        state = self.state_store.state
        signal = result.data.get("phase_finish_signal", {}) if isinstance(result.data, dict) else {}
        phase_name = str(signal.get("phase_name", "")).strip()
        if phase_name != state.current_phase:
            return Observation(
                kind="tool_error",
                summary=f"phase_finish rejected: requested `{phase_name}` while current phase is `{state.current_phase}`.",
                payload={"phase_finish_signal": signal},
            )
        self.state_store.sync_phase_artifacts_from_disk()
        missing = self.state_store.missing_required_artifacts(phase_name)
        if phase_name == "run_initialization":
            missing.extend(self._missing_initialization_outputs())
        if phase_name == "report_generation":
            missing.extend(self._missing_report_outputs())
        if missing:
            diagnostics = self.state_store.required_artifact_diagnostics(phase_name)
            invalid = [item for item in diagnostics if item.get("status") == "invalid_json"]
            if invalid:
                summary = (
                    "phase_finish rejected: required stage JSON file exists but is not parseable. "
                    "Rewrite the invalid JSON artifact, then call phase_finish again."
                )
            else:
                summary = f"phase_finish rejected: missing required stage files {missing}. Write them first, then call phase_finish again."
            return Observation(
                kind="tool_error",
                summary=summary,
                payload={
                    "missing_required_artifacts": missing,
                    "required_artifact_diagnostics": diagnostics,
                    "required": PHASE_REQUIRED_ARTIFACTS.get(phase_name, ()),
                },
            )
        route_surface_errors = self._route_surface_finish_errors(phase_name)
        if phase_name in {"D1_documentation", "D3_executability", "D5_reusability"}:
            self._record_route_surface_audit_warnings(phase_name, route_surface_errors)
        available_gate_errors = self._available_gate_finish_errors(phase_name)
        if available_gate_errors:
            self._record_available_gate_audit_warnings(available_gate_errors)
        self._final_scoring_finish_errors(phase_name)
        d4_gate_errors = self._d4_functional_gate_finish_errors(phase_name)
        if d4_gate_errors:
            return Observation(
                kind="tool_error",
                summary=(
                    "phase_finish rejected: D4 stage_packet.json must include a rubric-grounded "
                    "functional_predicate and d5_entry_gate before D4 can finish."
                ),
                payload={"d4_functional_gate_finish_errors": d4_gate_errors},
            )
        audit_observation = self._run_evidence_audit_before_commit(step_id=step_id, phase_name=phase_name)
        if audit_observation is not None:
            return audit_observation
        next_phase = self._next_phase_after_commit(phase_name)
        step_trace_ref = self._archive_current_stage_steps(phase_name)
        stage_packet = self._read_stage_packet_for_memory(phase_name)
        self.state_store.finish_phase(phase_name, signal, next_phase)
        self.memory.mark_phase_status(phase_name, "completed")
        self.memory.replace_tail_with_phase_summary(
            phase_name=phase_name,
            next_phase=next_phase,
            finish_signal=signal,
            phase_summary=stage_packet,
            artifact_refs=self._stage_refs(phase_name),
        )
        if next_phase:
            self._reset_current_stage_memory(next_phase)
        summary = f"Finished `{phase_name}`; next phase is `{next_phase or 'none'}`."
        self.trajectory.append(phase=phase_name, event_type="phase_finish_committed", step_id=step_id, summary=summary, payload={"next_phase": next_phase, "step_trace_ref": step_trace_ref})
        return Observation(kind="phase_finish", summary=summary, payload={"next_phase": next_phase, "phase_finish_signal": signal, "step_trace_ref": step_trace_ref})

    def _next_phase_after_commit(self, phase_name: str) -> str | None:
        if phase_name == "D4_claim_support":
            status = self._d4_functional_gate_runtime_status()
            self.state_store.merge_shared_context({D5_FUNCTIONAL_SKIP_SHARED_KEY: status})
            if status.get("skip_d5") is True:
                self._write_skipped_d5_packet_from_d4_gate(status)
                return "final_scoring"
            return next_phase_name(phase_name)
        if phase_name != "available_gate":
            return next_phase_name(phase_name)
        status = self._available_gate_runtime_status()
        if status.get("short_circuit") is True:
            self.state_store.merge_shared_context({AVAILABLE_SHORT_CIRCUIT_SHARED_KEY: status})
            self._write_no_badge_final_packet_from_available_gate(status)
            return "report_generation"
        self.state_store.merge_shared_context({AVAILABLE_SHORT_CIRCUIT_SHARED_KEY: status})
        return next_phase_name(phase_name)

    def _d4_functional_gate_runtime_status(self) -> dict[str, Any]:
        packet_ref = "artifacts/D4_claim_support/stage_packet.json"
        packet = self._read_json_object(self.runtime.paths.run_root / packet_ref)
        predicate = packet.get("functional_predicate") if isinstance(packet, dict) else {}
        gate = packet.get("d5_entry_gate") if isinstance(packet, dict) else {}
        predicate = predicate if isinstance(predicate, dict) else {}
        gate = gate if isinstance(gate, dict) else {}

        predicate_holds = predicate.get("holds")
        gate_proceed = gate.get("proceed")
        d5_next_action = str(predicate.get("d5_next_action") or gate.get("next_action") or "").strip()
        explicit_skip = (
            predicate_holds is False
            or gate_proceed is False
            or d5_next_action == "skip_D5_go_to_final_scoring"
        )
        explicit_proceed = (
            predicate_holds is True
            or gate_proceed is True
            or d5_next_action == "proceed_to_D5"
        )
        reason = str(predicate.get("reason") or gate.get("reason") or "").strip()
        blocking_reasons = predicate.get("blocking_reasons", [])
        if not isinstance(blocking_reasons, list):
            blocking_reasons = [str(blocking_reasons)]
        if explicit_skip:
            runtime_reason = reason or "D4 Functional predicate is false; D5 requires Functional as a prerequisite."
            skip_d5 = True
        elif explicit_proceed:
            runtime_reason = reason or "D4 Functional predicate allows D5 to proceed."
            skip_d5 = False
        else:
            runtime_reason = (
                "D4 did not provide an explicit Functional predicate/gate; runtime treats the D5 prerequisite as not satisfied."
            )
            skip_d5 = True
        return {
            "schema": "d4_functional_gate_runtime_status_v1",
            "skip_d5": skip_d5,
            "functional_predicate_holds": predicate_holds if isinstance(predicate_holds, bool) else None,
            "d5_entry_gate_proceed": gate_proceed if isinstance(gate_proceed, bool) else None,
            "d5_next_action": d5_next_action,
            "reason": runtime_reason,
            "blocking_reasons": blocking_reasons,
            "refs": [packet_ref],
        }

    def _d4_functional_gate_finish_errors(self, phase_name: str) -> list[dict[str, Any]]:
        if phase_name != "D4_claim_support":
            return []
        packet_ref = "artifacts/D4_claim_support/stage_packet.json"
        packet = self._read_json_object(self.runtime.paths.run_root / packet_ref)
        errors: list[dict[str, Any]] = []
        predicate = packet.get("functional_predicate") if isinstance(packet, dict) else None
        gate = packet.get("d5_entry_gate") if isinstance(packet, dict) else None
        if not isinstance(predicate, dict):
            errors.append(
                {
                    "code": "missing_functional_predicate",
                    "message": "D4 stage_packet.json lacks functional_predicate.",
                    "required_action": (
                        "Rewrite artifacts/D4_claim_support/stage_packet.json with functional_predicate using "
                        "the rubric conditions: Available, D2>=WA, D3>=WA, D4>=WA, RW1-RW5, E2/E3, not I4, "
                        "and I3 semantic-preserving rationale when applicable."
                    ),
                    "refs": [packet_ref],
                }
            )
        else:
            holds = predicate.get("holds")
            if not isinstance(holds, bool):
                errors.append(
                    {
                        "code": "functional_predicate_holds_not_boolean",
                        "message": "functional_predicate.holds must be a boolean.",
                        "required_action": "Set functional_predicate.holds to true or false and cite condition evidence.",
                        "refs": [packet_ref],
                    }
                )
            conditions = predicate.get("conditions")
            if not isinstance(conditions, list) or not conditions:
                errors.append(
                    {
                        "code": "functional_predicate_conditions_missing",
                        "message": "functional_predicate.conditions is missing or empty.",
                        "required_action": "List each Functional condition with satisfied/evidence/refs.",
                        "refs": [packet_ref],
                    }
                )
            if holds is False and not predicate.get("blocking_reasons"):
                errors.append(
                    {
                        "code": "functional_predicate_false_without_blocking_reason",
                        "message": "functional_predicate.holds is false but blocking_reasons is empty.",
                        "required_action": "Name the exact failed Functional condition(s).",
                        "refs": [packet_ref],
                    }
                )
            next_action = str(predicate.get("d5_next_action") or "").strip()
            if next_action not in {"proceed_to_D5", "skip_D5_go_to_final_scoring"}:
                errors.append(
                    {
                        "code": "functional_predicate_missing_d5_next_action",
                        "message": "functional_predicate.d5_next_action must tell the runtime whether to run D5.",
                        "required_action": "Set d5_next_action to proceed_to_D5 or skip_D5_go_to_final_scoring.",
                        "refs": [packet_ref],
                    }
                )
        if not isinstance(gate, dict):
            errors.append(
                {
                    "code": "missing_d5_entry_gate",
                    "message": "D4 stage_packet.json lacks d5_entry_gate.",
                    "required_action": "Add d5_entry_gate with proceed, reason, source=functional_predicate, and refs.",
                    "refs": [packet_ref],
                }
            )
        elif not isinstance(gate.get("proceed"), bool):
            errors.append(
                {
                    "code": "d5_entry_gate_proceed_not_boolean",
                    "message": "d5_entry_gate.proceed must be a boolean.",
                    "required_action": "Set d5_entry_gate.proceed consistently with functional_predicate.holds.",
                    "refs": [packet_ref],
                }
            )
        return errors

    def _write_skipped_d5_packet_from_d4_gate(self, status: dict[str, Any]) -> None:
        run_root = self.runtime.paths.run_root
        packet = {
            "schema": "d5_stage_packet_v2",
            "stage": "D5_reusability",
            "stage_goal": "Collect and validate reuse evidence only when Functional prerequisite holds.",
            "status": "skipped",
            "summary": "D5 was skipped because D4's rubric-grounded Functional predicate did not hold.",
            "skip_reason": status.get("reason", ""),
            "functional_prerequisite_evidence": {
                "d4_stage_ref": "artifacts/D4_claim_support/stage_packet.json",
                "functional_predicate_holds": status.get("functional_predicate_holds"),
                "prerequisite_status": "blocked",
                "blocking_reasons": status.get("blocking_reasons", []),
            },
            "route_scope": "configured_repo_only",
            "artifact_universe_ref": "artifacts/run_initialization/artifact_universe.json",
            "artifact_boundary_warnings": [],
            "core_surface_ref": "",
            "core_surface_summary": "Core surface analysis was not attempted because Functional prerequisite did not hold.",
            "reuse_information": {
                "input_documented": "unknown",
                "output_documented": "unknown",
                "dependencies_documented": "unknown",
                "invocation_documented": "unknown",
                "reuse_boundary_documented": "unknown",
                "refs": [],
            },
            "reuse_plan_ref": "",
            "transfer_validation": {
                "attempted": False,
                "gate_passed": False,
                "succeeded": False,
                "selected_candidate": "",
                "driver_ref": "",
                "input_refs": [],
                "command": "",
                "command_refs": [],
                "driver_or_input_refs": [],
                "output_refs": [],
                "d3_d5_separation": "",
                "failure_location": "not_attempted",
                "failure_class": "not_attempted_prerequisite_missing",
                "failure_cause": status.get("reason", ""),
                "attempts": 0,
                "repair_attempted": False,
                "repair_description": "",
                "repair_result": "not_attempted",
                "transfer_result_interpretation": "not_attempted_prerequisite_missing",
            },
            "resource_boundary": {
                "type": "unknown",
                "blocks_all_routes": False,
                "artifact_logic_reached": "not_applicable",
                "reason": "D5 was skipped before reuse validation.",
                "evidence_refs": ["artifacts/D4_claim_support/stage_packet.json"],
            },
            "classifications": {
                "reuse_target_level": "unknown",
                "transfer_novelty": "no_transfer",
                "validated_reuse_surface": "no_validated_surface",
                "relationship_to_d3": "unclear",
                "core_reuse_validation_result": "not_attempted_prerequisite_missing",
            },
            "d5_transfer_sufficiency_check": {
                "reuse_target_level": "unknown",
                "transfer_novelty": "no_transfer",
                "relationship_to_d3": "unclear",
                "validated_reuse_surface": "no_validated_surface",
                "artifact_level_sufficiency": "insufficient",
                "transfer_failure_interpretation": "not_attempted_prerequisite_missing",
                "core_reuse_validation_result": "not_attempted_prerequisite_missing",
                "reviewer_created_validation": {
                    "created": False,
                    "driver_or_test_ref": "",
                    "input_ref": "",
                    "command_ref": "",
                    "output_refs": [],
                    "modifies_core_logic": False,
                    "harness_attribution": "not_attempted_prerequisite_missing",
                },
                "same_workflow_variant_check": {
                    "same_script_or_mode_as_d3": False,
                    "only_parameter_or_config_change": False,
                    "only_path_or_relocation_change": False,
                    "only_same_task_new_input": False,
                    "reviewer_owned_new_context": False,
                    "conclusion": "D5 was not attempted because Functional prerequisite did not hold.",
                },
                "field_conflicts": [],
            },
            "route_accounting": [
                {
                    "route_id": "",
                    "status": "not_applicable",
                    "d5_candidate": "",
                    "reason": "D5 skipped because Functional prerequisite did not hold.",
                }
            ],
            "commands": [],
            "outputs_observed": [],
            "output_snapshots": [],
            "interventions": [],
            "limitations": status.get("blocking_reasons", []),
            "handoff": {
                "d5_skipped": True,
                "skip_reason": status.get("reason", ""),
                "core_surface": "",
                "transfer_attempted": False,
                "transfer_succeeded": False,
                "d3_d5_separation": "",
                "validated_surface": "",
                "failure_summary": status.get("reason", ""),
                "unvalidated_surfaces": [],
                "validation_refs": [],
                "confirmed_limitations": status.get("blocking_reasons", []),
                "transfer_sufficiency_semantics": {},
                "same_workflow_variant_check": {},
            },
            "evidence_refs": ["artifacts/D4_claim_support/stage_packet.json"],
        }
        write_json(run_root / "artifacts" / "D5_reusability" / "stage_packet.json", packet)
        self.state_store.sync_phase_artifacts_from_disk()

    def _available_gate_runtime_status(self) -> dict[str, Any]:
        packet = self._read_json_object(self.runtime.paths.run_root / "artifacts" / "available_gate" / "stage_packet.json")
        gc = self._extract_available_gate_gc_status(packet)
        failed = sorted(key for key, value in gc.items() if value == "fail")
        uncertain = sorted(key for key, value in gc.items() if value == "uncertain")
        missing = sorted(key for key in ("GC1", "GC2", "GC3") if key not in gc)
        short_circuit = bool(failed)
        reason = (
            "Available gate hard short-circuit: at least one GC1-GC3 condition is explicitly failed."
            if short_circuit
            else "Available gate did not hard short-circuit because no GC condition has an explicit fail status."
        )
        return {
            "schema": "available_gate_runtime_status_v1",
            "short_circuit": short_circuit,
            "failed_conditions": failed,
            "uncertain_conditions": uncertain,
            "missing_conditions": missing,
            "gc_status": gc,
            "reason": reason,
            "refs": ["artifacts/available_gate/stage_packet.json"],
        }

    def _extract_available_gate_gc_status(self, packet: dict[str, Any]) -> dict[str, str]:
        if not isinstance(packet, dict):
            return {}
        status: dict[str, str] = {}
        status.update(self._extract_gc_status_from_conditions(packet.get("conditions")))
        status.update(self._extract_gc_status_from_conditions(packet.get("available_gate", {}).get("conditions") if isinstance(packet.get("available_gate"), dict) else None))
        if "GC1" not in status:
            source = packet.get("source_accessibility")
            if isinstance(source, dict):
                status["GC1"] = self._status_from_source_accessibility(source)
        if "GC2" not in status:
            gc2 = packet.get("gc2_entry_document_assessment")
            if isinstance(gc2, dict):
                status["GC2"] = self._status_from_assessment(gc2.get("evidence_status") or gc2.get("decision") or gc2.get("status"))
        if "GC3" not in status:
            paper = packet.get("paper_connection")
            if isinstance(paper, dict):
                status["GC3"] = self._status_from_paper_connection(paper)
        return {key: value for key, value in status.items() if key in {"GC1", "GC2", "GC3"} and value in {"pass", "fail", "uncertain"}}

    def _extract_gc_status_from_conditions(self, conditions: Any) -> dict[str, str]:
        if not isinstance(conditions, list):
            return {}
        status: dict[str, str] = {}
        for item in conditions:
            if not isinstance(item, dict):
                continue
            name = str(item.get("condition", item.get("id", item.get("name", "")))).upper()
            matched = next((gc for gc in ("GC1", "GC2", "GC3") if gc in name), "")
            if not matched:
                continue
            if isinstance(item.get("satisfied"), bool):
                status[matched] = "pass" if item.get("satisfied") else "fail"
                continue
            value = item.get("evidence_status", item.get("decision", item.get("status", "")))
            normalized = self._status_from_assessment(value)
            if normalized:
                status[matched] = normalized
        return status

    @staticmethod
    def _status_from_assessment(value: Any) -> str:
        text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        if text in {"pass", "passed", "yes", "true", "available", "satisfied"}:
            return "pass"
        if text in {"fail", "failed", "no", "false", "not_satisfied", "missing", "absent"}:
            return "fail"
        if text in {"uncertain", "unknown", "ambiguous", "conflict", "conflicted", "partial"}:
            return "uncertain"
        return ""

    def _status_from_source_accessibility(self, source: dict[str, Any]) -> str:
        bool_keys = ("source_readable", "readable", "source_available", "downloadable", "cloneable")
        values = [source.get(key) for key in bool_keys if key in source]
        if any(value is True for value in values):
            return "pass"
        if any(value is False for value in values):
            return "fail"
        status = self._status_from_assessment(source.get("status"))
        return status or "uncertain"

    def _status_from_paper_connection(self, paper: dict[str, Any]) -> str:
        negative_values = []
        for key in ("artifact_covers_paper_claims", "artifact_paper_consistency", "status"):
            value = str(paper.get(key, "")).strip().lower()
            if value:
                negative_values.append(value)
        if any(value in {"unrelated", "inconsistent", "fail", "failed", "no"} for value in negative_values):
            return "fail"
        if any(paper.get(key) is True for key in ("paper_cites_artifact", "artifact_references_paper", "paper_has_method_reference")):
            return "pass"
        if self._status_from_assessment(paper.get("status")):
            return self._status_from_assessment(paper.get("status"))
        if paper.get("connection_evidence") or paper.get("paper_text_ref"):
            return "pass"
        return "uncertain"

    def _write_no_badge_final_packet_from_available_gate(self, short_circuit_status: dict[str, Any]) -> None:
        run_root = self.runtime.paths.run_root
        packet_path = run_root / "artifacts" / "final_scoring" / "final_scoring_packet.json"
        if packet_path.exists():
            return
        ensure_dir(packet_path.parent)
        failed = short_circuit_status.get("failed_conditions", [])
        reason = "Available gate failed before D1-D5 evaluation; D1-D5 were intentionally skipped."
        if failed:
            reason = f"Available gate failed on {', '.join(str(item) for item in failed)}; D1-D5 were intentionally skipped."
        conditions = [
            {
                "condition": gc,
                "satisfied": short_circuit_status.get("gc_status", {}).get(gc) == "pass",
                "evidence": f"Runtime available-gate status: {short_circuit_status.get('gc_status', {}).get(gc, 'missing')}.",
                "refs": ["artifacts/available_gate/stage_packet.json"],
            }
            for gc in ("GC1", "GC2", "GC3")
        ]
        skipped_checklist = [
            {
                "item": "Dimension evaluation",
                "judgment": "not_applicable",
                "reason": "Available gate failed; dimension not evaluated.",
                "refs": ["artifacts/available_gate/stage_packet.json"],
            }
        ]
        skipped_score = {
            "score": "R",
            "score_reason": "Skipped because Available gate did not hold.",
            "blocking_or_limiting_items": ["Available gate failed; dimension not evaluated."],
            "evidence_refs": ["artifacts/available_gate/stage_packet.json"],
            "reason": "Skipped because Available gate did not hold.",
            "refs": ["artifacts/available_gate/stage_packet.json"],
        }
        packet = {
            "schema": "final_scoring_packet_v1",
            "status": "complete",
            "rubric_source": {"name": "artifactguide", "refs": ["artifactguide/rubric_overview.md"]},
            "available_gate": {"holds": False, "reason": reason, "conditions": conditions},
            "dimension_checklists": {dim: list(skipped_checklist) for dim in ("D1", "D2", "D3", "D4", "D5")},
            "dimension_scores": {dim: dict(skipped_score) for dim in ("D1", "D2", "D3", "D4", "D5")},
            "evidence_tier": {"tier": "E0", "reason": "No D1-D5 execution evidence collected after Available gate short-circuit.", "refs": ["artifacts/available_gate/stage_packet.json"]},
            "e2_sufficiency_check": {
                "workflow_kind": "no_runnable_workflow",
                "output_provenance": "no_valid_output",
                "representative_workflow_assessment": {"skipped": True, "reason": "Available gate failed."},
                "upstream_generation_attempted": "unknown",
                "resource_boundary_position": "unknown",
                "artifact_specific_logic_exercised": "unknown",
                "paper_facing_output": "unknown",
                "generic_dependency_or_example": False,
                "component_only": False,
                "functional_representativeness_blocker": "Available gate failed before D3.",
                "sufficiency_reason": "E2 evidence is unavailable because the artifact did not pass GC1-GC3.",
                "score_or_badge_effect": "blocks_functional",
            },
            "route_arbitration_check": {
                "d3_selected_route_decisiveness": "unclear",
                "d3_alternative_routes_considered": "Skipped before D1-D5 because Available gate failed.",
                "d3_score_effect": "D3 skipped.",
                "d5_selected_transfer_decisiveness": "unclear",
                "d5_alternative_transfers_considered": "Skipped before D1-D5 because Available gate failed.",
                "d5_score_effect": "D5 skipped.",
                "human_review_sensitive": False,
            },
            "resource_boundary_assessment": {
                "boundary_type": "none",
                "declared_by_artifact": "unknown",
                "position": "unknown",
                "artifact_logic_reached": "unknown",
                "paper_facing_output_before_boundary": "unknown",
                "blocks_all_reviewer_realistic_routes": "unknown",
                "alternative_route_available": "unknown",
                "alternative_evidence_exists": "unknown",
                "badge_effect": "available_gate_failed",
                "reason": "No resource-boundary evaluation was performed after Available gate failed.",
                "refs": ["artifacts/available_gate/stage_packet.json"],
            },
            "rubric_evidence_adjudication": {
                "strongest_functional_evidence": {
                    "evidence_type": "no_valid_evidence",
                    "route_or_surface": "none",
                    "representative_strength": "none",
                    "paper_mapping_strength": "none",
                    "resource_or_api_boundary": "unknown",
                    "refs": ["artifacts/available_gate/stage_packet.json"],
                    "reason": "Available gate failed before D3/D4.",
                },
                "functional_sufficiency": {
                    "sufficient": False,
                    "reason": "Available prerequisite failed.",
                    "unmet_conditions": ["Available gate failed."],
                    "downgrade_reason": "D1-D5 were skipped after GC1-GC3 failure.",
                    "refs": ["artifacts/available_gate/stage_packet.json"],
                },
                "strongest_reusable_evidence": {
                    "evidence_type": "failed_or_no_transfer",
                    "reuse_surface": "none",
                    "transfer_strength": "failed_or_none",
                    "transfer_novelty": "no_transfer",
                    "relationship_to_d3": "unknown",
                    "refs": ["artifacts/available_gate/stage_packet.json"],
                    "reason": "Available gate failed before D5.",
                },
                "reusable_sufficiency": {
                    "sufficient": False,
                    "reason": "Available and Functional prerequisites failed.",
                    "unmet_conditions": ["Available gate failed.", "Functional gate does not hold."],
                    "downgrade_reason": "D5 was skipped after GC1-GC3 failure.",
                    "refs": ["artifacts/available_gate/stage_packet.json"],
                },
                "final_alignment_check": {
                    "badge_matches_adjudication": True,
                    "stage_final_consistency": "consistent",
                    "reason": "No-badge recommendation follows directly from GC1-GC3 short-circuit.",
                },
            },
            "highest_intervention": {
                "level": "I0",
                "summary": "No D1-D5 commands were run after Available gate failed.",
                "affected_stages": [],
                "depends_on_i4": False,
                "refs": [],
                "reason": "No D1-D5 commands were run after Available gate failed.",
            },
            "functional_decision": {"holds": False, "reason": "Functional cannot hold because Available does not hold.", "conditions": [{"condition": "Available holds", "satisfied": False, "evidence": reason, "refs": ["artifacts/available_gate/stage_packet.json"]}]},
            "reusable_decision": {"holds": False, "reason": "Reusable cannot hold because Available and Functional do not hold.", "conditions": [{"condition": "Functional holds", "satisfied": False, "evidence": "Functional is false after Available gate failure.", "refs": ["artifacts/available_gate/stage_packet.json"]}]},
            "d5_transfer_sufficiency_check": {
                "reuse_target_level": "unknown",
                "transfer_novelty": "no_transfer",
                "relationship_to_d3": "unknown",
                "validated_reuse_surface": "no_validated_surface",
                "artifact_level_sufficiency": "insufficient",
                "transfer_failure_interpretation": "not_attempted_prerequisite_missing",
                "core_reuse_validation_result": "not_attempted_prerequisite_missing",
                "reviewer_created_validation": {
                    "created": False,
                    "reason": "D5 was skipped after Available gate failure.",
                    "refs": ["artifacts/available_gate/stage_packet.json"],
                },
                "sufficiency_reason": "No D5 core reuse validation was attempted because the Available gate failed.",
                "score_or_badge_effect": "blocks_reusable",
                "field_conflicts": [],
            },
            "badge_recommendation": {"badge": "no badge", "available": False, "functional": False, "reusable": False, "reason": reason},
            "confidence": {"level": "high", "reason": "The no-badge decision follows the explicit Available gate failure."},
            "limitations": [{"tag": "available_gate_short_circuit", "evidence": reason, "authors_declared": False, "alternative_evidence": ""}],
            "author_clarification_requests": [],
            "human_review_sensitive_points": ["D1-D5 were not evaluated because the Available gate failed."],
            "evidence_refs": ["artifacts/available_gate/stage_packet.json"],
        }
        write_json(packet_path, normalize_final_scoring_packet(packet))

    def _final_scoring_finish_errors(self, phase_name: str) -> list[dict[str, Any]]:
        if phase_name != "final_scoring":
            return []
        run_root = self.runtime.paths.run_root
        packet_path = run_root / "artifacts" / "final_scoring" / "final_scoring_packet.json"
        packet = self._read_json_object(packet_path)
        normalized_packet = normalize_final_scoring_packet(packet)
        if normalized_packet != packet:
            write_json(packet_path, normalized_packet)
            packet = normalized_packet
        ledger = self._read_json_object(run_root / "artifacts" / "interventions" / "intervention_ledger.json")
        records = ledger.get("interventions", []) if isinstance(ledger, dict) else []
        if not isinstance(records, list) or not records:
            records = []
        ledger_highest = self._highest_intervention_from_records(records)
        command_floor = self._highest_intervention_from_command_ledger(run_root)
        if self._intervention_value(command_floor) > self._intervention_value(ledger_highest):
            ledger_highest = command_floor
        reported = self._intervention_level(packet.get("highest_intervention"))
        if ledger_highest and self._intervention_value(ledger_highest) > self._intervention_value(reported):
            packet = self._normalize_highest_intervention(packet, records, ledger_highest, reported)
            write_json(packet_path, packet)
        self._record_final_scoring_audit_warnings(run_root, packet_path, packet)
        return []

    def _record_final_scoring_audit_warnings(self, run_root: Path, packet_path: Path, packet: dict[str, Any]) -> None:
        """Attach deterministic semantic guardrail findings to the packet.

        These findings are recomputed on every final_scoring finish attempt so
        stale warnings do not survive after the scorer resolves them. The
        evidence auditor decides which findings block phase completion.
        """

        audit_findings: list[dict[str, Any]] = []
        audit_sources = (
            ("final_packet_internal_consistency", lambda: final_packet_internal_consistency_findings(packet)),
            ("environment_consistency", lambda: environment_consistency_findings(run_root, final_scoring_packet=packet)),
            ("representative_workflow", lambda: representative_workflow_findings(packet)),
            ("resource_boundary", lambda: resource_boundary_findings(packet)),
            ("stage_final_semantics", lambda: stage_final_semantics_findings(run_root, packet)),
            ("badge_guardrails", lambda: badge_guardrail_findings(run_root, packet)),
            ("predicate_audit", lambda: predicate_consistency_findings(packet)),
        )
        for source, build_findings in audit_sources:
            try:
                findings = build_findings()
            except Exception as exc:
                audit_findings.append(
                    {
                        "source": source,
                        "code": "audit_source_failed",
                        "message": f"{type(exc).__name__}: {exc}",
                        "severity": "warning",
                    }
                )
                continue
            for item in findings:
                if not isinstance(item, dict):
                    continue
                audit_findings.append(
                    {
                        "source": source,
                        "code": str(item.get("code", "")),
                        "message": str(item.get("message", "")),
                        "severity": str(item.get("severity", "warning")),
                    }
                )
        normalized = dict(packet)
        existing = normalized.get("deterministic_audit_findings")
        if not isinstance(existing, list):
            existing = []
        refreshed_sources = {source for source, _ in audit_sources}
        existing = [
            item
            for item in existing
            if not (isinstance(item, dict) and str(item.get("source", "")) in refreshed_sources)
        ]
        seen = {
            (str(item.get("source", "")), str(item.get("code", "")), str(item.get("message", "")))
            for item in existing
            if isinstance(item, dict)
        }
        for item in audit_findings:
            key = (item["source"], item["code"], item["message"])
            if key not in seen:
                existing.append(item)
                seen.add(key)
        normalized["deterministic_audit_findings"] = existing
        write_json(packet_path, normalized)

    def _record_route_surface_audit_warnings(self, phase_name: str, errors: list[dict[str, Any]]) -> None:
        phase_dir = self.runtime.paths.run_root / "artifacts" / phase_name
        phase_dir.mkdir(parents=True, exist_ok=True)
        path = phase_dir / "route_surface_audit_warnings.json"
        warnings: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for error in errors:
            item = {
                "severity": "warning",
                "code": str(error.get("code", "route_surface_audit_warning")),
                "message": str(error.get("message", "")),
                "required_action": str(error.get("required_action", "")),
                "refs": error.get("refs", []),
            }
            key = (item["code"], item["message"])
            if key not in seen:
                warnings.append(item)
                seen.add(key)
        write_json(
            path,
            {
                "schema": "route_surface_audit_warnings_v1",
                "phase": phase_name,
                "updated_at": now_iso(),
                "warnings": warnings,
            },
        )

    def _normalize_highest_intervention(
        self,
        packet: dict[str, Any],
        records: list[Any],
        ledger_highest: str,
        reported: str,
    ) -> dict[str, Any]:
        normalized = dict(packet)
        normalized["highest_intervention"] = self._highest_intervention_object(records, fallback_level=ledger_highest)
        sensitive = normalized.get("human_review_sensitive_points")
        if not isinstance(sensitive, list):
            sensitive = []
        sensitive.append(
            {
                "type": "intervention_consistency_normalized",
                "description": (
                    f"final_scoring_packet.highest_intervention was normalized from {reported or '(missing)'} "
                    f"to {ledger_highest} based on runtime intervention ledgers."
                ),
                "refs": [
                    "artifacts/interventions/intervention_ledger.json",
                    "artifacts/command_ledger/commands.json",
                ],
            }
        )
        normalized["human_review_sensitive_points"] = sensitive
        return normalized

    def _route_surface_finish_errors(self, phase_name: str) -> list[dict[str, Any]]:
        return route_surface_finish_errors(
            run_root=self.runtime.paths.run_root,
            repo_root=self.runtime.repo_root,
            phase_name=phase_name,
        )

    def _record_available_gate_audit_warnings(self, errors: list[dict[str, Any]]) -> None:
        if not errors:
            return
        packet_path = self.runtime.paths.run_root / "artifacts" / "final_scoring" / "final_scoring_packet.json"
        packet = self._read_json_object(packet_path)
        if not packet:
            return
        existing = packet.get("deterministic_audit_findings")
        if not isinstance(existing, list):
            existing = []
        seen = {
            (str(item.get("source", "")), str(item.get("code", "")), str(item.get("message", "")))
            for item in existing
            if isinstance(item, dict)
        }
        for error in errors:
            item = {
                "source": "available_gate_guardrail",
                "code": str(error.get("code", "available_gate_audit_warning")),
                "message": str(error.get("message", "")),
                "severity": "warning",
            }
            key = (item["source"], item["code"], item["message"])
            if key not in seen:
                existing.append(item)
                seen.add(key)
        packet["deterministic_audit_findings"] = existing
        write_json(packet_path, packet)

    def _available_gate_finish_errors(self, phase_name: str) -> list[dict[str, Any]]:
        return available_gate_finish_errors(
            run_root=self.runtime.paths.run_root,
            phase_name=phase_name,
        )

    def _run_evidence_audit_before_commit(self, *, step_id: str, phase_name: str) -> Observation | None:
        if not self._evidence_auditor_enabled_for_phase(phase_name):
            return None
        self.run_logger.log(
            "Evidence auditor enabled: "
            f"phase={phase_name} max_rounds={self.evidence_auditor_max_rounds} "
            f"max_steps={self.evidence_auditor_max_steps} "
            f"max_tokens={self.evidence_auditor_max_tokens}"
        )
        result = run_evidence_audit_gate(
            llm=self.llm,
            run_root=self.runtime.paths.run_root,
            repo_root=self.runtime.repo_root,
            phase_name=phase_name,
            state=self.state_store.state,
            skill_path=self.evidence_auditor_skill_path,
            max_rounds=self.evidence_auditor_max_rounds,
            max_steps=self.evidence_auditor_max_steps,
            max_tokens=self.evidence_auditor_max_tokens,
        )
        if result is None:
            return None
        usage = result.packet.get("usage") if isinstance(result.packet, dict) else {}
        if isinstance(usage, dict):
            self.run_stats.record_llm_usage(usage)
        audit_state = update_state_with_audit_result(self.state_store.state, run_root=self.runtime.paths.run_root, result=result)
        self.state_store.merge_shared_context({"evidence_audit": audit_state})
        packet_ref = relpath_str(result.packet_path, self.runtime.paths.run_root)
        summary = (
            f"Evidence audit `{result.decision}` for `{phase_name}` "
            f"round {result.audit_round}/{self.evidence_auditor_max_rounds}; "
            f"blocking_findings={len(result.blocking_findings)}."
        )
        payload = {
            "phase_name": phase_name,
            "decision": result.decision,
            "audit_round": result.audit_round,
            "max_rounds": self.evidence_auditor_max_rounds,
            "audit_packet_ref": packet_ref,
            "audit_summary": result.packet.get("audit_summary", ""),
            "blocking_findings": result.blocking_findings,
            "must_carry_to_final_scoring": result.packet.get("must_carry_to_final_scoring", []),
        }
        self.trajectory.append(
            phase=phase_name,
            event_type="evidence_audit_result",
            step_id=step_id,
            summary=summary,
            payload=payload,
        )
        self._append_step_trace_audit_result(phase_name=phase_name, summary=summary, payload=payload)
        if not result.requires_revision:
            return None
        revise_summary = (
            f"phase_finish blocked by evidence auditor for `{phase_name}` "
            f"round {result.audit_round}/{self.evidence_auditor_max_rounds}: "
            f"{len(result.blocking_findings)} blocking finding(s). "
            f"Address the findings, update `{phase_name}` artifacts, write "
            f"`artifacts/{phase_name}/audit_response_round_{result.audit_round:02d}.json`, "
            "then call phase_finish again."
        )
        return Observation(kind="audit_revise_required", summary=revise_summary, payload=payload)

    def _evidence_auditor_enabled_for_phase(self, phase_name: str) -> bool:
        if not self.evidence_auditor_enabled:
            return False
        if not phase_name:
            return False
        return phase_name in self.evidence_auditor_phases or "*" in self.evidence_auditor_phases

    def _phase_semantic_finish_errors(self, phase_name: str) -> list[dict[str, Any]]:
        """Compatibility semantic checks used by local protocol regressions.

        Current phase finish validation is mostly file/guardrail based. This
        method keeps the older repo-mutation invariant explicit: if a command
        changed repository files and the stage has not classified that mutation,
        the phase still has unresolved evidence semantics.
        """

        findings: list[dict[str, Any]] = []
        pending = self.state_store.state.shared_context.get("pending_repo_mutations", [])
        if not isinstance(pending, list):
            return findings
        classifications = self._mutation_classification_command_ids(phase_name)
        for item in pending:
            if not isinstance(item, dict):
                continue
            if str(item.get("phase_name", "")).strip() != phase_name:
                continue
            if not bool(item.get("review_required")):
                continue
            command_id = str(item.get("command_id", "")).strip()
            if command_id and command_id in classifications:
                continue
            findings.append(
                {
                    "code": "unclassified_repo_mutation",
                    "message": (
                        "A command changed repository files, but the stage has not recorded whether the mutation is "
                        "author workflow output, evaluator intervention, or contamination."
                    ),
                    "command_id": command_id,
                    "required_action": "Write a mutation_classification artifact for the command before finishing the phase.",
                }
            )
        return findings

    def _mutation_classification_command_ids(self, phase_name: str) -> set[str]:
        payload = self.state_store.read_phase_artifact(phase_name, "mutation_classification")
        command_ids: set[str] = set()
        candidates: list[Any]
        if isinstance(payload, dict) and isinstance(payload.get("classifications"), list):
            candidates = payload.get("classifications", [])
        elif isinstance(payload, list):
            candidates = payload
        else:
            candidates = [payload]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            command_id = str(item.get("command_id", "")).strip()
            if command_id:
                command_ids.add(command_id)
        return command_ids

    @staticmethod
    def _highest_intervention_from_records(records: list[Any]) -> str:
        highest = "I0"
        for item in records:
            if not isinstance(item, dict):
                continue
            level = ArtifactEvaluationAgent._intervention_level(item)
            if ArtifactEvaluationAgent._intervention_value(level) > ArtifactEvaluationAgent._intervention_value(highest):
                highest = level
        return highest

    @staticmethod
    def _intervention_value(value: str) -> int:
        return INTERVENTION_ORDER.get(str(value).strip(), -1)

    def _highest_intervention_from_command_ledger(self, run_root: Path) -> str:
        payload = self._read_json_object(run_root / "artifacts" / "command_ledger" / "commands.json")
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
            level = "I2" if any(self._repo_mutation_path_requires_intervention(item) for item in changed if isinstance(item, dict)) else "I1"
            if self._intervention_value(level) > self._intervention_value(highest):
                highest = level
        return highest

    @staticmethod
    def _highest_intervention_object(records: list[Any], *, fallback_level: str, fallback_payload: Any = None) -> dict[str, Any]:
        level = ArtifactEvaluationAgent._intervention_level(fallback_level) or "I0"
        selected = [item for item in records if isinstance(item, dict) and ArtifactEvaluationAgent._intervention_level(item) == level]
        affected_stages: list[Any] = []
        refs: list[Any] = ["artifacts/interventions/intervention_ledger.json"] if records else []
        summaries: list[str] = []
        depends_on_i4 = level == "I4"
        for item in selected:
            summary = str(item.get("summary", "")).strip()
            if summary:
                summaries.append(summary)
            item_refs = item.get("evidence_refs", [])
            if isinstance(item_refs, list):
                refs.extend(item_refs)
            details = item.get("details", {})
            if isinstance(details, dict):
                command_phase = str(details.get("phase_name", "")).strip()
                if command_phase:
                    affected_stages.append(command_phase)
                command_id = str(details.get("command_id", "")).strip()
                if command_id:
                    refs.append(command_id)
            depends_on_i4 = depends_on_i4 or bool(item.get("depends_on_i4"))
        if isinstance(fallback_payload, dict):
            payload_stages = fallback_payload.get("affected_stages", [])
            if isinstance(payload_stages, list):
                affected_stages.extend(payload_stages)
            payload_refs = fallback_payload.get("refs", [])
            if isinstance(payload_refs, list):
                refs.extend(payload_refs)
            depends_on_i4 = depends_on_i4 or bool(fallback_payload.get("depends_on_i4"))
        return {
            "level": level,
            "summary": "; ".join(summaries) if summaries else f"Highest recorded intervention is {level}.",
            "affected_stages": sorted({str(item) for item in affected_stages if str(item).strip()}),
            "depends_on_i4": depends_on_i4,
            "refs": sorted({str(item) for item in refs if str(item).strip()}),
        }

    @staticmethod
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

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _intervention_level(value: Any) -> str:
        if isinstance(value, dict):
            value = value.get("level", value.get("kind", ""))
        text = str(value or "").strip().upper().replace("LEVEL_", "")
        if text and not text.startswith("I") and text in {"0", "1", "2", "3", "4"}:
            text = f"I{text}"
        return text

    def _read_stage_packet_for_memory(self, phase_name: str) -> dict[str, Any] | None:
        path = phase_artifact_path(self.runtime.paths.run_root, phase_name, "stage_packet")
        if not path.exists():
            path = phase_artifact_path(self.runtime.paths.run_root, phase_name, "final_scoring_packet")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            return None
        return None

    def _stage_refs(self, phase_name: str) -> list[str]:
        phase_dir = self.runtime.paths.run_root / "artifacts" / phase_name
        if not phase_dir.exists():
            return []
        return [relpath_str(path, self.runtime.paths.run_root) for path in sorted(phase_dir.glob("*")) if path.is_file()]

    def _reset_current_stage_memory(self, next_phase: str) -> None:
        memory_dir = self.runtime.paths.run_root / "artifacts" / "memory"
        write_text(
            memory_dir / "current_stage_memory.md",
            f"# Current Stage Memory\n\nCurrent stage: `{next_phase}`\n\nUse completed stage summaries and evidence refs above. Add concise step-level notes here as the stage progresses.\n",
        )
        self._reset_current_stage_steps(next_phase)

    def _record_decision(self, step_id: str, decision: AgentDecision) -> None:
        self.trajectory.append(
            phase=self.state_store.state.current_phase,
            event_type="llm_decision",
            step_id=step_id,
            summary=f"next_action={decision.next_action}",
            payload={
                "thought": decision.thought,
                "next_action": decision.next_action,
                "tool_calls": [call.__dict__ for call in decision.tool_calls],
                "phase_finish": decision.phase_finish,
                "memory_note": decision.memory_note,
            },
        )
        self._append_step_trace_decision(step_id, decision)

    def _current_stage_steps_path(self) -> Path:
        return self.runtime.paths.run_root / "artifacts" / "memory" / "current_stage_steps.md"

    def _missing_initialization_outputs(self) -> list[str]:
        missing: list[str] = []
        config_path = self.runtime.paths.run_root / "ae_config.yaml"
        if not config_path.exists():
            missing.append("ae_config")
            return missing
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8", errors="replace")) or {}
        except Exception:
            config = {}
        inputs = config.get("inputs", {}) if isinstance(config, dict) and isinstance(config.get("inputs"), dict) else {}
        paper_source = str(inputs.get("paper_pdf") or inputs.get("paper_source") or "").strip()
        if paper_source:
            paper_text_path = self.runtime.paths.run_root / "artifacts" / "paper_source" / "paper_text.txt"
            if not paper_text_path.exists() or not paper_text_path.is_file() or paper_text_path.stat().st_size == 0:
                missing.append("paper_text")
        for rel in (
            "artifacts/memory/global_memory.md",
            "artifacts/memory/evidence_index.md",
            "artifacts/memory/current_stage_memory.md",
        ):
            path = self.runtime.paths.run_root / rel
            if not path.exists() or not path.is_file():
                missing.append(rel)
        return missing

    def _reset_current_stage_steps(self, phase_name: str) -> None:
        try:
            write_text(
                self._current_stage_steps_path(),
                f"# Current Stage Step Trace\n\nCurrent stage: `{phase_name}`\n\n"
                "This file is appended by the runtime after each agent decision and tool result in the current stage. "
                "It is injected into the next prompt so exact earlier observations remain available until phase_finish.\n",
            )
        except OSError as exc:
            self.run_logger.log(f"current_stage_steps reset failed: {type(exc).__name__}: {exc}")

    def _append_current_stage_steps(self, text: str) -> None:
        path = self._current_stage_steps_path()
        try:
            ensure_dir(path.parent)
            previous = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            path.write_text(previous.rstrip() + "\n\n" + text.strip() + "\n", encoding="utf-8")
        except OSError as exc:
            self.run_logger.log(f"current_stage_steps append failed: {type(exc).__name__}: {exc}")

    def _render_current_stage_steps(self, phase_name: str) -> str:
        path = self._current_stage_steps_path()
        if not path.exists():
            self._reset_current_stage_steps(phase_name)
        text = _read_optional(path)
        if estimate_text_tokens(text) <= self.step_trace_token_limit:
            return text
        return _compact_text_middle(text, max_chars=max(4000, self.step_trace_token_limit * 4))

    def _archive_current_stage_steps(self, phase_name: str) -> str:
        source = self._current_stage_steps_path()
        if not source.exists():
            return ""
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self.run_logger.log(f"current_stage_steps archive read failed: {type(exc).__name__}: {exc}")
            return ""
        if not text.strip():
            return ""
        target = self.runtime.paths.run_root / "artifacts" / phase_name / "step_trace.md"
        try:
            write_text(target, text)
        except OSError as exc:
            self.run_logger.log(f"current_stage_steps archive write failed: {type(exc).__name__}: {exc}")
            return ""
        return relpath_str(target, self.runtime.paths.run_root)

    def _record_runtime_failure(self, exc: BaseException, *, phase_name: str, step_index: int) -> None:
        payload = {
            "schema": "ae_agent_runtime_failure_v1",
            "phase": phase_name,
            "step_index": step_index,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "run_root": str(self.runtime.paths.run_root),
            "repo_root": str(self.runtime.repo_root),
            "recorded_at": now_iso(),
        }
        path = self.runtime.paths.run_root / "artifacts" / "runtime_errors" / "runtime_failure.json"
        try:
            write_json(path, payload)
        except OSError as write_exc:
            self.run_logger.log(f"runtime_failure write failed: {type(write_exc).__name__}: {write_exc}")
        try:
            self.trajectory.append(
                phase=phase_name,
                event_type="runtime_failure",
                step_id=f"{phase_name}-step-{step_index:02d}",
                summary=f"{type(exc).__name__}: {exc}",
                payload=payload,
            )
        except OSError as trajectory_exc:
            self.run_logger.log(f"runtime_failure trajectory append failed: {type(trajectory_exc).__name__}: {trajectory_exc}")

    def _append_step_trace_decision(self, step_id: str, decision: AgentDecision) -> None:
        phase_name = self.state_store.state.current_phase
        lines = [
            f"## Step `{step_id}` decision",
            f"- time: {now_iso()}",
            f"- phase: `{phase_name}`",
            f"- next_action: `{decision.next_action}`",
        ]
        if decision.thought:
            lines.append(f"- decision_reason: {_compact_inline(decision.thought, 2000)}")
        if decision.memory_note:
            lines.append(f"- memory_note: {_compact_inline(decision.memory_note, 1000)}")
        if decision.tool_calls:
            lines.append("- planned_tool_calls:")
            for index, call in enumerate(decision.tool_calls, start=1):
                lines.append(f"  {index}. `{call.name}` args={_compact_json_text(call.args, 2400)}")
        if decision.phase_finish:
            lines.append(f"- phase_finish_payload: {_compact_json_text(decision.phase_finish, 2400)}")
        self._append_current_stage_steps("\n".join(lines))

    def _append_step_trace_tool_observation(self, step_id: str, tool_call: ToolCall, observation: Observation) -> None:
        phase_name = self.state_store.state.current_phase
        lines = [
            f"## Step `{step_id}` tool result",
            f"- time: {now_iso()}",
            f"- phase: `{phase_name}`",
            f"- tool: `{tool_call.name}`",
            f"- summary: {_compact_inline(observation.summary, 1200)}",
        ]
        if observation.kind:
            lines.append(f"- observation_kind: `{observation.kind}`")
        detail = _step_trace_observation_detail(tool_call.name, observation.payload)
        if detail:
            lines.append("")
            lines.append(detail)
        self._append_current_stage_steps("\n".join(lines))

    def _append_step_trace_audit_result(self, *, phase_name: str, summary: str, payload: dict[str, Any]) -> None:
        lines = [
            f"## Evidence audit for `{phase_name}`",
            f"- time: {now_iso()}",
            f"- summary: {_compact_inline(summary, 1200)}",
            f"- decision: `{payload.get('decision', '')}`",
            f"- audit_round: {payload.get('audit_round', '')}/{payload.get('max_rounds', '')}",
            f"- audit_packet_ref: `{payload.get('audit_packet_ref', '')}`",
        ]
        audit_summary = str(payload.get("audit_summary", "")).strip()
        if audit_summary:
            lines.append(f"- audit_summary: {_compact_inline(audit_summary, 1600)}")
        blocking = payload.get("blocking_findings", [])
        if isinstance(blocking, list) and blocking:
            lines.append("- blocking_findings:")
            for item in blocking[:12]:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "  - "
                    + _compact_json_text(
                        {
                            "id": item.get("id", ""),
                            "category": item.get("category", ""),
                            "message": item.get("message", ""),
                            "required_action": item.get("required_action", ""),
                            "related_stage_refs": item.get("related_stage_refs", []),
                        },
                        1800,
                    )
                )
            if len(blocking) > 12:
                lines.append(f"  - ... omitted {len(blocking) - 12} finding(s)")
        self._append_current_stage_steps("\n".join(lines))

    def _log_step_end(self, step_id: str, decision: AgentDecision, observation: Observation, elapsed: float) -> None:
        detail = _step_result_log_detail(decision=decision, observation=observation, elapsed=elapsed)
        self.run_logger.log(f"{step_id} result_detail: {json.dumps(detail, ensure_ascii=False, default=str)}")

    def _final_report_exists(self) -> bool:
        return (self.runtime.paths.run_root / "ae_report.md").exists() or (
            self.runtime.paths.run_root / "artifacts" / "report_generation" / "ae_report.md"
        ).exists()

    def _missing_report_outputs(self) -> list[str]:
        required = (
            "ae_report.md",
            "ae_report.json",
            "artifacts/report_generation/ae_report.md",
            "artifacts/report_generation/ae_report.json",
        )
        return [path for path in required if not (self.runtime.paths.run_root / path).exists()]

    def _all_report_outputs_exist(self) -> bool:
        return not self._missing_report_outputs()

    def _report_generation_finished(self) -> bool:
        if not self._all_report_outputs_exist():
            return False
        finish_signal = self.runtime.paths.run_root / "artifacts" / "report_generation" / "finish_signal.json"
        if finish_signal.exists():
            return True
        return any(item.get("phase") == "report_generation" for item in self.state_store.state.phase_history)


def _parse_tool_call(raw: Any) -> ToolCall | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or raw.get("tool_name") or "").strip()
    args = raw.get("args") if isinstance(raw.get("args"), dict) else {}
    if not name:
        return None
    return ToolCall(name=name, args=args)


def _read_optional(path: Path) -> str:
    return safe_read_text(path).strip() if path.exists() else ""


def _bounded_env_int(name: str, *, default: int, lower: int, upper: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            value = default
    return max(lower, min(upper, value))


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "disabled"}


def _env_phase_set(name: str, *, default: set[str]) -> set[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return set(default)
    phases = {item.strip() for item in raw.split(",") if item.strip()}
    return phases or set(default)


def _compact_value(value: Any, *, char_limit: int) -> Any:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    if len(text) <= char_limit:
        return value
    head = text[: char_limit // 2]
    tail = text[-char_limit // 2 :]
    return {"_truncated": True, "excerpt": f"{head}\n...[truncated {len(text) - char_limit} chars]...\n{tail}"}


def _compact_inline(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    head = text[: max(0, limit // 2)].rstrip()
    tail = text[-max(0, limit // 2) :].lstrip()
    return f"{head} ...[truncated {len(text) - limit} chars]... {tail}"


def _compact_block(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    head = text[: max(0, limit // 2)].rstrip()
    tail = text[-max(0, limit // 2) :].lstrip()
    return f"{head}\n...[truncated {len(text) - limit} chars]...\n{tail}"


def _compact_json_text(value: Any, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str, indent=2)
    except Exception:
        text = str(value)
    return _compact_block(text, limit)


def _compact_text_middle(text: str, *, max_chars: int) -> str:
    raw = str(text or "")
    if len(raw) <= max_chars:
        return raw
    head_chars = max_chars // 2
    tail_chars = max_chars - head_chars
    return f"{raw[:head_chars]}\n\n...[current stage step trace truncated {len(raw) - max_chars} chars; archived full trace remains on disk after phase_finish]...\n\n{raw[-tail_chars:]}"


def _step_result_log_detail(*, decision: AgentDecision, observation: Observation, elapsed: float) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "action": decision.next_action,
        "observation": observation.kind,
        "elapsed_sec": round(elapsed, 3),
        "summary": _compact_inline(observation.summary, 1200),
    }
    if decision.thought:
        detail["decision_reason"] = _compact_inline(decision.thought, 1600)
    if decision.memory_note:
        detail["memory_note"] = _compact_inline(decision.memory_note, 800)
    if decision.tool_calls:
        detail["planned_tools"] = [_tool_call_log_detail(call) for call in decision.tool_calls]
    if decision.phase_finish:
        detail["phase_finish_request"] = _compact_value(decision.phase_finish, char_limit=3000)
    detail["result"] = _observation_log_detail(observation)
    return detail


def _tool_call_log_detail(call: ToolCall) -> dict[str, Any]:
    args = call.args if isinstance(call.args, dict) else {}
    detail: dict[str, Any] = {"tool": call.name}
    for key in ("phase_name", "purpose", "cwd", "timeout_sec", "log_label", "subpath", "base", "path", "relative_path", "paper_source"):
        value = args.get(key)
        if value not in (None, "", []):
            detail[key] = _compact_inline(value, 1000)
    if call.name == "run_command" and args.get("command"):
        detail["command"] = _compact_block(args.get("command"), 2200)
        if args.get("intervention"):
            detail["intervention"] = _compact_value(args.get("intervention"), char_limit=1800)
    elif call.name == "read_file":
        paths = args.get("paths", args.get("path", []))
        detail["paths"] = _compact_value(paths, char_limit=1800)
    elif call.name == "write_file":
        for key in ("content", "payload"):
            if key in args:
                detail[f"{key}_preview"] = _compact_value(args.get(key), char_limit=1800)
                break
    elif call.name == "phase_finish":
        detail["finish_reason"] = _compact_inline(args.get("finish_reason", ""), 1200)
        detail["handoff_summary"] = _compact_inline(args.get("handoff_summary", ""), 1200)
        detail["evidence_refs"] = _compact_value(args.get("evidence_refs", []), char_limit=1800)
    return detail


def _observation_log_detail(observation: Observation) -> dict[str, Any]:
    payload = observation.payload if isinstance(observation.payload, dict) else {}
    if observation.kind == "tool_queue":
        observations = payload.get("observations", [])
        items = observations if isinstance(observations, list) else []
        return {"tool_results": [_single_observation_log_detail(item) for item in items[:12]], "omitted_results": max(0, len(items) - 12)}
    return _single_observation_log_detail({"kind": observation.kind, "summary": observation.summary, "payload": payload})


def _single_observation_log_detail(item: Any) -> dict[str, Any]:
    if isinstance(item, Observation):
        item = item.__dict__
    if not isinstance(item, dict):
        return {"summary": _compact_inline(item, 1000)}
    payload = item.get("payload", {}) if isinstance(item.get("payload"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    tool = str(payload.get("tool") or data.get("tool") or "").strip()
    detail: dict[str, Any] = {
        "tool": tool,
        "kind": item.get("kind", ""),
        "ok": payload.get("ok", ""),
        "summary": _compact_inline(item.get("summary", ""), 1000),
    }
    error = payload.get("error")
    if error:
        detail["error"] = _compact_inline(error, 1200)
    if tool == "run_command" or data.get("command_id"):
        detail.update(_run_command_observation_log_detail(data))
    elif tool == "read_file" or data.get("files"):
        detail.update(_read_file_observation_log_detail(data))
    elif tool == "list_repo_tree" or "entries" in data:
        detail.update(_repo_tree_observation_log_detail(data))
    elif tool == "write_file" or data.get("relative_path"):
        detail.update({"wrote_ref": data.get("relative_path") or data.get("path", ""), "size_bytes": data.get("size_bytes", "")})
    elif tool == "get_paper_info" or data.get("paper_source_profile"):
        profile = data.get("paper_source_profile", {}) if isinstance(data.get("paper_source_profile"), dict) else {}
        detail.update(
            {
                "paper_loaded": profile.get("paper_loaded", ""),
                "paper_text_chars": profile.get("text_chars", ""),
                "paper_text_ref": profile.get("text_path", ""),
                "paper_profile_ref": profile.get("profile_path", ""),
            }
        )
    elif data.get("phase_finish_signal") or data.get("next_phase"):
        detail.update(
            {
                "next_phase": data.get("next_phase", ""),
                "phase_finish_signal": _compact_value(data.get("phase_finish_signal", {}), char_limit=2200),
                "step_trace_ref": data.get("step_trace_ref", ""),
            }
        )
    elif data:
        detail["data_excerpt"] = _compact_value(data, char_limit=2200)
    return {key: value for key, value in detail.items() if value not in (None, "", [], {})}


def _run_command_observation_log_detail(data: dict[str, Any]) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "command_id": data.get("command_id", ""),
        "command": _compact_block(data.get("command", ""), 2200),
        "cwd": data.get("cwd", ""),
        "purpose": _compact_inline(data.get("purpose", ""), 1000),
        "final_status": data.get("final_status", data.get("outcome", "")),
        "returncode": data.get("returncode", data.get("exit_code", "")),
        "strict_shell": data.get("strict_shell", ""),
        "stdout_ref": data.get("stdout_path", data.get("stdout_log_path", "")),
        "stderr_ref": data.get("stderr_path", data.get("stderr_log_path", "")),
        "phase_command_ledger_ref": data.get("phase_command_ledger_path", ""),
        "duration_sec": data.get("duration_sec", ""),
    }
    if data.get("agent_action_required"):
        detail["agent_action_required"] = data.get("agent_action_required")
    stdout = str(data.get("stdout_tail") or data.get("stdout") or "").strip()
    stderr = str(data.get("stderr_tail") or data.get("stderr") or "").strip()
    if stdout:
        detail["stdout_excerpt"] = _compact_block(stdout, 2200)
    if stderr:
        detail["stderr_excerpt"] = _compact_block(stderr, 2200)
    fatal = data.get("fatal_patterns")
    warning = data.get("warning_patterns")
    if fatal:
        detail["fatal_patterns"] = fatal
    if warning:
        detail["warning_patterns"] = warning
    if data.get("repo_mutation_review_required"):
        detail["repo_mutation_review_required"] = True
    return detail


def _read_file_observation_log_detail(data: dict[str, Any]) -> dict[str, Any]:
    files = data.get("files", []) if isinstance(data.get("files"), list) else []
    summarized = []
    for item in files[:12]:
        if not isinstance(item, dict):
            continue
        entry = {
            "path": item.get("path", ""),
            "exists": item.get("exists", False),
            "read_status": item.get("read_status", ""),
            "content_chars": item.get("content_chars", ""),
            "evidence_ref": item.get("evidence_ref", ""),
        }
        excerpt = str(item.get("excerpt", "")).strip()
        if excerpt:
            entry["excerpt"] = _compact_block(excerpt, 1200)
        if item.get("error"):
            entry["error"] = _compact_inline(item.get("error"), 600)
        summarized.append(entry)
    return {"base": data.get("base", ""), "files": summarized, "omitted_files": max(0, len(files) - len(summarized))}


def _repo_tree_observation_log_detail(data: dict[str, Any]) -> dict[str, Any]:
    entries = data.get("entries", []) if isinstance(data.get("entries"), list) else []
    paths = []
    for item in entries[:50]:
        if isinstance(item, dict):
            paths.append(f"{item.get('kind', 'unknown')}: {item.get('path', '')}")
    return {"subpath": data.get("subpath", "."), "entry_count": len(entries), "entries_preview": paths, "omitted_entries": max(0, len(entries) - len(paths))}


def _step_trace_observation_detail(tool_name: str, payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        error = payload.get("error") if isinstance(payload, dict) else ""
        return f"- error: {_compact_inline(error, 1200)}" if error else ""
    if tool_name == "read_file":
        return _step_trace_read_file_detail(data)
    if tool_name == "list_repo_tree":
        entries = data.get("entries", []) if isinstance(data.get("entries"), list) else []
        paths = []
        for item in entries[:80]:
            if isinstance(item, dict):
                paths.append(f"{item.get('kind', 'unknown')}: {item.get('path', '')}")
        omitted = max(0, len(entries) - len(paths))
        body = "\n".join(f"  - {path}" for path in paths)
        if omitted:
            body += f"\n  - ... omitted {omitted} entries"
        return f"- listed_subpath: `{data.get('subpath', '.')}`\n- entry_count: {len(entries)}\n- entries:\n{body}" if body else f"- listed_subpath: `{data.get('subpath', '.')}`\n- entry_count: {len(entries)}"
    if tool_name == "run_command":
        lines = [
            f"- command_id: `{data.get('command_id', '')}`",
            f"- final_status: `{data.get('final_status', '')}`",
            f"- returncode: `{data.get('returncode', '')}`",
            f"- stdout_ref: `{data.get('stdout_path', '')}`",
            f"- stderr_ref: `{data.get('stderr_path', '')}`",
        ]
        command = data.get("command")
        if command:
            lines.insert(0, f"- command: `{command}`")
        stdout_tail = str(data.get("stdout_tail", "") or data.get("stdout_preview", "") or "").strip()
        stderr_tail = str(data.get("stderr_tail", "") or data.get("stderr_preview", "") or "").strip()
        if stdout_tail:
            lines.append("- stdout_excerpt:\n```text\n" + _compact_block(stdout_tail, 4000) + "\n```")
        if stderr_tail:
            lines.append("- stderr_excerpt:\n```text\n" + _compact_block(stderr_tail, 4000) + "\n```")
        return "\n".join(lines)
    if tool_name == "write_file":
        return f"- wrote_ref: `{data.get('relative_path', '')}`\n- size_bytes: {data.get('size_bytes', '')}"
    if tool_name == "get_paper_info":
        profile = data.get("paper_source_profile", {}) if isinstance(data.get("paper_source_profile"), dict) else {}
        return "\n".join(
            [
                f"- paper_loaded: `{profile.get('paper_loaded', '')}`",
                f"- text_chars: {profile.get('text_chars', '')}",
                f"- text_ref: `{profile.get('text_path', '')}`",
                f"- profile_ref: `{profile.get('profile_path', '')}`",
            ]
        )
    return "- result_data_excerpt:\n```json\n" + _compact_json_text(data, 5000) + "\n```"


def _step_trace_read_file_detail(data: dict[str, Any]) -> str:
    files = data.get("files", []) if isinstance(data.get("files"), list) else []
    lines = [f"- base: `{data.get('base', '')}`", f"- files_read: {sum(1 for item in files if isinstance(item, dict) and item.get('exists'))}"]
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        lines.extend(
            [
                f"- file: `{path}`",
                f"  exists: `{item.get('exists', False)}`",
                f"  read_status: `{item.get('read_status', '')}`",
                f"  content_chars: {item.get('content_chars', '')}",
                f"  evidence_ref: `{item.get('evidence_ref', '')}`",
            ]
        )
        if item.get("exists"):
            excerpt = str(item.get("excerpt", "") or "")
            if item.get("was_truncated"):
                excerpt = _compact_block(excerpt, 20_000)
            if excerpt:
                lines.append("  excerpt:")
                lines.append("```text")
                lines.append(excerpt)
                lines.append("```")
        elif item.get("error"):
                lines.append(f"  error: {_compact_inline(item.get('error'), 500)}")
    return "\n".join(lines)
