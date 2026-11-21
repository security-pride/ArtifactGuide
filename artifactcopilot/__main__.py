from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.agent import ArtifactEvaluationAgent
from .core.runtime import AgentRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ArtifactCopilot AE agent")
    run_parser = parser
    run_parser.add_argument("repo", help="Path to the target artifact repository.")
    run_parser.add_argument(
        "--run-root",
        help="Optional output directory for this run. Defaults to <repo>/.artifactcopilot_run/<timestamp>.",
    )
    run_parser.add_argument("--paper", help="Optional path to the paper PDF or paper text source.")
    run_parser.add_argument("--config", help="Optional ArtifactCopilot YAML config path. Stored in run state for now.")
    run_parser.add_argument("--manual-paper-targets", help="Optional path to manual paper targets.")
    run_parser.add_argument(
        "--start-phase",
        default="run_initialization",
        choices=[
            "run_initialization",
            "available_gate",
            "D1_documentation",
            "D2_environment",
            "D3_executability",
            "D4_claim_support",
            "D5_reusability",
            "final_scoring",
            "report_generation",
        ],
    )
    run_parser.add_argument("--max-steps", type=int, default=0, help="Maximum agent steps. Use 0 for no explicit step limit.")
    run_parser.add_argument(
        "--stop-after-phase",
        choices=[
            "run_initialization",
            "available_gate",
            "D1_documentation",
            "D2_environment",
            "D3_executability",
            "D4_claim_support",
            "D5_reusability",
            "final_scoring",
            "report_generation",
        ],
        default="",
        help="Stop immediately after the named phase is successfully finished.",
    )
    run_parser.add_argument(
        "--memory-mode",
        choices=["legacy", "rubric_structured", "operational", "stable"],
        help=(
            "Prompt memory strategy. legacy keeps full working memory in every prompt; "
            "rubric_structured injects bounded evidence-index memory_v2 while still writing the full prompt memory to disk."
        ),
    )
    run_parser.add_argument(
        "--retrieval-max-tokens",
        type=int,
        help="Maximum token budget for rubric_structured/operational memory prompt slice.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:])

    runtime = AgentRuntime.create(
        repo_root=Path(args.repo).resolve(),
        run_root=Path(args.run_root).resolve() if args.run_root else None,
        start_phase=args.start_phase,
    )
    initial_context = {}
    if args.paper:
        initial_context["paper_source"] = str(Path(args.paper).resolve())
    if args.config:
        initial_context["config_path"] = str(Path(args.config).resolve())
    if args.manual_paper_targets:
        initial_context["manual_paper_targets"] = str(Path(args.manual_paper_targets).resolve())
    if initial_context:
        runtime.state_store.merge_shared_context(initial_context)
    if args.memory_mode:
        import os

        os.environ["AE_AGENT_MEMORY_MODE"] = args.memory_mode
    if args.retrieval_max_tokens is not None:
        import os

        os.environ["AE_AGENT_RETRIEVAL_MEMORY_MAX_TOKENS"] = str(int(args.retrieval_max_tokens))
    agent = ArtifactEvaluationAgent(runtime=runtime)
    agent.run(max_steps=int(args.max_steps), stop_after_phase=str(args.stop_after_phase or ""))


if __name__ == "__main__":
    main()
