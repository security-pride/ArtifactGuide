from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


PRECOMPUTED_WORKFLOWS = {
    "dataset_loading_or_aggregation",
    "result_validation_only",
    "post_processing_only",
}

PRECOMPUTED_PROVENANCE = {
    "loaded_from_deposited_files",
    "validated_precomputed_output",
    "derived_from_existing_results",
}

ACTIVE_ARTIFACT_MARKERS = (
    "tool",
    "tool_repo",
    "model",
    "model_repo",
    "pipeline",
    "pipeline_repo",
    "cli",
    "compiler",
    "analyzer",
    "fuzzer",
    "simulator",
)

PRIMARY_UNVALIDATED_MARKERS = (
    "primary reusable surface",
    "primary public surface",
    "primary tool surface",
    "intended reusable surface",
    "intended public surface",
    "broader public surface",
    "broader reusable surface",
    "full pipeline",
    "primary pipeline",
    "main pipeline",
    "live tool",
    "model inference",
    "training pipeline",
    "evaluation pipeline",
    "automatic pipeline",
    "detection/ocr",
    "detector/ocr",
    "eth1",
    "eth2",
    "firmware",
    "edk2",
    "proxyex",
)

UNVALIDATED_MARKERS = (
    "unvalidated",
    "not validated",
    "not independently validated",
    "not attempted",
    "not run",
    "not executed",
    "could not be validated",
    "remains gated",
    "remains unvalidated",
    "blocked",
    "failed",
    "gated",
)

PRIMARY_SURFACE_EXCEPTION_MARKERS = (
    "validates_intended_surface",
    "validates the intended",
    "validates the public",
    "validates the declared",
    "primary reuse surface",
    "primary reusable surface is",
    "primary documented reuse path",
    "documented reuse surface",
    "documented reuse path",
    "intended reusable object",
    "intended reusable surface is",
    "public reusable surface is",
    "declared reusable surface",
    "first-class api",
    "first class api",
    "plug-and-play",
    "plug and play",
    "for others",
    "no broader public surface",
    "no broader reusable surface",
    "no higher-level tool",
    "no higher level tool",
)

WEAK_BOUNDED_ROUTE_MARKERS = (
    "toy input",
    "toy example",
    "trivial c program",
    "trivial program",
    "synthetic c file",
    "single-file",
    "single file",
    "small 80-byte",
    "80-byte",
    "minimal input",
    "smoke",
    "dry run",
)


def build_evidence_contract(run_root: Path, final_packet: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build immutable-ish evidence facts from stage packets and final packet.

    This is not a replacement for reviewer judgment. It is a compact contract
    for the final scorer: stage-level evidence boundaries must be preserved
    unless a later packet has genuinely stronger evidence, not just prose.
    """

    run_root = Path(run_root)
    final_packet = final_packet if isinstance(final_packet, dict) else {}
    available_stage = _read_json(run_root / "artifacts" / "available_gate" / "stage_packet.json")
    d3_stage = _read_json(run_root / "artifacts" / "D3_executability" / "stage_packet.json")
    d4_stage = _read_json(run_root / "artifacts" / "D4_claim_support" / "stage_packet.json")
    d5_stage = _read_json(run_root / "artifacts" / "D5_reusability" / "stage_packet.json")

    e2 = final_packet.get("e2_sufficiency_check", {}) if isinstance(final_packet, dict) else {}
    d5 = final_packet.get("d5_transfer_sufficiency_check", {}) if isinstance(final_packet, dict) else {}
    adjudication = final_packet.get("rubric_evidence_adjudication", {}) if isinstance(final_packet, dict) else {}

    d3_semantics = d3_stage.get("evidence_semantics", {}) if isinstance(d3_stage, dict) else {}
    if not isinstance(d3_semantics, dict):
        d3_semantics = {}
    d4_repr = d4_stage.get("representativeness_for_claims", {}) if isinstance(d4_stage, dict) else {}
    if not isinstance(d4_repr, dict):
        d4_repr = {}
    d5_semantics = _d5_stage_transfer_semantics(d5_stage)
    if not isinstance(d5_semantics, dict):
        d5_semantics = {}
    strongest_reusable = _dict_field(adjudication, "strongest_reusable_evidence")

    final_text = _json_text(final_packet).lower()
    stage_text = "\n".join(_json_text(item).lower() for item in (available_stage, d3_stage, d4_stage, d5_stage))
    combined_text = final_text + "\n" + stage_text

    workflow_kind = _first_nonempty(
        _lower(e2.get("workflow_kind") if isinstance(e2, dict) else ""),
        _lower(d3_semantics.get("workflow_kind")),
    )
    output_provenance = _first_nonempty(
        _lower(e2.get("output_provenance") if isinstance(e2, dict) else ""),
        _lower(d3_semantics.get("output_provenance")),
    )
    artifact_type = _first_nonempty(
        _lower(e2.get("declared_artifact_type") if isinstance(e2, dict) else ""),
        _lower(d3_semantics.get("declared_artifact_type")),
        _infer_artifact_type_from_text(combined_text),
    )

    structured_dataset_primary = _structured_dataset_or_result_primary(e2, d5, adjudication)
    result_validation_functional_surface = _result_validation_functional_surface(e2, d3_semantics, d4_repr)
    primary_surface_validated = _structured_primary_surface_validated(d5, adjudication) or _primary_surface_explicitly_validated(final_text)
    active_surface_unvalidated = _active_surface_unvalidated(combined_text)
    if primary_surface_validated or structured_dataset_primary:
        active_surface_unvalidated = False

    contract = {
        "schema": "ae_evidence_contract_v1",
        "available": {
            "gc2_static_report_only": _gc2_static_report_only(available_stage),
            "entry_source_type": _entry_source_type(available_stage),
        },
        "artifact": {
            "declared_type": artifact_type,
            "active_tool_like": _is_active_tool_like(artifact_type, combined_text),
            "dataset_or_result_primary": structured_dataset_primary or _dataset_or_result_primary(combined_text, artifact_type),
            "higher_level_reuse_surface_present": _higher_level_reuse_surface_present(combined_text),
        },
        "functional": {
            "workflow_kind": workflow_kind,
            "output_provenance": output_provenance,
            "resource_boundary_position": _first_nonempty(
                _lower((e2 or {}).get("resource_boundary_position") if isinstance(e2, dict) else ""),
                _lower(d3_semantics.get("resource_boundary_position")),
            ),
            "precomputed_validation": workflow_kind in PRECOMPUTED_WORKFLOWS
            and output_provenance in PRECOMPUTED_PROVENANCE,
            "component_only": _truthy((e2 or {}).get("component_only")) or _truthy(d3_semantics.get("component_only")),
            "generic_dependency_or_example": _truthy((e2 or {}).get("generic_dependency_or_example"))
            or _truthy(d3_semantics.get("generic_dependency_or_example")),
            "paper_facing_output": _first_nonempty(
                _lower((e2 or {}).get("paper_facing_output") if isinstance(e2, dict) else ""),
                _lower(d3_semantics.get("paper_facing_output")),
            ),
            "artifact_specific_logic": _first_nonempty(
                _lower((e2 or {}).get("artifact_specific_logic_exercised") if isinstance(e2, dict) else ""),
                _lower(d3_semantics.get("artifact_specific_logic_exercised")),
            ),
            "single_file_or_smoke_only": _single_file_or_smoke_only(combined_text),
            "weak_bounded_route": _weak_bounded_route(combined_text),
            "stage_says_not_artifact_level": _stage_says_not_artifact_level(stage_text),
            "active_surface_unvalidated": active_surface_unvalidated,
            "result_validation_functional_surface": result_validation_functional_surface,
        },
        "reusable": {
            "stage_artifact_level_sufficiency": _sufficiency_value(d5_semantics.get("artifact_level_sufficiency")),
            "final_artifact_level_sufficiency": _sufficiency_value((d5 or {}).get("artifact_level_sufficiency") if isinstance(d5, dict) else ""),
            "reuse_target_level": _first_nonempty(
                _lower((d5 or {}).get("reuse_target_level") if isinstance(d5, dict) else ""),
                _lower(d5_semantics.get("reuse_target_level")),
            ),
            "transfer_novelty": _first_nonempty(
                _lower((d5 or {}).get("transfer_novelty") if isinstance(d5, dict) else ""),
                _lower(d5_semantics.get("transfer_novelty")),
            ),
            "relationship_to_d3": _first_nonempty(
                _lower((d5 or {}).get("relationship_to_d3") if isinstance(d5, dict) else ""),
                _lower(d5_semantics.get("relationship_to_d3")),
            ),
            "strongest_transfer_strength": _lower(strongest_reusable.get("transfer_strength")),
            "strongest_evidence_type": _lower(strongest_reusable.get("evidence_type")),
            "surface_exception_markers": _surface_exception_markers(final_text),
            "active_surface_unvalidated": active_surface_unvalidated,
            "primary_surface_explicitly_validated": primary_surface_validated,
            "weak_mock_or_demo_transfer": _weak_mock_or_demo_transfer(combined_text, d5, adjudication),
        },
    }
    return contract


def write_evidence_contract(run_root: Path, final_packet: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = build_evidence_contract(run_root, final_packet)
    path = Path(run_root) / "artifacts" / "evidence_contract" / "evidence_contract.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return contract


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _dict_field(payload: Any, key: str) -> dict[str, Any]:
    value = payload.get(key, {}) if isinstance(payload, dict) else {}
    return value if isinstance(value, dict) else {}


def _d5_stage_transfer_semantics(d5_stage: Any) -> dict[str, Any]:
    if not isinstance(d5_stage, dict):
        return {}
    for key in ("d5_transfer_sufficiency_check", "transfer_sufficiency_semantics", "classifications"):
        value = d5_stage.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value or "")


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _first_nonempty(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _lower(value) in {"true", "yes", "y", "1", "component_only"}


def _sufficiency_value(value: Any) -> str:
    text = _lower(value).replace("-", "_").replace(" ", "_")
    if "supporting_only" in text:
        return "supporting_only"
    if "insufficient" in text:
        return "insufficient"
    if "sufficient" in text:
        return "sufficient"
    return ""


def _entry_source_type(packet: dict[str, Any]) -> str:
    assessment = _gc2_entry_assessment(packet)
    if isinstance(assessment, dict):
        return _lower(assessment.get("entry_source_type"))
    return ""


def _gc2_static_report_only(packet: dict[str, Any]) -> bool:
    text = _json_text(packet).lower()
    assessment = _gc2_entry_assessment(packet)
    entry_type = _entry_source_type(packet)
    reviewer_action = _lower(assessment.get("reviewer_action_from_entry") if isinstance(assessment, dict) else "")
    static_report = any(
        marker in text
        for marker in (
            "static vulnerability-report",
            "static vulnerability report",
            "html report",
            "vulnerability entries",
            "static result page",
            "static result report",
            "report table",
        )
    )
    action_is_inspection_only = reviewer_action and not any(
        marker in reviewer_action
        for marker in (
            "run",
            "execute",
            "load",
            "validate",
            "reproduce",
            "evaluate",
            "notebook",
            "script",
            "workflow",
        )
    )
    explicitly_dataset_index = entry_type in {"dataset_or_result_index", "notebook_or_workbook"}
    return static_report and action_is_inspection_only and not explicitly_dataset_index


def _gc2_entry_assessment(packet: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(packet, dict):
        return {}
    direct = packet.get("gc2_entry_document_assessment")
    if isinstance(direct, dict):
        return direct
    gc2 = packet.get("GC2")
    if isinstance(gc2, dict):
        nested = gc2.get("gc2_entry_document_assessment")
        if isinstance(nested, dict):
            return nested
    return {}


def _infer_artifact_type_from_text(text: str) -> str:
    if any(marker in text for marker in ("dataset package", "data package", "result package", "replication package")):
        return "dataset_or_result_package"
    if any(marker in text for marker in ACTIVE_ARTIFACT_MARKERS):
        return "tool_repo"
    return ""


def _is_active_tool_like(artifact_type: str, text: str) -> bool:
    if any(marker in artifact_type for marker in ACTIVE_ARTIFACT_MARKERS):
        return True
    return any(marker in text for marker in ("tool surface", "model pipeline", "primary pipeline", "cli", "fuzzer", "compiler"))


def _dataset_or_result_primary(text: str, artifact_type: str) -> bool:
    if "tool" in artifact_type or "pipeline" in artifact_type or "model" in artifact_type:
        return False
    if _higher_level_reuse_surface_present(text) and not _strong_dataset_primary_declaration(text):
        return False
    primary_markers = (
        "artifact is a dataset",
        "artifact is a result package",
        "dataset package is the artifact",
        "result package is the artifact",
        "deposited files are the intended",
        "deposited public files are the intended",
        "no separate active tool",
        "no active tool/pipeline",
        "no higher-level tool",
    )
    return any(marker in text for marker in primary_markers) or "dataset_or_result_package" in artifact_type


def _higher_level_reuse_surface_present(text: str) -> bool:
    markers = (
        "inference pipeline",
        "api pipeline",
        "documented api pipeline",
        "custom dataset via openai api",
        "run orca for custom dataset",
        "live vulnerability detection",
        "model inference pipeline",
        "trained model and evaluation pipeline",
        "tool/api surface",
        "primary tool surface",
        "full tool",
        "full pipeline",
        "automatic pipeline",
        "option a api",
        "detection/ocr",
        "edk2-specific workflow",
    )
    return any(marker in text for marker in markers)


def _strong_dataset_primary_declaration(text: str) -> bool:
    markers = (
        "artifact is a dataset",
        "artifact is a data package",
        "artifact is a result package",
        "dataset package is the artifact",
        "result package is the artifact",
        "deposited files are the intended",
        "deposited public files are the intended",
        "data files are the artifact",
        "no separate active tool",
        "no active tool/pipeline",
        "no broader tool/pipeline",
        "no higher-level tool",
    )
    return any(marker in text for marker in markers)


def _single_file_or_smoke_only(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "single-file",
            "single file",
            "toy input",
            "synthetic file",
            "synthetic c file",
            "smoke",
            "dry run",
            "0-widget",
            "0 widgets",
        )
    ) and any(
        marker in text
        for marker in (
            "full pipeline",
            "primary workflow",
            "main workflow",
            "edk2",
            "firmware",
            "fuzzing campaign",
            "automatic pipeline",
            "full automatic",
        )
    )


def _stage_says_not_artifact_level(stage_text: str) -> bool:
    return any(
        marker in stage_text
        for marker in (
            "component_only",
            "component-only",
            "component level",
            "component-level",
            "supporting_only",
            "supporting only",
            "helper component",
            "cannot support artifact-level",
            "not sufficient for artifact-level",
            "primary reusable surface",
            "full pipeline",
            "broader artifact",
        )
    )


def _active_surface_unvalidated(text: str) -> bool:
    text = _lower(text)
    if _strong_primary_surface_blocker(text):
        return True
    return any(
        any(marker in sentence for marker in PRIMARY_UNVALIDATED_MARKERS)
        and any(marker in sentence for marker in UNVALIDATED_MARKERS)
        for sentence in _evidence_sentences(text)
    )


def _strong_primary_surface_blocker(text: str) -> bool:
    strong_patterns = (
        "primary reusable surface remains unvalidated",
        "primary public surface remains unvalidated",
        "primary tool surface remains unvalidated",
        "primary tool/api surface remains unvalidated",
        "primary workflow remains unvalidated",
        "primary pipeline remains unvalidated",
        "full pipeline remains unvalidated",
        "primary routes remain blocked",
        "primary documented routes remain blocked",
        "primary documented edk2 routes remain blocked",
        "edk2 source",
        "edk2-specific workflow remains unexecuted",
        "model inference pipeline was not exercised",
        "model inference pipeline could not be validated",
        "trained model and evaluation pipeline",
        "gpu/model-weight gated",
        "bypasses_detection_ocr",
        "bypasses detection/ocr",
        "bypass of gpu-dependent detection/ocr",
        "detection and ocr components are available",
        "detection/ocr components were not exercised",
        "yolo detection and pp-ocr",
    )
    return any(pattern in text for pattern in strong_patterns)


def _evidence_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[.;\n]+", text) if part.strip()]


def _surface_exception_markers(text: str) -> list[str]:
    return [marker for marker in PRIMARY_SURFACE_EXCEPTION_MARKERS if marker in text]


def _primary_surface_explicitly_validated(text: str) -> bool:
    has_exception = bool(_surface_exception_markers(text))
    if not has_exception:
        return False
    if _active_surface_unvalidated(text):
        benign = any(
            marker in text
            for marker in (
                "no broader public surface",
                "no broader reusable surface",
                "no higher-level tool",
                "no separate primary surface",
            )
        )
        first_class_component = any(marker in text for marker in ("plug-and-play", "plug and play", "for others"))
        return benign or first_class_component
    return True


def _structured_dataset_or_result_primary(e2: Any, d5: Any, adjudication: Any) -> bool:
    if not isinstance(d5, dict) or not isinstance(adjudication, dict):
        return False
    sufficiency = _sufficiency_value(d5.get("artifact_level_sufficiency"))
    target = _lower(d5.get("reuse_target_level"))
    reusable = _dict_field(adjudication, "reusable_sufficiency")
    strongest = _dict_field(adjudication, "strongest_reusable_evidence")
    if sufficiency != "sufficient" or not isinstance(reusable, dict) or reusable.get("sufficient") is not True:
        return False
    if target not in {"dataset_package_level", "result_package_level"}:
        return False
    evidence_type = _lower(strongest.get("evidence_type"))
    strength = _lower(strongest.get("transfer_strength"))
    if evidence_type == "dataset_or_result_package_reanalysis" or strength == "dataset_or_result_package":
        return True
    text = _json_text(e2).lower() + "\n" + _json_text(d5).lower() + "\n" + _json_text(adjudication).lower()
    return _strong_dataset_primary_declaration(text) or any(
        marker in text
        for marker in (
            "notebook is the primary",
            "spreadsheet is the primary",
            "workbook is the primary",
            "declared public reusable surface",
            "declared bounded-equivalent surface",
        )
    )


def _result_validation_functional_surface(e2: Any, d3_semantics: Any, d4_repr: Any) -> bool:
    fields: dict[str, Any] = {}
    for source in (e2, d3_semantics):
        if isinstance(source, dict):
            fields.update({key: value for key, value in source.items() if value not in ("", None)})
    workflow = _lower(fields.get("workflow_kind"))
    provenance = _lower(fields.get("output_provenance"))
    if workflow not in PRECOMPUTED_WORKFLOWS or provenance not in PRECOMPUTED_PROVENANCE:
        return False
    if fields.get("result_package_declared_by_artifact") is not True:
        return False
    if _truthy(fields.get("generic_dependency_or_example")):
        return False
    if _lower(fields.get("paper_facing_output")) not in {"yes", "limited"}:
        return False
    if _lower(fields.get("artifact_specific_logic_exercised")) not in {"yes", "limited"}:
        return False
    if _lower(fields.get("resource_boundary_position")) not in {
        "after_valid_bounded_output",
        "after_bounded_artifact_logic",
        "before_artifact_logic",
    }:
        return False
    if isinstance(d4_repr, dict):
        if d4_repr.get("paper_facing_output") is False:
            return False
        if d4_repr.get("generic_dependency_or_example") is True:
            return False
    return True


def _structured_primary_surface_validated(d5: Any, adjudication: Any) -> bool:
    if not isinstance(d5, dict) or not isinstance(adjudication, dict):
        return False
    sufficiency = _sufficiency_value(d5.get("artifact_level_sufficiency"))
    reusable = _dict_field(adjudication, "reusable_sufficiency")
    strongest = _dict_field(adjudication, "strongest_reusable_evidence")
    if sufficiency != "sufficient" or not isinstance(reusable, dict) or reusable.get("sufficient") is not True:
        return False
    if _weak_mock_or_demo_transfer(_json_text(d5).lower() + "\n" + _json_text(adjudication).lower(), d5, adjudication):
        return False
    strength = _lower(strongest.get("transfer_strength"))
    evidence_type = _lower(strongest.get("evidence_type"))
    if strength in {"artifact_surface", "documented_cli_or_api"}:
        return True
    if evidence_type in {"artifact_surface_transfer", "documented_cli_or_api_transfer"}:
        return True
    if strength == "dataset_or_result_package" or evidence_type == "dataset_or_result_package_reanalysis":
        text = _json_text(d5).lower() + "\n" + _json_text(adjudication).lower()
        return _strong_dataset_primary_declaration(text) and not _higher_level_reuse_surface_present(text)
    text = _json_text(d5).lower() + "\n" + _json_text(adjudication).lower()
    return all(
        any(marker in text for marker in group)
        for group in (
            ("intended reusable surface", "public reusable surface", "declared reusable surface"),
            ("selected transfer surface", "validated surface", "transfer validates"),
            ("validates_intended_surface", "validates the intended", "validates the public", "validates the declared"),
        )
    )


def _weak_mock_or_demo_transfer(text: str, d5: Any, adjudication: Any) -> bool:
    relationship = _lower(d5.get("relationship_to_d3") if isinstance(d5, dict) else "")
    target = _lower(d5.get("reuse_target_level") if isinstance(d5, dict) else "")
    strongest = _dict_field(adjudication, "strongest_reusable_evidence")
    transfer_strength = _lower(strongest.get("transfer_strength"))
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


def _weak_bounded_route(text: str) -> bool:
    lowered = _lower(text)
    if not any(marker in lowered for marker in WEAK_BOUNDED_ROUTE_MARKERS):
        return False
    return any(
        marker in lowered
        for marker in (
            "primary workflow",
            "primary route",
            "primary documented workflow",
            "missing sample",
            "missing input",
            "full pipeline",
            "full experiment",
            "main evaluation",
            "main table",
            "main figure",
        )
    )
