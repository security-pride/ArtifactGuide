from __future__ import annotations

import json
from typing import Any

SCORE_ORDER = {"R": 0, "WR": 1, "WA": 2, "A": 3}
INTERVENTION_ORDER = {"I0": 0, "I1": 1, "I2": 2, "I3": 3, "I4": 4}

DATASET_PACKAGE_TYPES = {
    "dataset_package",
    "dataset_deposit",
    "dataset/result_package",
    "dataset_result_package",
    "dataset/result package",
    "dataset/result package with supplementary tools",
    "dataset/result package with supplementary analysis tools",
    "dataset/result package with supplementary code",
    "dataset/result package with supplementary tool",
    "dataset/result package with supplementary tools",
    "dataset/result package with supplementary pipeline",
    "dataset/result package with supplementary pipelines",
    "dataset/result package with supplementary components",
    "dataset/result package with supplementary software",
    "dataset/result package with supplementary cli",
    "dataset/result package with supplementary clis",
    "dataset/result package with supplementary scripts",
    "dataset/result package with supplementary evaluation scripts",
    "dataset and result package",
    "dataset and result package with supplementary tools",
    "data_package",
    "data/result_package",
    "data_result_package",
    "data/result package",
    "data/result package with supplementary tools",
    "benchmark_data_package",
    "result_package",
    "notebook_package",
    "spreadsheet_package",
    "replication_package",
}

DATASET_PACKAGE_WORKFLOWS = {
    "dataset_loading_or_aggregation",
    "result_validation_only",
    "post_processing_only",
}

DEPOSITED_OUTPUT_PROVENANCE = {
    "loaded_from_deposited_files",
    "validated_precomputed_output",
    "derived_from_existing_results",
}


def derive_badge_predicates(packet: dict[str, Any]) -> dict[str, Any]:
    """Derive badge gates from structured rubric evidence.

    This is an evidence consistency layer. It only treats explicit structured
    contradictions as blockers; missing or uncertain fields are reported as
    warnings so the engineering layer does not replace reviewer-style reasoning.
    """
    packet = normalize_final_scoring_packet(packet if isinstance(packet, dict) else {}, overwrite_decisions=False)
    scores = _extract_scores(packet)
    warnings: list[str] = []

    available, available_failures = _derive_available(packet)
    functional_failures: list[str] = []
    reusable_failures: list[str] = []

    if not available:
        functional_failures.extend(available_failures or ["available gate does not hold"])
    for dim in ("D2", "D3", "D4"):
        if _score_lt(scores.get(dim), "WA"):
            functional_failures.append(f"{dim} is below WA")

    tier = _lower(_nested(packet.get("evidence_tier"), "tier"))
    if tier and tier not in {"e2", "e3"}:
        functional_failures.append(f"evidence tier {tier.upper()} is below executable evidence")
    elif not tier:
        warnings.append("evidence tier is missing or unclear")

    highest = packet.get("highest_intervention", {})
    if isinstance(highest, dict):
        if highest.get("depends_on_i4") is True:
            functional_failures.append("successful path depends on I4 intervention")
        if _intervention_value(str(highest.get("level", ""))) >= _intervention_value("I4"):
            functional_failures.append("highest intervention is I4")

    functional_failures.extend(_functional_evidence_blockers(packet, warnings))
    functional = not functional_failures

    if not functional:
        reusable_failures.append("Functional gate does not hold")
    for dim in ("D1", "D5"):
        if _score_lt(scores.get(dim), "WA"):
            reusable_failures.append(f"{dim} is below WA")
    reusable_failures.extend(_reusable_evidence_blockers(packet, warnings))
    reusable = not reusable_failures

    badge = _expected_badge(available, functional, reusable)
    claimed = {
        "available": _badge_bool(packet.get("badge_recommendation"), "available"),
        "functional": _decision_holds(packet.get("functional_decision")) or _badge_bool(packet.get("badge_recommendation"), "functional"),
        "reusable": _decision_holds(packet.get("reusable_decision")) or _badge_bool(packet.get("badge_recommendation"), "reusable"),
        "badge": _badge_text(packet.get("badge_recommendation")),
    }

    contradictions: list[str] = []
    if claimed["available"] and not available:
        contradictions.extend(available_failures or ["Available is claimed but the available gate does not hold"])
    if claimed["functional"] and not functional:
        contradictions.extend(f"Functional claimed despite: {item}" for item in functional_failures)
    if claimed["reusable"] and not reusable:
        contradictions.extend(f"Reusable claimed despite: {item}" for item in reusable_failures)
    if claimed["badge"] and claimed["badge"] != badge and not contradictions:
        contradictions.append(f"badge recommendation `{claimed['badge']}` does not match derived badge `{badge}`")

    return {
        "schema": "rubric_badge_predicate_result_v1",
        "available": available,
        "functional": functional,
        "reusable": reusable,
        "derived_badge": badge,
        "claimed": claimed,
        "failed_predicates": {
            "available": available_failures,
            "functional": functional_failures,
            "reusable": reusable_failures,
        },
        "warnings": warnings,
        "contradictions": _dedupe(contradictions),
    }


def normalize_final_scoring_packet(packet: dict[str, Any], *, overwrite_decisions: bool = True) -> dict[str, Any]:
    """Return a normalized copy of the final scoring packet.

    This keeps LLM-written explanations intact but canonicalizes the enum-like
    fields consumed by the deterministic evidence engine. The agent can still
    write rich prose in reason fields; gate fields must be stable.
    """
    packet = dict(packet if isinstance(packet, dict) else {})
    d5 = packet.get("d5_transfer_sufficiency_check")
    if isinstance(d5, dict):
        d5 = dict(d5)
        d5["reuse_target_level"] = _normalize_reuse_target_level(d5.get("reuse_target_level"))
        d5["transfer_novelty"] = _normalize_transfer_novelty(d5.get("transfer_novelty"))
        d5["relationship_to_d3"] = _normalize_relationship_to_d3(d5.get("relationship_to_d3"))
        d5["validated_reuse_surface"] = _normalize_validated_reuse_surface(d5.get("validated_reuse_surface"))
        d5["artifact_level_sufficiency"] = _artifact_level_sufficiency_value(d5.get("artifact_level_sufficiency")) or "unknown"
        d5["transfer_failure_interpretation"] = _normalize_transfer_failure(d5.get("transfer_failure_interpretation"))
        d5["core_reuse_validation_result"] = _normalize_core_reuse_result(d5.get("core_reuse_validation_result"))
        packet["d5_transfer_sufficiency_check"] = d5

    route = packet.get("route_arbitration_check")
    if isinstance(route, dict):
        route = dict(route)
        route["d3_selected_route_decisiveness"] = _normalize_decisiveness(route.get("d3_selected_route_decisiveness"), d5=False)
        route["d5_selected_transfer_decisiveness"] = _normalize_decisiveness(route.get("d5_selected_transfer_decisiveness"), d5=True)
        packet["route_arbitration_check"] = route

    resource = packet.get("resource_boundary_assessment")
    if isinstance(resource, dict):
        resource = dict(resource)
        resource["boundary_type"] = _normalize_boundary_type(resource.get("boundary_type"))
        resource["position"] = _normalize_boundary_position(resource.get("position"))
        resource["artifact_logic_reached"] = _normalize_yes_limited_no(resource.get("artifact_logic_reached"))
        resource["paper_facing_output_before_boundary"] = _normalize_yes_limited_no(resource.get("paper_facing_output_before_boundary"))
        resource["blocks_all_reviewer_realistic_routes"] = _normalize_yes_no_unknown(resource.get("blocks_all_reviewer_realistic_routes"))
        resource["alternative_route_available"] = _normalize_yes_no_unknown(resource.get("alternative_route_available"))
        resource["alternative_evidence_exists"] = _normalize_yes_no_unknown(resource.get("alternative_evidence_exists"))
        resource["badge_effect"] = _normalize_badge_effect(resource.get("badge_effect"))
        packet["resource_boundary_assessment"] = resource

    e2 = packet.get("e2_sufficiency_check")
    if isinstance(e2, dict):
        e2 = dict(e2)
        e2["workflow_kind"] = _normalize_workflow_kind(e2.get("workflow_kind"))
        e2["output_provenance"] = _normalize_output_provenance(e2.get("output_provenance"))
        e2["upstream_generation_attempted"] = _normalize_upstream_generation(e2.get("upstream_generation_attempted"))
        e2["resource_boundary_position"] = _normalize_boundary_position(e2.get("resource_boundary_position"))
        e2["artifact_specific_logic_exercised"] = _normalize_yes_limited_no(e2.get("artifact_specific_logic_exercised"))
        e2["paper_facing_output"] = _normalize_yes_limited_no(e2.get("paper_facing_output"))
        packet["e2_sufficiency_check"] = e2

    packet = _normalize_available_gate_holds(packet)
    derived = derive_badge_predicates_without_normalizing(packet)
    if not overwrite_decisions:
        return packet
    hard_gates = _derive_hard_gate_decisions(packet)
    available = hard_gates["available"]
    functional = hard_gates["functional"]
    reusable = hard_gates["reusable"]
    final_badge = _expected_badge(available, functional, reusable)
    packet = _overwrite_gate_decisions_from_hard_gates(packet, hard_gates)
    packet["badge_recommendation"] = {
        "badge": final_badge,
        "available": available,
        "functional": functional,
        "reusable": reusable,
        "reason": "Runtime-derived badge from structured rubric predicates.",
    }
    packet["derived_badge"] = {
        "schema": "derived_badge_v1",
        "source": "runtime_gate_derivation",
        "available": available,
        "functional": functional,
        "reusable": reusable,
        "badge": final_badge,
        "predicate_audit": {
            "available": derived["available"],
            "functional": derived["functional"],
            "reusable": derived["reusable"],
            "badge": derived["derived_badge"],
            "failed_predicates": derived["failed_predicates"],
            "warnings": derived["warnings"],
            "contradictions": derived["contradictions"],
        },
    }
    return packet


def _derive_hard_gate_decisions(packet: dict[str, Any]) -> dict[str, Any]:
    scores = _extract_scores(packet)
    available, available_failures = _derive_available(packet)
    functional = _decision_holds(packet.get("functional_decision"))
    reusable = _decision_holds(packet.get("reusable_decision"))
    functional_blockers: list[str] = []
    reusable_blockers: list[str] = []

    if not available:
        functional_blockers.extend(available_failures or ["available gate does not hold"])
    for dim in ("D2", "D3", "D4"):
        if _score_lt(scores.get(dim), "WA"):
            functional_blockers.append(f"{dim} is below WA")

    tier = _lower(_nested(packet.get("evidence_tier"), "tier"))
    if tier and tier not in {"e2", "e3"}:
        functional_blockers.append(f"evidence tier {tier.upper()} is below executable evidence")

    highest = packet.get("highest_intervention", {})
    if isinstance(highest, dict):
        if highest.get("depends_on_i4") is True:
            functional_blockers.append("successful path depends on I4 intervention")
        if _intervention_value(str(highest.get("level", ""))) >= _intervention_value("I4"):
            functional_blockers.append("highest intervention is I4")

    if functional_blockers:
        functional = False

    if not functional:
        reusable_blockers.append("Functional gate does not hold")
    for dim in ("D1", "D5"):
        if _score_lt(scores.get(dim), "WA"):
            reusable_blockers.append(f"{dim} is below WA")
    if reusable_blockers:
        reusable = False

    return {
        "available": available,
        "functional": functional,
        "reusable": reusable,
        "failed_predicates": {
            "available": available_failures,
            "functional": _dedupe(functional_blockers),
            "reusable": _dedupe(reusable_blockers),
        },
    }


def _overwrite_gate_decisions_from_hard_gates(packet: dict[str, Any], hard_gates: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(packet)
    _set_decision_holds(normalized, "available_gate", bool(hard_gates["available"]))
    _set_decision_holds(normalized, "functional_decision", bool(hard_gates["functional"]))
    _set_decision_holds(normalized, "reusable_decision", bool(hard_gates["reusable"]))
    failed = hard_gates.get("failed_predicates", {})
    if isinstance(failed, dict):
        _append_predicate_reason(normalized, "available_gate", failed.get("available"))
        _append_predicate_reason(normalized, "functional_decision", failed.get("functional"))
        _append_predicate_reason(normalized, "reusable_decision", failed.get("reusable"))
    return normalized


def _set_decision_holds(packet: dict[str, Any], key: str, holds: bool) -> None:
    decision = packet.get(key)
    if not isinstance(decision, dict):
        decision = {}
    else:
        decision = dict(decision)
    decision["holds"] = holds
    packet[key] = decision


def _append_predicate_reason(packet: dict[str, Any], key: str, failures: Any) -> None:
    decision = packet.get(key)
    if not isinstance(decision, dict) or decision.get("holds") is not False:
        return
    if not isinstance(failures, list) or not failures:
        return
    reason = str(decision.get("reason", "")).strip()
    predicate_reason = "Structured predicate blockers: " + "; ".join(str(item) for item in failures[:8])
    if predicate_reason in reason:
        return
    decision["reason"] = f"{reason} {predicate_reason}".strip()
    packet[key] = decision


def _normalize_available_gate_holds(packet: dict[str, Any]) -> dict[str, Any]:
    gate = packet.get("available_gate")
    if not isinstance(gate, dict):
        return packet
    derived_holds = _derive_available_from_gc_conditions(gate)
    if derived_holds is None:
        return packet
    normalized = dict(packet)
    normalized_gate = dict(gate)
    normalized_gate["holds"] = derived_holds
    if not derived_holds:
        reason = str(normalized_gate.get("reason", "")).strip()
        if "GC" not in reason:
            normalized_gate["reason"] = "Available is false because at least one GC1-GC3 condition is not satisfied."
    normalized["available_gate"] = normalized_gate
    return normalized


def _derive_available_from_gc_conditions(gate: dict[str, Any]) -> bool | None:
    conditions = gate.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        return None
    gc_values: dict[str, bool] = {}
    for item in conditions:
        if not isinstance(item, dict):
            continue
        name = str(item.get("condition", "")).upper()
        for gc in ("GC1", "GC2", "GC3"):
            if gc in name and isinstance(item.get("satisfied"), bool):
                gc_values[gc] = bool(item.get("satisfied"))
    if not gc_values:
        return None
    return all(gc_values.get(gc) is True for gc in ("GC1", "GC2", "GC3"))


def derive_badge_predicates_without_normalizing(packet: dict[str, Any]) -> dict[str, Any]:
    packet = packet if isinstance(packet, dict) else {}
    scores = _extract_scores(packet)
    warnings: list[str] = []

    available, available_failures = _derive_available(packet)
    functional_failures: list[str] = []
    reusable_failures: list[str] = []

    if not available:
        functional_failures.extend(available_failures or ["available gate does not hold"])
    for dim in ("D2", "D3", "D4"):
        if _score_lt(scores.get(dim), "WA"):
            functional_failures.append(f"{dim} is below WA")

    tier = _lower(_nested(packet.get("evidence_tier"), "tier"))
    if tier and tier not in {"e2", "e3"}:
        functional_failures.append(f"evidence tier {tier.upper()} is below executable evidence")
    elif not tier:
        warnings.append("evidence tier is missing or unclear")

    highest = packet.get("highest_intervention", {})
    if isinstance(highest, dict):
        if highest.get("depends_on_i4") is True:
            functional_failures.append("successful path depends on I4 intervention")
        if _intervention_value(str(highest.get("level", ""))) >= _intervention_value("I4"):
            functional_failures.append("highest intervention is I4")

    functional_failures.extend(_functional_evidence_blockers(packet, warnings))
    functional = not functional_failures

    if not functional:
        reusable_failures.append("Functional gate does not hold")
    for dim in ("D1", "D5"):
        if _score_lt(scores.get(dim), "WA"):
            reusable_failures.append(f"{dim} is below WA")
    reusable_failures.extend(_reusable_evidence_blockers(packet, warnings))
    reusable = not reusable_failures

    badge = _expected_badge(available, functional, reusable)
    claimed = {
        "available": _badge_bool(packet.get("badge_recommendation"), "available"),
        "functional": _decision_holds(packet.get("functional_decision")) or _badge_bool(packet.get("badge_recommendation"), "functional"),
        "reusable": _decision_holds(packet.get("reusable_decision")) or _badge_bool(packet.get("badge_recommendation"), "reusable"),
        "badge": _badge_text(packet.get("badge_recommendation")),
    }

    contradictions: list[str] = []
    if claimed["available"] and not available:
        contradictions.extend(available_failures or ["Available is claimed but the available gate does not hold"])
    if claimed["functional"] and not functional:
        contradictions.extend(f"Functional claimed despite: {item}" for item in functional_failures)
    if claimed["reusable"] and not reusable:
        contradictions.extend(f"Reusable claimed despite: {item}" for item in reusable_failures)
    if claimed["badge"] and claimed["badge"] != badge and not contradictions:
        contradictions.append(f"badge recommendation `{claimed['badge']}` does not match derived badge `{badge}`")

    return {
        "schema": "rubric_badge_predicate_result_v1",
        "available": available,
        "functional": functional,
        "reusable": reusable,
        "derived_badge": badge,
        "claimed": claimed,
        "failed_predicates": {
            "available": available_failures,
            "functional": functional_failures,
            "reusable": reusable_failures,
        },
        "warnings": warnings,
        "contradictions": _dedupe(contradictions),
    }


def result_package_exception(packet: dict[str, Any]) -> bool:
    return _result_package_exception(packet if isinstance(packet, dict) else {})


def dataset_package_primary_surface_exception(packet: dict[str, Any]) -> bool:
    return _dataset_package_primary_surface_exception(packet if isinstance(packet, dict) else {})


def predicate_consistency_findings(packet: dict[str, Any]) -> list[dict[str, str]]:
    result = derive_badge_predicates(packet)
    findings: list[dict[str, str]] = []
    contradictions = result.get("contradictions", [])
    if isinstance(contradictions, list) and contradictions:
        findings.append(
            {
                "severity": "warning",
                "code": "rubric_predicate_badge_contradiction",
                "message": (
                    "Final badge decisions contradict the structured rubric evidence predicates. "
                    + " | ".join(str(item) for item in contradictions[:8])
                ),
            }
        )
    warnings = result.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        findings.append(
            {
                "severity": "warning",
                "code": "rubric_predicate_missing_or_uncertain_fields",
                "message": (
                    "Rubric predicate derivation found missing or uncertain structured evidence fields: "
                    + " | ".join(str(item) for item in warnings[:8])
                ),
            }
        )
    return findings


def _derive_available(packet: dict[str, Any]) -> tuple[bool, list[str]]:
    gate = packet.get("available_gate")
    if not isinstance(gate, dict):
        return False, ["available gate is missing"]
    if isinstance(gate.get("holds"), bool):
        holds = bool(gate.get("holds"))
        return holds, [] if holds else ["available_gate.holds is false"]
    if isinstance(gate.get("available"), bool):
        holds = bool(gate.get("available"))
        return holds, [] if holds else ["available_gate.available is false"]
    conditions = gate.get("conditions")
    if isinstance(conditions, list) and conditions:
        failed = [
            str(item.get("condition", "available condition")).strip() or "available condition"
            for item in conditions
            if isinstance(item, dict) and item.get("satisfied") is False
        ]
        return not failed, failed
    return False, ["available gate has no deterministic pass field"]


def _functional_evidence_blockers(packet: dict[str, Any], warnings: list[str]) -> list[str]:
    blockers: list[str] = []
    e2 = packet.get("e2_sufficiency_check", {})
    adjudication = packet.get("rubric_evidence_adjudication", {})
    functional_sufficiency = adjudication.get("functional_sufficiency", {}) if isinstance(adjudication, dict) else {}
    resource = packet.get("resource_boundary_assessment", {})

    if isinstance(functional_sufficiency, dict) and functional_sufficiency.get("sufficient") is False:
        blockers.append("functional sufficiency is explicitly false")

    if isinstance(e2, dict):
        result_package_exception = _result_package_exception(packet) or _dataset_package_primary_surface_exception(packet)
        if e2.get("generic_dependency_or_example") is True:
            blockers.append("selected D3 evidence is a generic dependency or example")
        if e2.get("component_only") is True and not (result_package_exception or _functional_component_exception(packet)):
            blockers.append("selected D3 evidence is component-only")
        if _lower(e2.get("artifact_specific_logic_exercised")) == "no" and not result_package_exception:
            blockers.append("selected D3 evidence does not exercise artifact-specific logic")
        if _lower(e2.get("paper_facing_output")) == "no":
            blockers.append("selected D3 evidence has no paper-facing output")
        workflow = _lower(e2.get("workflow_kind"))
        provenance = _lower(e2.get("output_provenance"))
        if workflow in {"dry_run_or_plan", "no_runnable_workflow"}:
            blockers.append(f"selected D3 workflow is {workflow}")
        if provenance in {"dry_run_no_artifact_output", "no_valid_output"}:
            blockers.append(f"selected D3 output provenance is {provenance}")
        if (
            workflow in {"dataset_loading_or_aggregation", "result_validation_only", "post_processing_only"}
            and provenance in {"loaded_from_deposited_files", "validated_precomputed_output", "derived_from_existing_results"}
            and not result_package_exception
        ):
            warnings.append("selected D3 evidence is result/precomputed validation; final scoring must justify rubric sufficiency")
        if _weak_bounded_route(packet) and not result_package_exception:
            blockers.append("selected D3 evidence is a weak bounded smoke/toy route while the primary workflow remains unvalidated")
    else:
        warnings.append("e2_sufficiency_check is missing or not structured")

    if isinstance(resource, dict):
        effect = _lower(resource.get("badge_effect"))
        if effect == "blocks_functional":
            blockers.append("resource boundary explicitly blocks Functional")
        if (
            _lower(resource.get("position")) == "before_artifact_logic"
            and _lower(resource.get("artifact_logic_reached")) in {"no", "unknown", ""}
            and _lower(resource.get("alternative_evidence_exists")) != "yes"
        ):
            blockers.append("resource boundary occurs before artifact logic with no alternative evidence")
    else:
        warnings.append("resource_boundary_assessment is missing or not structured")
    return _dedupe(blockers)


def _reusable_evidence_blockers(packet: dict[str, Any], warnings: list[str]) -> list[str]:
    blockers: list[str] = []
    d5 = packet.get("d5_transfer_sufficiency_check", {})
    adjudication = packet.get("rubric_evidence_adjudication", {})
    reusable_sufficiency = adjudication.get("reusable_sufficiency", {}) if isinstance(adjudication, dict) else {}
    strongest = adjudication.get("strongest_reusable_evidence", {}) if isinstance(adjudication, dict) else {}

    if isinstance(reusable_sufficiency, dict) and reusable_sufficiency.get("sufficient") is False:
        blockers.append("reusable sufficiency is explicitly false")

    if isinstance(d5, dict):
        sufficiency = _artifact_level_sufficiency_value(d5.get("artifact_level_sufficiency"))
        if sufficiency in {"supporting_only", "insufficient"} and not _reusable_surface_exception(packet):
            blockers.append(f"D5 transfer is {sufficiency}")
        novelty = _lower(d5.get("transfer_novelty"))
        if novelty in {"no_transfer", "same_as_d3_variant"}:
            blockers.append(f"D5 transfer novelty is {novelty}")
        relationship = _lower(d5.get("relationship_to_d3"))
        if relationship == "same_workflow_with_new_input" and not _structured_dataset_or_result_reuse(packet):
            blockers.append("D5 transfer is the same workflow with a new input")
        if relationship == "post_processing_of_d3_outputs" and not _structured_dataset_or_result_reuse(packet):
            blockers.append("D5 transfer is post-processing of D3 outputs")
        if _same_workflow_variant_check_blocks_reuse(d5) and not _structured_dataset_or_result_reuse(packet):
            blockers.append("D5 same_workflow_variant_check indicates only parameter/config/path/same-task input change")
        surface = _lower(d5.get("validated_reuse_surface"))
        if surface in {"component_or_helper_surface", "unclear_surface", "no_validated_surface"} and not _reusable_surface_exception(packet):
            blockers.append(f"D5 validated reuse surface is {surface}")
        failure = _lower(d5.get("transfer_failure_interpretation"))
        core_result = _lower(d5.get("core_reuse_validation_result"))
        if core_result in {"fail_artifact_interface", "no_core_reuse_surface", "not_attempted_prerequisite_missing"}:
            blockers.append(f"D5 core reuse validation result is {core_result}")
        if failure in {"fail_artifact_interface", "artifact_behavior_failure"}:
            blockers.append(f"D5 transfer failure interpretation is {failure}")
        elif failure in {"resource_gated", "resource_or_api_gated", "fail_reviewer_harness", "input_contract_mismatch", "route_or_config_choice", "evaluator_wrapper_error"}:
            warnings.append(f"D5 transfer was inconclusive due to {failure}; do not treat it as artifact interface failure")
        elif failure not in {"", "not_applicable_success", "unknown"}:
            blockers.append(f"D5 transfer failure interpretation is {failure}")
        if _weak_mock_or_demo_transfer(packet):
            blockers.append("D5 transfer is a weak mock/demo same-workflow variant")
    else:
        warnings.append("d5_transfer_sufficiency_check is missing or not structured")

    if isinstance(strongest, dict):
        evidence_type = _lower(strongest.get("evidence_type"))
        transfer_strength = _lower(strongest.get("transfer_strength"))
        if evidence_type in {"component_or_helper_transfer", "same_workflow_variant", "failed_or_no_transfer"} and not _reusable_surface_exception(packet):
            blockers.append(f"strongest reusable evidence is {evidence_type}")
        if transfer_strength in {"component_or_helper", "same_workflow_variant", "failed_or_none"} and not _reusable_surface_exception(packet):
            blockers.append(f"reusable transfer strength is {transfer_strength}")
    else:
        warnings.append("strongest_reusable_evidence is missing or not structured")
    return _dedupe(blockers)


def _functional_component_exception(packet: dict[str, Any]) -> bool:
    e2 = packet.get("e2_sufficiency_check", {})
    adjudication = packet.get("rubric_evidence_adjudication", {})
    if isinstance(e2, dict) and e2.get("api_resource_gated_result_package_exception") is True:
        return True
    if isinstance(e2, dict):
        if e2.get("generic_dependency_or_example") is True:
            return False
        if _lower(e2.get("artifact_specific_logic_exercised")) in {"yes", "limited"} and _lower(
            e2.get("paper_facing_output")
        ) in {"yes", "limited"}:
            return True
    if not isinstance(adjudication, dict):
        return False
    alignment = adjudication.get("final_alignment_check", {})
    strongest = adjudication.get("strongest_functional_evidence", {})
    if not isinstance(alignment, dict) or _lower(alignment.get("stage_final_consistency")) != "justified_override":
        return False
    if not isinstance(strongest, dict):
        return False
    return _lower(strongest.get("representative_strength")) in {
        "artifact_level",
        "bounded_artifact_level",
        "paper_facing_result_package",
    }


def _result_package_exception(packet: dict[str, Any]) -> bool:
    e2 = packet.get("e2_sufficiency_check", {})
    if not isinstance(e2, dict):
        return False
    if e2.get("api_resource_gated_result_package_exception") is not True:
        return False
    if e2.get("generic_dependency_or_example") is True:
        return False
    workflow = _lower(e2.get("workflow_kind"))
    provenance = _lower(e2.get("output_provenance"))
    if workflow not in {"dataset_loading_or_aggregation", "result_validation_only", "post_processing_only"}:
        return False
    if provenance not in {"loaded_from_deposited_files", "validated_precomputed_output", "derived_from_existing_results"}:
        return False
    if _lower(e2.get("paper_facing_output")) not in {"yes", "limited"}:
        return False
    declared = _lower(e2.get("declared_artifact_type"))
    if declared not in {
        "dataset_package",
        "data_package",
        "result_package",
        "notebook_package",
        "spreadsheet_package",
        "replication_package",
    }:
        return False
    if e2.get("result_package_declared_by_artifact") is not True:
        return False
    active_markers = " ".join(
        [
            _lower(e2.get("upstream_tool_surface_unattempted_reason")),
            _lower(e2.get("sufficiency_reason")),
            _lower(e2.get("score_or_badge_effect")),
        ]
    )
    if _mentions_active_unvalidated_surface(active_markers):
        return False
    return _lower(e2.get("resource_boundary_position")) == "after_valid_bounded_output"


def _dataset_package_primary_surface_exception(packet: dict[str, Any]) -> bool:
    e2 = packet.get("e2_sufficiency_check", {})
    if not isinstance(e2, dict):
        return False
    workflow = _lower(e2.get("workflow_kind"))
    provenance = _lower(e2.get("output_provenance"))
    if workflow not in DATASET_PACKAGE_WORKFLOWS or provenance not in DEPOSITED_OUTPUT_PROVENANCE:
        return False
    declared = _lower(e2.get("declared_artifact_type"))
    if declared not in DATASET_PACKAGE_TYPES:
        return False
    if e2.get("generic_dependency_or_example") is True or e2.get("component_only") is True:
        return False
    if _lower(e2.get("paper_facing_output")) not in {"yes", "limited"}:
        return False
    if _lower(e2.get("artifact_specific_logic_exercised")) not in {"yes", "limited"}:
        return False
    if _lower(e2.get("resource_boundary_position")) in {"before_artifact_logic", "unknown"}:
        return False
    active_markers = " ".join(
        [
            _lower(e2.get("upstream_tool_surface_unattempted_reason")),
            _lower(e2.get("representative_workflow_assessment")),
            _lower(e2.get("functional_representativeness_blocker")),
            _lower(e2.get("sufficiency_reason")),
            _lower(e2.get("score_or_badge_effect")),
        ]
    )
    if _mentions_active_unvalidated_surface(active_markers) and not _text_negates_active_surface(active_markers):
        return False
    return _structured_or_text_declares_dataset_primary_surface(packet)


def _reusable_surface_exception(packet: dict[str, Any]) -> bool:
    adjudication = packet.get("rubric_evidence_adjudication", {})
    if not isinstance(adjudication, dict):
        return False
    sufficiency = adjudication.get("reusable_sufficiency", {})
    strongest = adjudication.get("strongest_reusable_evidence", {})
    alignment = adjudication.get("final_alignment_check", {})
    if not isinstance(sufficiency, dict) or sufficiency.get("sufficient") is not True:
        return False
    if not isinstance(strongest, dict):
        return False
    strength = _lower(strongest.get("transfer_strength"))
    evidence_type = _lower(strongest.get("evidence_type"))
    text = "\n".join(
        _json_text(item).lower()
        for item in (
            packet.get("d5_transfer_sufficiency_check", {}),
            packet.get("rubric_evidence_adjudication", {}),
            packet.get("e2_sufficiency_check", {}),
            packet.get("resource_boundary_assessment", {}),
        )
    )
    if _weak_mock_or_demo_transfer(packet):
        return False
    if _structured_dataset_or_result_reuse(packet):
        return True
    if _mentions_active_unvalidated_surface(text) and not _text_negates_active_surface(text):
        return _reusable_alignment_proves_intended_surface(alignment, adjudication)
    if strength in {"artifact_surface", "documented_cli_or_api"}:
        return True
    if evidence_type in {"artifact_surface_transfer", "documented_cli_or_api_transfer"}:
        return True
    if strength == "dataset_or_result_package" or evidence_type == "dataset_or_result_package_reanalysis":
        return _text_declares_dataset_primary_surface(packet)
    if isinstance(alignment, dict) and _lower(alignment.get("stage_final_consistency")) == "justified_override":
        return _reusable_alignment_proves_intended_surface(alignment, adjudication)
    return False


def _structured_dataset_or_result_reuse(packet: dict[str, Any]) -> bool:
    d5 = packet.get("d5_transfer_sufficiency_check", {})
    adjudication = packet.get("rubric_evidence_adjudication", {})
    if not isinstance(d5, dict) or not isinstance(adjudication, dict):
        return False
    if _artifact_level_sufficiency_value(d5.get("artifact_level_sufficiency")) != "sufficient":
        return False
    sufficiency = adjudication.get("reusable_sufficiency", {})
    strongest = adjudication.get("strongest_reusable_evidence", {})
    if not isinstance(sufficiency, dict) or sufficiency.get("sufficient") is not True or not isinstance(strongest, dict):
        return False
    target = _lower(d5.get("reuse_target_level"))
    evidence_type = _lower(strongest.get("evidence_type"))
    strength = _lower(strongest.get("transfer_strength"))
    if target not in {"dataset_package_level", "result_package_level"}:
        return False
    if evidence_type != "dataset_or_result_package_reanalysis" and strength != "dataset_or_result_package":
        return False
    return _structured_or_text_declares_dataset_primary_surface(packet)


def _weak_mock_or_demo_transfer(packet: dict[str, Any]) -> bool:
    d5 = packet.get("d5_transfer_sufficiency_check", {})
    adjudication = packet.get("rubric_evidence_adjudication", {})
    strongest = adjudication.get("strongest_reusable_evidence", {}) if isinstance(adjudication, dict) else {}
    text = "\n".join(
        _json_text(item).lower()
        for item in (
            d5,
            adjudication,
            packet.get("route_arbitration_check", {}),
        )
    )
    relationship = _lower(d5.get("relationship_to_d3") if isinstance(d5, dict) else "")
    target = _lower(d5.get("reuse_target_level") if isinstance(d5, dict) else "")
    transfer_strength = _lower(strongest.get("transfer_strength") if isinstance(strongest, dict) else "")
    weak_relation = relationship in {"same_workflow_with_new_input", "post_processing_of_d3_outputs"}
    weak_target = target in {"packaged_example_level", "utility_or_helper_level"} or transfer_strength in {
        "same_workflow_variant",
        "component_or_helper",
    }
    if (
        relationship == "same_workflow_with_new_input"
        and target in {"documented_cli_or_api", "artifact_level", "pipeline_level"}
        and transfer_strength in {"artifact_surface", "documented_cli_or_api", "pipeline", "pipeline_level"}
    ):
        return False
    weak_words = any(
        marker in text
        for marker in (
            "mock",
            "demo",
            "smoke",
            "helper",
            "toy",
            "example-only",
            "example only",
            "trivial c program",
            "synthetic c file",
            "single-file",
            "single file",
        )
    )
    tiny_bounded = any(
        marker in text
        for marker in (
            "tiny",
            "small 80-byte",
            "80-byte",
            "minimal",
            "single-contract test",
            "single contract test",
            "--context_depth 1",
            "context_depth 1",
            "--disable_inline",
            "disable_inline",
        )
    )
    full_surface_left = any(
        marker in text
        for marker in (
            "full dataset",
            "full experiment",
            "full experiments",
            "full pipeline",
            "cross-tool comparison",
            "main table",
            "main figure",
            "resource-gated",
            "resource gated",
        )
    )
    return (weak_relation and weak_words) or (weak_target and weak_words) or (tiny_bounded and full_surface_left)


def _same_workflow_variant_check_blocks_reuse(d5: dict[str, Any]) -> bool:
    if not isinstance(d5, dict):
        return False
    check = d5.get("same_workflow_variant_check")
    if not isinstance(check, dict):
        return False
    only_tweak = any(
        check.get(key) is True
        for key in (
            "only_parameter_or_config_change",
            "only_path_or_relocation_change",
            "only_same_task_new_input",
        )
    )
    if not only_tweak:
        return False
    context = check.get("reviewer_owned_new_context")
    if isinstance(context, bool):
        return not context
    return _lower(context) in {"", "no", "false", "none", "unknown"}


def _weak_bounded_route(packet: dict[str, Any]) -> bool:
    e2 = packet.get("e2_sufficiency_check", {})
    resource = packet.get("resource_boundary_assessment", {})
    text = "\n".join(_json_text(item).lower() for item in (e2, resource, packet.get("route_arbitration_check", {})))
    weak = (
        "toy input",
        "toy example",
        "trivial c program",
        "trivial program",
        "synthetic c file",
        "single-file",
        "single file",
        "minimal input",
        "smoke",
        "dry run",
    )
    primary_missing = (
        "primary workflow",
        "primary route",
        "primary documented workflow",
        "missing sample",
        "missing input",
        "full pipeline",
        "main evaluation",
        "main table",
        "main figure",
    )
    if not any(marker in text for marker in weak):
        return False
    return any(marker in text for marker in primary_missing)


def _reusable_alignment_proves_intended_surface(alignment: Any, adjudication: dict[str, Any]) -> bool:
    if not isinstance(alignment, dict) or _lower(alignment.get("stage_final_consistency")) != "justified_override":
        return False
    text = _json_text(adjudication).lower()
    return all(
        any(marker in text for marker in group)
        for group in (
            ("intended reusable surface", "public reusable surface", "declared reusable surface"),
            ("selected transfer surface", "validated surface", "transfer validates"),
            ("validates_intended_surface", "validates the intended", "validates the public"),
        )
    )


def _mentions_active_unvalidated_surface(text: str) -> bool:
    active_surface_markers = (
        "tool",
        "cli",
        "model",
        "pipeline",
        "agent",
        "compiler",
        "analyzer",
        "fuzzer",
        "main.py",
        "full experiment",
        "primary workflow",
    )
    api_surface_markers = (
        "artifact api",
        "public api",
        "documented api",
        "api endpoint",
        "api route",
        "api surface",
        "api workflow",
    )
    unvalidated_markers = (
        "not attempted",
        "not run",
        "not executed",
        "unattempted",
        "unvalidated",
        "blocked",
        "gated",
        "requires api",
        "api key",
        "paid api",
        "resource",
    )
    has_active_surface = any(marker in text for marker in active_surface_markers) or any(
        marker in text for marker in api_surface_markers
    )
    return has_active_surface and any(
        marker in text for marker in unvalidated_markers
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


def _text_declares_dataset_primary_surface(packet: dict[str, Any]) -> bool:
    e2 = packet.get("e2_sufficiency_check", {})
    adjudication = packet.get("rubric_evidence_adjudication", {})
    d5 = packet.get("d5_transfer_sufficiency_check", {})
    text = "\n".join(_json_text(item).lower() for item in (e2, adjudication, d5))
    direct_markers = (
        "artifact is a dataset",
        "artifact is a data package",
        "artifact is a dataset/result package",
        "artifact is a data/result package",
        "artifact is a dataset/replication package",
        "dataset deposit",
        "dataset package",
        "dataset/result package",
        "data/result package",
        "data file is the artifact",
        "data files are the artifact",
        "deposited csv data is the main review surface",
        "deposited data files are the intended",
        "deposited public files are the intended",
        "dataset files are the main review surface",
        "dataset route is the documented primary review surface",
        "dataset package is the primary artifact",
        "dataset is the primary artifact",
        "dataset is the main artifact",
        "data package is the primary artifact",
        "result package is the primary artifact",
        "deposited result data",
        "no separate active tool",
        "no active tool/pipeline",
        "no broader tool/pipeline",
        "no higher-level tool",
    )
    return any(marker in text for marker in direct_markers)


def _structured_or_text_declares_dataset_primary_surface(packet: dict[str, Any]) -> bool:
    if _text_declares_dataset_primary_surface(packet):
        return True
    e2 = packet.get("e2_sufficiency_check", {})
    if not isinstance(e2, dict):
        return False
    declared = _lower(e2.get("declared_artifact_type"))
    if declared not in DATASET_PACKAGE_TYPES:
        return False
    adjudication = packet.get("rubric_evidence_adjudication", {})
    if not isinstance(adjudication, dict):
        return True
    strongest = adjudication.get("strongest_functional_evidence", {})
    if not isinstance(strongest, dict):
        return True
    evidence_type = _lower(strongest.get("evidence_type"))
    return evidence_type in {
        "",
        "dataset_package_validation",
        "result_package_validation",
        "bounded_representative_workflow",
    }


def _extract_scores(payload: dict[str, Any]) -> dict[str, str]:
    scores: dict[str, str] = {}
    raw = payload.get("dimension_scores", {}) if isinstance(payload, dict) else {}
    if isinstance(raw, dict):
        for dim in ("D1", "D2", "D3", "D4", "D5"):
            value = raw.get(dim)
            scores[dim] = str(value.get("score", "") if isinstance(value, dict) else value or "").strip()
    return scores


def _score_lt(value: str | None, threshold: str) -> bool:
    if value not in SCORE_ORDER:
        return False
    return SCORE_ORDER[value] < SCORE_ORDER[threshold]


def _intervention_value(value: str) -> int:
    return INTERVENTION_ORDER.get(str(value or "").strip(), -1)


def _artifact_level_sufficiency_value(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if "supporting_only" in text:
        return "supporting_only"
    if text.startswith("insufficient") or "insufficient" in text:
        return "insufficient"
    if text.startswith("sufficient") or "sufficient" in text:
        return "sufficient"
    return ""


def _normalize_reuse_target_level(value: Any) -> str:
    text = _enum_text(value)
    aliases = {
        "cli_tool_level": "documented_cli_or_api",
        "tool_cli_level": "documented_cli_or_api",
        "documented_cli_or_api_level": "documented_cli_or_api",
        "model_pipeline_level": "pipeline_level",
        "tool_pipeline_level": "pipeline_level",
        "package_level": "artifact_level",
        "dataset_level": "dataset_package_level",
        "data_package_level": "dataset_package_level",
        "component_utility_level": "utility_or_helper_level",
        "plugin_surface_level": "documented_cli_or_api",
        "notebook_level": "notebook_package_level",
        "spreadsheet_level": "spreadsheet_package_level",
        "analysis_script_level": "replication_analysis_level",
        "replication_script_level": "replication_analysis_level",
        "not_applicable_prerequisite_missing": "unknown",
        "none": "unknown",
    }
    valid = {
        "artifact_level",
        "pipeline_level",
        "documented_cli_or_api",
        "module_or_function_level",
        "utility_or_helper_level",
        "component_level",
        "dataset_package_level",
        "result_package_level",
        "notebook_package_level",
        "spreadsheet_package_level",
        "replication_analysis_level",
        "packaged_example_level",
        "unknown",
    }
    return aliases.get(text, text if text in valid else "unknown")


def _normalize_transfer_novelty(value: Any) -> str:
    text = _enum_text(value)
    valid = {
        "new_input",
        "new_configuration",
        "new_project",
        "new_scenario",
        "independent_api_or_cli_call",
        "new_summary_over_existing_files",
        "same_as_d3_variant",
        "no_transfer",
        "unknown",
    }
    if text in {"new_config", "new_configuration_or_parameter"}:
        return "new_configuration"
    if text in {"independent_cli_call", "independent_api_call"}:
        return "independent_api_or_cli_call"
    return text if text in valid else "unknown"


def _normalize_relationship_to_d3(value: Any) -> str:
    text = _enum_text(value)
    aliases = {
        "independent_transfer": "independent_of_d3",
        "independent_transfer_route": "independent_of_d3",
        "independent_route": "independent_of_d3",
        "independent_api_or_cli_call": "independent_of_d3",
        "same_workflow_variant": "same_workflow_with_new_input",
        "same_workflow_new_input": "same_workflow_with_new_input",
        "new_input_only": "same_workflow_with_new_input",
        "supporting_component": "post_processing_of_d3_outputs",
        "supporting_only": "post_processing_of_d3_outputs",
        "component_or_helper": "post_processing_of_d3_outputs",
    }
    valid = {
        "independent_of_d3",
        "same_workflow_with_new_input",
        "post_processing_of_d3_outputs",
        "data_reanalysis_of_deposited_files",
        "unclear",
        "unknown",
    }
    if text in aliases:
        return aliases[text]
    return text if text in valid else "unknown"


def _normalize_validated_reuse_surface(value: Any) -> str:
    text = _enum_text(value)
    aliases = {
        "public_surface": "intended_public_surface",
        "intended_surface": "intended_public_surface",
        "documented_surface": "documented_subsurface",
        "helper_surface": "component_or_helper_surface",
        "component_surface": "component_or_helper_surface",
        "utility_surface": "component_or_helper_surface",
        "dataset_surface": "dataset_or_result_surface",
        "result_surface": "dataset_or_result_surface",
        "none": "no_validated_surface",
    }
    valid = {
        "intended_public_surface",
        "documented_subsurface",
        "component_or_helper_surface",
        "dataset_or_result_surface",
        "unclear_surface",
        "no_validated_surface",
        "unknown",
    }
    return aliases.get(text, text if text in valid else "unknown")


def _normalize_transfer_failure(value: Any) -> str:
    text = _enum_text(value)
    aliases = {
        "api_gated": "resource_or_api_gated",
        "resource_gated": "resource_gated",
        "fail_harness": "fail_reviewer_harness",
        "reviewer_harness_failure": "fail_reviewer_harness",
        "harness_failure": "fail_reviewer_harness",
        "fail_artifact": "fail_artifact_interface",
        "artifact_interface_failure": "fail_artifact_interface",
        "successful_core_reuse": "not_applicable_success",
        "success_core_reuse": "not_applicable_success",
    }
    valid = {
        "route_or_config_choice",
        "input_contract_mismatch",
        "evaluator_wrapper_error",
        "fail_reviewer_harness",
        "missing_dependency",
        "resource_gated",
        "resource_or_api_gated",
        "fail_artifact_interface",
        "artifact_behavior_failure",
        "not_attempted_prerequisite_missing",
        "not_applicable_success",
        "no_core_reuse_surface",
        "unknown",
    }
    if text in aliases:
        return aliases[text]
    if text in {"not_attempted", "not_attempted_functional_missing"}:
        return "not_attempted_prerequisite_missing"
    return text if text in valid else "unknown"


def _normalize_core_reuse_result(value: Any) -> str:
    text = _enum_text(value)
    aliases = {
        "success_core_reuse": "successful_core_reuse",
        "success": "successful_core_reuse",
        "successful_transfer": "successful_core_reuse",
        "resource_or_api_gated": "resource_gated",
        "api_gated": "resource_gated",
        "hardware_gated": "resource_gated",
        "docker_gated": "resource_gated",
        "time_gated": "resource_gated",
        "harness_failure": "fail_reviewer_harness",
        "reviewer_harness_failure": "fail_reviewer_harness",
        "artifact_interface_failure": "fail_artifact_interface",
        "artifact_behavior_failure": "fail_artifact_interface",
        "not_attempted": "not_attempted_prerequisite_missing",
        "none": "unknown",
    }
    valid = {
        "successful_core_reuse",
        "resource_gated",
        "fail_artifact_interface",
        "fail_reviewer_harness",
        "not_attempted_prerequisite_missing",
        "no_core_reuse_surface",
        "unknown",
    }
    if text in aliases:
        return aliases[text]
    return text if text in valid else "unknown"


def _normalize_decisiveness(value: Any, *, d5: bool) -> str:
    text = _enum_text(value)
    valid = {
        "decisive_failure",
        "not_decisive_alternative_exists",
        "supporting_only",
        "unclear",
    }
    valid.add("successful_transfer" if d5 else "successful_representative")
    if text in {"failed_prerequisite", "not_applicable"}:
        return "unclear"
    return text if text in valid else "unclear"


def _normalize_boundary_type(value: Any) -> str:
    text = _enum_text(value)
    valid = {
        "api_gated",
        "hardware_gated",
        "data_gated",
        "network_gated",
        "toolchain_gated",
        "time_gated",
        "host_policy_gated",
        "resource_gated",
        "platform_gated",
        "none",
        "unknown",
    }
    # Schema compatibility prefers known legacy forms, but evidence engine can
    # consume resource/platform as generic boundary labels.
    return text if text in valid else "unknown"


def _normalize_boundary_position(value: Any) -> str:
    text = _enum_text(value)
    valid = {
        "before_artifact_logic",
        "after_bounded_artifact_logic",
        "after_valid_bounded_output",
        "not_resource_gated",
        "unknown",
    }
    return text if text in valid else "unknown"


def _normalize_badge_effect(value: Any) -> str:
    text = _enum_text(value)
    if "blocks_functional" in text or "prevents_functional" in text or "blocks_d3" in text:
        return "blocks_functional"
    if "blocks_reusable" in text:
        return "blocks_reusable"
    if "supports_functional" in text:
        return "supports_functional_with_limitations"
    if "limitation" in text:
        return "limitation_only"
    if text in {"not_applicable", "none", ""}:
        return "not_applicable"
    return text or "unknown"


def _normalize_workflow_kind(value: Any) -> str:
    text = _enum_text(value)
    valid = {
        "full_reproduction",
        "near_full_reproduction",
        "reduced_representative",
        "documented_demo",
        "artifact_evaluation_path",
        "post_processing_only",
        "result_validation_only",
        "dataset_loading_or_aggregation",
        "dry_run_or_plan",
        "component_probe",
        "no_runnable_workflow",
        "unknown",
    }
    return text if text in valid else "unknown"


def _normalize_output_provenance(value: Any) -> str:
    text = _enum_text(value)
    valid = {
        "newly_generated_by_reviewer",
        "loaded_from_deposited_files",
        "validated_precomputed_output",
        "derived_from_existing_results",
        "dry_run_no_artifact_output",
        "no_valid_output",
        "unknown",
    }
    return text if text in valid else "unknown"


def _normalize_upstream_generation(value: Any) -> str:
    text = _enum_text(value)
    valid = {
        "yes",
        "no_resource_or_time_gated",
        "no_not_documented",
        "no_not_applicable_dataset_package",
        "no_unattempted",
        "unknown",
    }
    if text in {"not_applicable_dataset_package", "no_not_applicable_data_package"}:
        return "no_not_applicable_dataset_package"
    return text if text in valid else "unknown"


def _normalize_yes_limited_no(value: Any) -> str:
    text = _enum_text(value)
    if text in {"true", "yes"}:
        return "yes"
    if text in {"false", "no"}:
        return "no"
    if text in {"limited", "partial"}:
        return "limited"
    return "unknown" if text not in {"yes", "limited", "no"} else text


def _normalize_yes_no_unknown(value: Any) -> str:
    text = _enum_text(value)
    if text in {"true", "yes"}:
        return "yes"
    if text in {"false", "no"}:
        return "no"
    return text if text in {"yes", "no", "unknown"} else "unknown"


def _enum_text(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _decision_holds(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("holds"))
    return str(value or "").strip().lower() in {"true", "yes", "holds", "held", "satisfied"}


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


def _expected_badge(available: bool, functional: bool, reusable: bool) -> str:
    if not available:
        return "no badge"
    if reusable:
        return "available + functional + reusable"
    if functional:
        return "available + functional"
    return "available"


def _nested(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value or "")


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
