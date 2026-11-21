from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import Any

from ..llm.llm_client import DeepSeekLLMClient, PromptBundle
from ..shared.fs import ensure_dir, now_iso, relpath_str, safe_read_text, write_json
from ..tools.badge_guardrails import badge_guardrail_findings
from ..tools.consistency_checker import (
    final_packet_internal_consistency_findings,
    representative_workflow_findings,
    resource_boundary_findings,
    stage_final_semantics_findings,
)
from ..tools.rubric_evidence_engine import predicate_consistency_findings
from .prompting import estimate_text_tokens
from .state import AgentState


AUDITABLE_PHASES = {
    "run_initialization",
    "available_gate",
    "D1_documentation",
    "D2_environment",
    "D3_executability",
    "D4_claim_support",
    "D5_reusability",
    "final_scoring",
}
DEFAULT_MAX_AUDIT_ROUNDS = 3
DEFAULT_MAX_AUDITOR_STEPS = 5
MAX_AUDIT_FILE_CHARS = 48_000
MAX_AUDIT_TOTAL_CHARS = 180_000

RUBRIC_CRITICAL_FINAL_CODES = {
    "badge_claimed_with_unresolved_stage_final_boundary",
    "final_overrides_stage_representative_boundary_without_exception",
    "functional_claimed_despite_adjudication_insufficient",
    "functional_claimed_despite_resource_boundary_blocker",
    "functional_claimed_from_supporting_only_workflow",
    "missing_functional_sufficiency_value",
    "missing_representative_workflow_guardrail_fields",
    "missing_resource_boundary_assessment",
    "missing_resource_boundary_fields",
    "missing_reusable_sufficiency_value",
    "reusable_claimed_despite_adjudication_insufficient",
    "reusable_claimed_despite_resource_boundary_blocker",
    "reusable_claimed_from_supporting_transfer_with_unvalidated_primary_surface",
    "reusable_claimed_without_surface_arbitration",
    "reusable_supporting_surface_has_equivalence",
}

RUBRIC_CRITICAL_FINAL_MESSAGE_FRAGMENTS = (
    "functional_component_surface_has_equivalence",
    "functional_smoke_not_substitute_for_primary_pipeline",
    "functional_weak_bounded_route_not_primary_substitute",
    "reusable_supporting_surface_has_equivalence",
)


@dataclass(frozen=True)
class PhaseAuditProfile:
    phase_name: str
    mission: str
    required_refs: tuple[str, ...]
    audit_focus: tuple[str, ...]
    blocking_criteria: tuple[str, ...]
    warning_criteria: tuple[str, ...] = ()
    forbidden_overclaims: tuple[str, ...] = ()
    independent_read_guidance: tuple[str, ...] = ()
    suggested_read_refs: tuple[str, ...] = ()


PHASE_AUDIT_PROFILES: dict[str, PhaseAuditProfile] = {
    "run_initialization": PhaseAuditProfile(
        phase_name="run_initialization",
        mission="Audit initialization evidence: configuration, artifact universe, paper extraction when configured, and memory/index handoff into available_gate.",
        required_refs=(
            "ae_config.yaml",
            "artifacts/run_initialization/artifact_universe.json",
            "artifacts/memory/global_memory.md",
            "artifacts/memory/evidence_index.md",
            "artifacts/memory/current_stage_memory.md",
        ),
        audit_focus=(
            "Run configuration is present and points to the evaluated artifact.",
            "artifact_universe.json exists and preserves root-scope/boundary warnings for later stages.",
            "If a paper source is configured, current-run paper text/profile evidence exists or the limitation is explicit.",
            "Memory and evidence index carry initialization refs and available_gate starting context.",
        ),
        blocking_criteria=(
            "Configured artifact/root evidence is missing or cannot be inspected.",
            "artifact_universe.json is missing or boundary warnings are silently dropped.",
            "A configured paper source has no paper text/profile evidence and no explicit extraction limitation.",
            "Initialization memory/evidence index is missing, so available_gate would lose required context.",
        ),
        warning_criteria=(
            "Paper extraction is present but incomplete or weakly characterized.",
            "Artifact root scope is ambiguous but preserved for later stages.",
        ),
    ),
    "available_gate": PhaseAuditProfile(
        phase_name="available_gate",
        mission="Audit Available gate evidence before the run enters D1-D5 evidence collection.",
        required_refs=(
            "artifacts/available_gate/stage_packet.json",
            "artifacts/run_initialization/artifact_universe.json",
            "artifacts/memory/global_memory.md",
            "artifacts/memory/evidence_index.md",
            "artifacts/memory/current_stage_memory.md",
        ),
        audit_focus=(
            "GC1 source accessibility, GC2 reviewer-usable artifact-authored entry material, and GC3 paper connection are recorded as evidence, not final D1-D5 scores.",
            "The primary entry document or absence of one is grounded in files the collector actually read.",
            "Weak entry material such as source tree only, help text, title-only pages, or passive files is not overpromoted into GC2.",
            "The handoff tells D1 what documentation and artifact-scope caveats to preserve.",
        ),
        blocking_criteria=(
            "stage_packet.json is missing or lacks GC1/GC2/GC3-style evidence.",
            "GC2 is marked usable from source-discovered commands, help output, paper prose, or file names without artifact-authored entry material.",
            "Paper connection is asserted without paper/source evidence or an explicit limitation.",
            "The packet assigns D1-D5 scores, Functional/Reusability status, or badge recommendations.",
        ),
        warning_criteria=(
            "Entry documentation is weak but the uncertainty is explicitly recorded.",
            "Artifact universe scope is ambiguous but carried forward.",
        ),
        forbidden_overclaims=(
            "Do not require D1-D5 execution before Available gate.",
            "Do not promote source-code presence alone into an evaluation entry.",
        ),
    ),
    "D1_documentation": PhaseAuditProfile(
        phase_name="D1_documentation",
        mission="Audit whether D1 accurately extracted author-documented setup, run, data, and result instructions without inventing route decisions.",
        required_refs=(
            "artifacts/D1_documentation/stage_packet.json",
            "artifacts/D1_documentation/readme_workflow.json",
            "artifacts/run_initialization/artifact_universe.json",
            "artifacts/available_gate/stage_packet.json",
            "artifacts/memory/current_stage_memory.md",
            "artifacts/memory/stage_summaries.md",
            "artifacts/memory/evidence_index.md",
        ),
        audit_focus=(
            "README or equivalent entry documentation was read completely, or limitations are explicit.",
            "readme_workflow.json records a flat documented_commands list with source refs.",
            "Extracted commands preserve author-written command/action text, purpose, broad command_kind, is_shell_command, placeholders, and resource requirements when documented.",
            "The packet does not invent commands from arbitrary source files or classify D3/D5 eligibility.",
            "README file-purpose inventories are recorded as documented_files, not converted into invented commands such as 'Run analysis.py'.",
            "artifact_universe boundary warnings are preserved instead of claiming complete artifact coverage when scope is ambiguous.",
            "The stage packet summarizes documentation facts only and does not score D1-D5 or predict badges.",
        ),
        blocking_criteria=(
            "README/equivalent documentation was not read and the packet does not explain why.",
            "readme_workflow.json is missing or lacks a documented_commands list plus an explicit limitation when documentation contains no commands.",
            "A documented command/action lacks source_ref, so later stages cannot verify where it came from.",
            "The packet invents commands from source files that are not documented or explicitly pointed to by the artifact documentation.",
            "The packet rewrites passive file-purpose descriptions into commands, for example 'Run <script.py>' when the README only says what the script is for.",
            "The packet makes D3/D5 route selection, reuse, Functional/Reusability, score, or badge decisions.",
            "Artifact universe scope is ambiguous/subdir possible but D1 claims complete artifact-level route coverage.",
        ),
        warning_criteria=(
            "The documentation inventory is useful but may miss secondary docs.",
            "Documentation is weak, but the weakness is recorded without overclaiming.",
        ),
        independent_read_guidance=(
            "Before passing D1, independently check at least one artifact-authored entry document, preferably README.md or an equivalent README, using read_ref or a package excerpt.",
            "Compare the source document's documented instructions with documented_commands; do not rely only on the collector's prose summary.",
            "If the README has a 'Where is what', repository layout, file list, or script-purpose section without imperative run/open/execute wording, verify that those entries stayed in documented_files instead of documented_commands.",
            "Do not require route ranking, D3 eligibility, or D5 reuse classification in D1.",
        ),
    suggested_read_refs=(
        "repo:README.md",
        "repo:readme.md",
        "repo:README.rst",
        "repo:readme.rst",
        "repo:README.txt",
        "repo:readme.txt",
        "repo:INSTALL.md",
        "repo:install.md",
        "repo:REQUIREMENTS.md",
        "repo:requirements.txt",
    ),
    ),
    "D2_environment": PhaseAuditProfile(
        phase_name="D2_environment",
        mission="Audit whether D2 environment evidence is grounded, route-consistent, and sufficient for D3/D5 handoff.",
        required_refs=(
            "artifacts/D2_environment/stage_packet.json",
            "artifacts/D1_documentation/stage_packet.json",
            "artifacts/D1_documentation/readme_workflow.json",
            "artifacts/command_ledger/commands.json",
            "artifacts/interventions/intervention_ledger.json",
            "artifacts/memory/current_stage_memory.md",
            "artifacts/memory/stage_summaries.md",
            "artifacts/memory/evidence_index.md",
        ),
        audit_focus=(
            "The selected environment route is grounded in README/D1 evidence or clearly marked as fallback when README/D1 is missing or ambiguous.",
            "Setup/check commands are environment-only and do not run D3 representative workflows.",
            "Command results, failures, logs, image/tag/entrypoint, interpreter, activation, and working directory evidence are recorded with refs.",
            "Interventions and undeclared dependencies are recorded in stage packet and intervention ledger.",
            "The D3 handoff states the exact environment entry method and limitations.",
        ),
        blocking_criteria=(
            "stage_packet.json is missing or claims environment readiness without command/log or file evidence.",
            "The primary route ignores README/D1 instructions without explanation.",
            "D2 ran representative workflow, benchmark, paper-result, or claim-support commands and treated them as environment checks.",
            "Interpreter/container/activation context is contradictory or missing when the packet claims readiness.",
            "Interventions are visible in commands but missing from the packet/ledger.",
            "The packet assigns D1-D5 scores, Functional/Reusability status, or badge recommendations.",
        ),
        warning_criteria=(
            "Environment setup failed or is limited, but the limitation and logs are explicit.",
            "Version pins or dependency declarations are incomplete but handoff remains usable.",
        ),
        forbidden_overclaims=(
            "Do not require successful full setup when the evidence packet clearly records a setup blocker.",
            "Do not treat D3 workflow execution as a D2 success criterion.",
        ),
    ),
    "D3_executability": PhaseAuditProfile(
        phase_name="D3_executability",
        mission="Audit whether D3 execution evidence faithfully records the selected workflow attempt, observed outputs, and representativeness boundaries.",
        required_refs=(
            "artifacts/D3_executability/stage_packet.json",
            "artifacts/D3_executability/route_selection_checkpoint.json",
            "artifacts/D3_executability/route_evidence.json",
            "artifacts/D3_executability/evidence_semantics.json",
            "artifacts/D1_documentation/stage_packet.json",
            "artifacts/D1_documentation/readme_workflow.json",
            "artifacts/D2_environment/stage_packet.json",
            "artifacts/command_ledger/commands.json",
            "artifacts/interventions/intervention_ledger.json",
            "artifacts/memory/current_stage_memory.md",
            "artifacts/memory/stage_summaries.md",
            "artifacts/memory/evidence_index.md",
        ),
        audit_focus=(
            "The selected route is grounded in D1 flat documented_commands/documented_files or a README source correction with refs.",
            "route_selection_checkpoint.json records candidates, selected candidate, selection reason, and unselected candidate reasons before route execution evidence is finalized.",
            "The selected route's evidence eligibility is preserved: core public workflow, reduced representative workflow, component/helper/test/mock route, or result/post-processing-only route.",
            "Commands, cwd, environment entry, stdout/stderr refs, output snapshots, and failures are recorded.",
            "Observed output is classified without overclaiming: setup/help/generic examples are separated from artifact-specific, paper-facing output.",
            "route_evidence.json and evidence_semantics.json carry route arbitration, RW1-RW5 facts, resource boundaries, and alternative-route accounting.",
            "Mutable artifact outputs are copied or summarized under artifacts/D3_executability before leaving the stage.",
        ),
        blocking_criteria=(
            "stage_packet.json is missing or does not cite command/output evidence for the execution claim.",
            "route_selection_checkpoint.json, route_evidence.json, or evidence_semantics.json is missing or contradicted by stage_packet.json.",
            "The selected route is source-inferred from arbitrary code rather than grounded in D1/README facts.",
            "The packet claims successful execution from setup/help/import/generic dependency output alone.",
            "A demo/test/mock/helper route or result/post-processing-only route is presented as artifact-level representative execution without explicit route eligibility justification and boundary carry-forward.",
            "A failed/limited route is treated as decisive while route_evidence lists an unaddressed reviewer-realistic alternative and no explanation is given.",
            "Output refs/logs are missing or not preserved when the packet relies on them.",
            "Resource/API/toolchain boundaries are omitted or upgraded from suspected to confirmed without log evidence.",
            "The packet assigns D1-D5 scores, Functional/Reusability status, or badge recommendations.",
        ),
        warning_criteria=(
            "The route is bounded or component-only but this is clearly recorded.",
            "Execution failed, but the failure diagnosis and logs are concrete enough for final scoring.",
        ),
        independent_read_guidance=(
            "When route eligibility is material, inspect D1 readme_workflow/stage packet, route_selection_checkpoint, route_evidence, evidence_semantics, and, if needed, read the original repo README or log ref before passing D3.",
            "Do not accept collector wording alone when a selected route might be only a helper, mock, test, demo, or post-processing path.",
        ),
        suggested_read_refs=(
            "repo:README.md",
            "repo:README.rst",
        ),
        forbidden_overclaims=(
            "Do not require full paper reproduction when a bounded reviewer-realistic route is the stage result.",
            "Do not turn a single route failure into an artifact-level failure without route accounting.",
        ),
    ),
    "D4_claim_support": PhaseAuditProfile(
        phase_name="D4_claim_support",
        mission="Audit whether D4 maps D3 reviewer-observed evidence to locatable paper targets and applies the rubric Functional predicate required before D5.",
        required_refs=(
            "artifacts/D4_claim_support/stage_packet.json",
            "artifacts/D3_executability/stage_packet.json",
            "artifacts/D2_environment/stage_packet.json",
            "artifacts/D1_documentation/stage_packet.json",
            "artifacts/paper_source/paper_text.txt",
            "artifacts/memory/current_stage_memory.md",
            "artifacts/memory/stage_summaries.md",
            "artifacts/memory/evidence_index.md",
        ),
        audit_focus=(
            "Paper targets are locatable in paper text, tables, figures, sections, or result objects.",
            "Claim-support mapping uses D3 reviewer-observed outputs, not README promises or static files alone.",
            "Coverage boundaries distinguish full workflow, reduced workflow, component-only evidence, resource-gated evidence, and no-valid-output cases.",
            "Conflicts/differences and unsupported claims are recorded without assigning a D4 score, Reusable status, or badge recommendation.",
            "The packet contains a rubric-grounded functional_predicate and d5_entry_gate. The predicate conditions cite stage evidence and decide whether D5 should run.",
            "D4 carries D3 evidence_semantics/resource boundary facts forward rather than erasing them.",
        ),
        blocking_criteria=(
            "stage_packet.json is missing or lacks paper target mapping/blocked mapping explanation.",
            "stage_packet.json lacks functional_predicate or d5_entry_gate.",
            "functional_predicate.holds is true while a required Functional condition is missing, contradicted, below E2, or I4-dependent.",
            "functional_predicate.holds is false but the packet does not name the blocking Functional condition.",
            "D4 claims support from repository files, paper prose, README promises, or passive precomputed files without D3 reviewer-observed validation output.",
            "Paper targets are not locatable or have no paper refs/phrases/sections.",
            "D3 produced no valid output but D4 presents claim support as established.",
            "Known D3 boundaries or failures are omitted or contradicted.",
            "The packet assigns D1-D5 scores, Reusable status, or badge recommendations.",
        ),
        warning_criteria=(
            "Mapping is limited-scope but the coverage boundary is explicit.",
            "Paper text is available but target localization is coarse.",
        ),
        forbidden_overclaims=(
            "Do not run new commands in D4.",
            "Do not infer claim support from static/paper-only context when D3 execution evidence is absent.",
        ),
    ),
    "D5_reusability": PhaseAuditProfile(
        phase_name="D5_reusability",
        mission="Audit whether D5 records a grounded reuse surface, transfer plan, validation attempt, and transfer boundary without pre-deciding the Reusable badge.",
        required_refs=(
            "artifacts/D5_reusability/stage_packet.json",
            "artifacts/D4_claim_support/stage_packet.json",
            "artifacts/D3_executability/stage_packet.json",
            "artifacts/D2_environment/stage_packet.json",
            "artifacts/D1_documentation/readme_workflow.json",
            "artifacts/command_ledger/commands.json",
            "artifacts/interventions/intervention_ledger.json",
            "artifacts/memory/current_stage_memory.md",
            "artifacts/memory/stage_summaries.md",
            "artifacts/memory/evidence_index.md",
        ),
        audit_focus=(
            "Core surface analysis identifies the reusable module/API/CLI/data surface before transfer validation when D5 is not skipped.",
            "reuse_plan.json exists when transfer validation is planned or attempted and grounds the selected target in artifact evidence.",
            "Candidate consideration and route accounting cover meaningful D5 candidates and explain unattempted alternatives without forcing a large portfolio.",
            "Validation attempts cite commands/logs/outputs and distinguish successful transfer, failed transfer, same-workflow variant, and supporting-only probes.",
            "For any strong transfer claim, the packet identifies what artifact code/module/API/CLI surface was reused and what new task, project, scenario, integration, or reviewer-created driver/input made it outside the original artifact workflow.",
            "The packet explicitly separates true code reuse in a new context from rerunning the original artifact workflow with changed parameters, seeds, config values, paths, working directories, benchmark subset, or resource limits.",
            "d5_transfer_sufficiency_check explains whether the selected component/API/module is the artifact's intended public reusable surface, a documented subsurface, or only supporting/component evidence.",
            "If a full CLI/tool/pipeline surface is resource-gated and D5 validates a smaller component/API instead, the packet explicitly records whether that smaller surface is a bounded-equivalent public reuse surface or only partial evidence.",
            "Reviewer-created inputs/drivers stay under artifacts/D5_reusability and any artifact mutation is backed up/restored or explicitly recorded.",
            "D5 handoff gives final scoring enough facts to decide reuse novelty without badge advice.",
        ),
        blocking_criteria=(
            "stage_packet.json is missing, or reuse_plan.json is missing when a D5 validation is claimed.",
            "A reuse success is claimed without command/output evidence or without a grounded reusable surface.",
            "The selected transfer is merely the original paper workflow or demo but is presented as independent transfer without justification.",
            "The selected transfer changes only parameters, config values, seeds, paths, working directory, copied location, benchmark subset, input file within the same original task, or resource limits, but is presented as reuse in a new scenario.",
            "A strong transfer claim does not name the artifact code/module/API/CLI surface reused, the reviewer-owned driver/input/project/scenario, and why the validation is not just a same-workflow variant.",
            "validated_reuse_surface is upgraded to intended_public_surface without evidence that artifact-authored docs, public API contracts, or route records present that surface as future-user reusable rather than an internal helper/component.",
            "The transfer output is only stdout/helper output or a synthetic component run, while the primary public CLI/tool/pipeline surface remains unvalidated and D5 does not record this as a transfer boundary.",
            "Artifact mutations, backups, restores, or interventions are missing from packet/ledger.",
            "D3/D4 blockers make D5 prerequisite context unclear and D5 does not carry that boundary forward.",
            "The packet assigns D1-D5 scores, Functional/Reusability status, or badge recommendations.",
        ),
        warning_criteria=(
            "D5 only identified candidates or failed validation, but the boundary is explicit.",
            "Reuse target is component-level or data/result-level but surface intent is recorded.",
        ),
        independent_read_guidance=(
            "When a reuse success depends on a public surface claim, inspect reuse_plan/stage evidence and read the original README or documented API/CLI file if the supplied excerpt is insufficient.",
            "Do not rely only on collector prose to decide whether a helper/API/mock surface is the intended reusable surface.",
        ),
        suggested_read_refs=(
            "repo:README.md",
            "repo:README.rst",
            "repo:tool/README.md",
            "repo:docs/README.md",
        ),
        forbidden_overclaims=(
            "Do not equate a repeated paper workflow with independent reuse.",
            "Do not require a successful transfer when the stage packet honestly records why bounded reuse could not be validated.",
        ),
    ),
    "final_scoring": PhaseAuditProfile(
        phase_name="final_scoring",
        mission="Audit the final scoring packet for evidence-grounded rubric consistency without re-scoring from scratch.",
        required_refs=(
            "artifacts/final_scoring/final_scoring_packet.json",
            "artifacts/available_gate/stage_packet.json",
            "artifacts/D1_documentation/stage_packet.json",
            "artifacts/D2_environment/stage_packet.json",
            "artifacts/D3_executability/stage_packet.json",
            "artifacts/D4_claim_support/stage_packet.json",
            "artifacts/D5_reusability/stage_packet.json",
            "artifacts/command_ledger/commands.json",
            "artifacts/interventions/intervention_ledger.json",
            "artifacts/memory/evidence_index.md",
        ),
        audit_focus=(
            "Final scoring packet exists, is normalized, and cites stage packets/logs rather than memory-only claims.",
            "Available, D1-D5, evidence tier, Functional, Reusable, highest intervention, confidence, and badge fields are internally consistent.",
            "Functional/Reusability decisions name failed conditions when false and cite supporting evidence when true.",
            "D3/D4 resource-boundary and representativeness facts are preserved in e2_sufficiency/resource-boundary checks.",
            "D5 route facts allow reuse novelty to be recomputed; final badge follows gate booleans.",
            "When Reusable is true, final scoring explicitly arbitrates the intended reusable surface, the selected D5 transfer surface, and whether the selected transfer validates the artifact-level public surface or only supporting/component evidence.",
        ),
        blocking_criteria=(
            "final_scoring_packet.json is missing or structurally incomplete.",
            "A final score/gate/badge decision lacks evidence refs or contradicts cited stage packets.",
            "Functional or Reusable is true while required prerequisite gates are false or unsupported.",
            "Reusable is true but final scoring does not resolve a D5 reusable-surface boundary: intended public surface, selected transfer surface, and artifact-level sufficiency are missing, contradictory, or only asserted from component/helper evidence.",
            "Functional or Reusable is true while deterministic rubric-critical findings report unresolved representative-boundary, surface-equivalence, or sufficiency failures.",
            "highest_intervention is lower than intervention/command ledger evidence.",
            "The packet erases important D3/D4/D5 boundaries and replaces them with unsupported unknowns or stronger claims.",
        ),
        warning_criteria=(
            "A semantic judgment is debatable but all evidence and limitations are preserved for human review.",
            "Some optional refs are absent because Available gate short-circuited D1-D5.",
        ),
        forbidden_overclaims=(
            "Do not generate new stage evidence during final scoring audit.",
            "Do not re-score from scratch; audit consistency and evidence support.",
        ),
    ),
}


@dataclass
class EvidenceAuditResult:
    phase_name: str
    audit_round: int
    decision: str
    blocking_findings: list[dict[str, Any]]
    packet_path: Path
    packet: dict[str, Any]

    @property
    def requires_revision(self) -> bool:
        return self.decision == "revise_required" and bool(self.blocking_findings)


def run_evidence_audit_gate(
    *,
    llm: DeepSeekLLMClient,
    run_root: Path,
    repo_root: Path,
    phase_name: str,
    state: AgentState,
    skill_path: Path,
    max_rounds: int = DEFAULT_MAX_AUDIT_ROUNDS,
    max_steps: int = DEFAULT_MAX_AUDITOR_STEPS,
    max_tokens: int = 6000,
) -> EvidenceAuditResult | None:
    if phase_name not in AUDITABLE_PHASES:
        return None
    profile = _audit_profile(phase_name)
    max_rounds = max(1, int(max_rounds or DEFAULT_MAX_AUDIT_ROUNDS))
    max_steps = max(1, min(DEFAULT_MAX_AUDITOR_STEPS, int(max_steps or DEFAULT_MAX_AUDITOR_STEPS)))
    audit_state = _phase_audit_state(state, phase_name)
    audit_round = int(audit_state.get("rounds", 0) or 0) + 1
    evidence_package = _build_evidence_package(run_root=run_root, repo_root=repo_root, phase_name=phase_name, state=state, profile=profile)
    skill_text = safe_read_text(skill_path).strip()
    memory_path = run_root / "artifacts" / phase_name / f"evidence_auditor_memory_round_{audit_round:02d}.md"
    loop = _run_auditor_loop(
        llm=llm,
        run_root=run_root,
        repo_root=repo_root,
        phase_name=phase_name,
        audit_round=audit_round,
        max_rounds=max_rounds,
        max_steps=max_steps,
        skill_text=skill_text,
        profile=profile,
        evidence_package=evidence_package,
        memory_path=memory_path,
        max_tokens=max_tokens,
    )
    response_packet = loop.get("final_packet") if isinstance(loop.get("final_packet"), dict) else {}
    packet = _normalize_audit_packet(
        response_packet,
        phase_name=phase_name,
        audit_round=audit_round,
        max_rounds=max_rounds,
        evidence_package=evidence_package,
    )
    packet = _apply_deterministic_audit_escalations(
        packet,
        run_root=run_root,
        phase_name=phase_name,
        audit_round=audit_round,
        max_rounds=max_rounds,
    )
    packet["usage"] = _aggregate_usage(loop.get("step_usages", []))
    trace_path = run_root / "artifacts" / phase_name / f"evidence_audit_trace_round_{audit_round:02d}.json"
    packet_path = run_root / "artifacts" / phase_name / f"evidence_audit_round_{audit_round:02d}.json"
    loop_trace = {
        "schema": "evidence_auditor_loop_trace_v1",
        "phase_name": phase_name,
        "audit_round": audit_round,
        "max_audit_rounds": max_rounds,
        "max_auditor_steps": max_steps,
        "steps_taken": len(loop.get("steps", [])) if isinstance(loop.get("steps"), list) else 0,
        "final_decision": packet.get("decision", ""),
        "packet_ref": relpath_str(packet_path, run_root),
        "steps": loop.get("steps", []),
        "created_at": now_iso(),
    }
    write_json(trace_path, loop_trace)
    packet["auditor_loop"] = {
        "schema": "evidence_auditor_loop_summary_v1",
        "max_steps": max_steps,
        "steps_taken": loop_trace["steps_taken"],
        "trace_ref": relpath_str(trace_path, run_root),
        "memory_ref": relpath_str(memory_path, run_root),
        "completed_within_step_limit": loop_trace["steps_taken"] <= max_steps,
    }
    packet_path = run_root / "artifacts" / phase_name / f"evidence_audit_round_{audit_round:02d}.json"
    write_json(packet_path, packet)
    blocking = [
        item
        for item in packet.get("findings", [])
        if isinstance(item, dict)
        and str(item.get("severity", "")).strip() == "blocking"
        and bool(item.get("blocking_for_phase_finish", False))
    ]
    return EvidenceAuditResult(
        phase_name=phase_name,
        audit_round=audit_round,
        decision=str(packet.get("decision", "")).strip(),
        blocking_findings=blocking,
        packet_path=packet_path,
        packet=packet,
    )


def audit_context_for_prompt(state: AgentState, *, phase_name: str) -> str:
    audit = _phase_audit_state(state, phase_name)
    if not audit:
        return ""
    rounds = int(audit.get("rounds", 0) or 0)
    payload = {
        "phase_name": phase_name,
        "latest_decision": audit.get("latest_decision", ""),
        "rounds": rounds,
        "latest_packet_ref": audit.get("latest_packet_ref", ""),
        "unresolved_findings": audit.get("unresolved_findings", []),
        "instruction": (
            "If latest_decision is revise_required, address each finding before calling phase_finish again. "
            "Write the audit_response_path JSON with resolved/not_resolvable_with_stage_scope/disagreed_with_reason status, "
            "update current-stage artifacts, and preserve unresolved items as explicit limitations."
        ),
        "audit_response_path": f"artifacts/{phase_name}/audit_response_round_{rounds:02d}.json" if rounds > 0 else "",
        "audit_response_schema": {
            "schema": "audit_response_v1",
            "phase_name": phase_name,
            "audit_round": rounds,
            "audit_packet_ref": audit.get("latest_packet_ref", ""),
            "responses": [
                {
                    "finding_id": "copy from unresolved_findings[].id",
                    "status": "resolved | not_resolvable_with_stage_scope | disagreed_with_reason",
                    "action_taken": "exact artifact update, limitation added, or disagreement rationale",
                    "updated_refs": [],
                    "evidence_refs": [],
                }
            ],
            "remaining_limitations": [],
        },
    }
    return "\n".join(
        [
            "<evidence_audit_context>",
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            "</evidence_audit_context>",
        ]
    )


def update_state_with_audit_result(state: AgentState, *, run_root: Path, result: EvidenceAuditResult) -> dict[str, Any]:
    audit = state.shared_context.get("evidence_audit")
    if not isinstance(audit, dict):
        audit = {}
    rel_packet = relpath_str(result.packet_path, run_root)
    unresolved = [
        item
        for item in result.packet.get("findings", [])
        if isinstance(item, dict)
        and (
            str(item.get("severity", "")).strip() == "blocking"
            or bool(item.get("blocking_for_phase_finish", False))
        )
    ]
    audit[result.phase_name] = {
        "schema": "phase_evidence_audit_state_v1",
        "phase_name": result.phase_name,
        "rounds": result.audit_round,
        "latest_decision": result.decision,
        "latest_packet_ref": rel_packet,
        "unresolved_findings": unresolved,
        "must_carry_to_final_scoring": result.packet.get("must_carry_to_final_scoring", []),
        "updated_at": now_iso(),
    }
    return audit


def _phase_audit_state(state: AgentState, phase_name: str) -> dict[str, Any]:
    audit = state.shared_context.get("evidence_audit")
    if not isinstance(audit, dict):
        return {}
    item = audit.get(phase_name)
    return item if isinstance(item, dict) else {}


def _auditor_system_prompt() -> str:
    return dedent(
        """
        You are ArtifactCopilot's Evidence Auditor.

        Your job is to audit whether a stage evidence packet is grounded, complete enough for the stage's evidence-collection mission, and safe to admit into the next stage.

        You run as a separate lightweight auditor agent loop, not as the collector agent. You must complete the audit within 5 auditor steps or fewer. By step 5 you must return a final_audit action.

        In evidence-collection phases, you are not a scoring agent. Do not assign D1-D5 scores, Reusable status, or badge recommendations. D4 has one explicit exception: it must apply the rubric Functional predicate as `functional_predicate`/`d5_entry_gate` to decide whether D5 should run. Audit that D4 predicate for evidence support instead of treating it as an overclaim. In final_scoring, do not re-score from scratch; audit whether the final scoring packet is internally consistent and evidence-grounded. Do not request new commands unless a concrete evidence gap requires the collector to read, inspect, or execute something already in the stage's scope. Do not require full reproduction merely because bounded review evidence is limited.

        Output JSON only. Findings must be concrete, evidence-grounded, and actionable. Prefer no blocking finding when the packet gives a clear limitation with refs and no silent overclaim.
        """
    ).strip()


def _run_auditor_loop(
    *,
    llm: DeepSeekLLMClient,
    run_root: Path,
    repo_root: Path,
    phase_name: str,
    audit_round: int,
    max_rounds: int,
    max_steps: int,
    skill_text: str,
    profile: PhaseAuditProfile,
    evidence_package: dict[str, Any],
    memory_path: Path,
    max_tokens: int,
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    step_usages: list[dict[str, Any]] = []
    final_packet: dict[str, Any] | None = None
    _append_auditor_memory(
        memory_path,
        "\n".join(
            [
                f"# Evidence Auditor Memory Round {audit_round:02d}",
                "",
                f"- phase: `{phase_name}`",
                f"- mission: {profile.mission}",
                f"- max_auditor_steps: {max_steps}",
                f"- started_at: {now_iso()}",
                "",
                "This is append-only working memory for the lightweight auditor loop.",
            ]
        ),
    )
    for step_index in range(1, max_steps + 1):
        is_final_step = step_index >= max_steps
        memory_text = safe_read_text(memory_path)
        response = llm.chat_json(
            PromptBundle(
                system=_auditor_system_prompt(),
                user=_auditor_loop_user_prompt(
                    phase_name=phase_name,
                    audit_round=audit_round,
                    max_rounds=max_rounds,
                    step_index=step_index,
                    max_steps=max_steps,
                    is_final_step=is_final_step,
                    skill_text=skill_text,
                    profile=profile,
                    evidence_package=evidence_package,
                    observations=observations,
                    memory_text=memory_text,
                ),
                meta={
                    "task": "evidence_auditor_loop",
                    "label": phase_name,
                    "audit_round": audit_round,
                    "auditor_step": step_index,
                    "prompt_section_stats": {
                        "skill": estimate_text_tokens(skill_text),
                        "evidence_package": estimate_text_tokens(json.dumps(evidence_package, ensure_ascii=False, default=str)),
                        "observations": estimate_text_tokens(json.dumps(observations, ensure_ascii=False, default=str)),
                    },
                },
            ),
            max_tokens=max_tokens,
        )
        usage = response.get("usage", {}) if isinstance(response, dict) else {}
        if isinstance(usage, dict):
            step_usages.append(usage)
        action = _normalize_auditor_action(response if isinstance(response, dict) else {}, force_final=is_final_step)
        action = _enforce_required_auditor_probe(
            action,
            phase_name=phase_name,
            profile=profile,
            observations=observations,
            repo_root=repo_root,
            is_final_step=is_final_step,
        )
        observation = _execute_auditor_action(
            action,
            run_root=run_root,
            repo_root=repo_root,
            phase_name=phase_name,
            evidence_package=evidence_package,
        )
        steps.append(
            {
                "step_index": step_index,
                "action": action,
                "observation": observation,
            }
        )
        observations.append(observation)
        _append_auditor_memory(
            memory_path,
            _format_auditor_memory_step(
                step_index=step_index,
                action=action,
                observation=observation,
            ),
        )
        if action.get("action") == "final_audit":
            final_packet = action.get("audit_packet") if isinstance(action.get("audit_packet"), dict) else {}
            break
    if final_packet is None:
        final_packet = _fallback_loop_packet(phase_name=phase_name, observations=observations)
    _append_auditor_memory(
        memory_path,
        "\n".join(
            [
                "## Final Audit",
                f"- time: {now_iso()}",
                f"- decision: `{final_packet.get('decision', '')}`",
                f"- summary: {str(final_packet.get('audit_summary', '')).strip()}",
                f"- findings: {len(final_packet.get('findings', [])) if isinstance(final_packet.get('findings'), list) else 0}",
            ]
        ),
    )
    return {
        "final_packet": final_packet,
        "steps": steps,
        "step_usages": step_usages,
    }


def _auditor_loop_user_prompt(
    *,
    phase_name: str,
    audit_round: int,
    max_rounds: int,
    step_index: int,
    max_steps: int,
    is_final_step: bool,
    skill_text: str,
    profile: PhaseAuditProfile,
    evidence_package: dict[str, Any],
    observations: list[dict[str, Any]],
    memory_text: str,
) -> str:
    return "\n".join(
        [
            f"<phase_name>{phase_name}</phase_name>",
            f"<audit_round>{audit_round}</audit_round>",
            f"<max_audit_rounds>{max_rounds}</max_audit_rounds>",
            f"<auditor_step>{step_index}</auditor_step>",
            f"<max_auditor_steps>{max_steps}</max_auditor_steps>",
            "<step_limit_instruction>",
            (
                "This is the final allowed auditor step. You must return action `final_audit` now."
                if is_final_step
                else "You must complete the whole audit within 5 auditor steps or fewer. Use `final_audit` as soon as you have enough evidence."
            ),
            "</step_limit_instruction>",
            "",
            "<evidence_auditor_skill>",
            skill_text,
            "</evidence_auditor_skill>",
            "",
            "<phase_audit_profile>",
            json.dumps(_profile_prompt_payload(profile), ensure_ascii=False, indent=2),
            "</phase_audit_profile>",
            "",
            "<independent_source_check_instruction>",
            (
                "If phase_audit_profile.independent_read_guidance is non-empty, use read_ref or inspect_packet to verify the material point from source evidence before final_audit. "
                "For D1_documentation, independently read at least one artifact-authored repo entry document such as README.md when available, then compare it with documented_commands and documented_files. Do not pass invented 'Run <file>' commands derived only from a passive file-purpose list."
            ),
            "</independent_source_check_instruction>",
            "",
            "<evidence_package>",
            json.dumps(evidence_package, ensure_ascii=False, indent=2, default=str),
            "</evidence_package>",
            "",
            "<auditor_observations_so_far>",
            json.dumps(observations, ensure_ascii=False, indent=2, default=str),
            "</auditor_observations_so_far>",
            "",
            "<auditor_append_only_memory>",
            memory_text.strip() or "(empty)",
            "</auditor_append_only_memory>",
            "",
            "<allowed_actions>",
            json.dumps(_auditor_action_schema_hint(), ensure_ascii=False, indent=2),
            "</allowed_actions>",
            "",
            "<final_audit_packet_schema>",
            json.dumps(_audit_output_schema_hint(), ensure_ascii=False, indent=2),
            "</final_audit_packet_schema>",
            "",
            "Return one JSON object only. Do not call collector tools. Do not execute commands. Do not score the artifact.",
        ]
    ).strip()


def _profile_prompt_payload(profile: PhaseAuditProfile) -> dict[str, Any]:
    return {
        "phase_name": profile.phase_name,
        "mission": profile.mission,
        "required_refs": list(profile.required_refs),
        "audit_focus": list(profile.audit_focus),
        "blocking_criteria": list(profile.blocking_criteria),
        "warning_criteria": list(profile.warning_criteria),
        "forbidden_overclaims": list(profile.forbidden_overclaims),
        "independent_read_guidance": list(profile.independent_read_guidance),
        "suggested_read_refs": list(profile.suggested_read_refs),
    }


def _auditor_action_schema_hint() -> dict[str, Any]:
    return {
        "action": "inspect_packet | read_ref | final_audit",
        "rationale": "brief reason",
        "inspect_packet": {
            "targets": [
                "required_refs",
                "current_stage_packet",
                "prior_stage_packets",
                "final_scoring_packet",
                "readme_workflow",
                "reuse_plan",
                "artifact_universe",
                "available_gate",
                "command_ledger",
                "intervention_ledger",
                "paper_source",
                "memory",
            ],
            "questions": ["specific checks to perform"],
        },
        "read_ref": {
            "base": "run | repo",
            "path": "relative path under run root or repo root",
            "reason": "why this file is necessary for the audit",
            "max_chars": 12000,
        },
        "audit_packet": _audit_output_schema_hint(),
    }


def _normalize_auditor_action(obj: dict[str, Any], *, force_final: bool) -> dict[str, Any]:
    if isinstance(obj, dict) and ("decision" in obj or "findings" in obj):
        return {
            "action": "final_audit",
            "rationale": str(obj.get("audit_summary", "Returned audit packet directly.")).strip(),
            "audit_packet": obj,
        }
    action = str(obj.get("action", "")).strip()
    if action not in {"inspect_packet", "read_ref", "final_audit"}:
        action = "final_audit" if force_final else "inspect_packet"
    if force_final and action != "final_audit":
        action = "final_audit"
    normalized: dict[str, Any] = {
        "action": action,
        "rationale": str(obj.get("rationale", "")).strip(),
    }
    if action == "inspect_packet":
        inspect = obj.get("inspect_packet") if isinstance(obj.get("inspect_packet"), dict) else {}
        normalized["inspect_packet"] = {
            "targets": _string_list(inspect.get("targets", [])),
            "questions": _string_list(inspect.get("questions", [])),
        }
    elif action == "read_ref":
        read_ref = obj.get("read_ref") if isinstance(obj.get("read_ref"), dict) else {}
        base = str(read_ref.get("base", "")).strip()
        if base not in {"run", "repo"}:
            base = "run"
        normalized["read_ref"] = {
            "base": base,
            "path": str(read_ref.get("path", "")).strip(),
            "reason": str(read_ref.get("reason", "")).strip(),
            "max_chars": _safe_int(read_ref.get("max_chars"), default=12_000, lower=1000, upper=48_000),
        }
    else:
        packet = obj.get("audit_packet") if isinstance(obj.get("audit_packet"), dict) else {}
        if not packet and isinstance(obj.get("packet"), dict):
            packet = obj.get("packet")
        if not packet and ("decision" in obj or "findings" in obj):
            packet = obj
        normalized["audit_packet"] = packet if isinstance(packet, dict) else {}
    return normalized


def _enforce_required_auditor_probe(
    action: dict[str, Any],
    *,
    phase_name: str,
    profile: PhaseAuditProfile,
    observations: list[dict[str, Any]],
    repo_root: Path,
    is_final_step: bool,
) -> dict[str, Any]:
    if is_final_step:
        return action
    if phase_name != "D1_documentation":
        return action
    if _auditor_has_repo_source_read(observations):
        return action
    if action.get("action") == "read_ref":
        read_ref = action.get("read_ref") if isinstance(action.get("read_ref"), dict) else {}
        if str(read_ref.get("base", "")).strip() == "repo":
            return action

    ref = _first_existing_suggested_repo_ref(repo_root, profile.suggested_read_refs)
    if not ref:
        return action
    return {
        "action": "read_ref",
        "rationale": (
            "D1 audit requires an independent check of an artifact-authored entry document before passing route "
            "portfolio and canonical route plan evidence."
        ),
        "read_ref": {
            "base": "repo",
            "path": ref,
            "reason": "Verify D1 route extraction and route eligibility against original documentation.",
            "max_chars": 48000,
        },
    }


def _auditor_has_repo_source_read(observations: list[dict[str, Any]]) -> bool:
    for item in observations:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind", "")).strip() != "read_ref":
            continue
        if str(item.get("base", "")).strip() == "repo" and bool(item.get("exists", False)):
            return True
    return False


def _first_existing_suggested_repo_ref(repo_root: Path, refs: tuple[str, ...]) -> str:
    for ref in refs:
        raw = str(ref).strip()
        if not raw.startswith("repo:"):
            continue
        rel = raw.removeprefix("repo:").strip()
        if not rel or Path(rel).is_absolute():
            continue
        path = (repo_root / rel).resolve()
        try:
            path.relative_to(repo_root.resolve())
        except ValueError:
            continue
        if path.exists() and path.is_file():
            return rel
    return ""


def _execute_auditor_action(
    action: dict[str, Any],
    *,
    run_root: Path,
    repo_root: Path,
    phase_name: str,
    evidence_package: dict[str, Any],
) -> dict[str, Any]:
    action_name = str(action.get("action", "")).strip()
    if action_name == "inspect_packet":
        return _auditor_inspect_packet_observation(action, evidence_package=evidence_package, phase_name=phase_name)
    if action_name == "read_ref":
        return _auditor_read_ref_observation(action, run_root=run_root, repo_root=repo_root)
    if action_name == "final_audit":
        packet = action.get("audit_packet") if isinstance(action.get("audit_packet"), dict) else {}
        return {
            "kind": "final_audit_received",
            "summary": f"Auditor returned final audit packet with decision={packet.get('decision', '')}.",
        }
    return {"kind": "unsupported_action", "summary": f"Unsupported auditor action `{action_name}`."}


def _auditor_inspect_packet_observation(action: dict[str, Any], *, evidence_package: dict[str, Any], phase_name: str) -> dict[str, Any]:
    files = evidence_package.get("files", []) if isinstance(evidence_package.get("files"), list) else []
    by_ref = {str(item.get("ref", "")): item for item in files if isinstance(item, dict)}
    targets = action.get("inspect_packet", {}).get("targets", []) if isinstance(action.get("inspect_packet"), dict) else []
    target_refs = _target_refs_for_inspection(phase_name, targets, evidence_package=evidence_package)
    snippets: list[dict[str, Any]] = []
    for ref in target_refs:
        item = by_ref.get(ref)
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", ""))
        snippets.append(
            {
                "ref": ref,
                "exists": bool(item.get("exists")),
                "chars": item.get("chars", 0),
                "content_excerpt": _truncate_middle(content, 12_000),
            }
        )
    if not snippets:
        snippets = [
            {
                "ref": str(item.get("ref", "")),
                "exists": bool(item.get("exists")),
                "chars": item.get("chars", 0),
                "content_excerpt": _truncate_middle(str(item.get("content", "")), 6000),
            }
            for item in files[:8]
            if isinstance(item, dict)
        ]
    return {
        "kind": "packet_inspection",
        "summary": f"Inspected {len(snippets)} evidence package file(s).",
        "targets": targets,
        "questions": action.get("inspect_packet", {}).get("questions", []) if isinstance(action.get("inspect_packet"), dict) else [],
        "snippets": snippets,
    }


def _target_refs_for_inspection(phase_name: str, targets: Any, *, evidence_package: dict[str, Any]) -> list[str]:
    target_set = set(_string_list(targets))
    required_refs = [
        str(item)
        for item in evidence_package.get("required_refs", [])
        if str(item).strip()
    ]
    prior_stage_refs = [
        "artifacts/available_gate/stage_packet.json",
        "artifacts/D1_documentation/stage_packet.json",
        "artifacts/D2_environment/stage_packet.json",
        "artifacts/D3_executability/stage_packet.json",
        "artifacts/D4_claim_support/stage_packet.json",
        "artifacts/D5_reusability/stage_packet.json",
    ]
    mapping = {
        "required_refs": required_refs,
        "stage_packet": [f"artifacts/{phase_name}/stage_packet.json"],
        "current_stage_packet": [f"artifacts/{phase_name}/stage_packet.json"],
        "prior_stage_packets": [ref for ref in prior_stage_refs if ref != f"artifacts/{phase_name}/stage_packet.json"],
        "final_scoring_packet": ["artifacts/final_scoring/final_scoring_packet.json"],
        "readme_workflow": ["artifacts/D1_documentation/readme_workflow.json"],
        "reuse_plan": ["artifacts/D5_reusability/reuse_plan.json"],
        "artifact_universe": ["artifacts/run_initialization/artifact_universe.json"],
        "available_gate": ["artifacts/available_gate/stage_packet.json"],
        "command_ledger": ["artifacts/command_ledger/commands.json", f"artifacts/{phase_name}/command_ledger.json"],
        "intervention_ledger": ["artifacts/interventions/intervention_ledger.json"],
        "paper_source": ["artifacts/paper_source/paper_text.txt", "artifacts/paper_source/paper_source_profile.json"],
        "memory": [
            "artifacts/memory/global_memory.md",
            "artifacts/memory/stage_summaries.md",
            "artifacts/memory/evidence_index.md",
            "artifacts/memory/current_stage_memory.md",
            "artifacts/memory/current_stage_steps.md",
        ],
        "route_surface_audit": [f"artifacts/{phase_name}/route_surface_audit_warnings.json"],
    }
    refs: list[str] = []
    for target in target_set:
        refs.extend(mapping.get(target, []))
    if not refs:
        refs.extend(required_refs or mapping["current_stage_packet"])
    return _dedupe_strings(refs)


def _auditor_read_ref_observation(action: dict[str, Any], *, run_root: Path, repo_root: Path) -> dict[str, Any]:
    read_ref = action.get("read_ref") if isinstance(action.get("read_ref"), dict) else {}
    base = str(read_ref.get("base", "run")).strip()
    root = repo_root if base == "repo" else run_root
    rel = str(read_ref.get("path", "")).strip()
    max_chars = _safe_int(read_ref.get("max_chars"), default=12_000, lower=1000, upper=48_000)
    if not rel or Path(rel).is_absolute():
        return {
            "kind": "read_ref_error",
            "summary": "Auditor read_ref rejected: path must be relative.",
            "base": base,
            "path": rel,
        }
    path = (root / rel).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return {
            "kind": "read_ref_error",
            "summary": "Auditor read_ref rejected: path escapes allowed root.",
            "base": base,
            "path": rel,
        }
    if not path.exists() or not path.is_file():
        return {
            "kind": "read_ref",
            "summary": "Requested file does not exist.",
            "base": base,
            "path": rel,
            "exists": False,
        }
    text = safe_read_text(path)
    return {
        "kind": "read_ref",
        "summary": f"Read {rel}.",
        "base": base,
        "path": rel,
        "exists": True,
        "chars": len(text),
        "truncated": len(text) > max_chars,
        "content_excerpt": _truncate_middle(text, max_chars),
    }


def _fallback_loop_packet(*, phase_name: str, observations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "evidence_audit_packet_v1",
        "phase_name": phase_name,
        "decision": "pass",
        "audit_summary": "Auditor loop reached its step limit without an explicit final packet; no blocking finding was produced by the loop observations.",
        "checked_refs": [],
        "findings": [],
        "resolved_prior_findings": [],
        "unresolved_prior_findings": [],
        "must_carry_to_final_scoring": [
            {
                "type": "auditor_loop_limit_fallback",
                "observations": observations[-3:],
            }
        ],
    }


def _audit_output_schema_hint() -> dict[str, Any]:
    return {
        "schema": "evidence_audit_packet_v1",
        "phase_name": "current phase name",
        "audit_round": 1,
        "decision": "pass | revise_required | pass_with_unresolved_findings",
        "audit_summary": "short summary",
        "checked_refs": [],
        "findings": [
            {
                "id": "D3_EXEC_001",
                "severity": "blocking | warning | info",
                "category": "missing_ref | contradiction | route_coverage | environment_context | execution_evidence | paper_target_mapping | reuse_validation | final_consistency | evidence_refs | intervention_consistency | artifact_scope | stage_protocol",
                "message": "specific issue",
                "evidence_gap": "missing evidence",
                "required_action": "concrete collector action",
                "refs_checked": [],
                "related_stage_refs": [],
                "blocking_for_phase_finish": True,
            }
        ],
        "resolved_prior_findings": [],
        "unresolved_prior_findings": [],
        "must_carry_to_final_scoring": [],
    }


def _normalize_audit_packet(
    packet: dict[str, Any],
    *,
    phase_name: str,
    audit_round: int,
    max_rounds: int,
    evidence_package: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(packet if isinstance(packet, dict) else {})
    normalized["schema"] = "evidence_audit_packet_v1"
    normalized["phase_name"] = phase_name
    normalized["audit_round"] = audit_round
    findings = normalized.get("findings")
    if not isinstance(findings, list):
        findings = []
    normalized_findings: list[dict[str, Any]] = []
    for index, item in enumerate(findings, start=1):
        if not isinstance(item, dict):
            continue
        severity = _enum(str(item.get("severity", "")).strip(), {"blocking", "warning", "info"}, default="warning")
        category = _enum(
            str(item.get("category", "")).strip(),
            {
                "missing_ref",
                "contradiction",
                "route_coverage",
                "command_interpretation",
                "representative_boundary",
                "claim_mapping",
                "transfer_boundary",
                "intervention_consistency",
                "environment_context",
                "execution_evidence",
                "paper_target_mapping",
                "reuse_validation",
                "final_consistency",
                "evidence_refs",
                "initialization",
                "artifact_scope",
                "stage_protocol",
            },
            default="stage_protocol",
        )
        normalized_findings.append(
            {
                "id": str(item.get("id") or f"{phase_name}_AUDIT_{index:03d}"),
                "severity": severity,
                "category": category,
                "message": str(item.get("message", "")).strip(),
                "evidence_gap": str(item.get("evidence_gap", "")).strip(),
                "required_action": str(item.get("required_action", "")).strip(),
                "refs_checked": _string_list(item.get("refs_checked", [])),
                "related_stage_refs": _string_list(item.get("related_stage_refs", [])),
                "blocking_for_phase_finish": bool(item.get("blocking_for_phase_finish", severity == "blocking")),
            }
        )
    normalized["findings"] = normalized_findings
    blocking = [item for item in normalized_findings if item["severity"] == "blocking" and item["blocking_for_phase_finish"]]
    decision = _enum(str(normalized.get("decision", "")).strip(), {"pass", "revise_required", "pass_with_unresolved_findings"}, default="")
    if blocking and audit_round >= max_rounds:
        decision = "pass_with_unresolved_findings"
    elif blocking:
        decision = "revise_required"
    elif not decision:
        decision = "pass"
    normalized["decision"] = decision
    normalized["audit_summary"] = str(normalized.get("audit_summary", "")).strip() or _default_summary(decision, blocking)
    normalized["checked_refs"] = _string_list(normalized.get("checked_refs", [])) or _default_checked_refs(evidence_package)
    normalized["resolved_prior_findings"] = _list_of_dicts(normalized.get("resolved_prior_findings", []))
    normalized["unresolved_prior_findings"] = _list_of_dicts(normalized.get("unresolved_prior_findings", []))
    carry = normalized.get("must_carry_to_final_scoring")
    if not isinstance(carry, list):
        carry = []
    if decision == "pass_with_unresolved_findings":
        carry = carry + blocking
    normalized["must_carry_to_final_scoring"] = carry
    normalized["audited_at"] = now_iso()
    return normalized


def _apply_deterministic_audit_escalations(
    packet: dict[str, Any],
    *,
    run_root: Path,
    phase_name: str,
    audit_round: int,
    max_rounds: int,
) -> dict[str, Any]:
    if phase_name in {"D1_documentation", "D3_executability", "D5_reusability"}:
        packet = _apply_route_surface_audit_escalations(
            packet,
            run_root=run_root,
            phase_name=phase_name,
            audit_round=audit_round,
            max_rounds=max_rounds,
        )
    if phase_name != "final_scoring":
        return packet
    final_packet_path = run_root / "artifacts" / "final_scoring" / "final_scoring_packet.json"
    try:
        final_packet = json.loads(final_packet_path.read_text(encoding="utf-8"))
    except Exception:
        return packet
    if not isinstance(final_packet, dict):
        return packet

    escalated = _rubric_critical_final_findings(run_root, final_packet)
    if not escalated:
        return packet

    normalized = dict(packet)
    findings = normalized.get("findings")
    if not isinstance(findings, list):
        findings = []
    else:
        findings = list(findings)
    seen = {
        str(item.get("id", ""))
        for item in findings
        if isinstance(item, dict)
    }
    for finding in escalated:
        if finding["id"] not in seen:
            findings.append(finding)
            seen.add(finding["id"])
    normalized["findings"] = findings
    blocking = [
        item
        for item in findings
        if isinstance(item, dict)
        and str(item.get("severity", "")).strip() == "blocking"
        and bool(item.get("blocking_for_phase_finish", False))
    ]
    if blocking and audit_round >= max_rounds:
        normalized["decision"] = "pass_with_unresolved_findings"
        carry = normalized.get("must_carry_to_final_scoring")
        if not isinstance(carry, list):
            carry = []
        normalized["must_carry_to_final_scoring"] = carry + blocking
    elif blocking:
        normalized["decision"] = "revise_required"
    if blocking:
        base_summary = str(normalized.get("audit_summary", "")).strip()
        suffix = (
            f" Deterministic rubric-critical audit escalated {len(blocking)} finding(s); "
            "final_scoring must resolve the gate/surface boundary or lower the affected badge decision."
        )
        normalized["audit_summary"] = (base_summary + suffix).strip() if base_summary else suffix.strip()
    return normalized


def _apply_route_surface_audit_escalations(
    packet: dict[str, Any],
    *,
    run_root: Path,
    phase_name: str,
    audit_round: int,
    max_rounds: int,
) -> dict[str, Any]:
    warning_path = run_root / "artifacts" / phase_name / "route_surface_audit_warnings.json"
    try:
        payload = json.loads(warning_path.read_text(encoding="utf-8"))
    except Exception:
        return packet
    warnings = payload.get("warnings") if isinstance(payload, dict) else None
    if not isinstance(warnings, list) or not warnings:
        return packet
    blocking_codes = {
        "d1_primary_entry_document_not_recorded_complete",
        "d1_route_rank_schema_incomplete",
        "d1_stage_handoff_inconsistent_with_canonical_plan",
        "d3_flat_route_contract_incomplete",
        "d3_ranked_route_execution_missing",
        "d3_selected_lower_rank_without_higher_rank_accounting",
        "d5_same_workflow_variant_claimed_as_transfer",
    }
    findings = list(packet.get("findings", [])) if isinstance(packet.get("findings"), list) else []
    seen = {str(item.get("id", "")) for item in findings if isinstance(item, dict)}
    added = 0
    for item in warnings:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        if code not in blocking_codes:
            continue
        finding_id = f"{phase_name}_ROUTE_{_finding_id_suffix(code)}"
        if finding_id in seen:
            continue
        findings.append(
            {
                "id": finding_id,
                "severity": "blocking",
                "category": "route_coverage",
                "message": str(item.get("message", "")).strip(),
                "evidence_gap": "Stage route/rank/surface evidence is incomplete or overclaims beyond structured route facts.",
                "required_action": str(item.get("required_action", "")).strip(),
                "refs_checked": _string_list(item.get("refs", [])) or [f"artifacts/{phase_name}/stage_packet.json"],
                "related_stage_refs": [f"artifacts/{phase_name}/route_surface_audit_warnings.json"],
                "blocking_for_phase_finish": True,
            }
        )
        seen.add(finding_id)
        added += 1
    if not added:
        return packet
    normalized = dict(packet)
    normalized["findings"] = findings
    blocking = [
        item
        for item in findings
        if isinstance(item, dict)
        and str(item.get("severity", "")).strip() == "blocking"
        and bool(item.get("blocking_for_phase_finish", False))
    ]
    if blocking and audit_round >= max_rounds:
        normalized["decision"] = "pass_with_unresolved_findings"
        carry = normalized.get("must_carry_to_final_scoring")
        if not isinstance(carry, list):
            carry = []
        normalized["must_carry_to_final_scoring"] = carry + blocking
    elif blocking:
        normalized["decision"] = "revise_required"
    base_summary = str(normalized.get("audit_summary", "")).strip()
    suffix = f" Deterministic route/surface audit escalated {added} blocking finding(s); {phase_name} must resolve route rank, route accounting, or transfer novelty before phase finish."
    normalized["audit_summary"] = (base_summary + suffix).strip() if base_summary else suffix.strip()
    return normalized


def _rubric_critical_final_findings(run_root: Path, final_packet: dict[str, Any]) -> list[dict[str, Any]]:
    audit_sources = (
        ("final_packet_internal_consistency", lambda: final_packet_internal_consistency_findings(final_packet)),
        ("representative_workflow", lambda: representative_workflow_findings(final_packet)),
        ("resource_boundary", lambda: resource_boundary_findings(final_packet)),
        ("stage_final_semantics", lambda: stage_final_semantics_findings(run_root, final_packet)),
        ("badge_guardrails", lambda: badge_guardrail_findings(run_root, final_packet)),
        ("predicate_audit", lambda: predicate_consistency_findings(final_packet)),
    )
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for source, build_findings in audit_sources:
        try:
            findings = build_findings()
        except Exception as exc:
            findings = [
                {
                    "severity": "warning",
                    "code": "audit_source_failed",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            ]
        for item in findings:
            if not isinstance(item, dict):
                continue
            if not _is_rubric_critical_final_finding(source, item):
                continue
            code = str(item.get("code", "")).strip() or "rubric_critical_final_audit"
            message = str(item.get("message", "")).strip()
            key = (source, code, message)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "id": f"FINAL_RUBRIC_{len(results) + 1:03d}_{_finding_id_suffix(code)}",
                    "severity": "blocking",
                    "category": "final_consistency",
                    "message": f"{source}:{code}: {message}",
                    "evidence_gap": "Final scoring has not resolved a rubric-critical gate, representative-boundary, or reusable-surface arbitration issue.",
                    "required_action": (
                        "Revise artifacts/final_scoring/final_scoring_packet.json so the affected Functional/Reusable decision "
                        "is supported by explicit rubric_evidence_adjudication and surface/representativeness fields, or lower "
                        "the affected decision and badge while preserving the bounded evidence as a limitation."
                    ),
                    "refs_checked": [
                        "artifacts/final_scoring/final_scoring_packet.json",
                        "artifacts/D3_executability/stage_packet.json",
                        "artifacts/D4_claim_support/stage_packet.json",
                        "artifacts/D5_reusability/stage_packet.json",
                    ],
                    "related_stage_refs": [
                        "artifacts/evidence_contract/evidence_contract.json",
                    ],
                    "blocking_for_phase_finish": True,
                }
            )
    return results


def _is_rubric_critical_final_finding(source: str, item: dict[str, Any]) -> bool:
    code = str(item.get("code", "")).strip()
    message = str(item.get("message", "")).strip()
    severity = str(item.get("severity", "")).strip()
    if severity == "error":
        return True
    if code in RUBRIC_CRITICAL_FINAL_CODES:
        return True
    if source == "badge_guardrails" and any(fragment in message for fragment in RUBRIC_CRITICAL_FINAL_MESSAGE_FRAGMENTS):
        return True
    return False


def _finding_id_suffix(code: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in code.upper()).strip("_")
    return cleaned[:48] or "FINDING"


def _default_summary(decision: str, blocking: list[dict[str, Any]]) -> str:
    if decision == "pass":
        return "Evidence audit passed with no blocking findings."
    if decision == "pass_with_unresolved_findings":
        return f"Evidence audit reached the round limit with {len(blocking)} unresolved blocking finding(s)."
    return f"Evidence audit requires revision for {len(blocking)} blocking finding(s)."


def _default_checked_refs(evidence_package: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for item in evidence_package.get("files", []):
        if isinstance(item, dict) and item.get("ref"):
            refs.append(str(item["ref"]))
    return refs


def _build_evidence_package(*, run_root: Path, repo_root: Path, phase_name: str, state: AgentState, profile: PhaseAuditProfile) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for base, rel in _audit_file_plan(phase_name, profile=profile):
        root = run_root if base == "run" else repo_root
        path = root / rel
        files.append(_read_audit_file(path=path, root=root, base=base, rel=rel))
    previous_audits = _previous_audit_packets(run_root, phase_name)
    return {
        "schema": "evidence_audit_input_v1",
        "phase_name": phase_name,
        "run_root": str(run_root),
        "repo_root": str(repo_root),
        "current_phase": state.current_phase,
        "phase_artifacts": state.phase_artifacts.get(phase_name, {}),
        "shared_audit_context": _phase_audit_state(state, phase_name),
        "phase_audit_profile": _profile_prompt_payload(profile),
        "required_refs": list(profile.required_refs),
        "files": _truncate_files_total(files),
        "previous_audit_packets": previous_audits,
        "audit_focus": list(profile.audit_focus),
        "blocking_criteria": list(profile.blocking_criteria),
        "warning_criteria": list(profile.warning_criteria),
        "forbidden_overclaims": list(profile.forbidden_overclaims),
        "independent_read_guidance": list(profile.independent_read_guidance),
        "suggested_read_refs": list(profile.suggested_read_refs),
    }


def _audit_file_plan(phase_name: str, *, profile: PhaseAuditProfile) -> list[tuple[str, str]]:
    common = [
        ("run", "artifacts/memory/global_memory.md"),
        ("run", "artifacts/memory/stage_summaries.md"),
        ("run", "artifacts/memory/evidence_index.md"),
        ("run", "artifacts/memory/current_stage_memory.md"),
        ("run", "artifacts/memory/current_stage_steps.md"),
        ("run", f"artifacts/{phase_name}/step_trace.md"),
    ]
    refs = [(("repo" if ref.startswith("repo:") else "run"), ref.removeprefix("repo:")) for ref in profile.required_refs]
    if phase_name not in {"run_initialization", "final_scoring"}:
        refs.append(("run", f"artifacts/{phase_name}/stage_packet.json"))
    if phase_name == "final_scoring":
        refs.append(("run", "artifacts/final_scoring/final_scoring_packet.json"))
    if phase_name in {"D2_environment", "D3_executability", "D5_reusability", "final_scoring"}:
        refs.append(("run", f"artifacts/{phase_name}/command_ledger.json"))
    if phase_name in {"D1_documentation", "D3_executability", "D5_reusability"}:
        refs.append(("run", f"artifacts/{phase_name}/route_surface_audit_warnings.json"))
    return _dedupe_file_plan([*refs, *common])


def _read_audit_file(*, path: Path, root: Path, base: str, rel: str) -> dict[str, Any]:
    exists = path.exists() and path.is_file()
    text = safe_read_text(path) if exists else ""
    return {
        "base": base,
        "ref": relpath_str(path, root) if exists else rel,
        "exists": exists,
        "chars": len(text),
        "truncated": len(text) > MAX_AUDIT_FILE_CHARS,
        "content": _truncate_middle(text, MAX_AUDIT_FILE_CHARS) if exists else "",
    }


def _truncate_files_total(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = 0
    result: list[dict[str, Any]] = []
    for item in files:
        item = dict(item)
        content = str(item.get("content", ""))
        remaining = MAX_AUDIT_TOTAL_CHARS - total
        if remaining <= 0:
            item["content"] = ""
            item["truncated_by_total_budget"] = True
        elif len(content) > remaining:
            item["content"] = _truncate_middle(content, remaining)
            item["truncated_by_total_budget"] = True
            total = MAX_AUDIT_TOTAL_CHARS
        else:
            total += len(content)
        result.append(item)
    return result


def _previous_audit_packets(run_root: Path, phase_name: str) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    phase_dir = run_root / "artifacts" / phase_name
    for path in sorted(phase_dir.glob("evidence_audit_round_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            packets.append(
                {
                    "ref": relpath_str(path, run_root),
                    "decision": payload.get("decision", ""),
                    "findings": payload.get("findings", []),
                }
            )
    return packets[-3:]


def _audit_profile(phase_name: str) -> PhaseAuditProfile:
    profile = PHASE_AUDIT_PROFILES.get(phase_name)
    if profile is not None:
        return profile
    return PhaseAuditProfile(
        phase_name=phase_name,
        mission="Audit the stage evidence packet for grounded, complete, non-scoring evidence.",
        required_refs=(f"artifacts/{phase_name}/stage_packet.json",),
        audit_focus=("The stage packet exists, cites evidence refs, records limitations, and provides a usable handoff.",),
        blocking_criteria=(
            "The stage packet is missing or does not cite evidence refs.",
            "The packet makes unsupported or premature scoring/badge claims.",
        ),
    )


def _truncate_middle(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return text[:head] + "\n...[truncated]...\n" + text[-tail:]


def _append_auditor_memory(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    previous = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    content = (previous.rstrip() + "\n\n" + text.strip() + "\n").lstrip()
    path.write_text(content, encoding="utf-8")


def _format_auditor_memory_step(*, step_index: int, action: dict[str, Any], observation: dict[str, Any]) -> str:
    lines = [
        f"## Auditor Step {step_index}",
        f"- time: {now_iso()}",
        f"- action: `{action.get('action', '')}`",
    ]
    rationale = str(action.get("rationale", "")).strip()
    if rationale:
        lines.append(f"- rationale: {_compact_inline(rationale, 1200)}")
    lines.append(f"- observation_kind: `{observation.get('kind', '')}`")
    summary = str(observation.get("summary", "")).strip()
    if summary:
        lines.append(f"- observation_summary: {_compact_inline(summary, 1200)}")
    if action.get("action") == "read_ref" and isinstance(action.get("read_ref"), dict):
        read_ref = action["read_ref"]
        lines.append(f"- read_ref: `{read_ref.get('base', '')}:{read_ref.get('path', '')}`")
    if action.get("action") == "inspect_packet" and isinstance(action.get("inspect_packet"), dict):
        targets = action["inspect_packet"].get("targets", [])
        if targets:
            lines.append(f"- inspected_targets: {', '.join(str(item) for item in targets)}")
    return "\n".join(lines)


def _aggregate_usage(usages: Any) -> dict[str, Any]:
    if not isinstance(usages, list):
        return {}
    totals: dict[str, int] = {}
    detail_keys = ("prompt_tokens_details",)
    details: dict[str, dict[str, int]] = {key: {} for key in detail_keys}
    for usage in usages:
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            if key in detail_keys and isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if isinstance(sub_value, int):
                        details[key][str(sub_key)] = details[key].get(str(sub_key), 0) + sub_value
                continue
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    for key, value in details.items():
        if value:
            totals[key] = value  # type: ignore[assignment]
    return totals


def _safe_int(value: Any, *, default: int, lower: int, upper: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))


def _compact_inline(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    head = text[: max(0, limit // 2)].rstrip()
    tail = text[-max(0, limit // 2) :].lstrip()
    return f"{head} ...[truncated {len(text) - limit} chars]... {tail}"


def _enum(value: str, allowed: set[str], *, default: str) -> str:
    text = value.strip()
    return text if text in allowed else default


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _dedupe_file_plan(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for base, rel in items:
        key = (str(base).strip(), str(rel).strip())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
