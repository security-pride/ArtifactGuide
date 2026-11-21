from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..shared.fs import ensure_dir, make_run_id
from .memory import AgentMemory
from .run_logging import RunLogger, RunStatsTracker
from .state import AgentStateStore
from .trajectory import TrajectoryLogger


@dataclass
class AgentPaths:
    run_root: Path
    state_path: Path
    memory_path: Path
    trajectory_path: Path
    llm_monitor_path: Path
    run_log_path: Path
    run_stats_path: Path
    artifacts_dir: Path
    logs_dir: Path


@dataclass
class AgentRuntime:
    repo_root: Path
    run_id: str
    paths: AgentPaths
    requested_start_phase: str
    phase_preflight: dict
    state_store: AgentStateStore
    memory: AgentMemory
    trajectory: TrajectoryLogger
    run_logger: RunLogger
    run_stats: RunStatsTracker

    @classmethod
    def create(cls, *, repo_root: Path, run_root: Path | None, start_phase: str) -> "AgentRuntime":
        resolved_repo = repo_root.resolve()
        run_id = make_run_id()
        effective_run_root = run_root or (resolved_repo / ".artifactcopilot_run" / run_id)
        ensure_dir(effective_run_root)
        paths = AgentPaths(
            run_root=effective_run_root,
            state_path=effective_run_root / "agent_state.json",
            memory_path=effective_run_root / "agent_memory.json",
            trajectory_path=effective_run_root / "trajectory.jsonl",
            llm_monitor_path=effective_run_root / "llm_monitor.jsonl",
            run_log_path=effective_run_root / "run.log",
            run_stats_path=effective_run_root / "run_stats.json",
            artifacts_dir=effective_run_root / "artifacts",
            logs_dir=effective_run_root / "logs",
        )
        state_store = AgentStateStore(
            paths.state_path,
            run_id=run_id,
            repo_root=resolved_repo,
            run_root=effective_run_root,
            start_phase=start_phase,
        )
        state_store.load_or_init()
        state_store.prune_legacy_phase_finish_artifacts()
        phase_rerun_prune: dict | None = None
        if start_phase != "run_initialization":
            phase_rerun_prune = state_store.prune_rerun_phase_artifacts(start_phase=start_phase)
        phase_preflight = state_store.apply_start_phase_preflight(requested_start_phase=start_phase)
        if phase_rerun_prune is not None:
            phase_preflight["phase_rerun_prune"] = phase_rerun_prune
        if start_phase != "run_initialization":
            prune_result = state_store.prune_unfinished_phase_runtime_artifacts(
                start_phase=str(phase_preflight.get("effective_start_phase") or start_phase)
            )
            phase_preflight["phase_runtime_prune"] = prune_result
        memory = AgentMemory(paths.memory_path)
        trajectory = TrajectoryLogger(paths.trajectory_path)
        run_logger = RunLogger(paths.run_log_path)
        run_stats = RunStatsTracker(
            paths.run_stats_path,
            run_id=run_id,
            repo_root=str(resolved_repo),
        )
        return cls(
            repo_root=resolved_repo,
            run_id=run_id,
            paths=paths,
            requested_start_phase=start_phase,
            phase_preflight=phase_preflight,
            state_store=state_store,
            memory=memory,
            trajectory=trajectory,
            run_logger=run_logger,
            run_stats=run_stats,
        )
