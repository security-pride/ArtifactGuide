from __future__ import annotations

from pathlib import Path
from typing import Any

from .evidence_contract import build_evidence_contract, write_evidence_contract

SCORE_ORDER = {"R": 0, "WR": 1, "WA": 2, "A": 3}


def run_badge_guardrails(run_root: Path, final_packet: dict[str, Any]) -> dict[str, Any]:
    contract = write_evidence_contract(run_root, final_packet)
    available_claimed = _available_claimed(final_packet)
    functional_claimed = _functional_claimed(final_packet)
    reusable_claimed = _reusable_claimed(final_packet)
    scores = _scores(final_packet)

    tests: list[dict[str, Any]] = []

    available = contract["available"]
    if available_claimed:
        tests.append(
            _test(
                "available_gc2_not_static_report_only",
                not available["gc2_static_report_only"],
                "Available GC2 is based on a static/report-table inspection path rather than a reviewer-usable evaluation entry.",
            )
        )

    functional = contract["functional"]
    artifact = contract["artifact"]
    if functional_claimed:
        tests.append(
            _test(
                "functional_scores_meet_threshold",
                _score_gte(scores.get("D2"), "WA") and _score_gte(scores.get("D3"), "WA") and _score_gte(scores.get("D4"), "WA"),
                "Functional claimed while one of D2/D3/D4 is below WA.",
            )
        )
        tests.append(
            _test(
                "functional_not_generic_or_dependency_only",
                not functional["generic_dependency_or_example"],
                "Functional claimed from generic dependency/example/setup-only evidence.",
            )
        )
        tests.append(
            _test(
                "functional_component_surface_has_equivalence",
                not functional["component_only"] or _functional_component_equivalent(contract),
                "Functional claimed from component-only evidence without a stable artifact-level or bounded-equivalent surface.",
            )
        )
        tests.append(
            _test(
                "functional_smoke_not_substitute_for_primary_pipeline",
                functional.get("result_validation_functional_surface")
                or _bounded_functional_surface(functional)
                or not functional["single_file_or_smoke_only"],
                "Functional claimed from a single-file/smoke/dry-run path while the primary workflow or full pipeline remains unvalidated.",
            )
        )
        tests.append(
            _test(
                "functional_weak_bounded_route_not_primary_substitute",
                not functional.get("weak_bounded_route")
                or functional.get("result_validation_functional_surface")
                or functional.get("paper_facing_output") == "yes",
                "Functional claimed from a toy/smoke/minimal bounded route while the documented primary workflow remains missing or unvalidated.",
            )
        )

    reusable = contract["reusable"]
    if reusable_claimed:
        tests.append(
            _test(
                "reusable_functional_prerequisite",
                functional_claimed,
                "Reusable claimed while Functional does not hold.",
            )
        )
        tests.append(
            _test(
                "reusable_scores_meet_threshold",
                _score_gte(scores.get("D1"), "WA") and _score_gte(scores.get("D5"), "WA"),
                "Reusable claimed while D1 or D5 is below WA.",
            )
        )
        tests.append(
            _test(
                "reusable_has_real_transfer_novelty",
                reusable["transfer_novelty"] not in {"", "unknown", "no_transfer", "same_as_d3_variant"},
                "Reusable claimed without a real transfer novelty.",
            )
        )
        tests.append(
            _test(
                "reusable_supporting_surface_has_equivalence",
                _reusable_surface_equivalent(contract),
                "Reusable claimed from supporting/component/result surface without validating the primary reusable surface or a bounded-equivalent declared surface.",
            )
        )
        tests.append(
            _test(
                "reusable_result_reanalysis_not_active_tool_substitute",
                not _result_reanalysis_substitutes_for_active_tool(contract),
                "Reusable claimed from result/data reanalysis while the artifact's active tool/model/pipeline reuse surface remains unvalidated.",
            )
        )

    failed = [item for item in tests if not item["passed"]]
    return {
        "schema": "badge_guardrail_results_v1",
        "status": "failed" if failed else "passed",
        "contract": contract,
        "tests": tests,
        "failed_tests": failed,
    }


def badge_guardrail_findings(run_root: Path, final_packet: dict[str, Any]) -> list[dict[str, Any]]:
    results = run_badge_guardrails(run_root, final_packet)
    findings: list[dict[str, Any]] = []
    for item in results["failed_tests"]:
        findings.append(
            {
                "severity": "warning",
                "code": "badge_guardrail_failed",
                "message": f"{item['name']}: {item['reason']}",
                "required_action": (
                    "Review this heuristic audit finding during human inspection. It does not block phase completion "
                    "or override the final scorer when the packet cites concrete evidence."
                ),
                "refs": [
                    "artifacts/evidence_contract/evidence_contract.json",
                    "artifacts/final_scoring/final_scoring_packet.json",
                    "artifacts/D3_executability/stage_packet.json",
                    "artifacts/D5_reusability/stage_packet.json",
                ],
            }
        )
    return findings


def _test(name: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "reason": "" if passed else reason}


def _scores(packet: dict[str, Any]) -> dict[str, str]:
    raw = packet.get("dimension_scores", {}) if isinstance(packet, dict) else {}
    scores: dict[str, str] = {}
    if isinstance(raw, dict):
        for dim in ("D1", "D2", "D3", "D4", "D5"):
            value = raw.get(dim)
            scores[dim] = str(value.get("score", "") if isinstance(value, dict) else value or "").strip()
    return scores


def _score_gte(value: str | None, threshold: str) -> bool:
    if value not in SCORE_ORDER or threshold not in SCORE_ORDER:
        return False
    return SCORE_ORDER[value] >= SCORE_ORDER[threshold]


def _available_claimed(packet: dict[str, Any]) -> bool:
    gate = packet.get("available_gate", {}) if isinstance(packet, dict) else {}
    if isinstance(gate, dict):
        if isinstance(gate.get("holds"), bool):
            return bool(gate["holds"])
        if isinstance(gate.get("available"), bool):
            return bool(gate["available"])
    badge = packet.get("badge_recommendation", {})
    if isinstance(badge, dict):
        if badge.get("available") is True:
            return True
        text = str(badge.get("badge") or "").lower()
    else:
        text = str(badge or "").lower()
    return text in {"available", "available + functional", "available + functional + reusable"}


def _functional_claimed(packet: dict[str, Any]) -> bool:
    decision = packet.get("functional_decision", {}) if isinstance(packet, dict) else {}
    if isinstance(decision, dict) and decision.get("holds") is True:
        return True
    badge = packet.get("badge_recommendation", {})
    if isinstance(badge, dict):
        return badge.get("functional") is True or "functional" in str(badge.get("badge") or "").lower()
    return "functional" in str(badge or "").lower()


def _reusable_claimed(packet: dict[str, Any]) -> bool:
    decision = packet.get("reusable_decision", {}) if isinstance(packet, dict) else {}
    if isinstance(decision, dict) and decision.get("holds") is True:
        return True
    badge = packet.get("badge_recommendation", {})
    if isinstance(badge, dict):
        return badge.get("reusable") is True or "reusable" in str(badge.get("badge") or "").lower()
    return "reusable" in str(badge or "").lower()


def _functional_component_equivalent(contract: dict[str, Any]) -> bool:
    functional = contract["functional"]
    reusable = contract["reusable"]
    artifact = contract["artifact"]
    if functional.get("result_validation_functional_surface"):
        return True
    if artifact["dataset_or_result_primary"]:
        return True
    if _bounded_functional_surface(functional):
        return True
    if functional["stage_says_not_artifact_level"] and functional["active_surface_unvalidated"]:
        return False
    if reusable["primary_surface_explicitly_validated"]:
        return True
    strength = reusable["strongest_transfer_strength"]
    return strength in {"artifact_surface", "bounded_artifact_level", "documented_cli_or_api"} and not functional[
        "active_surface_unvalidated"
    ]


def _bounded_functional_surface(functional: dict[str, Any]) -> bool:
    """Functional can hold on bounded paper-facing evidence even when Reusable cannot."""

    if functional.get("generic_dependency_or_example"):
        return False
    if functional.get("artifact_specific_logic") not in {"yes", "limited"}:
        return False
    if functional.get("paper_facing_output") not in {"yes", "limited"}:
        return False
    if functional.get("single_file_or_smoke_only") and functional.get("paper_facing_output") != "yes":
        return False
    if functional.get("resource_boundary_position") == "before_artifact_logic" and functional.get("paper_facing_output") != "yes":
        return False
    return True


def _reusable_surface_equivalent(contract: dict[str, Any]) -> bool:
    reusable = contract["reusable"]
    artifact = contract["artifact"]
    final_suff = reusable["final_artifact_level_sufficiency"]
    stage_suff = reusable["stage_artifact_level_sufficiency"]
    target = reusable["reuse_target_level"]
    strength = reusable["strongest_transfer_strength"]
    evidence_type = reusable["strongest_evidence_type"]

    if reusable.get("weak_mock_or_demo_transfer"):
        return False
    if reusable.get("active_surface_unvalidated") and target in {"dataset_package_level", "result_package_level"}:
        return False
    if final_suff == "sufficient" and reusable["primary_surface_explicitly_validated"]:
        return True
    if reusable["primary_surface_explicitly_validated"] and not reusable["active_surface_unvalidated"]:
        return True
    if stage_suff in {"supporting_only", "insufficient"} or final_suff in {"supporting_only", "insufficient"}:
        if reusable["primary_surface_explicitly_validated"]:
            return True
        if stage_suff == "supporting_only" and artifact.get("active_tool_like"):
            return False
        if artifact["dataset_or_result_primary"] and target in {"dataset_package_level", "result_package_level"}:
            return True
        return False
    if strength in {"artifact_surface", "pipeline", "pipeline_level", "documented_cli_or_api"}:
        return not reusable["active_surface_unvalidated"] or reusable["primary_surface_explicitly_validated"]
    if evidence_type in {"artifact_surface_transfer", "documented_cli_or_api_transfer"}:
        return not reusable["active_surface_unvalidated"] or reusable["primary_surface_explicitly_validated"]
    return False


def _result_reanalysis_substitutes_for_active_tool(contract: dict[str, Any]) -> bool:
    reusable = contract["reusable"]
    artifact = contract["artifact"]
    is_reanalysis = reusable["reuse_target_level"] in {"dataset_package_level", "result_package_level"} or reusable[
        "strongest_evidence_type"
    ] in {"dataset_or_result_package_reanalysis", "result_package_reanalysis"}
    return is_reanalysis and artifact["active_tool_like"] and (
        reusable["active_surface_unvalidated"]
        or artifact.get("higher_level_reuse_surface_present", False)
        or not artifact["dataset_or_result_primary"]
    )
