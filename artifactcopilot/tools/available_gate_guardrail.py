from __future__ import annotations

import json
from pathlib import Path
from typing import Any


WEAK_ENTRY_SOURCE_TYPES = {
    "source_discovered_cli",
    "source_tree_only",
    "placeholder_or_error_page",
    "none",
}

WEAK_ENTRY_PHRASES = (
    "readme is sparse",
    "readme.md is sparse",
    "readme is minimal",
    "readme.md is minimal",
    "readme only",
    "only a title",
    "title-only",
    "only '#",
    "only `#",
    "placeholder",
    "502 bad gateway",
    "source directories",
    "source tree",
)

STATIC_REPORT_PHRASES = (
    "static vulnerability-report",
    "static vulnerability report",
    "html report",
    "vulnerability entries",
    "static result page",
    "static result report",
    "result table",
    "report table",
)

PASSIVE_BENCHMARK_ONLY_PHRASES = (
    "contains only the",
    "repository contains only",
    "artifact repository contains only",
    "dataset-only",
    "dataset only",
    "data file only",
    "only the adult dataset",
    "only the benchmark",
    "only a benchmark",
    "only a data file",
)

MISSING_ARTIFACT_SURFACE_PHRASES = (
    "does not contain the",
    "does not contain source code",
    "does not contain the source code",
    "does not contain models",
    "does not contain evaluation scripts",
    "does not contain the evaluation",
    "no executable tool",
    "no source code",
    "no scripts",
    "no api",
    "no cli",
    "no setup instructions",
    "no evaluation workflow",
)

REVIEWER_ACTION_VERBS = (
    "run",
    "execute",
    "load",
    "open notebook",
    "validate",
    "reproduce",
    "evaluate",
    "install",
    "build",
    "test",
    "script",
    "workflow",
    "notebook",
    "workbook",
    "extract",
    "aggregate",
)

SOURCE_DISCOVERY_PHRASES = (
    "argparse",
    "--help",
    "source code",
    "run.py provides",
    "cli with",
    "cli arguments",
    "discovered from code",
    "infer the workflow from code",
)


def available_gate_finish_errors(run_root: Path, phase_name: str) -> list[dict[str, Any]]:
    if phase_name not in {"available_gate", "final_scoring"}:
        return []
    packet = _read_json(run_root / "artifacts" / "available_gate" / "stage_packet.json")
    if not packet:
        return []
    if phase_name == "available_gate":
        available_claimed = _available_stage_claims_pass(packet)
    else:
        final_packet = _read_json(run_root / "artifacts" / "final_scoring" / "final_scoring_packet.json")
        available_claimed = _final_packet_claims_available(final_packet)
    if not available_claimed:
        return []
    if not (
        _gc2_is_weak_or_source_discovered(packet)
        or _gc2_is_static_report_only(packet)
        or _gc2_is_passive_benchmark_only(packet)
    ):
        return []
    return [
        {
            "code": "gc2_weak_source_or_static_report_claimed_available",
            "message": (
                "Available GC2 is claimed, but the available_gate stage packet bases the review entry on "
                "a weak/identity-only entry document, source-discovered CLI, argparse/help text, source tree, "
                "placeholder/error material, or a static result/report page with no reviewer-usable evaluation route. "
                "GC2 requires artifact-authored entry material that tells a reviewer how to obtain, inspect, run, "
                "load, check, or evaluate the artifact."
            ),
            "required_action": (
                "Rewrite artifacts/available_gate/stage_packet.json so gc2_entry_document_assessment.decision is "
                "`fail`, or cite a separate artifact-authored document/index/notebook/result-package description with "
                "a concrete reviewer_action_from_entry. Do not pass GC2 from run.py/argparse/source tree or a static "
                "report page alone."
            ),
            "refs": ["artifacts/available_gate/stage_packet.json"],
        }
    ]


def _gc2_is_weak_or_source_discovered(packet: dict[str, Any]) -> bool:
    assessment = _gc2_entry_assessment(packet)
    if assessment:
        decision = str(assessment.get("decision", "")).strip().lower()
        entry_source_type = str(assessment.get("entry_source_type", "")).strip().lower()
        reviewer_action = str(assessment.get("reviewer_action_from_entry", "")).strip()
        if entry_source_type in WEAK_ENTRY_SOURCE_TYPES and decision in {"pass", "passed", "available", "yes", "true"}:
            return True
        if decision in {"pass", "passed", "available", "yes", "true"} and not reviewer_action:
            text = _json_text(assessment)
            return _text_has_weak_gc2_pattern(text)
        return False
    text = _json_text(packet)
    return _text_has_weak_gc2_pattern(text)


def _gc2_is_static_report_only(packet: dict[str, Any]) -> bool:
    assessment = _gc2_entry_assessment(packet)
    entry_source_type = ""
    reviewer_action = ""
    decision = ""
    if assessment:
        entry_source_type = str(assessment.get("entry_source_type", "")).strip().lower()
        reviewer_action = str(assessment.get("reviewer_action_from_entry", "")).strip().lower()
        decision = str(assessment.get("decision", "")).strip().lower()
    text = _json_text(packet)
    if decision not in {"pass", "passed", "available", "yes", "true"}:
        return False
    if entry_source_type in {"dataset_or_result_index", "notebook_or_workbook", "artifact_index"}:
        return False
    if not any(phrase in text for phrase in STATIC_REPORT_PHRASES):
        return False
    if any(phrase in text for phrase in MISSING_ARTIFACT_SURFACE_PHRASES):
        return True
    if not reviewer_action:
        return True
    return not any(verb in reviewer_action for verb in REVIEWER_ACTION_VERBS)


def _gc2_is_passive_benchmark_only(packet: dict[str, Any]) -> bool:
    assessment = _gc2_entry_assessment(packet)
    if not assessment:
        return False
    decision = str(assessment.get("decision", "")).strip().lower()
    if decision not in {"pass", "passed", "available", "yes", "true"}:
        return False
    entry_source_type = str(assessment.get("entry_source_type", "")).strip().lower()
    if entry_source_type not in {"dataset_or_result_index", "artifact_document", "artifact_index"}:
        return False
    text = _json_text(packet)
    passive_material = any(phrase in text for phrase in PASSIVE_BENCHMARK_ONLY_PHRASES)
    missing_surface = any(phrase in text for phrase in MISSING_ARTIFACT_SURFACE_PHRASES)
    paper_or_benchmark_only = any(
        phrase in text
        for phrase in (
            "paper explicitly uses",
            "used in the paper",
            "evaluation benchmark",
            "benchmark used in paper",
            "original url",
            "uci archive",
        )
    )
    declared_review_surface = any(
        phrase in text
        for phrase in (
            "artifact-authored entry material declares",
            "readme describes an evaluation workflow",
            "readme describes each component and its purpose",
            "notebook is the primary executable component",
            "artifact presents the files as the review surface",
            "declared review surface",
            "reviewable artifact surface",
        )
    )
    return passive_material and missing_surface and paper_or_benchmark_only and not declared_review_surface


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


def _text_has_weak_gc2_pattern(text: str) -> bool:
    lowered = text.lower()
    claims_gc2_pass = any(
        phrase in lowered
        for phrase in (
            "gc2: pass",
            "gc2 pass",
            "gc2 = pass",
            "gc2 passes",
            "usable evaluation entry point",
            "available gate conditions are met",
            "available gate conditions (gc1-gc3) are met",
        )
    )
    if not claims_gc2_pass:
        return False
    has_weak_entry = any(phrase in lowered for phrase in WEAK_ENTRY_PHRASES)
    has_source_discovery = any(phrase in lowered for phrase in SOURCE_DISCOVERY_PHRASES)
    return has_weak_entry and has_source_discovery


def _available_stage_claims_pass(packet: dict[str, Any]) -> bool:
    status = str(packet.get("status", packet.get("decision", packet.get("overall", "")))).strip().lower()
    if status in {"pass", "passed", "available", "pass_with_limitation", "passed_with_limitation"}:
        return True
    available_decision = str(packet.get("available_decision", "")).strip().lower()
    if available_decision in {"award available", "available", "pass", "passed"}:
        return True
    if "award available" in available_decision:
        return True
    for key in ("GC1", "GC2", "GC3"):
        item = packet.get(key)
        if not isinstance(item, dict):
            break
        decision = str(item.get("decision", "")).strip().lower()
        if decision not in {"pass", "passed", "available", "yes", "true"}:
            break
    else:
        return True
    summary = str(packet.get("summary", "")).strip().lower()
    return "gc1" in summary and "gc2" in summary and "gc3" in summary and "pass" in summary


def _final_packet_claims_available(packet: dict[str, Any]) -> bool:
    if not isinstance(packet, dict) or not packet:
        return False
    available = packet.get("available_gate")
    if isinstance(available, dict):
        if isinstance(available.get("holds"), bool):
            return bool(available.get("holds"))
        if isinstance(available.get("available"), bool):
            return bool(available.get("available"))
        status = str(available.get("status", available.get("decision", ""))).strip().lower()
        if status in {"pass", "passed", "available", "yes", "true"}:
            return True
    badge = packet.get("badge_recommendation")
    if isinstance(badge, dict):
        if bool(badge.get("available")):
            return True
        badge = badge.get("badge")
    badge_text = str(badge or "").strip().lower()
    return badge_text in {"available", "available + functional", "available + functional + reusable"}


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
        return str(payload or "").lower()
