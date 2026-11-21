# Rubric V3 Schema Draft

This file records the first implementation-facing schema plan for Rubric V3.

The source of truth for the rubric remains:

```text
artifactguide/rubric_overview.md
artifactguide/README.md
```

## Core Types

```text
ChecklistValue = yes | no | not_applicable
DimensionScore = R | WR | WA | A
EvidenceTier = E0 | E1 | E2 | E3
InterventionLevel = I0 | I1 | I2 | I3 | I4
Confidence = high | medium | low
GateStatus = pass | fail | not_determined
```

## Evidence Item

```json
{
  "evidence_id": "ev_001",
  "source_type": "repo_file",
  "source_path": "README.md",
  "summary": "...",
  "content_ref": "...",
  "used_by": ["D1"]
}
```

## Intervention Record

```json
{
  "intervention_id": "int_001",
  "level": "I2",
  "summary": "...",
  "reason": "...",
  "files_changed": ["requirements.txt"],
  "patch_path": "interventions/int_001.patch",
  "semantic_preserving_rationale": "...",
  "effect": "...",
  "badge_relevance": "environment_only"
}
```

## Paper Target

```json
{
  "target_id": "pt_001",
  "source": "manual",
  "target_type": "table",
  "paper_location": "Section 5, Table 2",
  "description": "...",
  "expected_result_object": "..."
}
```

## Workflow Candidate

```json
{
  "workflow_id": "wf_001",
  "entry_source": "README.md",
  "commands": ["..."],
  "rw_checks": {
    "RW1": "yes",
    "RW2": "yes",
    "RW3": "unknown",
    "RW4": "unknown",
    "RW5": "unknown"
  },
  "representative_status": "candidate",
  "coverage_boundary": "..."
}
```

## Dimension Judgment

```json
{
  "dimension": "D3",
  "score": "WA",
  "checklist": {
    "D3.1": "yes",
    "D3.2": "yes",
    "D3.3": "yes",
    "D3.4": "yes",
    "D3.5": "yes",
    "D3.6": "yes",
    "D3.7": "no"
  },
  "evidence_refs": ["ev_010", "ev_011"],
  "limitations": ["not a full reproduction"],
  "confidence": "medium",
  "rationale": "..."
}
```
