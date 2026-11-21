from __future__ import annotations

from pathlib import Path
from typing import Any

from .core.agent import ArtifactEvaluationAgent
from .core.runtime import AgentRuntime


def create_runtime(
    *,
    artifact_repo: str | Path,
    run_root: str | Path | None = None,
    start_phase: str = "run_initialization",
    paper_source: str | Path | None = None,
    config_path: str | Path | None = None,
    manual_paper_targets: str | Path | None = None,
) -> AgentRuntime:
    runtime = AgentRuntime.create(
        repo_root=Path(artifact_repo).resolve(),
        run_root=Path(run_root).resolve() if run_root else None,
        start_phase=start_phase,
    )
    shared_context: dict[str, Any] = {}
    if paper_source:
        shared_context["paper_source"] = str(Path(paper_source).resolve())
    if config_path:
        shared_context["config_path"] = str(Path(config_path).resolve())
    if manual_paper_targets:
        shared_context["manual_paper_targets"] = str(Path(manual_paper_targets).resolve())
    if shared_context:
        runtime.state_store.merge_shared_context(shared_context)
    return runtime


def create_agent(
    *,
    artifact_repo: str | Path,
    run_root: str | Path | None = None,
    start_phase: str = "run_initialization",
    paper_source: str | Path | None = None,
    config_path: str | Path | None = None,
    manual_paper_targets: str | Path | None = None,
) -> ArtifactEvaluationAgent:
    runtime = create_runtime(
        artifact_repo=artifact_repo,
        run_root=run_root,
        start_phase=start_phase,
        paper_source=paper_source,
        config_path=config_path,
        manual_paper_targets=manual_paper_targets,
    )
    return ArtifactEvaluationAgent(runtime=runtime)


def run_review(
    *,
    artifact_repo: str | Path,
    paper_source: str | Path | None = None,
    run_root: str | Path | None = None,
    config_path: str | Path | None = None,
    manual_paper_targets: str | Path | None = None,
    start_phase: str = "run_initialization",
    stop_after_phase: str = "",
    max_steps: int = 0,
) -> AgentRuntime:
    agent = create_agent(
        artifact_repo=artifact_repo,
        run_root=run_root,
        start_phase=start_phase,
        paper_source=paper_source,
        config_path=config_path,
        manual_paper_targets=manual_paper_targets,
    )
    agent.run(max_steps=max_steps, stop_after_phase=stop_after_phase)
    return agent.runtime

