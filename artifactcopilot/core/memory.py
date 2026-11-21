from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..shared.fs import ensure_dir, now_iso, safe_read_text

PHASE_OUTPUT_DEFAULTS = {
    "run_initialization": {
        "run_config": "missing",
        "budget_profile": "missing",
        "host_profile": "missing",
        "initial_handoff": "missing",
    },
    "available_gate": {
        "available_gate_evidence": "missing",
        "available_gate_llm_check": "missing",
        "available_gate_handoff": "missing",
    },
    "D1_documentation": {
        "documentation_evidence": "missing",
        "documentation_llm_check": "missing",
        "documentation_handoff": "missing",
    },
    "D2_environment": {
        "environment_evidence": "missing",
        "environment_llm_check": "missing",
        "environment_handoff": "missing",
        "command_ledger": "missing",
    },
    "D3_executability": {
        "execution_evidence": "missing",
        "execution_llm_check": "missing",
        "execution_handoff": "missing",
        "command_ledger": "missing",
        "representative_workflow_assessment": "missing",
    },
    "D4_claim_support": {
        "claim_support_evidence": "missing",
        "claim_support_llm_check": "missing",
        "claim_support_handoff": "missing",
    },
    "D5_reusability": {
        "reusability_evidence": "missing",
        "reusability_llm_check": "missing",
        "reusability_handoff": "missing",
    },
    "final_scoring": {
        "final_evidence_packet": "missing",
        "final_scoring_packet": "missing",
        "final_scoring_handoff": "missing",
    },
    "report_generation": {
        "ae_report": "missing",
        "badge_recommendation": "missing",
        "evidence_appendix": "missing",
        "report_handoff": "missing",
    },
}


@dataclass
class RollingMemoryState:
    recent_snippets: list[str] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "recent_snippets": self.recent_snippets,
            "updated_at": self.updated_at,
        }


class RollingMemory:
    def __init__(self, path: Path, *, max_recent_snippets: int = 10) -> None:
        self.path = path
        self.max_recent_snippets = max_recent_snippets
        self.state = RollingMemoryState()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.state = RollingMemoryState(
                recent_snippets=data.get("recent_snippets", []) if isinstance(data.get("recent_snippets"), list) else [],
                updated_at=str(data.get("updated_at", "")),
            )
        except Exception:
            self.state = RollingMemoryState()

    def save(self) -> None:
        self.state.updated_at = now_iso()
        ensure_dir(self.path.parent)
        self.path.write_text(json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def reset(self) -> None:
        self.state = RollingMemoryState()
        self.save()

    def replace_with_summary(self, summary_lines: list[str]) -> None:
        sanitized = []
        for item in summary_lines:
            compact = _trim_text(item, 400)
            if compact:
                sanitized.append(compact)
        self.state = RollingMemoryState(
            recent_snippets=sanitized,
            updated_at=now_iso(),
        )
        self.save()

    def add_snippet(self, snippet: str) -> None:
        compact = _trim_text(snippet, 220)
        if not compact:
            return
        if self.state.recent_snippets and self.state.recent_snippets[-1] == compact:
            return
        self.state.recent_snippets.append(compact)
        self.save()

    def get_context(self) -> str:
        if not self.state.recent_snippets:
            return "<rolling_tail>(empty)</rolling_tail>"
        lines = ["<rolling_tail>"]
        if self.state.recent_snippets:
            lines.append("Recent checkpoints:")
            lines.extend(f"- {item}" for item in self.state.recent_snippets)
        lines.append("</rolling_tail>")
        return "\n".join(lines)


class WorkingMemory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = self._base_state()
        self.load()

    def _base_state(self) -> dict[str, Any]:
        return {
            "schema_version": "artifactcopilot_working_memory_v1",
            "mission": {
                "repo_root": "",
                "current_phase": "bootstrap",
                "overall_goal": "",
                "phase_goal": "",
                "phase_focus": "",
            },
            "evaluation_rules_summary": {
                "name": "artifact_evaluation_rubric",
                "version": "2026-05",
                "profile": "icse2027_main",
                "available_gate": ["GC1", "GC2", "GC3"],
                "dimensions": ["D1", "D2", "D3", "D4", "D5"],
                "representative_workflow": ["RW1", "RW2", "RW3", "RW4", "RW5"],
                "evidence_tiers": ["E0", "E1", "E2", "E3"],
                "intervention_levels": ["I0", "I1", "I2", "I3", "I4"],
            },
            "phase_status": {
                "run_initialization": "pending",
                "available_gate": "pending",
                "D1_documentation": "pending",
                "D2_environment": "pending",
                "D3_executability": "pending",
                "D4_claim_support": "pending",
                "D5_reusability": "pending",
                "final_scoring": "pending",
                "report_generation": "pending",
            },
            "phase_outputs_progress": json.loads(json.dumps(PHASE_OUTPUT_DEFAULTS)),
            "artifact_profile": {},
            "documentation_assessment": {},
            "environment_facts": {},
            "execution_facts": {},
            "validation_facts": {},
            "workflow_roadmap": {},
            "phase_handoffs": {},
            "read_ledger": {
                "files_read": [],
                "last_read_batch": [],
                "attempted_paths": [],
                "missing_paths": [],
                "file_takeaways": {},
                "file_evidence_index": {},
            },
            "repo_tree_ledger": {
                "listed_subpaths": [],
                "known_files": [],
                "known_dirs": [],
                "last_listing": {},
            },
            "tool_call_history": [],
            "durable_tool_facts": {
                "known_existing_paths": [],
                "known_missing_paths": [],
                "listed_subpaths": [],
                "recent_successful_commands": [],
                "recent_failed_commands": [],
                "recent_tool_errors": [],
                "docker_facts": [],
            },
            "evidence_index": {
                "recent_evidence_refs": [],
            },
            "durable_learnings": {
                "artifact_facts": [],
                "workflow_facts": [],
                "environment_lessons": [],
                "failure_avoidance": [],
                "durable_notes": [],
            },
            "memory_corrections": {
                "fully_read_sources": {},
                "invalidated_hypotheses": [],
                "latest_grounded_corrections": [],
            },
            "recent_checkpoints": [],
            "open_issues": [],
            "compact_overlay": {},
            "updated_at": "",
        }

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            merged = self._base_state()
            if isinstance(data, dict):
                _deep_merge_dict(merged, data)
            self.state = merged
        except Exception:
            self.state = self._base_state()

    def save(self) -> None:
        self.state["updated_at"] = now_iso()
        ensure_dir(self.path.parent)
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def reset(self) -> None:
        self.state = self._base_state()
        self.save()

    def update_mission(
        self,
        *,
        repo_root: str | Path | None = None,
        current_phase: str | None = None,
        overall_goal: str | None = None,
        phase_goal: str | None = None,
        phase_focus: str | None = None,
    ) -> None:
        mission = self.state.setdefault("mission", {})
        if repo_root is not None:
            mission["repo_root"] = str(repo_root)
        if current_phase is not None:
            mission["current_phase"] = str(current_phase)
        if overall_goal is not None:
            mission["overall_goal"] = str(overall_goal)
        if phase_goal is not None:
            mission["phase_goal"] = str(phase_goal)
        if phase_focus is not None:
            mission["phase_focus"] = str(phase_focus)
        self.save()

    def mark_phase_status(self, phase_name: str, status: str) -> None:
        self.state.setdefault("phase_status", {})[str(phase_name)] = str(status)
        self.save()

    def set_open_questions(self, questions: list[str]) -> None:
        return

    def add_durable_fact(self, text: str) -> None:
        durable = self.state.setdefault("durable_learnings", {})
        durable["durable_notes"] = _merge_unique_texts(
            durable.get("durable_notes", []),
            [text],
            item_limit=20,
            text_limit=180,
        )
        self.save()

    def add_tool_observation(self, text: str) -> None:
        return

    def update_from_tool_result(self, *, tool_name: str, args: dict[str, Any], data: dict[str, Any], summary: str) -> None:
        self.record_tool_call(
            tool_name=tool_name,
            args=args,
            ok=True,
            summary=summary,
            data=data,
            error="",
        )
        self.add_tool_observation(f"{tool_name}: {summary}")
        if tool_name == "read_repo_files":
            self._record_read_files(data)
        elif tool_name == "list_repo_tree":
            self._record_repo_tree_listing(args, data)
        elif tool_name == "scan_repository":
            self._record_repo_profile(data)
        elif tool_name == "extract_paper_source":
            self._record_paper_source_extraction(data)
        elif tool_name == "record_blocker":
            blocker = data.get("blocker", {})
            if isinstance(blocker, dict):
                self.state["open_issues"] = _merge_unique_texts(
                    self.state.get("open_issues", []),
                    [blocker.get("summary", "")],
                    item_limit=12,
                    text_limit=180,
                )
                refs = blocker.get("evidence_refs", [])
                self._merge_recent_evidence_refs(refs)
                self.save()
        elif tool_name == "record_intervention":
            intervention = data.get("intervention", {})
            if isinstance(intervention, dict):
                durable = self.state.setdefault("durable_learnings", {})
                durable["environment_lessons"] = _merge_unique_texts(
                    durable.get("environment_lessons", []),
                    [intervention.get("summary", "")],
                    item_limit=10,
                    text_limit=180,
                )
                refs = intervention.get("evidence_refs", [])
                self._merge_recent_evidence_refs(refs)
                self.save()
        elif tool_name == "phase_finish":
            finish_signal = data.get("phase_finish_signal", {})
            if isinstance(finish_signal, dict):
                phase_name = str(finish_signal.get("phase_name", "")).strip()
                if phase_name:
                    self.record_phase_handoff(
                        phase_name,
                        {
                            "handoff_summary": finish_signal.get("handoff_summary", ""),
                            "residual_risks": finish_signal.get("residual_risks", []),
                            "evidence_refs": finish_signal.get("evidence_refs", []),
                        },
                    )
        elif tool_name == "run_host_command" and isinstance(data, dict):
            self._record_command_ledger_result(data)
            self._record_runtime_correction(tool_name=tool_name, args=args, data=data, summary=summary)

    def record_tool_call(
        self,
        *,
        tool_name: str,
        args: dict[str, Any] | None,
        ok: bool,
        summary: str,
        data: Any = None,
        error: str = "",
    ) -> None:
        history = self.state.setdefault("tool_call_history", [])
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "phase": str(self.state.get("mission", {}).get("current_phase", "")).strip(),
                "tool_name": str(tool_name).strip(),
                "args": _compact_json(args or {}, 2_000_000),
                "ok": bool(ok),
                "summary": str(summary or ""),
                "result_excerpt": _tool_result_excerpt(tool_name, data, 2_000_000) if ok else "",
                "data_excerpt": _tool_result_excerpt(tool_name, data, 2_000_000) if data is not None else "",
                "error_excerpt": str(error or ""),
                "updated_at": now_iso(),
            }
        )
        self.state["tool_call_history"] = history
        self._update_durable_tool_facts(
            tool_name=tool_name,
            args=args or {},
            ok=ok,
            summary=summary,
            data=data,
            error=error,
        )
        self.save()

    def _update_durable_tool_facts(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        ok: bool,
        summary: str,
        data: Any,
        error: str,
    ) -> None:
        facts = self.state.setdefault("durable_tool_facts", {})
        if tool_name == "list_repo_tree" and isinstance(data, dict):
            entries = data.get("entries", [])
            if isinstance(entries, list):
                files: list[str] = []
                dirs: list[str] = []
                for item in entries:
                    if not isinstance(item, dict):
                        continue
                    path = str(item.get("path", "")).strip()
                    kind = str(item.get("kind", "")).strip()
                    if not path:
                        continue
                    if kind == "dir":
                        dirs.append(path)
                    else:
                        files.append(path)
                facts["known_existing_paths"] = _merge_unique_texts(
                    facts.get("known_existing_paths", []),
                    files + dirs,
                    item_limit=260,
                    text_limit=180,
                )
            subpath = str(data.get("subpath", args.get("subpath", "."))).strip() or "."
            facts["listed_subpaths"] = _merge_unique_texts(
                facts.get("listed_subpaths", []),
                [subpath],
                item_limit=60,
                text_limit=180,
            )
        elif tool_name == "read_repo_files" and isinstance(data, dict):
            files = data.get("files", [])
            if isinstance(files, list):
                existing: list[str] = []
                missing: list[str] = []
                for item in files:
                    if not isinstance(item, dict):
                        continue
                    path = str(item.get("path", "")).strip()
                    if not path:
                        continue
                    if item.get("exists"):
                        existing.append(path)
                    else:
                        missing.append(path)
                facts["known_existing_paths"] = _merge_unique_texts(
                    facts.get("known_existing_paths", []),
                    existing,
                    item_limit=260,
                    text_limit=180,
                )
                facts["known_missing_paths"] = _merge_unique_texts(
                    facts.get("known_missing_paths", []),
                    missing,
                    item_limit=160,
                    text_limit=180,
                )
        elif tool_name == "run_host_command":
            command = str(args.get("command", "")).strip()
            if command:
                final_status = str(data.get("final_status", "") if isinstance(data, dict) else "").strip()
                key = "recent_successful_commands" if final_status in {"success", "success_with_warnings"} else "recent_failed_commands"
                facts[key] = _merge_unique_texts(
                    facts.get(key, []),
                    [f"{command} -> final_status={final_status or 'unknown'}; {summary}"],
                    item_limit=40,
                    text_limit=220,
                )
                command_lower = command.lower()
                stdout_tail = ""
                stderr_tail = ""
                if isinstance(data, dict):
                    stdout_tail = str(data.get("stdout_tail", "")).strip()
                    stderr_tail = str(data.get("stderr_tail", "")).strip()
                noteworthy_lines: list[str] = []
                combined = "\n".join(part for part in [stdout_tail, stderr_tail] if part).strip()
                if "uname -m" in command_lower and combined:
                    noteworthy_lines.append(f"host_arch_observation: {combined[:220]}")
                if "docker inspect" in command_lower and combined:
                    noteworthy_lines.append(f"docker_inspect_observation: {combined[:260]}")
                if "exec format error" in combined.lower():
                    noteworthy_lines.append(f"compatibility_error: {combined[:260]}")
                if noteworthy_lines:
                    facts["docker_facts"] = _merge_unique_texts(
                        facts.get("docker_facts", []),
                        noteworthy_lines,
                        item_limit=40,
                        text_limit=260,
                    )
        if not ok or error:
            facts["recent_tool_errors"] = _merge_unique_texts(
                facts.get("recent_tool_errors", []),
                [f"{tool_name}: {error or summary}"],
                item_limit=40,
                text_limit=240,
            )

    def _memory_corrections(self) -> dict[str, Any]:
        corrections = self.state.setdefault("memory_corrections", {})
        if not isinstance(corrections, dict):
            corrections = {}
            self.state["memory_corrections"] = corrections
        corrections.setdefault("fully_read_sources", {})
        corrections.setdefault("invalidated_hypotheses", [])
        corrections.setdefault("latest_grounded_corrections", [])
        return corrections

    def _record_fully_read_source(self, *, path: str, snapshot_meta: dict[str, Any]) -> None:
        if not path or not isinstance(snapshot_meta, dict):
            return
        corrections = self._memory_corrections()
        fully_read = corrections.setdefault("fully_read_sources", {})
        if not isinstance(fully_read, dict):
            fully_read = {}
            corrections["fully_read_sources"] = fully_read
        fully_read[path] = {
            "read_status": str(snapshot_meta.get("read_status", "")).strip() or "full",
            "content_chars": int(snapshot_meta.get("content_chars", 0) or 0),
            "snapshot_path": str(snapshot_meta.get("snapshot_path", "")).strip(),
            "updated_at": now_iso(),
        }

    def _append_invalidated_hypothesis(self, *, value: str, reason: str, evidence_ref: str = "") -> None:
        compact_value = _trim_text(value, 220)
        compact_reason = _trim_text(reason, 220)
        if not compact_value or not compact_reason:
            return
        corrections = self._memory_corrections()
        current = corrections.setdefault("invalidated_hypotheses", [])
        if not isinstance(current, list):
            current = []
        for item in current:
            if not isinstance(item, dict):
                continue
            if str(item.get("value", "")).strip() == compact_value:
                item["reason"] = compact_reason
                if evidence_ref:
                    item["evidence_ref"] = _trim_text(evidence_ref, 220)
                item["updated_at"] = now_iso()
                corrections["invalidated_hypotheses"] = current
                return
        current.append(
            {
                "value": compact_value,
                "reason": compact_reason,
                "evidence_ref": _trim_text(evidence_ref, 220),
                "updated_at": now_iso(),
            }
        )
        corrections["invalidated_hypotheses"] = current

    def _append_grounded_correction(
        self,
        *,
        value: str,
        reason: str,
        evidence_ref: str = "",
        category: str = "general",
    ) -> None:
        compact_value = _trim_text(value, 260)
        compact_reason = _trim_text(reason, 180)
        if not compact_value:
            return
        corrections = self._memory_corrections()
        current = corrections.setdefault("latest_grounded_corrections", [])
        if not isinstance(current, list):
            current = []
        for item in current:
            if not isinstance(item, dict):
                continue
            if str(item.get("value", "")).strip() == compact_value:
                item["reason"] = compact_reason
                item["category"] = _trim_text(category, 80)
                if evidence_ref:
                    item["evidence_ref"] = _trim_text(evidence_ref, 220)
                item["updated_at"] = now_iso()
                corrections["latest_grounded_corrections"] = current
                return
        current.append(
            {
                "value": compact_value,
                "reason": compact_reason,
                "category": _trim_text(category, 80),
                "evidence_ref": _trim_text(evidence_ref, 220),
                "updated_at": now_iso(),
            }
        )
        corrections["latest_grounded_corrections"] = current

    def record_checkpoint(
        self,
        *,
        phase_name: str,
        label: str,
        status: str = "",
        facts: list[str] | None = None,
        issues: list[str] | None = None,
        next_actions: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> None:
        checkpoints = self.state.setdefault("recent_checkpoints", [])
        checkpoints.append(
            {
                "phase": str(phase_name),
                "label": str(label),
                "status": str(status),
                "facts": _trim_list(facts or [], item_limit=5, text_limit=160),
                "issues": _trim_list(issues or [], item_limit=5, text_limit=180),
                "evidence_refs": _trim_list(evidence_refs or [], item_limit=6, text_limit=140),
                "updated_at": now_iso(),
            }
        )
        self.state["recent_checkpoints"] = checkpoints
        self.state["open_issues"] = _merge_unique_texts(
            self.state.get("open_issues", []),
            issues or [],
            item_limit=12,
            text_limit=180,
        )
        self._merge_recent_evidence_refs(evidence_refs or [])
        self.save()

    def record_phase_handoff(self, phase_name: str, handoff: dict[str, Any]) -> None:
        compact = {
            "handoff_summary": _trim_text(handoff.get("handoff_summary", ""), 220),
            "residual_risks": _trim_list(handoff.get("residual_risks", []), item_limit=6, text_limit=160),
            "evidence_refs": _trim_list(handoff.get("evidence_refs", []), item_limit=6, text_limit=140),
        }
        self.state.setdefault("phase_handoffs", {})[str(phase_name)] = compact
        self.save()

    def get_session_stable_context(self) -> str:
        stable = {
            "mission": self._compact_mission_static(),
            "evaluation_rules_summary": self.state.get("evaluation_rules_summary", {}),
            "phase_outputs_progress": self.state.get("phase_outputs_progress", {}),
            "durable_learnings": self._compact_durable_learnings(),
            "phase_handoffs": self.state.get("phase_handoffs", {}),
        }
        return "<session_stable_context>\n" + json.dumps(stable, ensure_ascii=False, indent=2) + "\n</session_stable_context>"

    def get_live_state_context(self) -> str:
        current_phase = str(self.state.get("mission", {}).get("current_phase", "")).strip()
        phase_outputs_progress = self.state.get("phase_outputs_progress", {})
        current_phase_progress = {}
        current_phase_missing_outputs: list[str] = []
        if isinstance(phase_outputs_progress, dict) and current_phase:
            phase_progress = phase_outputs_progress.get(current_phase, {})
            if isinstance(phase_progress, dict):
                current_phase_progress = phase_progress
                current_phase_missing_outputs = [
                    str(key)
                    for key, value in phase_progress.items()
                    if str(value).strip() != "ready"
                ]
        live = {
            "mission": self._compact_mission_live(),
            "phase_status": self.state.get("phase_status", {}),
            "current_phase_output_progress": current_phase_progress,
            "current_phase_missing_outputs": current_phase_missing_outputs,
            "memory_corrections": self._compact_memory_corrections(),
            "recent_tool_calls": self._compact_tool_call_history(),
            "durable_tool_facts": self._compact_durable_tool_facts(),
            "repo_tree_ledger": self._compact_repo_tree_ledger(),
            "read_ledger": self._compact_read_ledger(),
            "open_issues": _trim_list(self.state.get("open_issues", []), item_limit=10, text_limit=180),
        }
        return "<live_working_state>\n" + json.dumps(live, ensure_ascii=False, indent=2) + "\n</live_working_state>"

    def get_evidence_index_context(self) -> str:
        evidence = {
            "recent_checkpoints": self._compact_recent_checkpoints(),
            "recent_evidence_refs": _trim_list(self.state.get("evidence_index", {}).get("recent_evidence_refs", []), item_limit=12, text_limit=140),
        }
        return "<evidence_index>\n" + json.dumps(evidence, ensure_ascii=False, indent=2) + "\n</evidence_index>"

    def get_agent_context(self) -> str:
        parts = [
            self.get_session_stable_context(),
            self.get_live_state_context(),
            self.get_evidence_index_context(),
        ]
        return "\n\n".join(parts).strip()

    def get_stable_prompt_context(self, rolling_context: str = "") -> str:
        """Render bounded continuity memory without embedding full tool history."""
        payload = {
            "schema": "ae_stable_prompt_memory_v1",
            "policy": [
                "This memory is operational continuity, not an evidence source.",
                "Rubric judgments must cite run artifacts, command ledgers, logs, paper targets, or repo refs.",
                "Do not treat phase_outputs_progress as authoritative if it conflicts with stored artifacts or final reports.",
                "Full prompt memory remains on disk in agent_memory.json; read exact artifacts/logs when needed.",
            ],
            "mission": {
                **self._compact_mission_static(),
                **self._compact_mission_live(),
            },
            "evaluation_rules_summary": self.state.get("evaluation_rules_summary", {}),
            "phase_status": self.state.get("phase_status", {}),
            "phase_handoffs": self.state.get("phase_handoffs", {}),
            "durable_learnings": self._compact_durable_learnings(),
            "memory_corrections": self._compact_memory_corrections(),
            "durable_tool_facts": self._compact_durable_tool_facts(),
            "repo_tree_ledger": self._compact_repo_tree_ledger(),
            "read_ledger": self._compact_read_ledger(),
            "evidence_index": {
                "recent_checkpoints": self._compact_recent_checkpoints(),
                "recent_evidence_refs": _trim_list(
                    self.state.get("evidence_index", {}).get("recent_evidence_refs", []),
                    item_limit=16,
                    text_limit=160,
                ),
            },
            "open_issues": _trim_list(self.state.get("open_issues", []), item_limit=12, text_limit=180),
            "omitted_from_prompt": {
                "tool_call_history": "excluded; use command ledger/log refs for exact command evidence",
            },
        }
        parts = [
            "<stable_prompt_memory>\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n</stable_prompt_memory>"
        ]
        tail = rolling_context.strip()
        if tail:
            parts.append(tail)
        return "\n\n".join(parts).strip()

    def get_full_context(self, rolling_context: str = "") -> str:
        parts = [self.get_agent_context()]
        tail = rolling_context.strip()
        if tail:
            parts.append(tail)
        return "\n\n".join(parts).strip()

    def get_snapshot(self) -> dict[str, Any]:
        return self.state

    def get_context_stats(self, rolling_context: str = "") -> dict[str, int]:
        stable = self.get_session_stable_context()
        live = self.get_live_state_context()
        evidence = self.get_evidence_index_context()
        tail = rolling_context.strip()
        stats = {
            "stable_chars": len(stable),
            "live_chars": len(live),
            "evidence_chars": len(evidence),
            "tail_chars": len(tail),
        }
        stats["stable_est_tokens"] = _estimate_tokens(stable)
        stats["live_est_tokens"] = _estimate_tokens(live)
        stats["evidence_est_tokens"] = _estimate_tokens(evidence)
        stats["tail_est_tokens"] = _estimate_tokens(tail)
        stats["total_chars"] = stats["stable_chars"] + stats["live_chars"] + stats["evidence_chars"] + stats["tail_chars"]
        stats["total_est_tokens"] = (
            stats["stable_est_tokens"]
            + stats["live_est_tokens"]
            + stats["evidence_est_tokens"]
            + stats["tail_est_tokens"]
        )
        return stats

    def render_for_prompt(self, rolling_context: str = "") -> str:
        compact_overlay = self.state.get("compact_overlay", {})
        if isinstance(compact_overlay, dict) and compact_overlay:
            parts = [
                "<memory_compact_overlay>\n"
                + json.dumps(compact_overlay, ensure_ascii=False, indent=2)
                + "\n</memory_compact_overlay>"
            ]
        else:
            parts = [self.get_agent_context()]
        tail = rolling_context.strip()
        if tail:
            parts.append(tail)
        return "\n\n".join(parts).strip()

    def get_compact_overlay(self) -> dict[str, Any]:
        value = self.state.get("compact_overlay", {})
        return value if isinstance(value, dict) else {}

    def set_compact_overlay(self, overlay: dict[str, Any]) -> None:
        self.state["compact_overlay"] = overlay if isinstance(overlay, dict) else {}
        self.save()

    def clear_compact_overlay(self) -> None:
        self.state["compact_overlay"] = {}
        self.save()

    def _record_read_files(self, data: dict[str, Any]) -> None:
        files = data.get("files", [])
        if not isinstance(files, list):
            return
        ledger = self.state.setdefault("read_ledger", {})
        files_read = ledger.setdefault("files_read", [])
        attempted_paths = ledger.setdefault("attempted_paths", [])
        missing_paths = ledger.setdefault("missing_paths", [])
        file_takeaways = ledger.setdefault("file_takeaways", {})
        file_evidence_index = ledger.setdefault("file_evidence_index", {})
        last_batch: list[str] = []
        missing_batch: list[str] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", "")).strip()
            if path:
                attempted_paths = _merge_unique_texts(
                    attempted_paths,
                    [path],
                    item_limit=120,
                    text_limit=180,
                )
            if not item.get("exists"):
                if path:
                    missing_batch.append(path)
                continue
            if not path:
                continue
            if path not in files_read:
                files_read.append(path)
            last_batch.append(path)
            excerpt = str(item.get("excerpt", "")).strip()
            content_chars = int(item.get("content_chars", len(excerpt) if excerpt else 0) or 0)
            was_truncated = bool(item.get("was_truncated", False))
            if excerpt:
                file_takeaways[path] = _extract_takeaway(excerpt)
                snapshot_meta = self._write_file_evidence_snapshot(
                    relative_path=path,
                    content=excerpt,
                    content_chars=content_chars,
                    was_truncated=was_truncated,
                )
                file_evidence_index[path] = snapshot_meta
                if not was_truncated:
                    self._record_fully_read_source(path=path, snapshot_meta=snapshot_meta)
                    if path.lower() == "readme.md":
                        self._append_grounded_correction(
                            value="README.md has already been read fully; do not assume it remains truncated.",
                            reason="full_source_read",
                            evidence_ref=str(snapshot_meta.get("snapshot_path", "")).strip(),
                            category="source_truth",
                        )
        ledger["files_read"] = files_read
        ledger["last_read_batch"] = last_batch
        ledger["attempted_paths"] = attempted_paths
        if missing_batch:
            ledger["missing_paths"] = _merge_unique_texts(
                missing_paths,
                missing_batch,
                item_limit=80,
                text_limit=180,
            )
        self.save()

    def _write_file_evidence_snapshot(
        self,
        *,
        relative_path: str,
        content: str,
        content_chars: int,
        was_truncated: bool,
    ) -> dict[str, Any]:
        safe_name = relative_path.replace("/", "__").replace("\\", "__")
        snapshot_dir = self.path.parent / "artifacts" / "evidence" / "full_reads"
        ensure_dir(snapshot_dir)
        snapshot_path = snapshot_dir / f"{safe_name}.txt"
        snapshot_path.write_text(content, encoding="utf-8")
        tags = _infer_file_tags(relative_path, content)
        return {
            "path": relative_path,
            "snapshot_path": str(snapshot_path),
            "read_status": "partial" if was_truncated else "full",
            "content_chars": content_chars,
            "stored_chars": len(content),
            "was_truncated": was_truncated,
            "tags": tags,
            "updated_at": now_iso(),
        }

    def _record_runtime_correction(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        data: dict[str, Any],
        summary: str,
    ) -> None:
        command = str(args.get("command", "")).strip()
        if not command:
            return
        stdout_text = _extract_runtime_stream(data, "stdout")
        stderr_text = _extract_runtime_stream(data, "stderr")
        combined = "\n".join(part for part in [stdout_text, stderr_text] if part).strip()
        evidence_ref = str(data.get("stdout_log_path", "")).strip() or str(data.get("stderr_log_path", "")).strip() or command
        try:
            tokens = shlex.split(command)
        except Exception:
            tokens = command.split()
        if not tokens:
            return

        lowered_combined = combined.lower()
        if "can't open file" in lowered_combined or "no such file or directory" in lowered_combined:
            self._append_invalidated_hypothesis(
                value=command,
                reason=_trim_text(combined or summary, 220),
                evidence_ref=evidence_ref,
            )

        if tokens[0] == "ls":
            target = tokens[-1] if len(tokens) >= 2 else ""
            if target and stdout_text:
                listing_lines = [line.strip() for line in stdout_text.splitlines() if line.strip()]
                has_top_level_python = any(line.endswith(".py") for line in listing_lines)
                if not has_top_level_python:
                    self._append_grounded_correction(
                        value=f"Directory listing for {target} shows no top-level .py script.",
                        reason="ls_listing_no_top_level_python",
                        evidence_ref=evidence_ref,
                        category="filesystem",
                    )

        if tokens[0] == "find" and ".py" in command:
            lines = [line.strip() for line in stdout_text.splitlines() if line.strip()]
            if lines:
                preview = "; ".join(lines)
                self._append_grounded_correction(
                    value=f"Observed Python-file inventory from runtime listing: {preview}",
                    reason="find_python_inventory",
                    evidence_ref=evidence_ref,
                    category="filesystem",
                )
            else:
                self._append_grounded_correction(
                    value=f"Runtime file search produced no Python-file matches for command: {command}",
                    reason="find_python_inventory_empty",
                    evidence_ref=evidence_ref,
                    category="filesystem",
                )

    def _record_repo_tree_listing(self, args: dict[str, Any], data: dict[str, Any]) -> None:
        entries = data.get("entries", [])
        if not isinstance(entries, list):
            return
        subpath = str(data.get("subpath", args.get("subpath", "."))).strip() or "."
        max_depth = data.get("max_depth", args.get("max_depth", ""))
        files: list[str] = []
        dirs: list[str] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", "")).strip()
            kind = str(item.get("kind", "")).strip()
            if not path:
                continue
            if kind == "dir":
                dirs.append(path)
            else:
                files.append(path)
        ledger = self.state.setdefault("repo_tree_ledger", {})
        ledger["listed_subpaths"] = _merge_unique_texts(
            ledger.get("listed_subpaths", []),
            [subpath],
            item_limit=40,
            text_limit=180,
        )
        ledger["known_files"] = _merge_unique_texts(
            ledger.get("known_files", []),
            files,
            item_limit=220,
            text_limit=180,
        )
        ledger["known_dirs"] = _merge_unique_texts(
            ledger.get("known_dirs", []),
            dirs,
            item_limit=160,
            text_limit=180,
        )
        ledger["last_listing"] = {
            "subpath": subpath,
            "max_depth": max_depth,
            "files": _trim_list(files, item_limit=80, text_limit=180),
            "dirs": _trim_list(dirs, item_limit=80, text_limit=180),
        }
        self.save()

    def _record_repo_profile(self, data: dict[str, Any]) -> None:
        profile = data.get("repo_profile", {})
        if not isinstance(profile, dict):
            return
        repo_summary = profile.get("repo_summary", {}) if isinstance(profile.get("repo_summary"), dict) else {}
        documentation = profile.get("documentation", {}) if isinstance(profile.get("documentation"), dict) else {}
        environment = profile.get("environment", {}) if isinstance(profile.get("environment"), dict) else {}
        artifact_profile = {
            "repo_name": repo_summary.get("repo_name", ""),
            "project_type": repo_summary.get("project_type", ""),
            "primary_language": repo_summary.get("primary_language", ""),
            "secondary_languages": _trim_list(repo_summary.get("secondary_languages", []), item_limit=6, text_limit=80),
            "readme_files": _trim_list(documentation.get("readme_files", []), item_limit=6, text_limit=120),
            "docker_available": bool(environment.get("docker_available", False)),
            "env_files": _trim_list(environment.get("env_files", []), item_limit=10, text_limit=120),
            "package_managers": _trim_list(environment.get("package_managers", []), item_limit=10, text_limit=80),
        }
        self.state["artifact_profile"] = artifact_profile
        progress = self.state.setdefault("phase_outputs_progress", {})
        phase1 = progress.setdefault("available_gate", {})
        phase1["available_gate_evidence"] = "partial"
        durable = self.state.setdefault("durable_learnings", {})
        durable["artifact_facts"] = _merge_unique_texts(
            durable.get("artifact_facts", []),
            [
                f"repo_name={artifact_profile.get('repo_name', '')}",
                f"project_type={artifact_profile.get('project_type', '')}",
                f"primary_language={artifact_profile.get('primary_language', '')}",
                "native_docker=no" if not artifact_profile.get("docker_available") else "native_docker=yes",
            ],
            item_limit=12,
            text_limit=120,
        )
        self.save()

    def _record_paper_source_extraction(self, data: dict[str, Any]) -> None:
        profile = data.get("paper_source_profile", {})
        if not isinstance(profile, dict):
            return
        self.state["paper_source_profile"] = profile
        progress = self.state.setdefault("phase_outputs_progress", {})
        phase_name = str(self.state.get("mission", {}).get("current_phase", "")).strip()
        if phase_name:
            phase_progress = progress.setdefault(phase_name, {})
            if "paper_source_profile" in phase_progress:
                phase_progress["paper_source_profile"] = "partial" if profile.get("paper_loaded") else "blocked"
        refs = [
            str(data.get("profile_path", "")).strip(),
            str(data.get("text_path", "")).strip(),
        ]
        profile_refs = profile.get("evidence_refs", [])
        if isinstance(profile_refs, list):
            refs.extend(str(item).strip() for item in profile_refs if str(item).strip())
        self._merge_recent_evidence_refs(refs)
        self.save()

    def _record_command_ledger_result(self, data: dict[str, Any]) -> None:
        command_id = str(data.get("command_id", "")).strip()
        if not command_id:
            return
        ledger_ref = str(data.get("command_ledger_path", "")).strip()
        stdout_ref = str(data.get("stdout_path", "") or data.get("stdout_log_path", "")).strip()
        stderr_ref = str(data.get("stderr_path", "") or data.get("stderr_log_path", "")).strip()
        self.state["command_ledger"] = {
            "schema": "command_ledger_ref_v1",
            "latest_command_id": command_id,
            "ledger_ref": ledger_ref,
            "latest_stdout_ref": stdout_ref,
            "latest_stderr_ref": stderr_ref,
            "latest_outcome": str(data.get("outcome", "")).strip(),
            "latest_final_status": str(data.get("final_status", "")).strip(),
            "latest_exit_code_status": str(data.get("exit_code_status", "")).strip(),
            "latest_pattern_status": str(data.get("pattern_status", "")).strip(),
            "latest_agent_action_required": str(data.get("agent_action_required", "")).strip(),
            "latest_command_status_summary": data.get("command_status_summary", {}),
            "latest_strict_shell": bool(data.get("strict_shell", False)),
            "latest_detected_failure_patterns": data.get("detected_failure_patterns", []),
            "latest_fatal_patterns": data.get("fatal_patterns", []),
            "latest_warning_patterns": data.get("warning_patterns", []),
        }
        progress = self.state.setdefault("phase_outputs_progress", {})
        phase_name = str(self.state.get("mission", {}).get("current_phase", "")).strip()
        if phase_name:
            phase_progress = progress.setdefault(phase_name, {})
            if "command_ledger" in phase_progress:
                phase_progress["command_ledger"] = "partial"
        self._merge_recent_evidence_refs([command_id, ledger_ref, stdout_ref, stderr_ref])
        self.save()

    def _merge_recent_evidence_refs(self, refs: list[str]) -> None:
        evidence_index = self.state.setdefault("evidence_index", {})
        evidence_index["recent_evidence_refs"] = _merge_unique_texts(
            evidence_index.get("recent_evidence_refs", []),
            refs,
            item_limit=20,
            text_limit=140,
        )

    def _compact_mission_static(self) -> dict[str, str]:
        mission = self.state.get("mission", {})
        if not isinstance(mission, dict):
            return {}
        return {
            "repo_root": str(mission.get("repo_root", "")).strip(),
            "overall_goal": _trim_text(mission.get("overall_goal", ""), 240),
        }

    def _compact_mission_live(self) -> dict[str, str]:
        mission = self.state.get("mission", {})
        if not isinstance(mission, dict):
            return {}
        return {
            "current_phase": str(mission.get("current_phase", "")).strip(),
            "phase_goal": _trim_text(mission.get("phase_goal", ""), 220),
            "phase_focus": _trim_text(mission.get("phase_focus", ""), 220),
        }

    def _compact_durable_learnings(self) -> dict[str, Any]:
        durable = self.state.get("durable_learnings", {})
        if not isinstance(durable, dict):
            return {}
        return {
            "artifact_facts": _trim_list(durable.get("artifact_facts", []), item_limit=8, text_limit=140),
            "workflow_facts": _trim_list(durable.get("workflow_facts", []), item_limit=8, text_limit=140),
            "environment_lessons": _trim_list(durable.get("environment_lessons", []), item_limit=8, text_limit=140),
            "failure_avoidance": _trim_list(durable.get("failure_avoidance", []), item_limit=8, text_limit=160),
            "durable_notes": _trim_list(durable.get("durable_notes", []), item_limit=8, text_limit=160),
        }

    def _compact_memory_corrections(self) -> dict[str, Any]:
        corrections = self.state.get("memory_corrections", {})
        if not isinstance(corrections, dict):
            return {}
        fully_read_sources = corrections.get("fully_read_sources", {})
        compact_sources: dict[str, Any] = {}
        if isinstance(fully_read_sources, dict):
            for key in list(fully_read_sources.keys()):
                meta = fully_read_sources.get(key, {})
                if not isinstance(meta, dict):
                    continue
                compact_sources[str(key)] = {
                    "read_status": str(meta.get("read_status", "")).strip(),
                    "content_chars": int(meta.get("content_chars", 0) or 0),
                    "snapshot_path": _trim_text(meta.get("snapshot_path", ""), 220),
                }
        compact_invalidated: list[dict[str, Any]] = []
        invalidated = corrections.get("invalidated_hypotheses", [])
        if isinstance(invalidated, list):
            for item in invalidated:
                if not isinstance(item, dict):
                    continue
                compact_invalidated.append(
                    {
                        "value": _trim_text(item.get("value", ""), 200),
                        "reason": _trim_text(item.get("reason", ""), 180),
                        "evidence_ref": _trim_text(item.get("evidence_ref", ""), 180),
                    }
                )
        compact_grounded: list[dict[str, Any]] = []
        grounded = corrections.get("latest_grounded_corrections", [])
        if isinstance(grounded, list):
            for item in grounded:
                if not isinstance(item, dict):
                    continue
                compact_grounded.append(
                    {
                        "value": _trim_text(item.get("value", ""), 220),
                        "reason": _trim_text(item.get("reason", ""), 160),
                        "category": _trim_text(item.get("category", ""), 60),
                        "evidence_ref": _trim_text(item.get("evidence_ref", ""), 180),
                    }
                )
        return {
            "fully_read_sources": compact_sources,
            "invalidated_hypotheses": compact_invalidated,
            "latest_grounded_corrections": compact_grounded,
        }

    def _compact_read_ledger(self) -> dict[str, Any]:
        ledger = self.state.get("read_ledger", {})
        if not isinstance(ledger, dict):
            return {}
        takeaways = ledger.get("file_takeaways", {})
        evidence_index = ledger.get("file_evidence_index", {})
        compact_takeaways = {}
        if isinstance(takeaways, dict):
            for key in list(takeaways.keys()):
                compact_takeaways[str(key)] = _trim_text(takeaways.get(key, ""), 140)
        compact_evidence_index: dict[str, Any] = {}
        if isinstance(evidence_index, dict):
            for key in list(evidence_index.keys()):
                meta = evidence_index.get(key, {})
                if not isinstance(meta, dict):
                    continue
                compact_evidence_index[str(key)] = {
                    "read_status": str(meta.get("read_status", "")).strip(),
                    "content_chars": meta.get("content_chars", 0),
                    "stored_chars": meta.get("stored_chars", 0),
                    "snapshot_path": str(meta.get("snapshot_path", "")).strip(),
                    "tags": _trim_list(meta.get("tags", []), item_limit=8, text_limit=80),
                }
        return {
            "files_read": _trim_list(ledger.get("files_read", []), item_limit=20, text_limit=120),
            "last_read_batch": _trim_list(ledger.get("last_read_batch", []), item_limit=10, text_limit=120),
            "attempted_paths": _trim_list(ledger.get("attempted_paths", []), item_limit=24, text_limit=140),
            "missing_paths": _trim_list(ledger.get("missing_paths", []), item_limit=24, text_limit=140),
            "file_takeaways": compact_takeaways,
            "file_evidence_index": compact_evidence_index,
        }

    def _compact_repo_tree_ledger(self) -> dict[str, Any]:
        ledger = self.state.get("repo_tree_ledger", {})
        if not isinstance(ledger, dict):
            return {}
        last_listing = ledger.get("last_listing", {})
        if not isinstance(last_listing, dict):
            last_listing = {}
        return {
            "listed_subpaths": _trim_list(ledger.get("listed_subpaths", []), item_limit=12, text_limit=120),
            "known_files": _trim_list(ledger.get("known_files", []), item_limit=45, text_limit=140),
            "known_dirs": _trim_list(ledger.get("known_dirs", []), item_limit=35, text_limit=140),
            "last_listing": {
                "subpath": str(last_listing.get("subpath", "")).strip(),
                "files": _trim_list(last_listing.get("files", []), item_limit=35, text_limit=140),
                "dirs": _trim_list(last_listing.get("dirs", []), item_limit=35, text_limit=140),
            },
        }

    def _compact_tool_call_history(self) -> list[dict[str, Any]]:
        history = self.state.get("tool_call_history", [])
        if not isinstance(history, list):
            return []
        compact: list[dict[str, Any]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            compact.append(
                {
                    "phase": str(item.get("phase", "")).strip(),
                    "tool_name": str(item.get("tool_name", "")).strip(),
                    "args": str(item.get("args", "") or ""),
                    "ok": bool(item.get("ok", False)),
                    "summary": str(item.get("summary", "") or ""),
                    "result_excerpt": str(item.get("result_excerpt", "") or ""),
                    "data_excerpt": str(item.get("data_excerpt", "") or ""),
                    "error_excerpt": str(item.get("error_excerpt", "") or ""),
                }
            )
        return compact

    def _compact_durable_tool_facts(self) -> dict[str, Any]:
        facts = self.state.get("durable_tool_facts", {})
        if not isinstance(facts, dict):
            return {}
        return {
            "known_existing_paths": _trim_list(facts.get("known_existing_paths", []), item_limit=60, text_limit=140),
            "known_missing_paths": _trim_list(facts.get("known_missing_paths", []), item_limit=40, text_limit=140),
            "listed_subpaths": _trim_list(facts.get("listed_subpaths", []), item_limit=20, text_limit=120),
            "recent_successful_commands": _trim_list(facts.get("recent_successful_commands", []), item_limit=12, text_limit=180),
            "recent_failed_commands": _trim_list(facts.get("recent_failed_commands", []), item_limit=12, text_limit=180),
            "recent_tool_errors": _trim_list(facts.get("recent_tool_errors", []), item_limit=12, text_limit=180),
            "docker_facts": _trim_list(facts.get("docker_facts", []), item_limit=12, text_limit=180),
        }

    def _compact_recent_checkpoints(self) -> list[dict[str, Any]]:
        checkpoints = self.state.get("recent_checkpoints", [])
        if not isinstance(checkpoints, list):
            return []
        compact: list[dict[str, Any]] = []
        for item in checkpoints:
            if not isinstance(item, dict):
                continue
            compact.append(
                {
                    "phase": str(item.get("phase", "")).strip(),
                    "label": str(item.get("label", "")).strip(),
                    "status": str(item.get("status", "")).strip(),
                    "facts": _trim_list(item.get("facts", []), item_limit=4, text_limit=140),
                    "issues": _trim_list(item.get("issues", []), item_limit=4, text_limit=160),
                }
            )
        return compact


class AgentMemory:
    def __init__(self, path: Path, *, max_recent_snippets: int = 10) -> None:
        self.working_path = path
        self.rolling_path = path.with_name(f"{path.stem}_tail{path.suffix}")
        self.working = WorkingMemory(self.working_path)
        self.rolling = RollingMemory(self.rolling_path, max_recent_snippets=max_recent_snippets)

    def set_mission(self, mission: str) -> None:
        self.working.update_mission(overall_goal=mission)
        self.rolling.add_snippet(f"mission={mission}")

    def set_phase_focus(self, phase_focus: str) -> None:
        current_phase = str(self.working.state.get("mission", {}).get("current_phase", "")).strip()
        self.working.update_mission(phase_focus=phase_focus, current_phase=current_phase)

    def set_current_phase(self, phase_name: str, *, phase_goal: str = "") -> None:
        self.working.update_mission(current_phase=phase_name, phase_goal=phase_goal)
        self.working.mark_phase_status(phase_name, "running")

    def add_fact(self, text: str) -> None:
        self.working.add_durable_fact(text)
        self.rolling.add_snippet(text)

    def add_observation(self, text: str) -> None:
        self.working.add_tool_observation(text)
        self.rolling.add_snippet(text)

    def set_questions(self, questions: list[str]) -> None:
        self.working.set_open_questions(questions)

    def record_tool_result(self, *, tool_name: str, args: dict[str, Any], data: dict[str, Any], summary: str) -> None:
        self.working.update_from_tool_result(tool_name=tool_name, args=args, data=data, summary=summary)
        self.rolling.add_snippet(f"{tool_name}: {summary}")

    def record_tool_error(
        self,
        *,
        tool_name: str,
        error: str,
        args: dict[str, Any] | None = None,
        data: Any = None,
        summary: str = "",
    ) -> None:
        self.working.record_tool_call(
            tool_name=tool_name,
            args=args or {},
            ok=False,
            summary=summary or f"{tool_name} failed",
            data=data,
            error=error,
        )
        if tool_name == "run_host_command" and isinstance(data, dict):
            runtime_data = dict(data)
            if error and not runtime_data.get("stderr"):
                runtime_data["stderr"] = error
            self.working._record_command_ledger_result(runtime_data)
            self.working._record_runtime_correction(tool_name=tool_name, args=args or {}, data=runtime_data, summary=summary or error)
        self.working.add_tool_observation(f"{tool_name} failed: {error}")
        self.working.record_checkpoint(
            phase_name=str(self.working.state.get("mission", {}).get("current_phase", "")).strip() or "unknown",
            label=f"{tool_name}_error",
            status="error",
            issues=[error],
        )
        self.rolling.add_snippet(f"{tool_name} failed: {error}")

    def record_checkpoint(
        self,
        *,
        phase_name: str,
        label: str,
        status: str = "",
        facts: list[str] | None = None,
        issues: list[str] | None = None,
        next_actions: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        narrative: str = "",
    ) -> None:
        self.working.record_checkpoint(
            phase_name=phase_name,
            label=label,
            status=status,
            facts=facts,
            issues=issues,
            next_actions=next_actions,
            evidence_refs=evidence_refs,
        )
        if narrative.strip():
            self.rolling.add_snippet(narrative.strip())

    def mark_phase_status(self, phase_name: str, status: str) -> None:
        self.working.mark_phase_status(phase_name, status)
        self.rolling.add_snippet(f"{phase_name}={status}")

    def render_for_prompt(self) -> str:
        return self.working.render_for_prompt(self.rolling.get_context())

    def render_stable_for_prompt(self) -> str:
        return self.working.get_stable_prompt_context(self.rolling.get_context())

    def get_context_stats(self) -> dict[str, int]:
        return self.working.get_context_stats(self.rolling.get_context())

    def get_stable_context_stats(self) -> dict[str, int]:
        text = self.render_stable_for_prompt()
        return {
            "stable_chars": len(text),
            "stable_est_tokens": _estimate_tokens(text),
            "live_chars": 0,
            "live_est_tokens": 0,
            "evidence_chars": 0,
            "evidence_est_tokens": 0,
            "tail_chars": 0,
            "tail_est_tokens": 0,
            "total_chars": len(text),
            "total_est_tokens": _estimate_tokens(text),
        }

    def get_full_context_text(self) -> str:
        return self.working.get_full_context(self.rolling.get_context())

    def get_compact_overlay_text(self) -> str:
        overlay = self.working.get_compact_overlay()
        if not overlay:
            return ""
        return json.dumps(overlay, ensure_ascii=False, indent=2)

    def set_compact_overlay(self, overlay: dict[str, Any]) -> None:
        self.working.set_compact_overlay(overlay)
        self.rolling.add_snippet("memory_compact_overlay=updated")

    def clear_compact_overlay(self) -> None:
        self.working.clear_compact_overlay()
        self.rolling.add_snippet("memory_compact_overlay=cleared")

    def replace_with_compacted_memory(self, compacted: dict[str, Any]) -> None:
        self.working.set_compact_overlay(compacted if isinstance(compacted, dict) else {})
        summary_lines: list[str] = []
        if isinstance(compacted, dict):
            current_phase = compacted.get("current_phase_grounding", {})
            if isinstance(current_phase, dict):
                phase = str(current_phase.get("phase", "")).strip()
                phase_goal = str(current_phase.get("phase_goal", "")).strip()
                current_status = str(current_phase.get("current_status", "")).strip()
                if phase:
                    summary_lines.append(f"phase={phase}")
                if phase_goal:
                    summary_lines.append(f"phase_goal={phase_goal}")
                if current_status:
                    summary_lines.append(f"status={current_status}")
            carry_forward = compacted.get("carry_forward_facts", {})
            if isinstance(carry_forward, dict):
                for item in carry_forward.get("stable_facts", []) or []:
                    text = str(item).strip()
                    if text:
                        summary_lines.append(text)
            next_guidance = compacted.get("next_step_guidance", {})
            if isinstance(next_guidance, dict):
                for item in next_guidance.get("highest_value_next_actions", []) or []:
                    text = str(item).strip()
                    if text:
                        summary_lines.append(f"next={text}")
        self.rolling.replace_with_summary(summary_lines[:12])

    def replace_tail_with_phase_summary(
        self,
        *,
        phase_name: str,
        next_phase: str | None,
        finish_signal: dict[str, Any],
        phase_summary: dict[str, Any] | None = None,
        artifact_refs: list[str] | None = None,
    ) -> None:
        """Collapse step-level tail memory after a phase is durably finished."""
        summary_lines: list[str] = []
        phase = str(phase_name or "").strip()
        if phase:
            summary_lines.append(f"completed_phase={phase}")
        if isinstance(phase_summary, dict):
            status = _trim_text(phase_summary.get("status", ""), 120)
            summary = _trim_text(phase_summary.get("summary", ""), 620)
            if status:
                summary_lines.append(f"status={status}")
            if summary:
                summary_lines.append(f"summary={summary}")
            for item in _trim_list(phase_summary.get("key_findings", []), item_limit=5, text_limit=220):
                summary_lines.append(f"finding={item}")
            for item in _trim_list(phase_summary.get("limitations", []), item_limit=4, text_limit=220):
                summary_lines.append(f"limitation={item}")
            for item in _trim_list(phase_summary.get("next_stage_needs", []), item_limit=4, text_limit=220):
                summary_lines.append(f"next_need={item}")
        else:
            finish_reason = _trim_text(finish_signal.get("finish_reason", ""), 360) if isinstance(finish_signal, dict) else ""
            handoff_summary = _trim_text(finish_signal.get("handoff_summary", ""), 520) if isinstance(finish_signal, dict) else ""
            if finish_reason:
                summary_lines.append(f"finish_reason={finish_reason}")
            if handoff_summary:
                summary_lines.append(f"handoff_summary={handoff_summary}")
        residual_risks = finish_signal.get("residual_risks", []) if isinstance(finish_signal, dict) else []
        if isinstance(phase_summary, dict):
            residual_risks = phase_summary.get("risks", residual_risks)
        for item in _trim_list(residual_risks, item_limit=4, text_limit=220):
            summary_lines.append(f"risk={item}")
        refs: list[str] = []
        if isinstance(phase_summary, dict):
            refs.extend(_trim_list(phase_summary.get("evidence_refs", []), item_limit=8, text_limit=180))
        if isinstance(finish_signal, dict):
            refs.extend(_trim_list(finish_signal.get("evidence_refs", []), item_limit=8, text_limit=180))
        refs.extend(_trim_list(artifact_refs or [], item_limit=12, text_limit=180))
        for ref in _dedupe_texts(refs)[:10]:
            summary_lines.append(f"evidence_ref={ref}")
        if next_phase:
            summary_lines.append(f"next_phase={next_phase}")
        self.rolling.replace_with_summary(summary_lines[:16])

    def get_snapshot(self) -> dict[str, Any]:
        return {
            "working": self.working.get_snapshot(),
            "rolling": self.rolling.state.to_dict(),
        }


def _trim_text(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _trim_list(values: Any, *, item_limit: int, text_limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    items: list[str] = []
    for item in values:
        compact = _trim_text(item, text_limit)
        if compact and compact not in items:
            items.append(compact)
        if len(items) >= item_limit:
            break
    return items


def _dedupe_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _merge_unique_texts(existing: Any, new_values: Any, *, item_limit: int, text_limit: int) -> list[str]:
    ordered: list[str] = []
    for source in (existing, new_values):
        if not isinstance(source, list):
            continue
        for item in source:
            compact = _trim_text(item, text_limit)
            if compact and compact not in ordered:
                ordered.append(compact)
            if len(ordered) >= item_limit:
                return ordered
    return ordered


def _estimate_tokens(text: str) -> int:
    compact = str(text or "")
    if not compact:
        return 0
    return max(1, (len(compact) + 3) // 4)


def _compact_json(value: Any, char_limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        text = str(value)
    return _trim_text(text, char_limit)


def _tool_result_excerpt(tool_name: str, data: Any, char_limit: int) -> str:
    if not isinstance(data, dict):
        return _compact_json(data, char_limit)
    if tool_name == "list_repo_tree":
        entries = data.get("entries", [])
        if isinstance(entries, list):
            compact_entries = []
            for item in entries:
                if not isinstance(item, dict):
                    continue
                compact_entries.append(
                    {
                        "path": item.get("path", ""),
                        "kind": item.get("kind", ""),
                    }
                )
            return _compact_json(
                {
                    "subpath": data.get("subpath", "."),
                    "max_depth": data.get("max_depth", ""),
                    "entries": compact_entries,
                    "truncated": len(entries) > len(compact_entries),
                },
                char_limit,
            )
    if tool_name in {"read_repo_files", "read_run_files"}:
        files = data.get("files", [])
        if isinstance(files, list):
            compact_files = []
            for item in files:
                if not isinstance(item, dict):
                    continue
                compact_files.append(
                    {
                        "path": item.get("path", ""),
                        "exists": bool(item.get("exists", False)),
                        "size_bytes": item.get("size_bytes", ""),
                        "content_chars": item.get("content_chars", ""),
                        "read_status": item.get("read_status", "full" if item.get("exists") else ""),
                        "excerpt_chars": item.get("excerpt_chars", ""),
                        "full_content_on_disk": bool(item.get("full_content_on_disk", False)),
                        "error": item.get("error", ""),
                        "excerpt": str(item.get("excerpt", "")),
                    }
                )
            return _compact_json(
                {
                    "files": compact_files,
                    "truncated": False,
                },
                char_limit,
            )
    if tool_name == "run_host_command":
        preferred = {}
        for key in (
            "command_id",
            "command",
            "exit_code",
            "exit_code_status",
            "outcome",
            "final_status",
            "pattern_status",
            "agent_action_required",
            "command_status_summary",
            "stdout",
            "stderr",
            "stdout_path",
            "stderr_path",
            "command_ledger_path",
            "strict_shell",
            "detected_failure_patterns",
            "fatal_patterns",
            "warning_patterns",
            "benign_patterns",
            "container_id",
            "image_tag",
            "log_path",
            "timed_out",
        ):
            if key in data:
                limit = 1800 if key in {"stdout", "stderr"} else 300
                preferred[key] = _trim_text(data.get(key, ""), limit)
        if preferred:
            return _compact_json(preferred, char_limit)
    if tool_name == "extract_paper_source" and isinstance(data, dict):
        profile = data.get("paper_source_profile", {})
        if isinstance(profile, dict):
            return _compact_json(
                {
                    "paper_loaded": profile.get("paper_loaded"),
                    "status": profile.get("status"),
                    "extractor": profile.get("extractor"),
                    "text_chars": profile.get("text_chars"),
                    "profile_path": profile.get("profile_path"),
                    "text_path": profile.get("text_path"),
                    "error": _trim_text(profile.get("error", ""), 800),
                    "text_preview": _trim_text(profile.get("text_preview", ""), 1800),
                },
                char_limit,
            )
    return _compact_json(data, char_limit)


def _extract_runtime_stream(data: dict[str, Any], stream: str) -> str:
    if not isinstance(data, dict):
        return ""
    direct = str(data.get(stream, "")).strip()
    if direct:
        return direct
    tail = str(data.get(f"{stream}_tail", "")).strip()
    if tail:
        return tail
    log_path = str(data.get(f"{stream}_log_path", "")).strip()
    if log_path:
        try:
            return safe_read_text(Path(log_path)).strip()
        except Exception:
            return ""
    return ""


def _deep_merge_dict(base: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, value in incoming.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge_dict(base[key], value)
        else:
            base[key] = value


def _extract_takeaway(excerpt: str) -> str:
    text = str(excerpt or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return _trim_text(" ".join(lines[:3]), 140)


def _infer_file_tags(relative_path: str, content: str) -> list[str]:
    tags: list[str] = []
    path_lower = str(relative_path or "").lower()
    content_lower = str(content or "").lower()
    if path_lower.endswith("readme.md") or path_lower.endswith("readme"):
        tags.append("primary_doc")
    if "docker pull" in content_lower or "docker run" in content_lower:
        tags.append("docker_commands")
    if "arm" in content_lower and "amd" in content_lower:
        tags.append("multi_arch")
    if "experiment 1" in content_lower or "experiment 2" in content_lower:
        tags.append("multi_experiment")
    if "python" in content_lower:
        tags.append("python_usage")
    if "csv" in content_lower or "figure" in content_lower or "table" in content_lower:
        tags.append("validation_targets")
    return tags[:8]
