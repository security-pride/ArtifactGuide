from __future__ import annotations

import json
import re
from typing import Any

from ..shared.fs import ensure_dir, now_iso, write_json
from .common import dedupe_strings
from .specs import ToolContext, ToolError, ToolResult


def record_intervention(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    payload = normalize_intervention_payload(args)
    if not payload["summary"]:
        raise ToolError("`summary` must be non-empty.")
    payload, path_text = append_intervention_record(context, payload)
    return ToolResult(
        tool_name="record_intervention",
        ok=True,
        summary="Recorded intervention.",
        data={"intervention": payload, "path": path_text},
    )


def normalize_command_intervention(value: Any) -> dict[str, Any]:
    if value is None or value is False:
        return {}
    if value is True:
        value = {
            "kind": "I2",
            "summary": "Reviewer intervention was supplied as a boolean; runtime normalized it to a bounded command intervention.",
            "details": {"normalization_note": "Original run_command intervention value was true."},
        }
    elif isinstance(value, str):
        value = {
            "kind": value,
            "summary": f"Reviewer intervention level supplied as {value}.",
            "details": {"normalization_note": "Original run_command intervention value was a string."},
        }
    elif not isinstance(value, dict):
        value = {
            "kind": "I2",
            "summary": "Reviewer intervention was supplied in a non-object form; runtime normalized it to a bounded command intervention.",
            "details": {"original_type": type(value).__name__, "original_value": str(value)[:200]},
        }
    payload = normalize_intervention_payload(value)
    if payload["kind"] not in {"I1", "I2", "I3", "I4"}:
        payload["details"] = {
            **payload.get("details", {}),
            "normalization_note": f"Original intervention kind was not one of I1-I4: {payload['kind']!r}. Runtime defaulted to I2.",
        }
        payload["kind"] = "I2"
    return payload


def normalize_intervention_payload(args: dict[str, Any]) -> dict[str, Any]:
    details = args.get("details", {})
    if not isinstance(details, dict):
        details = {"value": details}
    summary = _first_nonempty(args, "summary", "reason", "description", "action", "rationale")
    kind = _normalize_intervention_kind(_first_nonempty(args, "kind", "level", "type", "intervention_level"))
    if not summary:
        summary = f"{kind or 'Intervention'} recorded by command context"
    if "semantic_preserving" in args and "semantic_preserving" not in details:
        details["semantic_preserving"] = args.get("semantic_preserving")
    if "rubric_dimensions_affected" in args and "rubric_dimensions_affected" not in details:
        details["rubric_dimensions_affected"] = args.get("rubric_dimensions_affected")
    evidence_refs = args.get("evidence_refs", [])
    if not isinstance(evidence_refs, list):
        evidence_refs = [evidence_refs]
    return {
        "summary": summary,
        "kind": kind,
        "details": details,
        "evidence_refs": dedupe_strings(evidence_refs),
        "recorded_at": str(args.get("recorded_at", "")).strip() or now_iso(),
    }


def _normalize_intervention_kind(value: str) -> str:
    text = str(value or "").strip().upper().replace("LEVEL_", "")
    if not text:
        return ""
    if text in {"1", "2", "3", "4"}:
        return f"I{text}"
    exact = re.search(r"\bI([1-4])\b", text)
    if exact:
        return f"I{exact.group(1)}"
    compact = text.replace("_", "-")
    if compact.startswith("I") and len(compact) >= 2 and compact[1] in {"1", "2", "3", "4"}:
        return f"I{compact[1]}"
    return text


def _first_nonempty(args: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(args.get(key, "")).strip()
        if value:
            return value
    return ""


def append_intervention_record(context: ToolContext, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    normalized = normalize_intervention_payload(payload)
    path = context.run_root / "interventions.jsonl"
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(normalized, ensure_ascii=False) + "\n")
    ledger_path = context.run_root / "artifacts" / "interventions" / "intervention_ledger.json"
    existing: dict[str, Any] = {"schema": "intervention_ledger_v1", "interventions": []}
    if ledger_path.exists():
        try:
            parsed = json.loads(ledger_path.read_text(encoding="utf-8"))
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            existing.update(parsed)
        if not isinstance(existing.get("interventions"), list):
            existing["interventions"] = []
    existing["interventions"].append(normalized)
    existing["updated_at"] = now_iso()
    write_json(ledger_path, existing)
    return normalized, str(path)


def record_blocker(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    payload = {
        "summary": str(args.get("summary", "")).strip(),
        "attribution": str(args.get("attribution", "uncertain")).strip(),
        "details": args.get("details", {}),
        "evidence_refs": args.get("evidence_refs", []),
    }
    if not payload["summary"]:
        raise ToolError("`summary` must be non-empty.")
    path = context.run_root / "blockers.jsonl"
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return ToolResult(
        tool_name="record_blocker",
        ok=True,
        summary="Recorded blocker.",
        data={"blocker": payload, "path": str(path)},
    )
