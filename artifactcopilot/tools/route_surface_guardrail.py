from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..shared.fs import relpath_str


ENTRY_NAME_RE = re.compile(r"(?:^|[_\-.])(?:demo|example|mock)(?:[_\-.]|$)|(?:demo|example|mock)", re.IGNORECASE)
ENTRY_SUFFIXES = {
    ".py",
    ".sh",
    ".R",
    ".jl",
    ".js",
    ".ts",
    ".java",
    ".scala",
    ".kt",
    ".jar",
    ".ipynb",
}
ROOT_ENTRY_DOC_PRIORITY = {
    "readme.md": 1,
    "readme.rst": 2,
    "readme.txt": 3,
    "readme": 4,
    "install.md": 5,
    "reproduce.md": 6,
    "artifact.md": 7,
}
SKIP_DIR_PARTS = {
    ".git",
    ".artifactcopilot_run",
    ".ae_venv",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "site-packages",
    "dist-packages",
    "__pypackages__",
    ".eggs",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "dist",
    "build",
    "artifacts",
}
MAX_SCAN_DEPTH = 8
UNCERTAIN_ROOT_SCOPES = {"subdir_possible", "ambiguous"}
VALID_ROUTE_SCOPES = {"configured_repo_only", "artifact_universe_incomplete", "artifact_universe_complete"}
IMPORTANT_PRIORITY = {"primary", "good_bounded_candidate"}
D3_RELEVANCE = {"D3_executability", "D4_claim_support", "Functional", "D3", "D4"}
D5_RELEVANCE = {"D5_reusability", "Reusable", "D5"}
SUPPORTING_D3_ROLES = {
    "paper_postprocessing_only",
    "setup_only",
    "component_or_helper_only",
}
SUPPORTING_D3_ELIGIBILITY = {"supporting_only", "not_eligible"}
SUPPORTING_D5_SURFACES = {
    "component_or_helper_surface",
    "same_workflow_only",
    "none",
}
REUSABLE_EVIDENCE_SURFACES = {
    "intended_public_surface",
    "documented_subsurface",
    "dataset_or_result_surface",
}
REUSABLE_EVIDENCE_ROLES = {
    "core_reuse_surface",
    "documented_result_validation",
}


def route_surface_finish_errors(run_root: Path, repo_root: Path, phase_name: str) -> list[dict[str, Any]]:
    if phase_name not in {"D1_documentation", "D3_executability", "D5_reusability"}:
        return []
    errors: list[dict[str, Any]] = []
    errors.extend(_stage_scope_errors(run_root, phase_name))
    errors.extend(_route_portfolio_consumption_errors(run_root, phase_name))
    d1_workflow = _read_json(run_root / "artifacts" / "D1_documentation" / "readme_workflow.json")
    if phase_name == "D1_documentation":
        errors.extend(_d1_entry_document_errors(repo_root, d1_workflow))
        if _d1_uses_flat_documented_command_schema(d1_workflow):
            errors.extend(_d1_documentation_inventory_errors(run_root, d1_workflow))
        else:
            errors.extend(_d1_rank_schema_errors(d1_workflow))
            errors.extend(_d1_stage_packet_handoff_errors(run_root, d1_workflow))
            candidates = _discover_surface_candidates(repo_root)
            if candidates:
                errors.extend(_d1_route_surface_errors(candidates, d1_workflow))
        return errors
    if phase_name == "D5_reusability":
        d5_packet = _read_json(run_root / "artifacts" / "D5_reusability" / "stage_packet.json")
        reuse_plan = _read_json(run_root / "artifacts" / "D5_reusability" / "reuse_plan.json")
        errors.extend(_d5_same_workflow_transfer_errors(_merged_d5_transfer_payload(d5_packet, reuse_plan)))
        return errors
    d3_packet = _read_json(run_root / "artifacts" / "D3_executability" / "stage_packet.json")
    if phase_name == "D3_executability":
        errors.extend(_d3_flat_route_contract_errors(run_root, d1_workflow, d3_packet))
        if _d1_uses_flat_documented_command_schema(d1_workflow):
            return errors
        errors.extend(_d3_rank_consumption_errors(d1_workflow, d3_packet))
    candidates = _discover_surface_candidates(repo_root)
    if not candidates:
        return errors
    if phase_name == "D3_executability":
        errors.extend(_d3_route_surface_errors(candidates, d1_workflow, d3_packet))
    return errors


def _stage_scope_errors(run_root: Path, phase_name: str) -> list[dict[str, Any]]:
    universe = _read_json(run_root / "artifacts" / "run_initialization" / "artifact_universe.json")
    root_scope = str(universe.get("root_scope", "")).strip().lower()
    if root_scope not in UNCERTAIN_ROOT_SCOPES:
        return []
    if phase_name == "D1_documentation":
        payload = _read_json(run_root / "artifacts" / "D1_documentation" / "readme_workflow.json")
        refs = [
            "artifacts/run_initialization/artifact_universe.json",
            "artifacts/D1_documentation/readme_workflow.json",
        ]
    elif phase_name == "D3_executability":
        payload = _read_json(run_root / "artifacts" / phase_name / "stage_packet.json")
        route_evidence = _read_json(run_root / "artifacts" / phase_name / "route_evidence.json")
        if isinstance(payload, dict) and isinstance(route_evidence, dict):
            payload = {**route_evidence, **payload}
        refs = [
            "artifacts/run_initialization/artifact_universe.json",
            f"artifacts/{phase_name}/stage_packet.json",
            f"artifacts/{phase_name}/route_evidence.json",
        ]
    else:
        payload = _read_json(run_root / "artifacts" / phase_name / "stage_packet.json")
        refs = [
            "artifacts/run_initialization/artifact_universe.json",
            f"artifacts/{phase_name}/stage_packet.json",
        ]
    if not isinstance(payload, dict) or not payload:
        return []
    route_scope = str(payload.get("route_scope", "")).strip().lower()
    if route_scope in VALID_ROUTE_SCOPES:
        return []
    return [
        {
            "code": "artifact_scope_boundary_not_recorded",
            "message": (
                "artifact_universe.json records that the configured repository may be only part of the artifact, "
                f"but {phase_name} did not record route_scope. Later route failures must stay component-scoped "
                "unless sibling/combined artifact components are covered or ruled unrelated."
            ),
            "required_action": (
                "Record route_scope as configured_repo_only, artifact_universe_incomplete, or artifact_universe_complete, "
                "with a short reason and artifact boundary warnings. Use artifact_universe_complete only when the stage "
                "covered all plausible artifact components or explains why the uncovered components are unrelated."
            ),
            "root_scope": root_scope,
            "boundary_warnings": universe.get("boundary_warnings", []),
            "refs": refs,
        }
    ]


def _route_portfolio_consumption_errors(run_root: Path, phase_name: str) -> list[dict[str, Any]]:
    if phase_name not in {"D3_executability", "D5_reusability"}:
        return []
    stage_packet = _read_json(run_root / "artifacts" / phase_name / "stage_packet.json")
    if not isinstance(stage_packet, dict) or not stage_packet:
        return []
    stage_text = _stage_accounting_text(run_root, phase_name, stage_packet)
    d1_workflow = _read_json(run_root / "artifacts" / "D1_documentation" / "readme_workflow.json")
    if _d1_uses_flat_documented_command_schema(d1_workflow):
        return []
    routes = _important_d1_routes(d1_workflow, phase_name)
    if _stage_records_successful_selected_route(stage_text, phase_name):
        weak_success = _stage_success_is_weak_or_supporting(stage_packet, phase_name)
        if not weak_success:
            return []
        missing_after_weak_success = [
            route
            for route in routes
            if _route_is_good_bounded_candidate(route) and not _route_consumed(route, stage_text)
        ]
        if missing_after_weak_success:
            return [
                {
                    "code": "weak_success_before_bounded_route_portfolio_consumed",
                    "message": (
                        f"{phase_name} records a successful but limited route. Before finishing, it must "
                        "consume or explicitly dismiss remaining good bounded candidates from D1 route_portfolio."
                    ),
                    "required_action": (
                        f"Update artifacts/{phase_name}/stage_packet.json so each remaining good bounded route has "
                        "an attempt/skip status, failure_or_skip_cause, and route_result_interpretation or transfer_result_interpretation. "
                        "Attempt grounded lighter routes when they may exercise paper-facing logic or validate the "
                        "intended reusable surface."
                    ),
                    "missing_routes": missing_after_weak_success[:8],
                    "refs": [
                        "artifacts/D1_documentation/readme_workflow.json",
                        f"artifacts/{phase_name}/stage_packet.json",
                    ],
                }
            ]
        return []
    missing = [route for route in routes if not _route_consumed(route, stage_text)]
    if not missing:
        return []
    return [
        {
            "code": "d1_route_portfolio_not_consumed",
            "message": (
                f"{phase_name} records no successful selected route, but it did not consume or explicitly dismiss "
                "important D1 route_portfolio candidates. This guardrail only asks for route accounting; it does "
                "not require running every candidate."
            ),
            "required_action": (
                f"Update artifacts/{phase_name}/stage_packet.json so each listed D1 primary/good bounded route has "
                "an attempt/skip status, failure_or_skip_cause, and route_result_interpretation or transfer_result_interpretation."
            ),
            "missing_routes": missing[:8],
            "refs": [
                "artifacts/D1_documentation/readme_workflow.json",
                f"artifacts/{phase_name}/stage_packet.json",
            ],
        }
    ]


def _stage_records_successful_selected_route(stage_text: str, phase_name: str) -> bool:
    if phase_name == "D3_executability":
        return "successful_representative" in stage_text or (
            "attempted_succeeded" in stage_text and "paper_facing_output" in stage_text
        )
    return "successful_transfer" in stage_text or (
        "selected_for_validation" in stage_text and "not_applicable_success" in stage_text
    )


def _important_d1_routes(d1_workflow: dict[str, Any], phase_name: str) -> list[dict[str, Any]]:
    routes = d1_workflow.get("route_portfolio", []) if isinstance(d1_workflow, dict) else []
    if not isinstance(routes, list):
        return []
    target_relevance = D3_RELEVANCE if phase_name == "D3_executability" else D5_RELEVANCE
    result: list[dict[str, Any]] = []
    for item in routes:
        if not isinstance(item, dict):
            continue
        if _priority(item) not in IMPORTANT_PRIORITY:
            continue
        relevance = _stage_relevance(item)
        if relevance and not (relevance & target_relevance):
            continue
        route_id = str(item.get("route_id", "")).strip()
        source = str(item.get("source", "")).strip()
        if not route_id and not source:
            continue
        result.append(
            {
                "route_id": route_id,
                "source": source,
                "selection_priority_hint": _priority(item),
                "stage_relevance": sorted(relevance),
            }
        )
    return result


def _priority(route: dict[str, Any]) -> str:
    return str(route.get("selection_priority_hint") or route.get("priority") or "").strip().lower()


def _stage_relevance(route: dict[str, Any]) -> set[str]:
    raw = route.get("stage_relevance") or route.get("rubric_relevance") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


def _route_consumed(route: dict[str, Any], stage_text: str) -> bool:
    keys = [
        str(route.get("route_id", "")).strip(),
        str(route.get("source", "")).strip(),
    ]
    if not any(key and key.lower() in stage_text for key in keys):
        return False
    status_markers = (
        "attempt_status",
        "attempted_",
        "not_attempted_",
        "selected_for_d3",
        "selected_for_validation",
        "limited_surface_only",
        "setup_or_component_evidence",
        "resource_gated",
        "resource_or_api_gated",
        "missing_input",
        "too_heavy",
        "lower_priority",
        "not_reviewer_realistic",
        "failure_or_skip_cause",
        "selection_or_skip_reason",
        "route_result_interpretation",
        "transfer_result_interpretation",
        "validated_reuse_surface",
    )
    return any(marker in stage_text for marker in status_markers)


def _stage_accounting_text(run_root: Path, phase_name: str, stage_packet: dict[str, Any]) -> str:
    text_parts = [_json_text(stage_packet)]
    if phase_name == "D5_reusability":
        phase_dir = run_root / "artifacts" / phase_name
        for name in ("d1_route_accounting.json", "route_accounting.json"):
            payload = _read_json(phase_dir / name)
            if payload:
                text_parts.append(_json_text(payload))
    return "\n".join(text_parts)


def _route_is_good_bounded_candidate(route: dict[str, Any]) -> bool:
    return str(route.get("selection_priority_hint", "")).strip().lower() == "good_bounded_candidate"


def _d1_entry_document_errors(repo_root: Path, d1_workflow: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(d1_workflow, dict) or not d1_workflow:
        return []
    entry_doc = _primary_root_entry_document(repo_root)
    if not entry_doc:
        return []
    documents = d1_workflow.get("documents_read")
    if not isinstance(documents, list):
        documents = []
    if _documents_read_contains_complete_ref(documents, entry_doc):
        return []
    return [
        {
            "code": "d1_primary_entry_document_not_recorded_complete",
            "message": (
                "A root artifact-authored entry document exists, but D1 did not record it as completely read "
                "in readme_workflow.documents_read."
            ),
            "required_action": (
                f"Read `{entry_doc}` completely, then update artifacts/D1_documentation/readme_workflow.json "
                "documents_read with read_completely=true and source_ref, and revise route_portfolio/canonical_route_plan "
                "if that document exposes additional routes or ordering."
            ),
            "entry_document": entry_doc,
            "refs": [
                f"repo:{entry_doc}",
                "artifacts/D1_documentation/readme_workflow.json",
            ],
        }
    ]


def _primary_root_entry_document(repo_root: Path) -> str:
    root = repo_root.resolve()
    candidates: list[tuple[int, str]] = []
    try:
        children = list(root.iterdir())
    except Exception:
        return ""
    for path in children:
        if not path.is_file():
            continue
        priority = ROOT_ENTRY_DOC_PRIORITY.get(path.name.lower())
        if priority is None:
            continue
        candidates.append((priority, path.name))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (item[0], item[1].lower()))
    return candidates[0][1]


def _documents_read_contains_complete_ref(documents: list[Any], entry_doc: str) -> bool:
    expected = entry_doc.strip().lower()
    for item in documents:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip().lower()
        source_ref = str(item.get("source_ref", "")).strip().lower()
        normalized_refs = {
            path,
            source_ref,
            source_ref.removeprefix("repo:"),
        }
        if expected not in normalized_refs:
            continue
        if item.get("read_completely") is True:
            return True
        if str(item.get("read_completely", "")).strip().lower() == "true":
            return True
    return False


def _d1_documentation_inventory_errors(run_root: Path, d1_workflow: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(d1_workflow, dict) or not d1_workflow:
        return []
    missing: list[dict[str, Any]] = []
    commands = d1_workflow.get("documented_commands", [])
    if not isinstance(commands, list):
        missing.append({"field": "documented_commands", "missing_fields": ["list_required"]})
        commands = []
    for index, item in enumerate(commands, start=1):
        if not isinstance(item, dict):
            missing.append({"field": f"documented_commands[{index}]", "missing_fields": ["object_required"]})
            continue
        absent = [
            key
            for key in ("command_id", "source_ref", "command", "purpose", "command_kind", "is_shell_command")
            if not str(item.get(key, "")).strip()
        ]
        source_ref = str(item.get("source_ref", "")).strip()
        if source_ref and not source_ref.startswith("repo:"):
            absent.append("source_ref_repo_prefix")
        kind = str(item.get("command_kind", "")).strip().lower()
        if kind and kind not in {"install", "data", "run", "evaluate", "test", "other"}:
            absent.append("command_kind_broad_enum")
        if "kind" in item and "command_kind" not in item:
            absent.append("use_command_kind_not_kind")
        if absent:
            missing.append({"field": f"documented_commands[{index}]", "missing_fields": absent})
        fabricated_file_command_issue = _fabricated_file_inventory_command_issue(item)
        if fabricated_file_command_issue:
            missing.append({"field": f"documented_commands[{index}]", "missing_fields": [fabricated_file_command_issue]})
    if "documented_workflows" in d1_workflow:
        workflows = d1_workflow.get("documented_workflows")
        if workflows not in (None, []):
            missing.append({"field": "documented_workflows", "missing_fields": ["omit_or_keep_empty_in_flat_d1_schema"]})
    if not commands:
        uncertainties = d1_workflow.get("uncertainties", [])
        status = str(d1_workflow.get("status", "")).strip().lower()
        if status != "blocked" and (not isinstance(uncertainties, list) or not uncertainties):
            missing.append(
                {
                    "field": "documented_commands",
                    "missing_fields": ["empty_without_uncertainty_or_no_command_explanation"],
                }
            )
    stage_packet = _read_json(run_root / "artifacts" / "D1_documentation" / "stage_packet.json")
    if isinstance(stage_packet, dict) and stage_packet:
        ref = str(stage_packet.get("readme_workflow_ref", "")).strip()
        if ref != "artifacts/D1_documentation/readme_workflow.json":
            missing.append({"field": "stage_packet.readme_workflow_ref", "missing_fields": ["expected_readme_workflow_ref"]})
        if "commands_extracted_count" not in stage_packet:
            missing.append({"field": "stage_packet.commands_extracted_count", "missing_fields": ["required_count"]})
    if not missing:
        return []
    return [
        {
            "code": "d1_documentation_inventory_incomplete",
            "message": "D1 documentation inventory is missing source-grounded flat command fields.",
            "required_action": (
                "Update artifacts/D1_documentation/readme_workflow.json so documented_commands is a flat list, "
                "each command/action has source_ref, command text, broad command_kind, is_shell_command, and "
                "empty inventories explain the documentation limitation."
            ),
            "missing": missing[:12],
            "refs": [
                "artifacts/D1_documentation/readme_workflow.json",
                "artifacts/D1_documentation/stage_packet.json",
            ],
        }
    ]


def _fabricated_file_inventory_command_issue(item: dict[str, Any]) -> str:
    command = str(item.get("command", "")).strip()
    notes = str(item.get("notes", "")).strip()
    purpose = str(item.get("purpose", "")).strip()
    combined = f"{command}\n{notes}\n{purpose}".lower()
    if not command:
        return ""
    command_looks_fabricated = bool(
        re.match(
            r"^(run|open|execute)\s+[`'\"]?[^`'\"\s]+\.(?:py|ipynb|r|jl|js|ts|java|sh|cpp|c|m)[`'\"]?$",
            command,
            flags=re.IGNORECASE,
        )
    )
    no_explicit_instruction_markers = (
        "no explicit",
        "no shell command",
        "no explicit shell",
        "no exact command",
        "no command",
        "file purpose",
        "describes file",
        "describes the file",
        "purpose mapping",
        "file-purpose",
        "only describes",
    )
    if command_looks_fabricated and any(marker in combined for marker in no_explicit_instruction_markers):
        return "passive_file_inventory_converted_to_command"
    return ""


def _d1_rank_schema_errors(d1_workflow: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(d1_workflow, dict) or not d1_workflow:
        return []
    routes = d1_workflow.get("route_portfolio")
    missing: list[dict[str, Any]] = []
    if not isinstance(routes, list):
        missing.append({"route_id": "route_portfolio", "missing_fields": ["route_portfolio_list_required"]})
        routes = []
    if routes:
        seen_ranks: set[int] = set()
        duplicate_ranks: set[int] = set()
        route_ids: set[str] = set()
        duplicate_route_ids: set[str] = set()
        for route in routes:
            if not isinstance(route, dict):
                missing.append({"route_id": "route_portfolio", "missing_fields": ["route_entry_object_required"]})
                continue
            route_id = str(route.get("route_id", "")).strip()
            if route_id:
                if route_id in route_ids:
                    duplicate_route_ids.add(route_id)
                route_ids.add(route_id)
            required = [
                "rank",
                "rank_reason",
                "source",
                "stage_relevance",
                "route_type",
                "selection_priority_hint",
                "evidence_role",
                "supports_functional_evidence",
                "supports_reusable_evidence",
                "d3_eligibility",
                "d5_eligibility",
                "route_flags",
            ]
            absent = [key for key in required if key not in route]
            rank = route.get("rank")
            if isinstance(rank, int) and rank > 0:
                if rank in seen_ranks:
                    duplicate_ranks.add(rank)
                seen_ranks.add(rank)
            elif "rank" not in absent:
                absent.append("rank_positive_integer")
            flags = route.get("route_flags")
            if isinstance(flags, dict):
                flag_missing = [
                    key
                    for key in (
                        "is_official_documented",
                        "is_public_user_surface",
                        "is_mock_or_test",
                        "is_result_or_postprocessing_only",
                        "is_setup_only",
                        "is_same_workflow_variant_only",
                    )
                    if key not in flags
                ]
                absent.extend(f"route_flags.{key}" for key in flag_missing)
            elif "route_flags" not in absent:
                absent.append("route_flags_object")
            if absent:
                missing.append({"route_id": route_id, "missing_fields": absent})
        if seen_ranks:
            expected = set(range(1, len(seen_ranks) + 1))
            if seen_ranks != expected:
                missing.append(
                    {
                        "route_id": "route_portfolio",
                        "missing_fields": [f"contiguous_unique_ranks_expected_{sorted(expected)}_got_{sorted(seen_ranks)}"],
                    }
                )
        if duplicate_ranks:
            missing.append({"route_id": "route_portfolio", "missing_fields": [f"duplicate_ranks_{sorted(duplicate_ranks)}"]})
        if duplicate_route_ids:
            missing.append({"route_id": "route_portfolio", "missing_fields": [f"duplicate_route_ids_{sorted(duplicate_route_ids)}"]})

    plan = d1_workflow.get("canonical_route_plan") if isinstance(d1_workflow.get("canonical_route_plan"), dict) else {}
    rank_table = plan.get("route_rank_table") if isinstance(plan, dict) else None
    if not isinstance(rank_table, list):
        missing.append({"route_id": "canonical_route_plan", "missing_fields": ["route_rank_table"]})
    elif routes and not rank_table:
        missing.append({"route_id": "canonical_route_plan", "missing_fields": ["nonempty_route_rank_table_for_nonempty_routes"]})
    if isinstance(plan, dict):
        missing.extend(_d1_canonical_plan_consistency_missing(d1_workflow, plan))
    provenance = d1_workflow.get("decision_provenance")
    if not isinstance(provenance, dict):
        missing.append({"route_id": "decision_provenance", "missing_fields": ["decision_provenance_object"]})
    else:
        missing.extend(_d1_decision_provenance_missing(provenance))
    if missing:
        return [
            {
                "code": "d1_route_rank_schema_incomplete",
                "message": (
                    "D1 route_portfolio/canonical_route_plan/decision_provenance does not provide the stable "
                    "ranked route contract needed by D2/D3/D5."
                ),
                "required_action": (
                    "Rewrite artifacts/D1_documentation/readme_workflow.json with complete ranked route_portfolio entries, "
                    "a canonical_route_plan consistent with those ranks, and decision_provenance entries for document selection, "
                    "route extraction/ranking, canonical handoff, D5 candidate selection, and scope boundary. Do not invent routes; "
                    "cite artifact-provided sources."
                ),
                "missing": missing[:12],
                "refs": ["artifacts/D1_documentation/readme_workflow.json"],
            }
        ]
    return []


def _d1_stage_packet_handoff_errors(run_root: Path, d1_workflow: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(d1_workflow, dict) or not d1_workflow:
        return []
    stage_packet = _read_json(run_root / "artifacts" / "D1_documentation" / "stage_packet.json")
    if not isinstance(stage_packet, dict) or not stage_packet:
        return []
    plan = d1_workflow.get("canonical_route_plan")
    if not isinstance(plan, dict):
        return []
    missing: list[str] = []
    for key in ("d2_environment_route_ids", "d3_route_order", "d5_core_reuse_candidate_ids"):
        expected = [str(item).strip() for item in plan.get(key, []) if str(item).strip()] if isinstance(plan.get(key), list) else []
        handoff = stage_packet.get("handoff")
        actual = []
        if isinstance(handoff, dict) and isinstance(handoff.get(key), list):
            actual = [str(item).strip() for item in handoff.get(key, []) if str(item).strip()]
        summary_actual = stage_packet.get("canonical_route_plan_summary", {})
        if not actual and isinstance(summary_actual, dict) and isinstance(summary_actual.get(key), list):
            actual = [str(item).strip() for item in summary_actual.get(key, []) if str(item).strip()]
        if actual != expected:
            missing.append(f"{key}_expected_{expected}_got_{actual}")
    if "decision_provenance_ref" not in stage_packet:
        missing.append("stage_packet_missing_decision_provenance_ref")
    if "decision_provenance_summary" not in stage_packet:
        missing.append("stage_packet_missing_decision_provenance_summary")
    if not missing:
        return []
    return [
        {
            "code": "d1_stage_handoff_inconsistent_with_canonical_plan",
            "message": (
                "D1 stage_packet handoff does not match readme_workflow.canonical_route_plan, or it omits "
                "decision provenance refs. Later stages may consume a different route order than D1 extracted."
            ),
            "required_action": (
                "Update artifacts/D1_documentation/stage_packet.json so handoff route arrays exactly mirror "
                "readme_workflow.canonical_route_plan, and include decision_provenance_ref plus a concise "
                "decision_provenance_summary."
            ),
            "missing": missing,
            "refs": [
                "artifacts/D1_documentation/readme_workflow.json",
                "artifacts/D1_documentation/stage_packet.json",
            ],
        }
    ]


def _d1_canonical_plan_consistency_missing(d1_workflow: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    routes = [item for item in d1_workflow.get("route_portfolio", []) if isinstance(item, dict)]
    by_id = {str(item.get("route_id", "")).strip(): item for item in routes if str(item.get("route_id", "")).strip()}
    missing: list[dict[str, Any]] = []
    rank_table = plan.get("route_rank_table")
    if isinstance(rank_table, list):
        table_ids = [str(item.get("route_id", "")).strip() for item in rank_table if isinstance(item, dict)]
        route_ids = [route_id for route_id in by_id]
        if set(table_ids) != set(route_ids):
            missing.append(
                {
                    "route_id": "canonical_route_plan.route_rank_table",
                    "missing_fields": [f"route_id_set_mismatch_routes_{sorted(route_ids)}_table_{sorted(table_ids)}"],
                }
            )
    for key, expected in (
        ("d3_route_order", _expected_d3_route_order(routes)),
        ("d5_core_reuse_candidate_ids", _expected_d5_candidate_order(routes)),
    ):
        actual = plan.get(key)
        if not isinstance(actual, list):
            missing.append({"route_id": f"canonical_route_plan.{key}", "missing_fields": ["list_required"]})
            continue
        actual_ids = [str(item).strip() for item in actual if str(item).strip()]
        if actual_ids != expected:
            missing.append(
                {
                    "route_id": f"canonical_route_plan.{key}",
                    "missing_fields": [f"expected_rank_order_{expected}_got_{actual_ids}"],
                }
            )
    route_roles = plan.get("route_roles")
    if not isinstance(route_roles, dict):
        missing.append({"route_id": "canonical_route_plan.route_roles", "missing_fields": ["object_required"]})
    else:
        missing_roles = [route_id for route_id in by_id if not str(route_roles.get(route_id, "")).strip()]
        if missing_roles:
            missing.append({"route_id": "canonical_route_plan.route_roles", "missing_fields": [f"missing_roles_{missing_roles}"]})
    tie_break = str(plan.get("tie_break_policy", "")).strip()
    if not tie_break:
        missing.append({"route_id": "canonical_route_plan.tie_break_policy", "missing_fields": ["nonempty_tie_break_policy"]})
    return missing


def _expected_d3_route_order(routes: list[dict[str, Any]]) -> list[str]:
    return [
        str(item.get("route_id", "")).strip()
        for item in sorted(routes, key=lambda route: int(route.get("rank", 9999)) if isinstance(route.get("rank"), int) else 9999)
        if str(item.get("route_id", "")).strip()
        and _stage_relevance(item)
        and (_stage_relevance(item) & D3_RELEVANCE)
        and str(item.get("d3_eligibility", "")).strip().lower() not in SUPPORTING_D3_ELIGIBILITY
    ]


def _expected_d5_candidate_order(routes: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for item in sorted(routes, key=lambda route: int(route.get("rank", 9999)) if isinstance(route.get("rank"), int) else 9999):
        route_id = str(item.get("route_id", "")).strip()
        if not route_id:
            continue
        if _route_is_d5_candidate(item):
            result.append(route_id)
    return result


def _route_is_d5_candidate(route: dict[str, Any]) -> bool:
    eligibility = str(route.get("d5_eligibility", "")).strip().lower()
    if eligibility in {"supporting_only", "not_eligible", "unknown", ""}:
        return False
    if eligibility == "fallback_only":
        return False
    surface = str(route.get("supports_reusable_evidence", "")).strip().lower()
    role = str(route.get("evidence_role", "")).strip().lower()
    relevance = _stage_relevance(route)
    return bool(
        (relevance & D5_RELEVANCE)
        or surface in REUSABLE_EVIDENCE_SURFACES
        or role in REUSABLE_EVIDENCE_ROLES
    )


def _d1_decision_provenance_missing(provenance: dict[str, Any]) -> list[dict[str, Any]]:
    decisions = provenance.get("primary_decisions")
    if not isinstance(decisions, list) or not decisions:
        return [{"route_id": "decision_provenance", "missing_fields": ["primary_decisions"]}]
    required_types = {
        "entry_document_selection",
        "route_extraction",
        "route_ranking",
        "canonical_route_plan",
        "d5_reuse_candidate_selection",
        "scope_boundary",
    }
    seen_types: set[str] = set()
    incomplete: list[str] = []
    for index, item in enumerate(decisions, start=1):
        if not isinstance(item, dict):
            incomplete.append(f"decision_{index}_object_required")
            continue
        decision_type = str(item.get("decision_type", "")).strip()
        if decision_type:
            seen_types.add(decision_type)
        if not str(item.get("decision_id", "")).strip():
            incomplete.append(f"decision_{index}_missing_decision_id")
        rule_ids = item.get("rule_ids")
        if not isinstance(rule_ids, list) or not [rule for rule in rule_ids if str(rule).strip()]:
            incomplete.append(f"decision_{index}_missing_rule_ids")
        refs = item.get("evidence_refs") or item.get("source_refs")
        if not isinstance(refs, list) or not [ref for ref in refs if str(ref).strip()]:
            incomplete.append(f"decision_{index}_missing_evidence_or_source_refs")
        alternatives = item.get("alternatives_considered")
        if decision_type in {"route_ranking", "canonical_route_plan", "d5_reuse_candidate_selection"} and not isinstance(alternatives, list):
            incomplete.append(f"decision_{index}_missing_alternatives_considered")
        confidence = str(item.get("confidence", "")).strip().lower()
        if confidence not in {"high", "medium", "low"}:
            incomplete.append(f"decision_{index}_invalid_confidence")
    missing_types = sorted(required_types - seen_types)
    fields = [f"missing_decision_type_{item}" for item in missing_types] + incomplete
    if not fields:
        return []
    return [{"route_id": "decision_provenance.primary_decisions", "missing_fields": fields}]


def _d3_rank_consumption_errors(d1_workflow: dict[str, Any], d3_packet: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(d1_workflow, dict) or not isinstance(d3_packet, dict) or not d3_packet:
        return []
    ranked = _ranked_d3_routes(d1_workflow)
    if not ranked:
        return []
    stage_text = _json_text(d3_packet)
    selected_rank = _selected_d3_rank(d3_packet, ranked)
    ranked_execution = d3_packet.get("ranked_route_execution")
    if not isinstance(ranked_execution, list) or not ranked_execution:
        return [
            {
                "code": "d3_ranked_route_execution_missing",
                "message": "D3 stage_packet is missing ranked_route_execution, so auditor/final scoring cannot verify that D3 followed D1 route order.",
                "required_action": (
                    "Add ranked_route_execution sorted by D1 rank. For each higher-ranked D3 route before the selected route, "
                    "record attempt_status, failure_or_skip_cause, and whether it remains reviewer-realistic."
                ),
                "refs": [
                    "artifacts/D1_documentation/readme_workflow.json",
                    "artifacts/D3_executability/stage_packet.json",
                ],
            }
        ]
    if selected_rank is None:
        return []
    unaccounted: list[dict[str, Any]] = []
    for route in ranked:
        if int(route.get("rank", 9999)) >= selected_rank:
            continue
        if _d3_route_accounted(route, ranked_execution, stage_text):
            continue
        unaccounted.append(route)
    if not unaccounted:
        return []
    return [
        {
            "code": "d3_selected_lower_rank_without_higher_rank_accounting",
            "message": (
                "D3 selected a lower-ranked route without accounting for higher-ranked D3-eligible routes. "
                "This can cause route drift and overclaiming from easier result/helper routes."
            ),
            "required_action": (
                "Update artifacts/D3_executability/stage_packet.json so every higher-ranked D3 route has "
                "attempt_status, failure_or_skip_cause, and route_result_interpretation; attempt feasible higher-ranked routes "
                "before relying on lower-ranked routes."
            ),
            "unaccounted_higher_rank_routes": unaccounted[:8],
            "refs": [
                "artifacts/D1_documentation/readme_workflow.json",
                "artifacts/D3_executability/stage_packet.json",
            ],
        }
    ]


def _d3_flat_route_contract_errors(run_root: Path, d1_workflow: dict[str, Any], d3_packet: dict[str, Any]) -> list[dict[str, Any]]:
    if not _d1_uses_flat_documented_command_schema(d1_workflow):
        return []
    if not isinstance(d3_packet, dict) or not d3_packet:
        return []
    missing: list[dict[str, Any]] = []
    checkpoint = _read_json(run_root / "artifacts" / "D3_executability" / "route_selection_checkpoint.json")
    route_evidence = _read_json(run_root / "artifacts" / "D3_executability" / "route_evidence.json")
    semantics = _read_json(run_root / "artifacts" / "D3_executability" / "evidence_semantics.json")
    if not isinstance(checkpoint, dict) or not checkpoint:
        missing.append({"artifact": "route_selection_checkpoint", "missing_fields": ["json_required"]})
    else:
        candidates = checkpoint.get("route_candidates")
        if not isinstance(candidates, list):
            missing.append({"artifact": "route_selection_checkpoint", "missing_fields": ["route_candidates_list_required"]})
        if not str(checkpoint.get("selected_candidate_id", "")).strip():
            missing.append({"artifact": "route_selection_checkpoint", "missing_fields": ["selected_candidate_id"]})
        if not str(checkpoint.get("selection_reason", "")).strip():
            missing.append({"artifact": "route_selection_checkpoint", "missing_fields": ["selection_reason"]})
    if not isinstance(route_evidence, dict) or not route_evidence:
        missing.append({"artifact": "route_evidence", "missing_fields": ["json_required"]})
    else:
        attempts = route_evidence.get("route_attempts")
        if not isinstance(attempts, list):
            missing.append({"artifact": "route_evidence", "missing_fields": ["route_attempts_list_required"]})
        arbitration = route_evidence.get("route_arbitration")
        if not isinstance(arbitration, dict):
            missing.append({"artifact": "route_evidence.route_arbitration", "missing_fields": ["object_required"]})
        else:
            for key in ("selected_candidate_id", "selection_reason", "selected_route_decisiveness"):
                if not str(arbitration.get(key, "")).strip():
                    missing.append({"artifact": "route_evidence.route_arbitration", "missing_fields": [key]})
    if not isinstance(semantics, dict) or not semantics:
        missing.append({"artifact": "evidence_semantics", "missing_fields": ["json_required"]})
    else:
        for key in (
            "workflow_kind",
            "output_provenance",
            "artifact_specific_logic_exercised",
            "paper_facing_output",
            "coverage_boundary",
            "rw1_rw5_fact_table",
            "resource_boundary_assessment",
        ):
            value = semantics.get(key)
            if value in (None, "", [], {}):
                missing.append({"artifact": "evidence_semantics", "missing_fields": [key]})
    for ref_key, expected in (
        ("route_selection_checkpoint_ref", "artifacts/D3_executability/route_selection_checkpoint.json"),
        ("route_evidence_ref", "artifacts/D3_executability/route_evidence.json"),
        ("evidence_semantics_ref", "artifacts/D3_executability/evidence_semantics.json"),
    ):
        if str(d3_packet.get(ref_key, "")).strip() != expected:
            missing.append({"artifact": f"stage_packet.{ref_key}", "missing_fields": [f"expected_{expected}"]})
    if not missing:
        return []
    return [
        {
            "code": "d3_flat_route_contract_incomplete",
            "message": (
                "D3 is running against flat D1 documented_commands/documented_files, but the new D3 route "
                "checkpoint/evidence/semantics contract is incomplete."
            ),
            "required_action": (
                "Write route_selection_checkpoint.json before execution, route_evidence.json for route attempts, "
                "evidence_semantics.json for fact-first interpretation, and keep stage_packet.json as a compact handoff "
                "with refs to those files."
            ),
            "missing": missing[:16],
            "refs": [
                "artifacts/D1_documentation/readme_workflow.json",
                "artifacts/D3_executability/stage_packet.json",
                "artifacts/D3_executability/route_selection_checkpoint.json",
                "artifacts/D3_executability/route_evidence.json",
                "artifacts/D3_executability/evidence_semantics.json",
            ],
        }
    ]


def _d1_uses_flat_documented_command_schema(d1_workflow: dict[str, Any]) -> bool:
    if not isinstance(d1_workflow, dict):
        return False
    if "route_portfolio" in d1_workflow or "canonical_route_plan" in d1_workflow:
        return False
    return "documented_commands" in d1_workflow and "route_portfolio" not in d1_workflow


def _d5_same_workflow_transfer_errors(d5_packet: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(d5_packet, dict) or not d5_packet:
        return []
    semantics = _d5_transfer_semantics(d5_packet)
    check = _d5_same_workflow_check(d5_packet)
    text = _json_text(d5_packet)
    strong_markers = (
        "successful_core_reuse",
        "successful_transfer",
        "independent_api_or_cli_call",
        "new_project",
        "new_scenario",
        "intended_public_surface",
    )
    claims_strong = any(marker in text for marker in strong_markers)
    same_relationship = str(semantics.get("relationship_to_d3", "")).strip().lower() == "same_workflow_with_new_input"
    same_novelty = str(semantics.get("transfer_novelty", "")).strip().lower() == "same_as_d3_variant"
    only_tweak = any(
        check.get(key) is True
        for key in (
            "only_parameter_or_config_change",
            "only_path_or_relocation_change",
            "only_same_task_new_input",
        )
    )
    reviewer_context = check.get("reviewer_owned_new_context")
    if isinstance(reviewer_context, str):
        reviewer_context_missing = reviewer_context.strip().lower() in {"", "no", "false", "none", "unknown"}
    else:
        reviewer_context_missing = reviewer_context is False or reviewer_context is None
    if not (claims_strong and (same_relationship or same_novelty or only_tweak or reviewer_context_missing)):
        return []
    return [
        {
            "code": "d5_same_workflow_variant_claimed_as_transfer",
            "message": (
                "D5 appears to present a same-workflow variant or parameter/config/path/input tweak as independent reuse. "
                "Reusable evidence must show artifact code/API/module/CLI/data contract used in a genuinely new reviewer-owned context."
            ),
            "required_action": (
                "Revise D5 d5_transfer_sufficiency_check and same_workflow_variant_check facts. If validation only changed "
                "parameters, config, seed, path, working directory, benchmark subset, or same-task input, classify it as "
                "same_as_d3_variant or same_workflow_with_new_input. Otherwise name the artifact-owned surface, "
                "reviewer-owned context, and output refs."
            ),
            "refs": ["artifacts/D5_reusability/stage_packet.json", "artifacts/D5_reusability/reuse_plan.json"],
        }
    ]


def _merged_d5_transfer_payload(stage_packet: dict[str, Any], reuse_plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(stage_packet, dict):
        stage_packet = {}
    if not isinstance(reuse_plan, dict):
        reuse_plan = {}
    merged = dict(reuse_plan)
    merged.update(stage_packet)

    plan_semantics = _d5_transfer_semantics(reuse_plan)
    stage_semantics = _d5_transfer_semantics(stage_packet)
    if isinstance(plan_semantics, dict) or isinstance(stage_semantics, dict):
        semantics: dict[str, Any] = {}
        if isinstance(plan_semantics, dict):
            semantics.update(plan_semantics)
        if isinstance(stage_semantics, dict):
            semantics.update(stage_semantics)
        merged["transfer_sufficiency_semantics"] = semantics

    check: dict[str, Any] = {}
    plan_check = _d5_same_workflow_check(reuse_plan)
    stage_check = _d5_same_workflow_check(stage_packet)
    if plan_check:
        check.update(plan_check)
    if stage_check:
        check.update(stage_check)
    if check:
        merged["same_workflow_variant_check"] = check
    return merged


def _d5_transfer_semantics(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key in ("d5_transfer_sufficiency_check", "transfer_sufficiency_semantics", "classifications"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _d5_same_workflow_check(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    value = payload.get("same_workflow_variant_check")
    if isinstance(value, dict):
        return value
    d5_check = payload.get("d5_transfer_sufficiency_check")
    if isinstance(d5_check, dict) and isinstance(d5_check.get("same_workflow_variant_check"), dict):
        return d5_check["same_workflow_variant_check"]
    handoff = payload.get("handoff")
    if isinstance(handoff, dict) and isinstance(handoff.get("same_workflow_variant_check"), dict):
        return handoff["same_workflow_variant_check"]
    return {}


def _ranked_d3_routes(d1_workflow: dict[str, Any]) -> list[dict[str, Any]]:
    routes = d1_workflow.get("route_portfolio", []) if isinstance(d1_workflow, dict) else []
    if not isinstance(routes, list):
        return []
    result: list[dict[str, Any]] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        relevance = _stage_relevance(route)
        if relevance and not (relevance & D3_RELEVANCE):
            continue
        eligibility = str(route.get("d3_eligibility", "")).strip().lower()
        role = str(route.get("evidence_role", "")).strip().lower()
        if eligibility in SUPPORTING_D3_ELIGIBILITY or role in SUPPORTING_D3_ROLES:
            continue
        rank = route.get("rank")
        if not isinstance(rank, int):
            rank = 9999
        route_id = str(route.get("route_id", "")).strip()
        if not route_id:
            continue
        result.append(
            {
                "rank": rank,
                "route_id": route_id,
                "evidence_role": role,
                "d3_eligibility": eligibility,
                "source": str(route.get("source", "")).strip(),
            }
        )
    return sorted(result, key=lambda item: (int(item.get("rank", 9999)), str(item.get("route_id", ""))))


def _selected_d3_rank(d3_packet: dict[str, Any], ranked: list[dict[str, Any]]) -> int | None:
    text = _json_text(d3_packet)
    selected = d3_packet.get("selected_workflow")
    selected_text = _json_text(selected) if isinstance(selected, dict) else ""
    for route in ranked:
        route_id = str(route.get("route_id", "")).strip().lower()
        if not route_id:
            continue
        if route_id in selected_text and ("selected" in selected_text or "route_id" in selected_text):
            return int(route.get("rank", 9999))
    for route in ranked:
        route_id = str(route.get("route_id", "")).strip().lower()
        if route_id and route_id in text and "selected_for_d3" in text:
            return int(route.get("rank", 9999))
    return None


def _d3_route_accounted(route: dict[str, Any], ranked_execution: list[Any], stage_text: str) -> bool:
    route_id = str(route.get("route_id", "")).strip().lower()
    if not route_id:
        return False
    status_markers = (
        "attempted_succeeded",
        "attempted_limited_progress",
        "attempted_failed",
        "not_attempted_resource_gated",
        "not_attempted_time_gated",
        "not_attempted_missing_input",
        "not_reviewer_realistic",
        "supporting_only_not_selected",
        "not_attempted_lower_priority",
    )
    for item in ranked_execution:
        if not isinstance(item, dict):
            continue
        item_text = _json_text(item)
        if route_id in item_text and any(marker in item_text for marker in status_markers):
            return True
    return route_id in stage_text and any(marker in stage_text for marker in status_markers)


def _stage_success_is_weak_or_supporting(stage_packet: dict[str, Any], phase_name: str) -> bool:
    if not isinstance(stage_packet, dict):
        return False
    text = _json_text(stage_packet)
    weak_markers = (
        "limited_surface_only",
        "setup_or_component_evidence",
        "component_only",
        "component only",
        "component-level",
        "component level",
        "helper",
        "generic_dependency_or_example",
        "result_validation_only",
        "post_processing_only",
        "validated_precomputed_output",
        "derived_from_existing_results",
        "dry_run_no_artifact_output",
        "alternative_exists",
        "unclear",
    )
    if any(marker in text for marker in weak_markers):
        return True
    if phase_name == "D3_executability":
        semantics = stage_packet.get("evidence_semantics", {})
        if isinstance(semantics, dict):
            if str(semantics.get("representativeness_boundary", "")).strip():
                return True
            if str(semantics.get("paper_facing_output", "")).strip().lower() in {"no", "unknown"}:
                return True
            if str(semantics.get("artifact_specific_logic_exercised", "")).strip().lower() in {"no", "unknown"}:
                return True
    if phase_name == "D5_reusability":
        semantics = _d5_transfer_semantics(stage_packet)
        if isinstance(semantics, dict):
            surface = str(semantics.get("validated_reuse_surface", "")).strip().lower()
            if surface in {"component_or_helper_surface", "unclear_surface", "no_validated_surface"}:
                return True
    return False


def _d1_route_surface_errors(candidates: list[dict[str, Any]], d1_workflow: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(d1_workflow, dict) or not d1_workflow:
        return []
    text = _json_text(d1_workflow)
    uncovered = [item for item in candidates if not _candidate_is_mentioned(item, text)]
    if not uncovered:
        return []
    return [
        {
            "code": "visible_demo_entry_missing_from_d1_route_portfolio",
            "message": (
                "D1 found artifact-visible demo/example/mock entry points, but readme_workflow.json does not "
                "include or explicitly dismiss them in route_portfolio/demo_entry_grounding."
            ),
            "required_action": (
                "Read the visible entry point enough to classify it. Add a route_portfolio entry or a concise "
                "dismissal explaining whether it exercises paper-facing artifact logic, is setup/component evidence, or is infeasible."
            ),
            "candidate_entries": uncovered[:8],
            "refs": ["artifacts/D1_documentation/readme_workflow.json"],
        }
    ]


def _d3_route_surface_errors(candidates: list[dict[str, Any]], d1_workflow: dict[str, Any], d3_packet: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(d3_packet, dict) or not d3_packet:
        return []
    route_text = _json_text(d1_workflow) + "\n" + _json_text(d3_packet)
    surfaced = [item for item in candidates if _candidate_is_mentioned(item, route_text)]
    if not surfaced:
        return []
    unexplained = [item for item in surfaced if not _candidate_has_d3_status(item, d3_packet)]
    if not unexplained:
        return []
    return [
        {
            "code": "visible_demo_entry_missing_d3_arbitration_status",
            "message": (
                "D3 has visible demo/example/mock route candidates from the artifact surface, but the D3 packet "
                "does not record whether they were attempted, skipped with a reason, or infeasible."
            ),
            "required_action": (
                "Update the D3 route portfolio/status so each surfaced bounded candidate has an attempt_status, "
                "failure_or_skip_cause, and route_result_interpretation. Attempt it when it is reviewer-realistic "
                "and could exercise paper-facing artifact logic after the primary route fails or is resource-gated."
            ),
            "candidate_entries": unexplained[:8],
            "refs": [
                "artifacts/D1_documentation/readme_workflow.json",
                "artifacts/D3_executability/stage_packet.json",
            ],
        }
    ]


def _discover_surface_candidates(repo_root: Path) -> list[dict[str, Any]]:
    root = repo_root.resolve()
    candidates: list[dict[str, Any]] = []
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        rel_current = relpath_str(current_path, root)
        depth = 0 if rel_current == "." else len(Path(rel_current).parts)
        dirs[:] = [item for item in dirs if item not in SKIP_DIR_PARTS and depth < MAX_SCAN_DEPTH]
        for name in files:
            if len(candidates) >= 40:
                return sorted(candidates, key=lambda item: (item["surface_kind"], item["path"]))
            path = current_path / name
            rel = relpath_str(path, root)
            parts = set(Path(rel).parts)
            if parts & SKIP_DIR_PARTS:
                continue
            if path.suffix and path.suffix not in ENTRY_SUFFIXES:
                continue
            if not ENTRY_NAME_RE.search(path.name):
                continue
            if not _looks_like_callable_entry(path):
                continue
            candidates.append(
                {
                    "path": rel,
                    "name": path.name,
                    "entry_key": _entry_key(path),
                    "surface_kind": _surface_kind(path),
                }
            )
    return sorted(candidates, key=lambda item: (item["surface_kind"], item["path"]))


def _entry_key(path: Path) -> str:
    name = path.name
    if path.suffix in {".java", ".scala", ".kt", ".py", ".js", ".ts", ".sh", ".R", ".jl", ".ipynb"}:
        return path.stem
    return name


def _surface_kind(path: Path) -> str:
    parts = path.parts
    if path.suffix in {".java", ".scala", ".kt", ".py", ".js", ".ts", ".sh", ".R", ".jl"}:
        return "source_entry"
    if path.suffix == ".ipynb":
        return "notebook_entry"
    return "artifact_entry"


def _looks_like_callable_entry(path: Path) -> bool:
    suffix = path.suffix
    if suffix in {".py", ".sh", ".R", ".jl", ".js", ".ts", ".ipynb", ".jar"}:
        return True
    if suffix in {".java", ".scala", ".kt"}:
        text = _read_text_prefix(path)
        lowered = text.lower()
        return "public static void main" in lowered or "def main(" in lowered or "@main" in lowered
    return False


def _read_text_prefix(path: Path, max_chars: int = 200_000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(max_chars)
    except Exception:
        return ""


def _candidate_is_mentioned(candidate: dict[str, Any], text: str) -> bool:
    lowered = text.lower()
    keys = {
        str(candidate.get("path", "")),
        str(candidate.get("name", "")),
        str(candidate.get("entry_key", "")),
    }
    for key in keys:
        normalized = key.strip().lower()
        if normalized and normalized in lowered:
            return True
    return False


def _candidate_has_d3_status(candidate: dict[str, Any], d3_packet: Any) -> bool:
    return _candidate_has_status_in_node(candidate, d3_packet, depth=0)


def _candidate_has_status_in_node(candidate: dict[str, Any], node: Any, *, depth: int) -> bool:
    if isinstance(node, dict):
        node_text = _json_text(node)
        if depth > 0 and _candidate_is_mentioned(candidate, node_text) and _has_d3_status_marker(node_text):
            return True
        return any(_candidate_has_status_in_node(candidate, value, depth=depth + 1) for value in node.values())
    if isinstance(node, list):
        return any(_candidate_has_status_in_node(candidate, value, depth=depth + 1) for value in node)
    return False


def _has_d3_status_marker(lowered_text: str) -> bool:
    lowered = lowered_text.lower()
    status_markers = (
        "attempt_status",
        "attempted_",
        "not_attempted_",
        "limited_surface_only",
        "setup_or_component_evidence",
        "infeasible",
        "resource_gated",
        "missing_input",
        "lower_priority",
        "route_result_interpretation",
        "failure_or_skip_cause",
    )
    return any(marker in lowered for marker in status_markers)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_text(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False).lower()
    except Exception:
        return str(payload).lower()
