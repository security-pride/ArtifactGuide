from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..shared.fs import ensure_dir, now_iso
from .types import PHASE_SEQUENCE


PHASE_REQUIRED_ARTIFACTS = {
    "run_initialization": (),
    "available_gate": ("stage_packet",),
    "D1_documentation": (
        "stage_packet",
        "readme_workflow",
    ),
    "D2_environment": ("stage_packet",),
    "D3_executability": (
        "stage_packet",
        "route_selection_checkpoint",
        "route_evidence",
        "evidence_semantics",
    ),
    "D4_claim_support": ("stage_packet",),
    "D5_reusability": ("stage_packet",),
    "final_scoring": ("final_scoring_packet",),
    "report_generation": (),
}


STATE_ARTIFACT_SUMMARY_KEYS = (
    "schema",
    "status",
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
)


LEGACY_PHASE_FINISH_ARTIFACT_KEYS = {
    "phase_name",
    "finish_reason",
    "handoff_summary",
    "residual_risks",
    "evidence_refs",
}


def phase_artifact_path(run_root: Path, phase_name: str, artifact_key: str) -> Path:
    if phase_name in {"available_gate", "D4_claim_support"} and artifact_key == "paper_source_profile":
        paper_source_path = run_root / "artifacts" / "paper_source" / "paper_source_profile.json"
        if paper_source_path.exists():
            return paper_source_path
    return run_root / "artifacts" / phase_name / f"{artifact_key}.json"


def phase_artifact_index_entry(
    *,
    run_root: Path,
    phase_name: str,
    artifact_key: str,
    payload: Any | None = None,
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    if phase_artifact_is_index_entry(payload):
        return payload
    path = artifact_path or phase_artifact_path(run_root, phase_name, artifact_key)
    entry: dict[str, Any] = {
        "artifact_ref": str(path.relative_to(run_root)) if _is_relative_to(path, run_root) else str(path),
        "storage": "run_artifact",
    }
    if path.exists() and path.is_file():
        entry["size_bytes"] = path.stat().st_size
    if isinstance(payload, dict):
        summary = {key: payload.get(key) for key in STATE_ARTIFACT_SUMMARY_KEYS if key in payload}
        if "dimension_scores" in payload:
            summary["dimension_scores"] = payload.get("dimension_scores")
        if "available_gate" in payload:
            summary["available_gate"] = payload.get("available_gate")
        if "keys" not in summary:
            summary["keys"] = sorted(str(key) for key in payload.keys())[:24]
        entry["summary"] = summary
    elif isinstance(payload, list):
        entry["summary"] = {"type": "list", "length": len(payload)}
    elif payload is not None:
        entry["summary"] = {"type": type(payload).__name__}
    return entry


def phase_artifact_is_index_entry(value: Any) -> bool:
    return isinstance(value, dict) and value.get("storage") == "run_artifact" and isinstance(value.get("artifact_ref"), str)


def phase_artifact_validation_error(run_root: Path, phase_name: str, artifact_key: str, phase_payload: dict[str, Any] | None = None) -> str:
    phase_payload = phase_payload if isinstance(phase_payload, dict) else {}
    artifact_ref = ""
    value = phase_payload.get(artifact_key)
    if phase_artifact_is_index_entry(value):
        artifact_ref = str(value.get("artifact_ref", "")).strip()
    path = (run_root / artifact_ref) if artifact_ref and not Path(artifact_ref).is_absolute() else Path(artifact_ref)
    if not artifact_ref:
        path = phase_artifact_path(run_root, phase_name, artifact_key)
    if not path.exists() or not path.is_file():
        return "missing"
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"invalid_json: {exc}"
    return ""


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


@dataclass
class AgentState:
    schema_version: str
    run_id: str
    repo_root: str
    run_root: str
    current_phase: str
    phase_history: list[dict[str, Any]] = field(default_factory=list)
    phase_artifacts: dict[str, Any] = field(default_factory=dict)
    shared_context: dict[str, Any] = field(default_factory=dict)
    evidence_store: dict[str, Any] = field(default_factory=dict)
    interventions: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "repo_root": self.repo_root,
            "run_root": self.run_root,
            "current_phase": self.current_phase,
            "phase_history": self.phase_history,
            "phase_artifacts": self.phase_artifacts,
            "shared_context": self.shared_context,
            "evidence_store": self.evidence_store,
            "interventions": self.interventions,
            "blockers": self.blockers,
            "updated_at": self.updated_at,
        }


class AgentStateStore:
    def __init__(self, path: Path, *, run_id: str, repo_root: Path, run_root: Path, start_phase: str) -> None:
        self.path = path
        self.state = AgentState(
            schema_version="artifactcopilot_state_v1",
            run_id=run_id,
            repo_root=str(repo_root),
            run_root=str(run_root),
            current_phase=start_phase if start_phase in PHASE_SEQUENCE else PHASE_SEQUENCE[0],
            updated_at=now_iso(),
        )

    def load_or_init(self) -> AgentState:
        if not self.path.exists():
            self.save()
            return self.state
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.state = AgentState(
                schema_version=str(data.get("schema_version", "artifactcopilot_state_v1")),
                run_id=str(data.get("run_id", self.state.run_id)),
                repo_root=self.state.repo_root,
                run_root=self.state.run_root,
                current_phase=str(data.get("current_phase", self.state.current_phase)),
                phase_history=data.get("phase_history", []) if isinstance(data.get("phase_history"), list) else [],
                phase_artifacts=data.get("phase_artifacts", {}) if isinstance(data.get("phase_artifacts"), dict) else {},
                shared_context=data.get("shared_context", {}) if isinstance(data.get("shared_context"), dict) else {},
                evidence_store=data.get("evidence_store", {}) if isinstance(data.get("evidence_store"), dict) else {},
                interventions=data.get("interventions", []) if isinstance(data.get("interventions"), list) else [],
                blockers=data.get("blockers", []) if isinstance(data.get("blockers"), list) else [],
                updated_at=str(data.get("updated_at", now_iso())),
            )
        except Exception:
            self.save()
        self.sync_phase_artifacts_from_disk()
        return self.state

    def save(self) -> None:
        self.state.updated_at = now_iso()
        ensure_dir(self.path.parent)
        self.path.write_text(json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def merge_shared_context(self, updates: dict[str, Any]) -> None:
        if not isinstance(updates, dict):
            return
        self.state.shared_context.update(updates)
        self.save()

    def set_current_phase(self, phase_name: str) -> None:
        if phase_name not in PHASE_SEQUENCE:
            return
        self.state.current_phase = phase_name
        self.save()

    def apply_start_phase_preflight(self, *, requested_start_phase: str) -> dict[str, Any]:
        self.sync_phase_artifacts_from_disk()
        requested = requested_start_phase if requested_start_phase in PHASE_SEQUENCE else PHASE_SEQUENCE[0]
        target_phase = requested
        target_index = PHASE_SEQUENCE.index(target_phase)
        missing_dependencies: list[dict[str, Any]] = []
        effective_phase = target_phase
        for phase_name in PHASE_SEQUENCE[:target_index]:
            missing = self.missing_required_artifacts(phase_name)
            if missing:
                missing_dependencies.append({"phase": phase_name, "missing_artifacts": missing})
                if effective_phase == target_phase:
                    effective_phase = phase_name
        adjusted = effective_phase != target_phase
        if adjusted:
            self.state.current_phase = effective_phase
        preflight = {
            "schema": "phase_preflight_v1",
            "requested_start_phase": requested,
            "target_phase_before_preflight": target_phase,
            "effective_start_phase": effective_phase,
            "adjusted": adjusted,
            "missing_dependencies": missing_dependencies,
            "checked_at": now_iso(),
        }
        self.state.shared_context["phase_preflight"] = preflight
        self.save()
        return preflight

    def prune_rerun_phase_artifacts(self, *, start_phase: str) -> dict[str, Any]:
        """Reset the explicitly requested phase and later phases for a clean rerun.

        A copied run root can contain old phase_history entries for final_scoring
        or report_generation even when their artifacts were removed or are stale.
        Explicit --start-phase means "redo this phase"; old commits for that
        phase must not allow stop-after-phase to accept a no-op run.
        """

        if start_phase not in PHASE_SEQUENCE:
            return {"schema": "phase_rerun_prune_v1", "start_phase": start_phase, "pruned": False, "reason": "unknown_phase"}
        start_index = PHASE_SEQUENCE.index(start_phase)
        rerun_phases = list(PHASE_SEQUENCE[start_index:])
        run_root = Path(self.state.run_root)
        removed_phase_artifacts: list[str] = []
        for phase_name in rerun_phases:
            phase_dir = run_root / "artifacts" / phase_name
            if phase_dir.exists() and phase_dir.is_dir():
                for path in phase_dir.glob("*.json"):
                    try:
                        path.unlink()
                        removed_phase_artifacts.append(str(path.relative_to(run_root)))
                    except OSError:
                        pass
            self.state.phase_artifacts.pop(phase_name, None)

        removed_command_ids = self._prune_command_ledgers_for_phases(rerun_phases)
        self.state.phase_history = [
            item
            for item in self.state.phase_history
            if not (isinstance(item, dict) and str(item.get("phase", "")).strip() in rerun_phases)
        ]
        self.state.current_phase = start_phase
        self.state.shared_context["phase_rerun_prune"] = {
            "schema": "phase_rerun_prune_v1",
            "start_phase": start_phase,
            "pruned": True,
            "pruned_phases": rerun_phases,
            "removed_command_ids": removed_command_ids,
            "removed_phase_artifacts": removed_phase_artifacts,
            "checked_at": now_iso(),
        }
        self.save()
        return self.state.shared_context["phase_rerun_prune"]

    def prune_unfinished_phase_runtime_artifacts(self, *, start_phase: str) -> dict[str, Any]:
        """Remove stale runtime evidence for unfinished start/later phases.

        This supports safe phase re-runs from copied run roots: previous command
        ledger entries from the same unfinished phase must not be treated as
        freshly collected evidence.
        """

        if start_phase not in PHASE_SEQUENCE:
            return {"schema": "phase_runtime_prune_v1", "start_phase": start_phase, "pruned": False, "reason": "unknown_phase"}
        start_index = PHASE_SEQUENCE.index(start_phase)
        finished_phases = {
            str(item.get("phase", "")).strip()
            for item in self.state.phase_history
            if isinstance(item, dict) and str(item.get("phase", "")).strip()
        }
        candidate_phases = PHASE_SEQUENCE[start_index:]
        prune_phases = [phase for phase in candidate_phases if phase not in finished_phases and self.missing_required_artifacts(phase)]
        if not prune_phases:
            return {"schema": "phase_runtime_prune_v1", "start_phase": start_phase, "pruned": False, "pruned_phases": []}

        run_root = Path(self.state.run_root)
        removed_phase_artifacts: list[str] = []
        for phase_name in prune_phases:
            for key in list(self.state.phase_artifacts.get(phase_name, {}) or {}):
                path = phase_artifact_path(run_root, phase_name, str(key))
                if path.exists() and path.is_file():
                    try:
                        path.unlink()
                        removed_phase_artifacts.append(str(path.relative_to(run_root)))
                    except OSError:
                        pass
            self.state.phase_artifacts.pop(phase_name, None)

        removed_command_ids = self._prune_command_ledgers_for_phases(prune_phases)
        self.state.phase_history = [
            item
            for item in self.state.phase_history
            if not (isinstance(item, dict) and str(item.get("phase", "")).strip() in prune_phases)
        ]
        self.state.shared_context["phase_runtime_prune"] = {
            "schema": "phase_runtime_prune_v1",
            "start_phase": start_phase,
            "pruned": True,
            "pruned_phases": prune_phases,
            "removed_command_ids": removed_command_ids,
            "removed_phase_artifacts": removed_phase_artifacts,
            "checked_at": now_iso(),
        }
        self.save()
        return self.state.shared_context["phase_runtime_prune"]

    def prune_legacy_phase_finish_artifacts(self) -> dict[str, Any]:
        removed: list[str] = []
        run_root = Path(self.state.run_root)
        for phase_name in PHASE_SEQUENCE:
            phase_payload = self.state.phase_artifacts.get(phase_name, {})
            if not isinstance(phase_payload, dict):
                continue
            for key in list(phase_payload):
                if key not in LEGACY_PHASE_FINISH_ARTIFACT_KEYS:
                    continue
                phase_payload.pop(key, None)
                path = phase_artifact_path(run_root, phase_name, key)
                if path.exists() and path.is_file():
                    try:
                        path.unlink()
                    except OSError:
                        pass
                removed.append(f"{phase_name}:{key}")
            if phase_payload:
                self.state.phase_artifacts[phase_name] = phase_payload
            else:
                self.state.phase_artifacts.pop(phase_name, None)
        result = {
            "schema": "legacy_phase_finish_artifact_prune_v1",
            "removed": removed,
            "removed_count": len(removed),
            "checked_at": now_iso(),
        }
        if removed:
            self.state.shared_context["legacy_phase_finish_artifact_prune"] = result
            self.save()
        return result

    def _prune_command_ledgers_for_phases(self, phase_names: list[str]) -> list[str]:
        phase_set = set(phase_names)
        if not phase_set:
            return []
        run_root = Path(self.state.run_root)
        ledger_path = run_root / "artifacts" / "command_ledger" / "commands.json"
        removed_ids: list[str] = []
        kept_commands: list[dict[str, Any]] = []
        updated_at = now_iso()
        if ledger_path.exists():
            try:
                payload = json.loads(ledger_path.read_text(encoding="utf-8"))
            except Exception:
                payload = {"schema": "command_ledger_v1", "commands": [], "updated_at": updated_at}
            if not isinstance(payload, dict):
                payload = {"schema": "command_ledger_v1", "commands": [], "updated_at": updated_at}
            commands = payload.get("commands", [])
            if not isinstance(commands, list):
                commands = []
            for command in commands:
                if not isinstance(command, dict):
                    continue
                phase = str(command.get("phase", "")).strip()
                if phase in phase_set:
                    command_id = str(command.get("command_id", "")).strip()
                    if command_id:
                        removed_ids.append(command_id)
                else:
                    kept_commands.append(command)
            payload["schema"] = "command_ledger_v1"
            payload["commands"] = kept_commands
            payload["updated_at"] = updated_at
            ensure_dir(ledger_path.parent)
            ledger_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        jsonl_path = run_root / "command_ledger.jsonl"
        if jsonl_path.exists():
            kept_lines: list[str] = []
            for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    item = json.loads(line)
                except Exception:
                    kept_lines.append(line)
                    continue
                if isinstance(item, dict) and str(item.get("phase", "")).strip() in phase_set:
                    continue
                kept_lines.append(line)
            jsonl_path.write_text(("\n".join(kept_lines) + "\n") if kept_lines else "", encoding="utf-8")
        return removed_ids

    def sync_phase_artifacts_from_disk(self) -> None:
        run_root = Path(self.state.run_root)
        changed = False
        for phase_name, artifact_keys in PHASE_REQUIRED_ARTIFACTS.items():
            phase_payload = self.state.phase_artifacts.get(phase_name, {})
            if not isinstance(phase_payload, dict):
                phase_payload = {}
            for artifact_key in artifact_keys:
                existing = phase_payload.get(artifact_key)
                if phase_artifact_is_index_entry(existing):
                    continue
                artifact_path = phase_artifact_path(run_root, phase_name, artifact_key)
                if not artifact_path.exists() or not artifact_path.is_file():
                    continue
                try:
                    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
                except Exception:
                    payload = None
                phase_payload[artifact_key] = phase_artifact_index_entry(
                    run_root=run_root,
                    phase_name=phase_name,
                    artifact_key=artifact_key,
                    payload=payload,
                    artifact_path=artifact_path,
                )
                changed = True
            if phase_payload:
                self.state.phase_artifacts[phase_name] = phase_payload
        if changed:
            self.save()

    def missing_required_artifacts(self, phase_name: str) -> list[str]:
        if phase_name == "run_initialization":
            return self._missing_run_initialization_artifacts()
        required = PHASE_REQUIRED_ARTIFACTS.get(phase_name, ())
        phase_payload = self.state.phase_artifacts.get(phase_name, {})
        if not isinstance(phase_payload, dict):
            phase_payload = {}
        return [artifact_key for artifact_key in required if not self._phase_artifact_present(phase_name, artifact_key, phase_payload)]

    def _missing_run_initialization_artifacts(self) -> list[str]:
        run_root = Path(self.state.run_root)
        missing: list[str] = []
        config_path = run_root / "ae_config.yaml"
        if not config_path.exists() or not config_path.is_file():
            missing.append("ae_config")
            return missing
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8", errors="replace")) or {}
        except Exception:
            config = {}
        inputs = config.get("inputs", {}) if isinstance(config, dict) and isinstance(config.get("inputs"), dict) else {}
        paper_source = str(inputs.get("paper_pdf") or inputs.get("paper_source") or "").strip()
        if paper_source:
            paper_text_path = run_root / "artifacts" / "paper_source" / "paper_text.txt"
            if not paper_text_path.exists() or not paper_text_path.is_file() or paper_text_path.stat().st_size == 0:
                missing.append("paper_text")
        for rel in (
            "artifacts/memory/global_memory.md",
            "artifacts/memory/evidence_index.md",
            "artifacts/memory/current_stage_memory.md",
        ):
            path = run_root / rel
            if not path.exists() or not path.is_file():
                missing.append(rel)
        return missing

    def _phase_artifact_present(self, phase_name: str, artifact_key: str, phase_payload: dict[str, Any]) -> bool:
        if artifact_key not in phase_payload:
            run_root = Path(self.state.run_root)
            return not phase_artifact_validation_error(run_root, phase_name, artifact_key, phase_payload)
        value = phase_payload.get(artifact_key)
        if not phase_artifact_is_index_entry(value):
            return True
        run_root = Path(self.state.run_root)
        return not phase_artifact_validation_error(run_root, phase_name, artifact_key, phase_payload)

    def required_artifact_diagnostics(self, phase_name: str) -> list[dict[str, str]]:
        run_root = Path(self.state.run_root)
        required = PHASE_REQUIRED_ARTIFACTS.get(phase_name, ())
        phase_payload = self.state.phase_artifacts.get(phase_name, {})
        phase_payload = phase_payload if isinstance(phase_payload, dict) else {}
        diagnostics: list[dict[str, str]] = []
        for artifact_key in required:
            error = phase_artifact_validation_error(run_root, phase_name, artifact_key, phase_payload)
            if not error:
                continue
            path = phase_artifact_path(run_root, phase_name, artifact_key)
            diagnostics.append(
                {
                    "artifact_key": artifact_key,
                    "path": str(path.relative_to(run_root)) if path.is_relative_to(run_root) else str(path),
                    "status": "invalid_json" if error.startswith("invalid_json:") else "missing",
                    "detail": error,
                }
            )
        return diagnostics

    def required_artifacts_status(self, phase_name: str) -> dict[str, Any]:
        required = list(PHASE_REQUIRED_ARTIFACTS.get(phase_name, ()))
        missing = self.missing_required_artifacts(phase_name)
        return {
            "phase_name": phase_name,
            "required_artifacts": required,
            "missing_artifacts": missing,
            "complete": not missing,
        }

    def write_phase_artifact(self, phase_name: str, payload: dict[str, Any]) -> None:
        existing = self.state.phase_artifacts.get(phase_name)
        if isinstance(existing, dict) and isinstance(payload, dict):
            merged = dict(existing)
            for artifact_key, artifact_payload in payload.items():
                self._ensure_phase_artifact_on_disk(phase_name, str(artifact_key), artifact_payload)
                merged[artifact_key] = self.artifact_index_entry(phase_name, str(artifact_key), artifact_payload)
            self.state.phase_artifacts[phase_name] = merged
        else:
            if isinstance(payload, dict):
                indexed = {}
                for key, value in payload.items():
                    self._ensure_phase_artifact_on_disk(phase_name, str(key), value)
                    indexed[str(key)] = self.artifact_index_entry(phase_name, str(key), value)
                self.state.phase_artifacts[phase_name] = indexed
            else:
                self.state.phase_artifacts[phase_name] = payload
        self.save()

    def _ensure_phase_artifact_on_disk(self, phase_name: str, artifact_key: str, payload: Any) -> None:
        if phase_artifact_is_index_entry(payload):
            return
        path = phase_artifact_path(Path(self.state.run_root), phase_name, artifact_key)
        ensure_dir(path.parent)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def artifact_index_entry(self, phase_name: str, artifact_key: str, payload: Any | None = None) -> dict[str, Any]:
        return phase_artifact_index_entry(
            run_root=Path(self.state.run_root),
            phase_name=phase_name,
            artifact_key=artifact_key,
            payload=payload,
        )

    def read_phase_artifact(self, phase_name: str, artifact_key: str) -> Any:
        run_root = Path(self.state.run_root)
        entry = self.state.phase_artifacts.get(phase_name, {})
        artifact_ref = ""
        if isinstance(entry, dict) and phase_artifact_is_index_entry(entry.get(artifact_key)):
            artifact_ref = str(entry[artifact_key].get("artifact_ref", ""))
        path = (run_root / artifact_ref) if artifact_ref and not Path(artifact_ref).is_absolute() else Path(artifact_ref)
        if not artifact_ref:
            path = phase_artifact_path(run_root, phase_name, artifact_key)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def write_evidence(self, key: str, payload: Any) -> None:
        self.state.evidence_store[key] = payload
        self.save()

    def record_intervention(self, payload: dict[str, Any]) -> None:
        self.state.interventions.append(payload)
        self.save()

    def record_blocker(self, payload: dict[str, Any]) -> None:
        self.state.blockers.append(payload)
        self.save()

    def finish_phase(self, phase_name: str, finish_payload: dict[str, Any], next_phase: str | None) -> None:
        self.state.phase_history.append(
            {
                "phase": phase_name,
                "finished_at": now_iso(),
                "finish_payload": finish_payload,
            }
        )
        if next_phase:
            self.state.current_phase = next_phase
        self.save()
