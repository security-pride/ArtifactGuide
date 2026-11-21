from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..shared.fs import ensure_dir, now_iso, write_json
from .badge_guardrails import badge_guardrail_findings
from .environment_consistency import environment_consistency_findings
from .rubric_evidence_engine import (
    dataset_package_primary_surface_exception,
    predicate_consistency_findings,
    result_package_exception,
)

SCORE_ORDER = {"R": 0, "WR": 1, "WA": 2, "A": 3}
INTERVENTION_ORDER = {"I0": 0, "I1": 1, "I2": 2, "I3": 3, "I4": 4}


def _check_required_report_fields(report: dict[str, Any]) -> list[dict[str, Any]]:
    required = [
        "available_gate",
        "dimension_scores",
        "evidence_tier",
        "highest_intervention",
        "functional_decision",
        "reusable_decision",
        "badge_recommendation",
        "confidence",
        "limitations",
        "author_clarification_requests",
        "human_review_sensitive_points",
        "evidence_refs",
    ]
    findings = []
    for key in required:
        if key not in report:
            findings.append(_finding("error", "missing_report_field", f"ae_report.json missing required key `{key}`."))
    return findings


def _check_report_canonical_field_copy(report: dict[str, Any], final_scoring_packet: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(report, dict) or not isinstance(final_scoring_packet, dict) or not final_scoring_packet:
        return []
    canonical_fields = [
        "available_gate",
        "dimension_scores",
        "evidence_tier",
        "highest_intervention",
        "functional_decision",
        "reusable_decision",
        "badge_recommendation",
        "confidence",
        "limitations",
        "author_clarification_requests",
        "human_review_sensitive_points",
        "evidence_refs",
    ]
    findings: list[dict[str, Any]] = []
    for key in canonical_fields:
        if key not in final_scoring_packet:
            continue
        if report.get(key) != final_scoring_packet.get(key):
            findings.append(
                _finding(
                    "error",
                    "canonical_report_field_mismatch",
                    f"ae_report.json `{key}` must exactly copy final_scoring_packet.json `{key}`.",
                )
            )
    return findings


def _check_score_consistency(report: dict[str, Any], final_scoring_packet: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    report_scores = _extract_scores(report)
    review_scores = _extract_scores(final_scoring_packet)
    if not report_scores:
        findings.append(_finding("error", "missing_report_scores", "ae_report.json has no dimension_scores."))
        return findings
    if not review_scores:
        findings.append(_finding("warning", "missing_final_scoring_packet_scores", "final_scoring_packet.json has no dimension_scores."))
        return findings
    for dim in ("D1", "D2", "D3", "D4", "D5"):
        if report_scores.get(dim) != review_scores.get(dim):
            findings.append(
                _finding(
                    "error",
                    "score_mismatch",
                    f"{dim} differs between report ({report_scores.get(dim)}) and final_scoring_packet ({review_scores.get(dim)}).",
                )
            )
    return findings


def _check_badge_consistency(report: dict[str, Any], final_scoring_packet: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    scores = _extract_scores(report)
    badge = _badge_text(report.get("badge_recommendation"))
    if not badge and isinstance(final_scoring_packet, dict):
        badge = _badge_text(final_scoring_packet.get("badge_recommendation"))
    available_passed = _available_passed(report.get("available_gate"))
    if badge and badge not in {"no badge", "reject"} and not available_passed:
        findings.append(_finding("error", "badge_without_available", "Badge recommended even though Available gate is not passed."))
    if _score_lt(scores.get("D4"), "WA") and "functional" in badge:
        findings.append(_finding("error", "functional_without_d4", "Functional/Reusability badge recommended while D4 is below WA."))
    if _score_lt(scores.get("D5"), "WA") and "reusable" in badge:
        findings.append(_finding("error", "reusable_without_d5", "Reusable badge recommended while D5 is below WA."))
    if "reusable" in badge and "functional" not in badge:
        findings.append(_finding("error", "reusable_without_functional", "Reusable badge recommended without Functional."))
    return findings


def final_packet_internal_consistency_findings(final_scoring_packet: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(final_scoring_packet, dict) or not final_scoring_packet:
        return []
    findings: list[dict[str, Any]] = []
    scores = _extract_scores(final_scoring_packet)
    functional = final_scoring_packet.get("functional_decision", {})
    reusable = final_scoring_packet.get("reusable_decision", {})
    badge = final_scoring_packet.get("badge_recommendation", {})

    findings.extend(_check_decision_conditions("functional_decision", functional))
    findings.extend(_check_decision_conditions("reusable_decision", reusable))
    findings.extend(_check_rubric_evidence_adjudication(final_scoring_packet))
    findings.extend(_check_reusable_surface_arbitration(final_scoring_packet))
    findings.extend(_check_d5_same_workflow_variant(final_scoring_packet))
    findings.extend(
        _check_required_condition_coverage(
            "functional_decision",
            functional,
            [
                ["Available"],
                ["D2 >= WA"],
                ["D3 >= WA"],
                ["D4 >= WA"],
                ["representative workflow"],
                ["E2", "reviewer-observed executable evidence"],
                ["I4"],
            ],
        )
    )
    findings.extend(
        _check_required_condition_coverage(
            "reusable_decision",
            reusable,
            [
                ["Functional"],
                ["D1 >= WA"],
                ["D5 >= WA"],
                ["transfer validation"],
                ["small input", "new configuration", "new project", "new scenario", "independent API", "independent CLI", "independent call"],
                ["explainable output"],
                ["I4"],
            ],
        )
    )

    expected_thresholds = {
        "functional_decision": {"D2 >= WA": ("D2", "WA"), "D3 >= WA": ("D3", "WA"), "D4 >= WA": ("D4", "WA")},
        "reusable_decision": {"D1 >= WA": ("D1", "WA"), "D5 >= WA": ("D5", "WA")},
    }
    for section_name, mapping in expected_thresholds.items():
        section = final_scoring_packet.get(section_name, {})
        conditions = section.get("conditions", []) if isinstance(section, dict) else []
        if not isinstance(conditions, list):
            continue
        for condition in conditions:
            if not isinstance(condition, dict):
                continue
            text = str(condition.get("condition", "")).strip()
            for marker, (dim, threshold) in mapping.items():
                if marker in text:
                    expected = not _score_lt(scores.get(dim), threshold)
                    observed = bool(condition.get("satisfied"))
                    if observed != expected:
                        findings.append(
                            _finding(
                                "error",
                                "final_condition_threshold_mismatch",
                                f"{section_name} condition `{text}` has satisfied={observed}, but {dim}={scores.get(dim)} makes expected satisfied={expected}.",
                            )
                        )

    available = _available_passed(final_scoring_packet.get("available_gate"))
    if not available:
        available = _badge_bool(badge, "available")
    functional_holds = _decision_holds(functional)
    reusable_holds = _decision_holds(reusable)
    badge_text = _badge_text(badge)
    expected_badge = _expected_badge(available, functional_holds, reusable_holds)
    if badge_text and expected_badge and badge_text != expected_badge:
        findings.append(
            _finding(
                "error",
                "final_badge_decision_mismatch",
                f"badge_recommendation={badge_text}, but Available={available}, Functional={functional_holds}, Reusable={reusable_holds} imply {expected_badge}.",
            )
        )

    return findings


def _check_reusable_surface_arbitration(final_scoring_packet: dict[str, Any]) -> list[dict[str, Any]]:
    """Record missing reusable-surface arbitration as audit context only.

    Surface arbitration is useful for human review, but it is a semantic
    completeness concern rather than a structural contradiction. Blocking
    phase_finish here can force the agent to rewrite an otherwise coherent
    final decision repeatedly, which is exactly the token-burning failure mode
    we want to avoid.
    """
    reusable_claimed = _decision_holds(final_scoring_packet.get("reusable_decision", {})) or _badge_bool(
        final_scoring_packet.get("badge_recommendation", {}),
        "reusable",
    )
    if not reusable_claimed:
        return []

    d5 = final_scoring_packet.get("d5_transfer_sufficiency_check", {})
    adjudication = final_scoring_packet.get("rubric_evidence_adjudication", {})
    reusable_evidence = adjudication.get("strongest_reusable_evidence", {}) if isinstance(adjudication, dict) else {}
    reusable_sufficiency = adjudication.get("reusable_sufficiency", {}) if isinstance(adjudication, dict) else {}
    text = "\n".join(
        [
            _json_text(d5),
            _json_text(reusable_evidence),
            _json_text(reusable_sufficiency),
        ]
    ).lower()

    intended_markers = (
        "intended reusable surface",
        "public reusable surface",
        "declared reusable surface",
        "primary reusable surface",
        "future users",
        "expected to reuse",
    )
    selected_markers = (
        "selected transfer surface",
        "selected transfer",
        "validated surface",
        "validated by d5",
        "d5 actually validated",
        "transfer surface",
        "d5 transfer",
        "transfer validates",
        "transfer exercises",
        "transfer uses",
    )
    relation_markers = (
        "validates_intended_surface",
        "meaningful_variant_of_intended_surface",
        "declared_bounded_equivalent_surface",
        "supporting_or_substitute_surface",
        "wrong_or_weaker_surface_selected",
        "same_workflow_variant",
        "substitute_or_smoke_surface",
        "component_or_helper_surface",
        "dataset_result_surface",
        "wrong_or_weaker_surface_selected",
        "validates the intended",
        "validates that intended",
        "validates the public",
        "validates the declared",
        "does not validate the intended",
        "does not validate that intended",
        "does not validate the public",
        "does not validate the declared",
    )
    if all(any(marker in text for marker in group) for group in (intended_markers, selected_markers, relation_markers)):
        return []

    return [
        _finding(
            "warning",
            "reusable_claimed_without_surface_arbitration",
            "Reusable is claimed, but final scoring does not explicitly arbitrate the intended reusable surface, "
            "the selected D5 transfer surface, and their relation. Preserve this as an audit note; do not block "
            "phase_finish unless the packet has a concrete badge, score, or condition contradiction.",
        )
    ]


def _check_d5_same_workflow_variant(final_scoring_packet: dict[str, Any]) -> list[dict[str, Any]]:
    reusable_claimed = _decision_holds(final_scoring_packet.get("reusable_decision", {})) or _badge_bool(
        final_scoring_packet.get("badge_recommendation", {}),
        "reusable",
    )
    if not reusable_claimed:
        return []
    d5 = final_scoring_packet.get("d5_transfer_sufficiency_check", {})
    if not isinstance(d5, dict):
        return [
            _finding(
                "error",
                "missing_d5_transfer_sufficiency_check",
                "Reusable is claimed, but d5_transfer_sufficiency_check is missing.",
            )
        ]
    check = d5.get("same_workflow_variant_check")
    if not isinstance(check, dict):
        return [
            _finding(
                "error",
                "missing_same_workflow_variant_check",
                "Reusable is claimed, but d5_transfer_sufficiency_check.same_workflow_variant_check is missing. "
                "Final scoring must distinguish genuine code reuse in a new context from parameter/config/path/input tweaks of the same workflow.",
            )
        ]
    only_tweak = any(
        check.get(key) is True
        for key in (
            "only_parameter_or_config_change",
            "only_path_or_relocation_change",
            "only_same_task_new_input",
        )
    )
    context = check.get("reviewer_owned_new_context")
    if isinstance(context, bool):
        missing_context = not context
    else:
        missing_context = _lower(context) in {"", "no", "false", "none", "unknown"}
    if only_tweak and missing_context and not dataset_package_primary_surface_exception(final_scoring_packet) and not result_package_exception(final_scoring_packet):
        return [
            _finding(
                "error",
                "reusable_claimed_from_same_workflow_variant",
                "Reusable is claimed, but same_workflow_variant_check indicates the D5 validation only changed parameters/config/path/same-task input "
                "without a reviewer-owned new context. Lower Reusable or provide evidence of artifact code/API/module/CLI reuse in a genuinely new scenario.",
            )
        ]
    return []


def _check_rubric_evidence_adjudication(final_scoring_packet: dict[str, Any]) -> list[dict[str, Any]]:
    adjudication = final_scoring_packet.get("rubric_evidence_adjudication")
    functional = final_scoring_packet.get("functional_decision", {})
    reusable = final_scoring_packet.get("reusable_decision", {})
    badge = final_scoring_packet.get("badge_recommendation", {})
    functional_claimed = _decision_holds(functional) or _badge_bool(badge, "functional")
    reusable_claimed = _decision_holds(reusable) or _badge_bool(badge, "reusable")

    if not isinstance(adjudication, dict):
        severity = "error" if functional_claimed or reusable_claimed else "warning"
        return [
            _finding(
                severity,
                "missing_rubric_evidence_adjudication",
                "final_scoring_packet is missing rubric_evidence_adjudication. "
                "Final scoring must classify the strongest Functional and Reusable evidence before mapping evidence to badge decisions.",
            )
        ]

    required = [
        "strongest_functional_evidence",
        "functional_sufficiency",
        "strongest_reusable_evidence",
        "reusable_sufficiency",
        "final_alignment_check",
    ]
    missing = [key for key in required if key not in adjudication]
    if missing:
        return [
            _finding(
                "error",
                "incomplete_rubric_evidence_adjudication",
                "rubric_evidence_adjudication is missing required fields: "
                + ", ".join(missing)
                + ". Rewrite final_scoring_packet with a complete evidence classification and sufficiency chain.",
            )
        ]

    findings: list[dict[str, Any]] = []
    functional_sufficiency = adjudication.get("functional_sufficiency", {})
    reusable_sufficiency = adjudication.get("reusable_sufficiency", {})
    alignment = adjudication.get("final_alignment_check", {})

    if not isinstance(functional_sufficiency, dict) or "sufficient" not in functional_sufficiency:
        findings.append(
            _finding(
                "error",
                "missing_functional_sufficiency_value",
                "rubric_evidence_adjudication.functional_sufficiency.sufficient is missing. "
                "Final scoring must explicitly decide whether the strongest Functional evidence is sufficient under the rubric.",
            )
        )
    elif functional_claimed and functional_sufficiency.get("sufficient") is False:
        findings.append(
            _finding(
                "error",
                "functional_claimed_despite_adjudication_insufficient",
                "Functional is claimed, but rubric_evidence_adjudication.functional_sufficiency.sufficient=false. "
                "Rewrite Functional false or revise the adjudication with concrete representative D3/D4 evidence.",
            )
        )

    if not isinstance(reusable_sufficiency, dict) or "sufficient" not in reusable_sufficiency:
        findings.append(
            _finding(
                "error",
                "missing_reusable_sufficiency_value",
                "rubric_evidence_adjudication.reusable_sufficiency.sufficient is missing. "
                "Final scoring must explicitly decide whether the strongest transfer validates the intended reusable surface.",
            )
        )
    elif reusable_claimed and reusable_sufficiency.get("sufficient") is False:
        findings.append(
            _finding(
                "error",
                "reusable_claimed_despite_adjudication_insufficient",
                "Reusable is claimed, but rubric_evidence_adjudication.reusable_sufficiency.sufficient=false. "
                "Rewrite Reusable false or revise the adjudication with concrete artifact-level D5 evidence.",
            )
        )

    if isinstance(alignment, dict):
        if alignment.get("badge_matches_adjudication") is False:
            findings.append(
                _finding(
                    "error",
                    "badge_marked_not_matching_adjudication",
                    "rubric_evidence_adjudication.final_alignment_check.badge_matches_adjudication=false. "
                    "Rewrite the badge or sufficiency chain until the final badge follows from Available, Functional, and Reusable decisions.",
                )
            )
        consistency = _lower(alignment.get("stage_final_consistency"))
        if consistency == "unresolved_boundary" and (functional_claimed or reusable_claimed):
            findings.append(
                _finding(
                    "error",
                    "badge_claimed_with_unresolved_stage_final_boundary",
                    "Functional or Reusable is claimed while rubric_evidence_adjudication.final_alignment_check.stage_final_consistency=unresolved_boundary. "
                    "Resolve the stage-final evidence boundary or lower the badge.",
                )
            )
    return findings


def representative_workflow_findings(final_scoring_packet: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(final_scoring_packet, dict) or not final_scoring_packet:
        return []
    e2 = final_scoring_packet.get("e2_sufficiency_check", {})
    functional = final_scoring_packet.get("functional_decision", {})
    badge = final_scoring_packet.get("badge_recommendation", {})
    functional_claimed = _decision_holds(functional) or _badge_bool(badge, "functional")
    if not functional_claimed:
        return []
    if not isinstance(e2, dict):
        return [
            _finding(
                "error",
                "missing_representative_workflow_guardrail_fields",
                "Functional is claimed, but e2_sufficiency_check is missing or not an object. "
                "Rewrite final_scoring_packet with explicit representative-workflow sufficiency semantics.",
            )
        ]

    required_fields = [
        "artifact_specific_logic_exercised",
        "paper_facing_output",
        "generic_dependency_or_example",
        "component_only",
        "functional_representativeness_blocker",
    ]
    missing_fields = [field for field in required_fields if field not in e2]
    if missing_fields:
        return [
            _finding(
                "error",
                "missing_representative_workflow_guardrail_fields",
                "Functional is claimed, but e2_sufficiency_check is missing required representative-workflow guardrail fields: "
                + ", ".join(missing_fields)
                + ". Rewrite final_scoring_packet with artifact-specific logic, paper-facing output, generic dependency/example, component-only, and blocker judgments.",
            )
        ]

    result_package_exception = _result_or_dataset_package_exception_applies(final_scoring_packet)
    justified_representative_override = _justified_representative_override_applies(final_scoring_packet)
    result_validation_functional_surface = _final_declares_result_validation_functional_surface(e2)
    blockers: list[str] = []
    if e2.get("generic_dependency_or_example") is True:
        blockers.append("generic_dependency_or_example=true")
    if e2.get("component_only") is True and not (result_package_exception or justified_representative_override or result_validation_functional_surface):
        blockers.append("component_only=true")
    artifact_logic = str(e2.get("artifact_specific_logic_exercised", "")).strip().lower()
    if artifact_logic == "no" and not (result_package_exception or justified_representative_override or result_validation_functional_surface):
        blockers.append("artifact_specific_logic_exercised=no")
    paper_output = str(e2.get("paper_facing_output", "")).strip().lower()
    if paper_output == "no":
        blockers.append("paper_facing_output=no")
    blocker_text = str(e2.get("functional_representativeness_blocker", "")).strip()
    if blocker_text and not (result_package_exception or justified_representative_override or result_validation_functional_surface):
        blockers.append("functional_representativeness_blocker present")

    if not blockers:
        return []
    return [
        _finding(
            "error",
            "functional_claimed_from_supporting_only_workflow",
            "Functional is claimed, but e2_sufficiency_check marks the selected D3 evidence as supporting-only or non-representative: "
            + ", ".join(blockers)
            + ". Rewrite final_scoring_packet so Functional is false unless another route provides artifact-specific paper-facing E2 evidence.",
        )
    ]


def _final_declares_result_validation_functional_surface(e2: dict[str, Any]) -> bool:
    if not isinstance(e2, dict):
        return False
    if e2.get("result_package_declared_by_artifact") is not True:
        return False
    if e2.get("generic_dependency_or_example") is True:
        return False
    workflow = _lower(e2.get("workflow_kind"))
    if workflow not in {"", "unknown", "dataset_loading_or_aggregation", "result_validation_only", "post_processing_only"}:
        return False
    if _lower(e2.get("output_provenance")) not in {"loaded_from_deposited_files", "validated_precomputed_output", "derived_from_existing_results"}:
        return False
    if _lower(e2.get("paper_facing_output")) not in {"yes", "limited"}:
        return False
    if _lower(e2.get("artifact_specific_logic_exercised")) not in {"yes", "limited"}:
        return False
    return True


def resource_boundary_findings(final_scoring_packet: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(final_scoring_packet, dict) or not final_scoring_packet:
        return []
    resource = final_scoring_packet.get("resource_boundary_assessment")
    functional_claimed = _decision_holds(final_scoring_packet.get("functional_decision", {})) or _badge_bool(
        final_scoring_packet.get("badge_recommendation", {}),
        "functional",
    )
    reusable_claimed = _decision_holds(final_scoring_packet.get("reusable_decision", {})) or _badge_bool(
        final_scoring_packet.get("badge_recommendation", {}),
        "reusable",
    )
    if not functional_claimed and not reusable_claimed:
        return []
    if not isinstance(resource, dict):
        return [
            _finding(
                "error",
                "missing_resource_boundary_assessment",
                "Functional or Reusable is claimed, but resource_boundary_assessment is missing. "
                "Final scoring must explicitly state whether API, hardware, data, network, toolchain, time, or host-policy boundaries affect the badge.",
            )
        ]

    required_fields = [
        "boundary_type",
        "declared_by_artifact",
        "position",
        "artifact_logic_reached",
        "paper_facing_output_before_boundary",
        "blocks_all_reviewer_realistic_routes",
        "alternative_route_available",
        "alternative_evidence_exists",
        "badge_effect",
        "reason",
        "refs",
    ]
    missing_fields = [field for field in required_fields if field not in resource]
    if missing_fields:
        return [
            _finding(
                "error",
                "missing_resource_boundary_fields",
                "Functional or Reusable is claimed, but resource_boundary_assessment is missing required fields: "
                + ", ".join(missing_fields)
                + ". Rewrite final_scoring_packet with a complete resource/API/toolchain boundary assessment.",
            )
        ]

    findings: list[dict[str, Any]] = []
    boundary_type = _lower(resource.get("boundary_type"))
    position = _lower(resource.get("position"))
    logic = _lower(resource.get("artifact_logic_reached"))
    paper_output = _lower(resource.get("paper_facing_output_before_boundary"))
    alternative_evidence = _lower(resource.get("alternative_evidence_exists"))
    blocks_all_routes = _lower(resource.get("blocks_all_reviewer_realistic_routes"))
    badge_effect = _lower(resource.get("badge_effect"))
    has_boundary = boundary_type not in {"", "none"}
    e2 = final_scoring_packet.get("e2_sufficiency_check", {})
    d5 = final_scoring_packet.get("d5_transfer_sufficiency_check", {})

    if functional_claimed and badge_effect == "blocks_functional":
        findings.append(
            _finding(
                "error",
                "functional_claimed_despite_resource_boundary_blocker",
                "Functional is claimed, but resource_boundary_assessment.badge_effect=blocks_functional. "
                "Rewrite Functional false or explain a qualifying alternative evidence channel and update badge_effect.",
            )
        )
    if reusable_claimed and badge_effect == "blocks_reusable":
        findings.append(
            _finding(
                "error",
                "reusable_claimed_despite_resource_boundary_blocker",
                "Reusable is claimed, but resource_boundary_assessment.badge_effect=blocks_reusable. "
                "Rewrite Reusable false or explain why D5 validates the intended public reusable surface despite the boundary.",
            )
        )
    if (
        functional_claimed
        and has_boundary
        and position == "before_artifact_logic"
        and logic in {"no", "unknown", ""}
        and alternative_evidence != "yes"
        and not _result_or_dataset_package_exception_applies(final_scoring_packet)
    ):
        findings.append(
            _finding(
                "error",
                "functional_claimed_before_artifact_logic_boundary",
                "Functional is claimed, but the resource boundary is before artifact logic and no alternative reviewer-observed evidence is recorded. "
                "A blocker before artifact logic cannot support Functional unless another route supplies artifact-specific paper-facing E2 evidence.",
            )
        )
    if functional_claimed and has_boundary and paper_output == "no" and alternative_evidence != "yes":
        findings.append(
            _finding(
                "error",
                "functional_claimed_without_paper_facing_boundary_output",
                "Functional is claimed across a resource boundary, but resource_boundary_assessment records no paper-facing output before the boundary "
                "and no alternative evidence. Rewrite Functional false or record the concrete alternative evidence.",
            )
        )
    if reusable_claimed and has_boundary and blocks_all_routes == "yes":
        sufficiency = _artifact_level_sufficiency_value(d5.get("artifact_level_sufficiency") if isinstance(d5, dict) else "")
        if sufficiency != "sufficient":
            findings.append(
                _finding(
                    "error",
                    "reusable_claimed_when_resource_boundary_blocks_transfer_surface",
                    "Reusable is claimed, but resource_boundary_assessment says the boundary blocks all reviewer-realistic routes "
                    "and d5_transfer_sufficiency_check does not record artifact_level_sufficiency=sufficient.",
                )
            )
    return findings


def _result_or_dataset_package_exception_applies(final_scoring_packet: dict[str, Any]) -> bool:
    if result_package_exception(final_scoring_packet) or dataset_package_primary_surface_exception(final_scoring_packet):
        return True
    e2 = final_scoring_packet.get("e2_sufficiency_check", {}) if isinstance(final_scoring_packet, dict) else {}
    return isinstance(e2, dict) and _result_package_exception_applies(e2)


def _result_package_exception_applies(e2: dict[str, Any]) -> bool:
    if e2.get("api_resource_gated_result_package_exception") is not True:
        return False
    if e2.get("generic_dependency_or_example") is True:
        return False
    workflow_kind = str(e2.get("workflow_kind", "")).strip().lower()
    if workflow_kind not in {"dataset_loading_or_aggregation", "result_validation_only", "post_processing_only"}:
        return False
    provenance = str(e2.get("output_provenance", "")).strip().lower()
    if provenance not in {"loaded_from_deposited_files", "validated_precomputed_output", "derived_from_existing_results"}:
        return False
    paper_output = str(e2.get("paper_facing_output", "")).strip().lower()
    if paper_output not in {"yes", "limited"}:
        return False
    if e2.get("result_package_declared_by_artifact") is not True:
        return False
    declared_type = str(e2.get("declared_artifact_type", "")).strip().lower()
    if declared_type not in {
        "dataset_package",
        "data_package",
        "dataset/result_package",
        "dataset_result_package",
        "dataset/result package",
        "dataset/result package with supplementary tools",
        "dataset/result package with supplementary analysis tools",
        "dataset/result package with supplementary code",
        "dataset/result package with supplementary components",
        "dataset and result package",
        "dataset and result package with supplementary tools",
        "data/result_package",
        "data_result_package",
        "data/result package",
        "data/result package with supplementary tools",
        "result_package",
        "notebook_package",
        "spreadsheet_package",
        "replication_package",
    }:
        return False
    resource_position = str(e2.get("resource_boundary_position", "")).strip().lower()
    if resource_position != "after_valid_bounded_output":
        return False
    active_surface_text = " ".join(
        [
            _lower(e2.get("upstream_tool_surface_unattempted_reason")),
            _lower(e2.get("sufficiency_reason")),
            _lower(e2.get("score_or_badge_effect")),
        ]
    )
    if _active_tool_surface_unvalidated(active_surface_text):
        return False
    assessment = e2.get("representative_workflow_assessment", {})
    assessment_text = json.dumps(assessment, ensure_ascii=False).lower() if isinstance(assessment, dict) else str(assessment).lower()
    required_markers = ("paper", "output", "coverage")
    return all(marker in assessment_text for marker in required_markers)


def _justified_representative_override_applies(final_scoring_packet: dict[str, Any]) -> bool:
    """Allow final adjudication to override stale component labels with explicit evidence.

    This is intentionally narrower than "Functional claimed": generic dependency
    examples and non-paper-facing outputs remain hard failures. The override only
    handles cases where D3/D4 used a conservative `component_only` label, but the
    final rubric adjudication names a documented bounded route that produced
    artifact-specific paper-facing output with a stated coverage/resource boundary.
    """
    adjudication = final_scoring_packet.get("rubric_evidence_adjudication", {})
    e2 = final_scoring_packet.get("e2_sufficiency_check", {})
    if not isinstance(adjudication, dict) or not isinstance(e2, dict):
        return False
    functional_sufficiency = adjudication.get("functional_sufficiency", {})
    alignment = adjudication.get("final_alignment_check", {})
    strongest = adjudication.get("strongest_functional_evidence", {})
    if not isinstance(functional_sufficiency, dict) or functional_sufficiency.get("sufficient") is not True:
        return False
    if not isinstance(alignment, dict) or _lower(alignment.get("stage_final_consistency")) != "justified_override":
        return False
    if e2.get("generic_dependency_or_example") is True:
        return False
    if _lower(e2.get("paper_facing_output")) == "no":
        return False
    if _lower(e2.get("artifact_specific_logic_exercised")) == "no":
        return False
    evidence_type = _lower(strongest.get("evidence_type") if isinstance(strongest, dict) else "")
    if evidence_type not in {
        "primary_workflow_execution",
        "bounded_representative_workflow",
        "result_package_validation",
        "dataset_package_validation",
    }:
        return False
    text = _json_text(adjudication).lower() + "\n" + _json_text(e2).lower()
    required_groups = (
        ("documented", "readme", "artifact-authored", "grounded"),
        ("paper-facing", "paper facing", "paper target", "claim", "figure", "table", "rq"),
        ("bounded", "coverage boundary", "resource", "api", "time", "limited", "reduced"),
        ("artifact-specific", "artifact specific", "workflow", "route", "output"),
    )
    return all(any(marker in text for marker in group) for group in required_groups)


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def stage_final_semantics_findings(run_root: Path, final_scoring_packet: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(final_scoring_packet, dict) or not final_scoring_packet:
        return []
    findings: list[dict[str, Any]] = []
    findings.extend(_stage_representative_workflow_boundary_findings(run_root, final_scoring_packet))
    findings.extend(_stage_precomputed_result_active_pipeline_findings(run_root, final_scoring_packet))
    findings.extend(_stage_d5_transfer_boundary_findings(run_root, final_scoring_packet))
    findings.extend(_stage_route_arbitration_findings(run_root, final_scoring_packet))
    return findings


def _stage_precomputed_result_active_pipeline_findings(run_root: Path, final_scoring_packet: dict[str, Any]) -> list[dict[str, Any]]:
    return []


def _structured_active_pipeline_result_boundary(e2: dict[str, Any], d3_stage: dict[str, Any], d5_stage: dict[str, Any]) -> bool:
    d3_semantics = d3_stage.get("evidence_semantics", {}) if isinstance(d3_stage, dict) else {}
    d5_semantics = _d5_stage_transfer_semantics(d5_stage)
    if not isinstance(d3_semantics, dict):
        d3_semantics = {}
    if not isinstance(d5_semantics, dict):
        d5_semantics = {}

    declared_type = _lower(d3_semantics.get("declared_artifact_type"))
    reuse_target = _lower(d5_semantics.get("reuse_target_level"))
    active_surface_declared = declared_type in {
        "tool_repo",
        "tool",
        "model",
        "model_repo",
        "pipeline",
        "pipeline_repo",
        "cli_tool",
        "software_tool",
    } or reuse_target in {
        "model_pipeline_level",
        "tool_pipeline_level",
        "pipeline_level",
        "model_level",
        "tool_level",
    }
    if not active_surface_declared:
        return False

    upstream = _lower(d3_semantics.get("upstream_generation_attempted") or e2.get("upstream_generation_attempted"))
    boundary_position = _lower(d3_semantics.get("resource_boundary_position") or e2.get("resource_boundary_position"))
    upstream_not_observed = upstream.startswith("no") or boundary_position == "before_artifact_logic"
    if not upstream_not_observed:
        return False

    d5_sufficiency = _artifact_level_sufficiency_value(d5_semantics.get("artifact_level_sufficiency"))
    return d5_sufficiency == "supporting_only"


def _selected_evidence_is_precomputed_validation(e2: dict[str, Any]) -> bool:
    workflow_kind = _lower(e2.get("workflow_kind"))
    provenance = _lower(e2.get("output_provenance"))
    return workflow_kind in {"dataset_loading_or_aggregation", "result_validation_only", "post_processing_only"} and provenance in {
        "loaded_from_deposited_files",
        "validated_precomputed_output",
        "derived_from_existing_results",
    }


def _stage_declares_result_package_functional_surface(
    d3_stage: dict[str, Any],
    d4_stage: dict[str, Any],
    e2: dict[str, Any],
) -> bool:
    d3_semantics = d3_stage.get("evidence_semantics", {}) if isinstance(d3_stage, dict) else {}
    d4_repr = d4_stage.get("representativeness_for_claims", {}) if isinstance(d4_stage, dict) else {}
    if not isinstance(d3_semantics, dict):
        d3_semantics = {}
    if not isinstance(d4_repr, dict):
        d4_repr = {}
    declared_type = _lower(d3_semantics.get("declared_artifact_type") or e2.get("declared_artifact_type"))
    if declared_type not in {
        "replication_package",
        "dataset_package",
        "data_package",
        "result_package",
        "tool_repo",
        "tool",
        "tool_pipeline",
        "software_tool",
        "dataset/result_package",
        "dataset_result_package",
        "dataset/result package",
        "data/result_package",
        "data_result_package",
        "data/result package",
    }:
        return False
    if (
        d3_semantics.get("result_package_declared_by_artifact") is not True
        and e2.get("result_package_declared_by_artifact") is not True
    ):
        return False
    if d3_semantics.get("generic_dependency_or_example") is True or e2.get("generic_dependency_or_example") is True:
        return False
    if _lower(d3_semantics.get("paper_facing_output") or e2.get("paper_facing_output")) not in {"yes", "limited"}:
        return False
    if _lower(d3_semantics.get("artifact_specific_logic_exercised") or e2.get("artifact_specific_logic_exercised")) not in {"yes", "limited"}:
        return False
    blocker = _lower(d3_semantics.get("functional_representativeness_blocker") or e2.get("functional_representativeness_blocker"))
    if blocker and not any(marker in blocker for marker in ("api", "resource", "config", "upstream", "full upstream")):
        return False
    d4_support = _lower(d4_repr.get("can_support_artifact_level_paper_claims"))
    if d4_support not in {"", "true", "yes", "limited"}:
        return False
    text = "\n".join([_json_text(d3_semantics), _json_text(d4_repr), _json_text(e2)]).lower()
    return any(
        marker in text
        for marker in (
            "result package",
            "precomputed output",
            "precomputed result",
            "deposited result",
            "tgen output",
            "need not run",
            "paper-facing results",
        )
    )


def _result_package_is_declared_primary_surface(text: str) -> bool:
    direct_markers = (
        "artifact is a dataset",
        "artifact is a dataset/result package",
        "artifact is a data/result package",
        "artifact is a result package",
        "no higher-level tool",
        "no tool/model/pipeline",
        "dataset/result package itself the main review surface",
        "data/result package itself the main review surface",
        "result package itself the main review surface",
        "dataset/result package",
        "data/result package",
        "dataset files are the main review surface",
        "dataset route is the documented primary review surface",
        "dataset package is the primary artifact",
        "dataset is the primary artifact",
        "dataset is the main artifact",
        "data package is the primary artifact",
        "result package is the primary artifact",
        "deposited result data",
        "deposited files are the intended",
        "deposited public files are the intended",
    )
    if any(marker in text for marker in direct_markers):
        return True
    package_pattern = r"(dataset|data package|result package|result_package|deposited files|deposited public files)"
    surface_noun = r"(review surface|artifact surface|reusable surface|public surface)"
    near_patterns = (
        package_pattern + r".{0,40}(itself )?(is|are|serves as|serve as).{0,40}(primary|main|intended|declared public).{0,20}" + surface_noun,
        r"(primary|main|intended|declared public).{0,20}" + surface_noun + r".{0,25}(is|are).{0,20}" + package_pattern,
    )
    negation_markers = (
        "not the primary",
        "not the main",
        "not sufficient",
        "supporting-only",
        "supporting only",
        "supporting evidence",
        "not just",
    )
    for pattern in near_patterns:
        for match in re.finditer(pattern, text):
            window = text[max(0, match.start() - 80) : min(len(text), match.end() + 80)]
            if any(marker in window for marker in negation_markers):
                continue
            return True
    return False


def _active_tool_model_pipeline_surface(text: str) -> bool:
    phrase_markers = (
        "tool/model",
        "tool or model",
        "cli",
        "command-line",
        "artifact api",
        "public api",
        "documented api",
        "api endpoint",
        "api route",
        "api surface",
        "api workflow",
        "main tool",
        "public tool",
        "artifact tool",
        "model inference",
        "model pipeline",
        "training pipeline",
        "evaluation pipeline",
        "main pipeline",
        "full pipeline",
        "primary pipeline",
        "compiler",
        "analyzer",
        "analysis engine",
        "fuzzer",
        "simulator",
        "evaluation script",
        "eval script",
        "benchmark script",
    )
    word_markers = ("model", "pipeline", "agent")
    return any(marker in text for marker in phrase_markers) or any(
        re.search(rf"\b{re.escape(marker)}\b", text) for marker in word_markers
    )


def _main_generation_or_execution_unobserved(text: str) -> bool:
    unobserved_markers = (
        "upstream generation not executed",
        "upstream generation was not executed",
        "fresh upstream",
        "fresh generation",
        "fresh regeneration",
        "not rerun",
        "not re-run",
        "not run",
        "not executed",
        "unvalidated",
        "remains unvalidated",
        "remains resource-gated",
        "resource-gated",
        "gpu-gated",
        "gpu gated",
        "model-weight gated",
        "model weights",
        "main pipeline was not",
        "full pipeline was not",
        "primary pipeline was not",
        "primary model was not",
        "model inference was not",
        "training pipeline was not",
        "evaluation pipeline was not",
        "main pipeline remains",
        "full pipeline remains",
        "primary pipeline remains",
        "primary model remains",
        "model inference remains",
        "training pipeline remains",
        "evaluation pipeline remains",
    )
    return any(marker in text for marker in unobserved_markers)


def _stage_d5_transfer_boundary_findings(run_root: Path, final_scoring_packet: dict[str, Any]) -> list[dict[str, Any]]:
    d5_stage = _read_json(run_root / "artifacts" / "D5_reusability" / "stage_packet.json")
    stage_semantics = _d5_stage_transfer_semantics(d5_stage)
    if not isinstance(stage_semantics, dict):
        return []
    stage_sufficiency = _artifact_level_sufficiency_value(stage_semantics.get("artifact_level_sufficiency"))
    if stage_sufficiency not in {"supporting_only", "insufficient"}:
        return []

    final_d5 = final_scoring_packet.get("d5_transfer_sufficiency_check", {})
    if not isinstance(final_d5, dict):
        return []
    final_sufficiency = _artifact_level_sufficiency_value(final_d5.get("artifact_level_sufficiency"))
    reusable_claimed = _decision_holds(final_scoring_packet.get("reusable_decision", {})) or _badge_bool(
        final_scoring_packet.get("badge_recommendation", {}),
        "reusable",
    )
    if final_sufficiency != "sufficient" or not reusable_claimed:
        return []

    if _stage_supporting_transfer_has_unvalidated_primary_surface(d5_stage, final_scoring_packet):
        return [
            _finding(
                "error",
                "reusable_claimed_from_supporting_transfer_with_unvalidated_primary_surface",
                "D5 stage recorded a supporting-only transfer while a broader public tool/model/pipeline surface "
                "remains unvalidated. Do not award Reusable from explanatory surface rebasing alone; either preserve "
                "Reusable=false or provide new stage evidence that the selected transfer validates the documented "
                "artifact-level public surface.",
            )
        ]

    adjudication = final_scoring_packet.get("rubric_evidence_adjudication", {})
    reusable_sufficiency = adjudication.get("reusable_sufficiency", {}) if isinstance(adjudication, dict) else {}
    if not isinstance(reusable_sufficiency, dict) or reusable_sufficiency.get("sufficient") is not True:
        return [
            _finding(
                "error",
                "final_overrides_d5_supporting_only_without_adjudication",
                "D5 stage recorded d5_transfer_sufficiency_check.artifact_level_sufficiency="
                f"{stage_sufficiency}, but final scoring claimed Reusable without a matching "
                "rubric_evidence_adjudication.reusable_sufficiency.sufficient=true explanation.",
            )
        ]

    if _has_stage_sufficiency_override_basis(final_d5, d5_stage, reusable_sufficiency):
        return []
    return [
        _finding(
            "error",
            "final_overrides_d5_supporting_only_without_basis",
            "D5 stage recorded d5_transfer_sufficiency_check.artifact_level_sufficiency="
            f"{stage_sufficiency}, but final scoring changed it to sufficient and claimed Reusable without "
            "a concrete rubric-grounded basis. Preserve the D5 boundary, or explicitly connect the transfer "
            "to D5.2-D5.6 and explain why the stage limitation does not make any Reusable condition fail.",
        )
    ]


def _stage_representative_workflow_boundary_findings(run_root: Path, final_scoring_packet: dict[str, Any]) -> list[dict[str, Any]]:
    functional_claimed = _decision_holds(final_scoring_packet.get("functional_decision", {})) or _badge_bool(
        final_scoring_packet.get("badge_recommendation", {}),
        "functional",
    )
    if not functional_claimed:
        return []
    e2 = final_scoring_packet.get("e2_sufficiency_check", {})
    if (
        not isinstance(e2, dict)
        or _result_or_dataset_package_exception_applies(final_scoring_packet)
        or _stage_declares_result_package_functional_surface(
            _read_json(run_root / "artifacts" / "D3_executability" / "stage_packet.json"),
            _read_json(run_root / "artifacts" / "D4_claim_support" / "stage_packet.json"),
            e2,
        )
        or _justified_representative_override_applies(final_scoring_packet)
    ):
        return []
    d3_stage = _read_json(run_root / "artifacts" / "D3_executability" / "stage_packet.json")
    d4_stage = _read_json(run_root / "artifacts" / "D4_claim_support" / "stage_packet.json")
    blockers = _stage_representative_blockers(d3_stage, d4_stage)
    if not blockers:
        return []
    return [
        _finding(
            "error",
            "final_overrides_stage_representative_boundary_without_exception",
            "Functional is claimed, but D3/D4 stage packets record that the selected evidence is component/data/helper-level "
            "or cannot support artifact-level paper claims: "
            + "; ".join(blockers)
            + ". Preserve the stage representative-workflow boundary in e2_sufficiency_check and Functional=false, "
            "or record a qualifying result-package exception with artifact-authored result-package evidence.",
        )
    ]


def _stage_representative_blockers(d3_stage: dict[str, Any], d4_stage: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    d3_semantics = d3_stage.get("evidence_semantics", {}) if isinstance(d3_stage, dict) else {}
    d4_repr = d4_stage.get("representativeness_for_claims", {}) if isinstance(d4_stage, dict) else {}
    if isinstance(d3_semantics, dict):
        blockers.extend(_representative_semantic_blockers(d3_semantics, "D3.evidence_semantics"))
    if isinstance(d4_repr, dict):
        blockers.extend(_representative_semantic_blockers(d4_repr, "D4.representativeness_for_claims"))
        claim_support = _lower(d4_repr.get("can_support_artifact_level_paper_claims"))
        if claim_support in {"false", "no", "component_method_level", "component-level", "component_only"}:
            blockers.append(f"D4.can_support_artifact_level_paper_claims={claim_support}")
    stage_text = (_json_text(d3_stage) + "\n" + _json_text(d4_stage)).lower()
    if "component_only_coverage" in stage_text:
        blockers.append("stage limitations include component_only_coverage")
    if "only covers dataset loading" in stage_text or "covers only the data package" in stage_text:
        blockers.append("stage text says selected route only covers dataset/data package")
    if "helper component" in stage_text or "utility/helper" in stage_text:
        blockers.append("stage text says selected evidence is helper/component-level")
    if "cannot by itself establish" in stage_text or "cannot support artifact-level" in stage_text:
        blockers.append("stage text says evidence cannot establish artifact-level claims")
    return _dedupe_preserve_order(blockers)


def _stage_route_arbitration_findings(run_root: Path, final_scoring_packet: dict[str, Any]) -> list[dict[str, Any]]:
    route_check = final_scoring_packet.get("route_arbitration_check", {})
    if not isinstance(route_check, dict):
        return []
    findings: list[dict[str, Any]] = []
    d3_stage = _read_json(run_root / "artifacts" / "D3_executability" / "stage_packet.json")
    d5_stage = _read_json(run_root / "artifacts" / "D5_reusability" / "stage_packet.json")

    d3_decisiveness = _lower(route_check.get("d3_selected_route_decisiveness"))
    d3_open_reasons = _d3_open_route_reasons(d3_stage)
    if d3_decisiveness == "decisive_failure" and d3_open_reasons:
        findings.append(
            _finding(
                "error",
                "final_marks_d3_decisive_despite_open_route",
                "Final scoring marks the selected D3 route as decisive_failure, but D3 route evidence records "
                "a reviewer-realistic alternative, a good bounded candidate, or an unresolved evaluator-side failure: "
                + "; ".join(d3_open_reasons)
                + ". Preserve the route arbitration boundary as not_decisive_alternative_exists or unclear unless the final packet names concrete evidence that closes those routes.",
            )
        )

    d5_decisiveness = _lower(route_check.get("d5_selected_transfer_decisiveness"))
    d5_open_reasons = _d5_open_transfer_reasons(d5_stage)
    if d5_decisiveness == "decisive_failure" and d5_open_reasons:
        findings.append(
            _finding(
                "error",
                "final_marks_d5_decisive_despite_open_transfer",
                "Final scoring marks the selected D5 transfer as decisive_failure, but D5 transfer evidence records "
                "a feasible alternative, a repairable reviewer-side failure, or unresolved transfer-route uncertainty: "
                + "; ".join(d5_open_reasons)
                + ". Preserve the transfer arbitration boundary as not_decisive_alternative_exists or unclear unless the final packet names concrete evidence that closes those candidates.",
            )
        )
    return findings


def _d3_open_route_reasons(d3_stage: dict[str, Any]) -> list[str]:
    if not isinstance(d3_stage, dict):
        return []
    reasons: list[str] = []
    for route in _extract_route_items(d3_stage, ("route_portfolio", "routes", "candidate_routes")):
        if not isinstance(route, dict):
            continue
        route_id = _route_label(route, fallback="D3 route")
        priority = _lower(route.get("selection_priority_hint") or route.get("priority"))
        status = _lower(route.get("attempt_status"))
        decisive = _lower(route.get("decisiveness_for_functional"))
        failure_class = _lower(route.get("failure_cause_class"))
        alternative = _lower(route.get("alternative_route_available"))
        selected = bool(route.get("selected_for_d3"))
        if status in {"not_attempted_lower_priority", "not_attempted", "not_attempted_time_gated"} and priority == "good_bounded_candidate":
            reasons.append(f"{route_id} is an unattempted good_bounded_candidate")
        if status in {"not_attempted_lower_priority", "not_attempted"} and alternative == "yes":
            reasons.append(f"{route_id} records alternative_route_available=yes")
        if selected and failure_class in {"evaluator_wrapper_error", "undiagnosed"}:
            reasons.append(f"{route_id} failed with {failure_class}")
        if decisive in {"not_decisive_alternative_exists", "unclear"}:
            reasons.append(f"{route_id} decisiveness_for_functional={decisive}")
        if _route_has_open_mode_or_demo(route):
            reasons.append(f"{route_id} has unresolved lighter mode or demo/test grounding")

    arbitration_text = _json_text(d3_stage.get("route_arbitration", {})).lower()
    if "not_decisive_alternative_exists" in arbitration_text:
        reasons.append("D3 route_arbitration says not_decisive_alternative_exists")
    if re.search(r"\bunclear\b", arbitration_text) and "decisive" in arbitration_text:
        reasons.append("D3 route_arbitration leaves decisiveness unclear")
    return _dedupe_preserve_order(reasons)


def _d5_open_transfer_reasons(d5_stage: dict[str, Any]) -> list[str]:
    if not isinstance(d5_stage, dict):
        return []
    reasons: list[str] = []
    for candidate in _extract_route_items(d5_stage, ("transfer_candidate_portfolio", "candidate_portfolio", "reuse_candidates")):
        if not isinstance(candidate, dict):
            continue
        candidate_id = _route_label(candidate, fallback="D5 candidate")
        feasibility = _lower(candidate.get("feasibility"))
        selected = bool(candidate.get("selected_for_validation"))
        failure_class = _lower(candidate.get("failure_class") or candidate.get("failure_interpretation_if_attempted"))
        repair_decision = _lower(candidate.get("repair_decision"))
        repair_attempted = bool(candidate.get("repair_attempted"))
        decisive = _lower(candidate.get("decisiveness_after_attempts"))
        if not selected and feasibility == "feasible_bounded":
            reasons.append(f"{candidate_id} is an unattempted feasible_bounded candidate")
        if selected and failure_class in {"input_contract_mismatch", "route_or_config_choice", "evaluator_wrapper_error"}:
            if repair_decision == "repair_required" and not repair_attempted:
                reasons.append(f"{candidate_id} has repair_required but no repair_attempted")
            elif decisive in {"not_decisive_alternative_exists", "unclear"}:
                reasons.append(f"{candidate_id} failure remains {decisive}")
        if decisive in {"not_decisive_alternative_exists", "unclear"}:
            reasons.append(f"{candidate_id} decisiveness_after_attempts={decisive}")

    transfer_text = _json_text(d5_stage.get("transfer_validation", {})).lower()
    if "not_decisive_alternative_exists" in transfer_text:
        reasons.append("D5 transfer_validation says not_decisive_alternative_exists")
    if re.search(r"\bunclear\b", transfer_text) and ("decisive" in transfer_text or "route" in transfer_text):
        reasons.append("D5 transfer_validation leaves decisiveness unclear")
    return _dedupe_preserve_order(reasons)


def _d5_stage_transfer_semantics(d5_stage: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(d5_stage, dict):
        return {}
    for key in ("d5_transfer_sufficiency_check", "transfer_sufficiency_semantics", "classifications"):
        value = d5_stage.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _extract_route_items(stage_packet: dict[str, Any], keys: tuple[str, ...]) -> list[Any]:
    items: list[Any] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in keys and isinstance(value, list):
                    items.extend(value)
                elif isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(stage_packet)
    return items


def _route_label(route: dict[str, Any], *, fallback: str) -> str:
    for key in ("route_id", "candidate_id", "target_name", "component_path", "source"):
        value = str(route.get(key, "")).strip()
        if value:
            return value
    return fallback


def _route_has_open_mode_or_demo(route: dict[str, Any]) -> bool:
    status = _lower(route.get("attempt_status"))
    if status.startswith("attempted_") or status in {"not_attempted_resource_gated", "not_attempted_missing_input", "not_attempted_out_of_scope"}:
        return False
    priority = _lower(route.get("selection_priority_hint") or route.get("priority"))
    if priority != "good_bounded_candidate":
        return False
    mode_variants = route.get("mode_variants")
    demo_grounding = str(route.get("demo_entry_grounding", "")).strip()
    if isinstance(mode_variants, list) and mode_variants:
        return True
    return bool(demo_grounding)


def _representative_semantic_blockers(semantics: dict[str, Any], source: str) -> list[str]:
    blockers: list[str] = []
    if _truthy_semantic(semantics.get("generic_dependency_or_example")):
        blockers.append(f"{source}.generic_dependency_or_example=true")
    if _truthy_semantic(semantics.get("component_only")):
        blockers.append(f"{source}.component_only=true")
    artifact_logic = _lower(semantics.get("artifact_specific_logic_exercised"))
    if artifact_logic == "no":
        blockers.append(f"{source}.artifact_specific_logic_exercised=no")
    paper_output = _lower(semantics.get("paper_facing_output"))
    if paper_output == "no":
        blockers.append(f"{source}.paper_facing_output=no")
    blocker = str(semantics.get("functional_representativeness_blocker", "")).strip()
    if blocker and any(
        phrase in blocker.lower()
        for phrase in (
            "component-only",
            "component only",
            "helper",
            "does not exercise the artifact's core",
            "does not exercise artifact core",
            "does not exercise model",
            "does not exercise training",
            "does not exercise evaluation",
            "only covers dataset",
            "only covers data",
            "only validates dataset",
            "cannot support artifact-level",
        )
    ):
        blockers.append(f"{source}.functional_representativeness_blocker present")
    return blockers


def _truthy_semantic(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "yes", "y", "1"}


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value or "")


def _has_stage_sufficiency_override_basis(
    final_d5: dict[str, Any],
    d5_stage: dict[str, Any],
    reusable_sufficiency: dict[str, Any],
) -> bool:
    final_text = json.dumps(final_d5, ensure_ascii=False).lower()
    adjudication_text = json.dumps(reusable_sufficiency, ensure_ascii=False).lower()
    stage_text = json.dumps(d5_stage, ensure_ascii=False).lower()
    text = final_text + "\n" + adjudication_text + "\n" + stage_text
    stage_semantics = _d5_stage_transfer_semantics(d5_stage)
    final_target = str(final_d5.get("reuse_target_level", "")).strip().lower()
    stage_target = ""
    if isinstance(stage_semantics, dict):
        stage_target = str(stage_semantics.get("reuse_target_level", "")).strip().lower()
    if _artifact_level_sufficiency_value(stage_semantics.get("artifact_level_sufficiency") if isinstance(stage_semantics, dict) else "") == "supporting_only":
        return _stage_supporting_only_override_exception(final_d5, d5_stage, reusable_sufficiency)
    surface_markers = (
        "primary reusable surface",
        "intended reusable surface",
        "public reusable surface",
        "declared reusable surface",
        "no higher-level tool",
        "notebook",
        "spreadsheet",
        "dataset",
        "result package",
        "result_package",
        "replication package",
    )
    override_markers = (
        "stage",
        "updated",
        "override",
        "because",
        "therefore",
        "artifact type",
        "for this artifact",
    )
    stronger_surface_markers = (
        "notebook",
        "spreadsheet",
        "dataset",
        "result package",
        "result_package",
        "deposited data",
        "deposited public files",
        "analysis code",
        "no higher-level tool",
        "primary reusable surface",
        "intended reusable surface",
    )
    explicit_override_markers = (
        "stage",
        "d5 packet",
        "earlier",
        "label",
        "updated",
        "override",
        "wrong",
        "incorrect",
        "misclassified",
        "not applicable",
    )
    if not any(marker in final_text for marker in surface_markers):
        return False
    if not any(marker in final_text for marker in override_markers):
        return False

    if _final_only_relaxes_full_pipeline_requirement(final_text + "\n" + adjudication_text):
        return False

    if not _final_addresses_reusable_rubric_conditions(final_text + "\n" + adjudication_text):
        return False

    if stage_target == final_target:
        return _final_explicitly_rebases_reusable_surface(final_text + "\n" + adjudication_text) or _final_explains_supporting_boundary_is_nonblocking(
            final_text + "\n" + adjudication_text
        )

    if stage_target in {"module_or_function_level", "component_level", "utility_level"}:
        if final_target in {"module_or_function_level", "component_level", "utility_level"}:
            return False
        if not any(marker in final_text for marker in explicit_override_markers):
            return False
        if not (
            _final_explicitly_rebases_reusable_surface(final_text + "\n" + adjudication_text)
            or _final_explains_supporting_boundary_is_nonblocking(final_text + "\n" + adjudication_text)
        ):
            return False
    if "supporting_only" not in final_text and not any(marker in final_text for marker in explicit_override_markers):
        return False
    return any(marker in text for marker in stronger_surface_markers)


def _stage_supporting_only_override_exception(
    final_d5: dict[str, Any],
    d5_stage: dict[str, Any],
    reusable_sufficiency: dict[str, Any],
) -> bool:
    """Narrow exception for deposited data/result/notebook surfaces.

    A D5 stage packet's `supporting_only` label is a strong evidence boundary.
    Final scoring may override it only when structured final fields identify a
    public deposited data/result/notebook surface as the intended
    reusable object and explicitly negate any higher-level active tool surface.
    """
    target = str(final_d5.get("reuse_target_level", "")).strip().lower()
    text = "\n".join(
        [
            _json_text(final_d5),
            _json_text(reusable_sufficiency),
            _json_text(d5_stage),
        ]
    ).lower()
    if target not in {
        "dataset_package_level",
        "result_package_level",
        "notebook_package_level",
        "spreadsheet_package_level",
    }:
        return False
    primary_markers = (
        "deposited public files are the intended",
        "deposited data files are the intended",
        "dataset package is the primary",
        "result package is the primary",
        "notebook is the primary",
        "spreadsheet is the primary",
    )
    no_higher_surface_markers = (
        "no higher-level tool",
        "no higher level tool",
        "no broader public surface",
        "no broader reusable surface",
        "no separate active tool",
        "no active tool/pipeline",
        "tool is supplementary",
        "tools are supplementary",
        "tool component is resource-gated but not the sole declared public artifact",
        "detection framework is not the sole public reusable surface",
    )
    active_unvalidated_markers = (
        "primary pipeline remains unvalidated",
        "full pipeline remains unvalidated",
        "primary tool surface remains unvalidated",
        "primary reusable surface remains unvalidated",
        "active pipeline surface was not run",
    )
    if any(marker in text for marker in active_unvalidated_markers) and not any(marker in text for marker in no_higher_surface_markers):
        return False
    return any(marker in text for marker in primary_markers) and any(marker in text for marker in no_higher_surface_markers)


def _stage_supporting_transfer_has_unvalidated_primary_surface(
    d5_stage: dict[str, Any],
    final_scoring_packet: dict[str, Any],
) -> bool:
    text = (
        _json_text(d5_stage)
        + "\n"
        + _json_text(final_scoring_packet.get("d5_transfer_sufficiency_check", {}))
        + "\n"
        + _json_text(final_scoring_packet.get("rubric_evidence_adjudication", {}))
    ).lower()
    supporting_markers = (
        "supporting_only",
        "supporting only",
        "component-level",
        "component level",
        "module-level",
        "module level",
        "helper",
        "result slice",
        "result-package",
        "result package",
    )
    primary_markers = (
        "primary public surface",
        "primary reusable surface",
        "intended public surface",
        "intended reusable surface",
        "documented live tool",
        "live tool",
        "full pipeline",
        "primary pipeline",
        "main pipeline",
        "tool surface",
        "model surface",
    )
    unvalidated_markers = (
        "unvalidated",
        "not validated",
        "not independently validated",
        "not attempted",
        "not run",
        "not executed",
        "remains gated",
        "resource-gated",
        "api-gated",
        "gpu-gated",
        "blocked",
    )
    benign_primary_markers = (
        "no higher-level tool",
        "no higher level tool",
        "no broader public surface",
        "no broader reusable surface",
        "notebook",
        "spreadsheet",
        "dataset package whose primary",
        "result package whose primary",
    )
    if any(marker in text for marker in benign_primary_markers):
        return False
    return (
        any(marker in text for marker in supporting_markers)
        and any(marker in text for marker in primary_markers)
        and any(marker in text for marker in unvalidated_markers)
    )


def _text_negates_active_surface(text: str) -> bool:
    negated_markers = (
        "no separate active tool",
        "no separate active tool/pipeline",
        "no active tool",
        "no active tool/pipeline",
        "no broader tool",
        "no broader tool/pipeline",
        "no higher-level tool",
        "no separate tool",
        "there is no separate active",
        "no separate active pipeline",
        "supplementary tool",
        "supplementary tools",
        "supplementary analysis tool",
        "supplementary analysis tools",
        "supplementary component",
        "supplementary components",
        "supplementary code",
        "supplementary script",
        "supplementary scripts",
        "tools are supplementary",
        "tool is supplementary",
        "components are supplementary",
        "component is supplementary",
    )
    return any(marker in text for marker in negated_markers)


def _active_tool_surface_unvalidated(text: str) -> bool:
    if _text_negates_active_surface(text):
        return False
    return _active_tool_model_pipeline_surface(text) and _main_generation_or_execution_unobserved(text)


def _final_only_relaxes_full_pipeline_requirement(final_text: str) -> bool:
    """Reject overrides that merely say the rubric does not require full coverage."""
    relaxation_markers = (
        "does not require full pipeline",
        "does not require full-pipeline",
        "not require full pipeline",
        "not require full-pipeline",
        "full pipeline coverage is not required",
        "full-pipeline coverage is not required",
        "full pipeline is not required",
        "full-pipeline is not required",
        "full pipeline coverage",
        "full-pipeline coverage",
    )
    full_surface_markers = (
        "full pipeline",
        "full-pipeline",
        "primary surface",
        "primary reusable surface",
        "intended reusable surface",
        "public reusable surface",
    )
    return any(marker in final_text for marker in relaxation_markers) and any(
        marker in final_text for marker in full_surface_markers
    )


def _final_explicitly_rebases_reusable_surface(final_text: str) -> bool:
    """True only when final scoring names why the stage's surface boundary is wrong."""
    rebasing_markers = (
        "stage label is wrong",
        "stage label is incorrect",
        "stage limitation is wrong",
        "stage limitation is incorrect",
        "stage misclassified",
        "d5 packet is wrong",
        "d5 packet is incorrect",
        "supporting_only label is wrong",
        "supporting_only label is incorrect",
        "no higher-level tool",
        "no higher level tool",
        "no broader public surface",
        "no broader reusable surface",
        "no separate primary surface",
        "primary reusable surface is",
        "intended reusable surface is",
        "public reusable surface is",
    )
    return any(marker in final_text for marker in rebasing_markers)


def _final_addresses_reusable_rubric_conditions(final_text: str) -> bool:
    condition_groups = (
        ("functional", "d5.1"),
        ("d1", "documentation"),
        ("d5", "wa", "a"),
        ("transfer", "validation", "d5.4"),
        ("new input", "new configuration", "new project", "new scenario", "independent"),
        ("explainable", "output", "d5.5"),
        ("i4", "manual patch", "tacit knowledge", "private"),
    )
    return all(any(marker in final_text for marker in group) for group in condition_groups)


def _final_explains_supporting_boundary_is_nonblocking(final_text: str) -> bool:
    nonblocking_markers = (
        "does not make d5.2 fail",
        "does not make d5.4 fail",
        "does not make d5.5 fail",
        "does not make d5.6 fail",
        "d5.2 is satisfied",
        "d5.4 is satisfied",
        "d5.5 is satisfied",
        "d5.6 is satisfied",
        "reusable condition",
        "rubric condition",
    )
    stage_boundary_markers = (
        "supporting_only",
        "stage",
        "d5 packet",
        "boundary",
        "limitation",
    )
    return any(marker in final_text for marker in nonblocking_markers) and any(
        marker in final_text for marker in stage_boundary_markers
    )


def _check_decision_conditions(section_name: str, decision: Any) -> list[dict[str, Any]]:
    if not isinstance(decision, dict):
        return []
    conditions = decision.get("conditions", [])
    if not isinstance(conditions, list) or not conditions:
        return []
    required = [
        bool(item.get("satisfied"))
        for item in conditions
        if isinstance(item, dict) and not _condition_is_vacuous(str(item.get("condition", "")), str(item.get("evidence", "")))
    ]
    if not required:
        return []
    expected_holds = all(required)
    observed_holds = bool(decision.get("holds"))
    if observed_holds != expected_holds:
        return [
            _finding(
                "error",
                "final_decision_holds_mismatch",
                f"{section_name}.holds={observed_holds}, but required condition booleans imply holds={expected_holds}.",
            )
        ]
    return []


def _check_required_condition_coverage(section_name: str, decision: Any, required_marker_groups: list[list[str]]) -> list[dict[str, Any]]:
    if not isinstance(decision, dict):
        return []
    conditions = decision.get("conditions", [])
    if not isinstance(conditions, list):
        return []
    text = "\n".join(
        str(item.get("condition", "")) + " " + str(item.get("evidence", ""))
        for item in conditions
        if isinstance(item, dict)
    ).lower()
    findings = []
    for markers in required_marker_groups:
        if not any(marker.lower() in text for marker in markers):
            findings.append(
                _finding(
                    "warning",
                    "final_decision_missing_condition",
                    f"{section_name} does not record required condition marker group `{markers}`.",
                )
            )
    return findings


def _condition_is_vacuous(condition: str, evidence: str) -> bool:
    text = f"{condition} {evidence}".lower()
    return "if highest intervention is i3" in text and "not i3" in text


def _check_stage_check_corrections(report: dict[str, Any], stage_checks: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    check_findings = _stage_check_findings(stage_checks)
    corrections = report.get("stage_check_corrections", report.get("audit_corrections", []))
    if check_findings and not corrections:
        findings.append(_finding("error", "missing_stage_check_corrections", "stage_checks has findings but report audit_corrections is empty."))
        return findings
    if check_findings and isinstance(corrections, list):
        correction_ids = {str(item.get("finding_id", item.get("id", ""))).strip() for item in corrections if isinstance(item, dict)}
        for finding in check_findings:
            finding_id = str(finding.get("id", finding.get("finding_id", ""))).strip()
            if finding_id and finding_id not in correction_ids:
                findings.append(_finding("warning", "stage_check_finding_without_correction", f"Stage-check finding `{finding_id}` has no matching correction record."))
    return findings


def _check_intervention_consistency(
    report: dict[str, Any],
    final_scoring_packet: dict[str, Any],
    intervention_ledger: dict[str, Any],
    command_ledger: dict[str, Any],
) -> list[dict[str, Any]]:
    findings = []
    reported = _intervention_level(report.get("highest_intervention"))
    review_value = _intervention_level(final_scoring_packet.get("highest_intervention")) if isinstance(final_scoring_packet, dict) else ""
    if review_value and reported and review_value != reported:
        findings.append(_finding("error", "highest_intervention_mismatch", f"Report highest_intervention={reported}, final_scoring_packet={review_value}."))

    interventions = intervention_ledger.get("interventions", []) if isinstance(intervention_ledger, dict) else []
    if isinstance(interventions, list) and interventions:
        ledger_highest = _highest_intervention_from_records(interventions)
        if ledger_highest and reported and _intervention_value(ledger_highest) > _intervention_value(reported):
            findings.append(
                _finding(
                    "error",
                    "highest_intervention_below_ledger",
                    f"Report highest_intervention={reported}, but ledger contains {ledger_highest}.",
                )
            )
    command_highest = _highest_intervention_from_command_ledger(command_ledger)
    if command_highest and reported and _intervention_value(command_highest) > _intervention_value(reported):
        findings.append(
            _finding(
                "error",
                "highest_intervention_below_command_mutation",
                f"Report highest_intervention={reported}, but command ledger repo mutations imply at least {command_highest}.",
            )
        )
    return findings


def _check_reported_interventions(
    report: dict[str, Any],
    intervention_ledger: dict[str, Any],
    command_ledger: dict[str, Any],
) -> list[dict[str, Any]]:
    reported = report.get("interventions", [])
    if not isinstance(reported, list):
        return [_finding("warning", "report_interventions_not_list", "ae_report.json interventions is not a list.")]
    ledger_records = intervention_ledger.get("interventions", []) if isinstance(intervention_ledger, dict) else []
    ledger_command_ids = _intervention_command_ids(ledger_records)
    ledger_summaries = {
        str(item.get("summary", "")).strip()
        for item in ledger_records
        if isinstance(item, dict) and str(item.get("summary", "")).strip()
    }
    command_ids = _command_ids(command_ledger)
    findings = []
    for item in reported:
        if not isinstance(item, dict):
            continue
        command_id = str(item.get("command_id", "")).strip()
        summary = str(item.get("summary", "")).strip()
        if command_id and command_id not in command_ids:
            findings.append(_finding("error", "reported_intervention_unknown_command", f"Reported intervention references unknown command `{command_id}`."))
        if command_id and ledger_command_ids and command_id not in ledger_command_ids:
            findings.append(_finding("error", "reported_intervention_not_in_ledger", f"Reported intervention command `{command_id}` is not present in the intervention ledger."))
        elif not command_id and summary and ledger_summaries and summary not in ledger_summaries:
            findings.append(_finding("error", "reported_intervention_not_in_ledger", f"Reported intervention `{summary}` is not present in the intervention ledger."))
    return findings


def _intervention_command_ids(records: list[Any]) -> set[str]:
    command_ids: set[str] = set()
    for item in records:
        if not isinstance(item, dict):
            continue
        details = item.get("details", {})
        if isinstance(details, dict):
            command_id = str(details.get("command_id", "")).strip()
            if command_id:
                command_ids.add(command_id)
        refs = item.get("evidence_refs", [])
        if isinstance(refs, list):
            command_ids.update(str(ref).strip() for ref in refs if str(ref).strip().startswith("cmd_"))
    return command_ids


def _check_evidence_refs(run_root: Path, report: dict[str, Any], command_ledger: dict[str, Any]) -> list[dict[str, Any]]:
    refs = report.get("evidence_refs", [])
    if not isinstance(refs, list) or not refs:
        return [_finding("error", "missing_evidence_refs", "ae_report.json evidence_refs is empty or missing.")]
    findings = []
    missing = []
    command_ids = _command_ids(command_ledger)
    repo_root = _infer_repo_root(run_root, report)
    for ref in refs:
        text = str(ref).strip()
        if not text:
            continue
        if text.startswith("repo:"):
            repo_relative = text.removeprefix("repo:").strip()
            if not repo_root or not repo_relative or not (repo_root / repo_relative).exists():
                missing.append(text)
            continue
        if text.startswith("cmd_"):
            if text not in command_ids:
                missing.append(text)
            continue
        if not _evidence_path_exists(run_root, text, repo_root=repo_root):
            missing.append(text)
    for ref in missing[:20]:
        findings.append(_finding("warning", "missing_evidence_ref_path", f"Evidence ref path does not exist under run root: {ref}"))
    if len(missing) > 20:
        findings.append(_finding("warning", "missing_evidence_ref_path_truncated", f"{len(missing) - 20} additional evidence refs are missing."))
    return findings


def _command_ids(command_ledger: dict[str, Any]) -> set[str]:
    commands = command_ledger.get("commands", []) if isinstance(command_ledger, dict) else []
    return {
        str(item.get("command_id", "")).strip()
        for item in commands
        if isinstance(item, dict) and str(item.get("command_id", "")).strip()
    }


def _infer_repo_root(run_root: Path, report: dict[str, Any]) -> Path | None:
    artifact = report.get("artifact") if isinstance(report, dict) else {}
    if isinstance(artifact, dict):
        for key in ("repo_root", "artifact_path", "repository_path", "path"):
            value = str(artifact.get(key, "")).strip()
            if value and Path(value).exists():
                return Path(value).resolve()
    run_config = _read_json(run_root / "artifacts" / "run_initialization" / "run_config.json")
    for key in ("artifact_path", "repo_root", "repository_path"):
        value = str(run_config.get(key, "")).strip()
        if value and Path(value).exists():
            return Path(value).resolve()
    agent_state = _read_json(run_root / "agent_state.json")
    value = str(agent_state.get("repo_root", "")).strip()
    if value and Path(value).exists():
        return Path(value).resolve()
    shared_context = agent_state.get("shared_context", {})
    if isinstance(shared_context, dict):
        run_config = shared_context.get("run_config", {})
        if isinstance(run_config, dict):
            inputs = run_config.get("inputs", {})
            if isinstance(inputs, dict):
                value = str(inputs.get("artifact_repo", "")).strip()
                if value and Path(value).exists():
                    return Path(value).resolve()
    return None


def _evidence_path_exists(run_root: Path, text: str, *, repo_root: Path | None = None) -> bool:
    path = Path(text)
    candidate = path if path.is_absolute() else run_root / text
    if candidate.exists():
        return True
    if repo_root is not None and not path.is_absolute():
        repo_candidate = repo_root / text
        if repo_candidate.exists():
            return True
    marker = "artifacts/"
    raw = path.as_posix()
    if marker in raw:
        suffix = raw[raw.rfind(marker) :]
        if (run_root / suffix).exists():
            return True
    run_marker = run_root.name.rstrip("/") + "/"
    if run_marker in raw:
        suffix = raw.split(run_marker, 1)[1].lstrip("/")
        if suffix and (run_root / suffix).exists():
            return True
    return False


def _check_command_ledger(command_ledger: dict[str, Any]) -> list[dict[str, Any]]:
    commands = command_ledger.get("commands", []) if isinstance(command_ledger, dict) else []
    if not commands:
        return [_finding("warning", "missing_command_ledger", "Command ledger is missing or empty.")]
    findings = []
    for item in commands:
        if not isinstance(item, dict):
            continue
        command_id = str(item.get("command_id", "")).strip()
        if not command_id:
            findings.append(_finding("warning", "command_missing_id", "Command ledger entry missing command_id."))
        if not item.get("stdout_ref") and not item.get("stderr_ref"):
            findings.append(_finding("warning", "command_missing_logs", f"Command `{command_id or '(unknown)'}` has no stdout/stderr refs."))
    return findings


def _extract_scores(payload: dict[str, Any]) -> dict[str, str]:
    scores: dict[str, str] = {}
    raw = payload.get("dimension_scores", {}) if isinstance(payload, dict) else {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            dim = _dimension_key(item.get("dimension", ""))
            score = str(item.get("score", "")).strip()
            if dim in {"D1", "D2", "D3", "D4", "D5"} and score:
                scores[dim] = score
        return scores
    if not isinstance(raw, dict):
        return scores
    for dim in ("D1", "D2", "D3", "D4", "D5"):
        value = raw.get(dim)
        if value is None:
            for key, candidate in raw.items():
                if _dimension_key(key) == dim:
                    value = candidate
                    break
        if isinstance(value, dict):
            scores[dim] = str(value.get("score", "")).strip()
        else:
            scores[dim] = str(value or "").strip()
    return scores


def _dimension_key(value: Any) -> str:
    text = str(value or "").strip()
    for dim in ("D1", "D2", "D3", "D4", "D5"):
        if text == dim or text.startswith(f"{dim}_") or text.startswith(f"{dim} "):
            return dim
    return text


def _badge_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("recommended_badge", "final_badge", "badge", "recommended"):
            text = str(value.get(key, "")).strip().lower()
            if text:
                return text
        parts = []
        if value.get("available"):
            parts.append("available")
        if value.get("functional"):
            parts.append("functional")
        if value.get("reusable"):
            parts.append("reusable")
        return " + ".join(parts)
    return str(value or "").strip().lower()


def _badge_bool(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return bool(value.get(key))
    text = _badge_text(value)
    if key == "available":
        return text in {"available", "available + functional", "available + functional + reusable"}
    if key == "functional":
        return "functional" in text
    if key == "reusable":
        return "reusable" in text
    return False


def _artifact_level_sufficiency_value(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    normalized = text.replace("-", "_").replace(" ", "_")
    if normalized.startswith("supporting_only") or "supporting_only" in normalized:
        return "supporting_only"
    if normalized.startswith("insufficient") or "insufficient" in normalized:
        return "insufficient"
    if normalized.startswith("sufficient") or "sufficient" in normalized:
        return "sufficient"
    return ""


def _decision_holds(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("holds"))
    return str(value or "").strip().lower() in {"true", "yes", "holds", "held", "satisfied"}


def _expected_badge(available: bool, functional: bool, reusable: bool) -> str:
    if not available:
        return "no badge"
    if reusable:
        return "available + functional + reusable"
    if functional:
        return "available + functional"
    return "available"


def _intervention_level(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("level", value.get("highest", value.get("kind", "")))).strip()
    text = str(value or "").strip()
    for level in ("I0", "I1", "I2", "I3", "I4"):
        if text == level or text.startswith(f"{level} ") or text.startswith(f"{level}("):
            return level
    return text


def _available_passed(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if isinstance(value.get("holds"), bool):
        return bool(value.get("holds"))
    if isinstance(value.get("available"), bool):
        return bool(value.get("available"))
    if isinstance(value.get("available_holds"), bool):
        return bool(value.get("available_holds"))
    if isinstance(value.get("available_decision"), bool):
        return bool(value.get("available_decision"))
    if isinstance(value.get("passed"), bool):
        return bool(value.get("passed"))
    overall = str(value.get("overall", value.get("decision", value.get("status", "")))).strip().lower()
    if overall in {"pass", "passed", "available", "pass_with_limitation", "passed_with_limitation"}:
        return True
    decision = str(value.get("available_decision", value.get("available_reason", ""))).strip().lower()
    if decision in {"pass", "passed", "available", "true", "yes"} or decision.startswith("available awarded"):
        return True
    conditions = value.get("conditions")
    if isinstance(conditions, list) and conditions:
        relevant = [
            bool(item.get("satisfied"))
            for item in conditions
            if isinstance(item, dict) and str(item.get("condition", "")).strip()
        ]
        if relevant:
            return all(relevant)
    return all(_gate_item_passed(value.get(key)) for key in ("GC1", "GC2", "GC3"))


def _gate_item_passed(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("pass"), bool):
            return bool(value.get("pass"))
        status = str(value.get("status", value.get("decision", value.get("result", "")))).strip().lower()
        return _status_text_passed(status)
    return _status_text_passed(str(value or ""))


def _status_text_passed(value: str) -> bool:
    text = str(value or "").strip().lower()
    if text in {"pass", "passed", "available", "pass_with_limitation", "passed_with_limitation"}:
        return True
    return (
        text.startswith("pass ")
        or text.startswith("pass(")
        or text.startswith("pass:")
        or text.startswith("passed ")
        or text.startswith("passed(")
        or text.startswith("passed:")
    )


def _score_lt(value: str | None, threshold: str) -> bool:
    if value not in SCORE_ORDER:
        return False
    return SCORE_ORDER[value] < SCORE_ORDER[threshold]


def _intervention_value(value: str) -> int:
    return INTERVENTION_ORDER.get(str(value).strip(), -1)


def _highest_intervention_from_records(records: list[Any]) -> str:
    highest = ""
    for item in records:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", item.get("level", ""))).strip()
        if _intervention_value(kind) > _intervention_value(highest):
            highest = kind
    return highest


def _highest_intervention_from_command_ledger(command_ledger: dict[str, Any]) -> str:
    commands = command_ledger.get("commands", []) if isinstance(command_ledger, dict) else []
    if not isinstance(commands, list):
        return ""
    highest = ""
    for command in commands:
        if not isinstance(command, dict):
            continue
        mutation = command.get("repo_mutation", {})
        changed = mutation.get("changed_files", []) if isinstance(mutation, dict) else []
        if not isinstance(changed, list) or not changed:
            continue
        level = "I2" if any(_repo_mutation_path_requires_intervention(item) for item in changed if isinstance(item, dict)) else "I1"
        if _intervention_value(level) > _intervention_value(highest):
            highest = level
    return highest


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


def _stage_check_findings(stage_checks: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("audit_findings", "findings"):
        value = stage_checks.get(key) if isinstance(stage_checks, dict) else None
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _finding(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}
