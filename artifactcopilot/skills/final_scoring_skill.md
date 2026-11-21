# Final Scoring Skill

skill_id: final_scoring_skill
stage: final_scoring
description: Use this skill only in the `final_scoring` phase, after `available_gate` and D1-D5 have written their stage packets, except when runtime explicitly short-circuited after a failed Available gate.

## Mission

Produce the final rubric packet from already collected evidence. This phase does not explore the artifact.

Final scoring has three jobs:

1. Read the full stage packets.
2. Decide rubric predicates from stage evidence.
3. Write `artifacts/final_scoring/final_scoring_packet.json`, then call `phase_finish`.

Do not run commands, repair artifacts, create new evidence, or score from memory. Memory is only a pointer to evidence refs.

## Tools

Use only:

- `read_file`
- `write_file`
- `phase_finish`

## Required Reads

First read these run-root files in one or more `read_file` calls without `max_chars`:

- `artifacts/run_initialization/artifact_universe.json` when present
- `artifacts/available_gate/stage_packet.json`
- `artifacts/D1_documentation/stage_packet.json`
- `artifacts/D2_environment/stage_packet.json`
- `artifacts/D3_executability/stage_packet.json`
- `artifacts/D3_executability/route_evidence.json`
- `artifacts/D3_executability/evidence_semantics.json`
- `artifacts/D4_claim_support/stage_packet.json`
- `artifacts/D5_reusability/stage_packet.json`
- `artifacts/D5_reusability/reuse_plan.json` when present
- `artifacts/interventions/intervention_ledger.json`

If runtime short-circuited after `available_gate` because GC1, GC2, or GC3 explicitly failed, D1-D5 stage packets may be absent by design. In that case, do not infer D1-D5 from repository contents. Write a no-badge final packet from the Available gate evidence only, mark D1-D5 as skipped, set Functional and Reusable false, and cite the failed GC condition.

If any required stage packet is returned as `read_status=preview`, read it again without `max_chars` before scoring. Do not score from preview, stage summaries, memory, or handoff text alone.

Read referenced logs or outputs only when the full stage packets do not contain enough evidence to decide a named rubric item.

## Decision Workflow

The injected rubric is the scoring authority. Use this fixed workflow:

1. Decide Available from GC1-GC3.
2. Decide D1-D5 checklist items as `yes`, `no`, or `not_applicable`.
3. Map each dimension to `R`, `WR`, `WA`, or `A` using the rubric rows.
4. Decide evidence tier from E0-E3.
5. Decide Functional predicates.
6. Decide Reusable predicates.
7. Let runtime derive the badge from `available_gate.holds`, `functional_decision.holds`, and `reusable_decision.holds`.

Runtime overwrites `badge_recommendation`. Your packet still needs the three gate booleans and evidence for each condition.

## Available Gate

Available follows GC1-GC3 as a short-circuit AND gate. GC1, GC2, and GC3 must all be satisfied. If any one of GC1, GC2, or GC3 is false, `available_gate.holds` is false, the final badge is `no badge`, and Functional/Reusable remain false because their Available prerequisite fails. Do not continue re-arguing Available from material relevance, source-code presence, or paper connection after a GC failure.

You may lower an overclaimed Available gate when stage evidence is weak, but do not raise GC2 above artifact-authored entry evidence.

If `available_gate` says GC2 failed and the entry source is `source_tree_only`, `none`, `placeholder_or_error_page`, or `source_discovered_cli`, GC2 remains false or uncertain unless another artifact-authored entry document, index, notebook, or result-package description is cited. Source code existence, paper-to-source mapping, reviewer-discovered CLI commands, help output, or a paper-only summary are not enough by themselves.

## Functional Gate

Functional holds only when all rubric Functional conditions hold:

- Available holds.
- D2 >= WA.
- D3 >= WA.
- D4 >= WA.
- A representative workflow satisfies RW1-RW5.
- Evidence tier is at least E2.
- The successful path does not depend on I4.

Functional is based on D2-D4 execution and claim-support evidence, not on whether D5 transfer succeeded.

Before deciding D3, D4, E2, or Functional, copy the D3/D4 structured facts into the final packet:

- From D3 `artifacts/D3_executability/evidence_semantics.json` first, falling back to `stage_packet.evidence_semantics`: `workflow_kind`, `output_provenance`, `upstream_generation_attempted`, `resource_boundary_position`, `artifact_specific_logic_exercised`, `paper_facing_output`, `entry_origin`, `input_origin`, `output_kind`, `paper_mapping_strength`, and `coverage_boundary`.
- From D3 `resource_boundary_assessment` in `evidence_semantics.json` first, falling back to `stage_packet.resource_boundary_assessment`: `boundary_type`, `position`, `artifact_logic_reached`, `paper_facing_output_before_boundary`, `blocks_all_reviewer_realistic_routes`, `alternative_route_available`, `alternative_evidence_exists`, and `evidence_effect`.
- From D3 `artifacts/D3_executability/route_evidence.json`: selected candidate, route arbitration, route attempts, unattempted reviewer-realistic alternatives, and route evidence refs.
- From D4: whether D4 mapped the D3 observed output to paper claims, the strength of that mapping, and any freshness or coverage boundary.

Do not replace present structured fields with `unknown`. Use `unknown` only when the field is absent or directly contradictory.

For `e2_sufficiency_check`, start from the D3 `evidence_semantics` values. Do not write `workflow_kind`, `output_provenance`, `upstream_generation_attempted`, `resource_boundary_position`, `artifact_specific_logic_exercised`, or `paper_facing_output` as `unknown` when D3 provides those fields.

Separate E2 eligibility from output freshness:

- Passive deposited files are E1/supporting context when the reviewer only lists, quotes, or observes them.
- Reviewer-observed validation can be E2-style evidence when the current run loads, checks, aggregates, summarizes, or otherwise produces checkable output from artifact-declared material, and D4 maps that reviewer-observed output to paper-facing claims.
- Fresh upstream generation is stronger than reviewer-observed validation, but it is not the only way to reach executable/checkable evidence under a documented time/API/resource boundary.

Therefore, do not set `e2_evidence_exists=false` solely because `output_provenance=validated_precomputed_output`. First decide whether D3 records reviewer-observed validation output, artifact-specific logic, paper-facing output, and a D4 mapping. If those are present, preserve them and explain the freshness/resource boundary separately.

Bounded execution can support Functional when artifact logic is reached, reviewer-observed output exists, D4 maps that output to paper claims, and the resource boundary is recorded. A timeout after valid bounded output is not the same as no evidence. If Functional is false, name the failed Functional condition.

Do not award Functional from setup checks, version/help output alone, import-only checks, generic dependency demos, empty tests, passive file existence, or unrelated component output.

## Reusable Gate

Reusable holds only when all rubric Reusable conditions hold:

- Functional holds.
- D1 >= WA.
- D5 >= WA.
- At least one transfer validation completed.
- At least one successful transfer demonstrates reuse outside the original paper workflow.
- The transfer output is explainable.
- The reuse path does not depend on I4.

Use D5 structured route evidence. Ignore D5 `summary`, `handoff`, or conclusion-like prose when it conflicts with route records, validation records, or cited command/output evidence.

Under the core-code reuse protocol, strong Reusable evidence normally requires a reviewer-created validation artifact: a unit test, driver script, tiny project, or tiny input under the run root that calls the artifact's core public API/module/CLI and produces explainable output without modifying core artifact logic.

### D5 Route Gate

For each successful D5 route, derive the route relationship from observable facts before deciding novelty:

- Surface origin: documented CLI/API/module/package/data surface, source-public API, official script, or reviewer-constructed helper.
- Invocation shape: paper experiment pipeline, direct public CLI/API call, module/API composition, deposited data/result reanalysis, or post-processing.
- Input/task relation: original paper input, same workflow with a new input, new downstream task, new project/scenario, or new integration.
- Output: generated/validated artifact output, paper-output reformatting, helper output, or failed/no output.

A route can satisfy Reusable novelty when it is successful, explainable, artifact-native, and outside the original paper workflow. Examples:

- Direct use of an artifact public CLI/API/module on a new user-supplied object outside the paper experiment runner.
- Reviewer-created unit test or driver that imports/calls the artifact's core module/API/function and checks a meaningful output.
- New downstream task, project/scenario, integration, or API/CLI composition not demonstrated by the paper workflow.
- Dataset/result package reanalysis only when the artifact-authored material declares the deposited files and loading/schema contract as the intended reuse surface.

These are not Reusable novelty by themselves:

- Reproducing paper results or generating paper figures/tables.
- Running an official demo or README example exactly as supplied.
- Same script/CLI/mode with a different input file when that is still the paper workflow.
- Parameter/config/seed/path/working-directory/benchmark-subset/resource-limit changes to the same artifact workflow.
- Changing directories or runtime context and rerunning the same workflow.
- Reviewer-owned post hoc summaries over existing paper-output CSVs for a software/tool/pipeline artifact.
- Internal helper/component output with no evidence that users are expected to reuse that surface.

If D5 has conflicting route labels, record `d5_transfer_sufficiency_check.field_conflicts` and decide from the cited observable facts. A label such as `same_workflow_with_new_input` can be wrong if the validation evidence shows a direct public CLI/API transfer outside the paper experiment pipeline; conversely, a label such as `independent_of_d3` can be wrong if the evidence is only rerunning the paper script with a new file.

Preserve same-workflow successful evidence as Functional-style or limitation evidence; do not erase it. Reusable fails only because the Reusable transfer condition is not met.

### D5 Failure Attribution

Do not collapse every failed D5 validation into artifact non-reusability. Map D5 outcomes as follows:

- `successful_core_reuse` or `success_core_reuse`: can support D5 >= WA when the called surface is core, grounded, outside the paper workflow, and output is explainable.
- `resource_gated` or `resource_or_api_gated`: not an artifact-interface failure. Treat runtime transfer as unverified; use static/interface evidence only as lower-confidence support and do not punish the artifact as if the interface failed.
- `fail_reviewer_harness`, `input_contract_mismatch`, `route_or_config_choice`, or `evaluator_wrapper_error`: inconclusive unless a bounded repair was attempted and the repaired validation proves a true artifact-interface failure.
- `fail_artifact_interface` or artifact behavior failure after a valid harness and environment: true negative D5 evidence.
- `not_attempted_prerequisite_missing` or `no_core_reuse_surface`: cannot support Reusable.

## Resource Boundaries

Record resource boundaries as evidence, not as a separate scoring system.

Distinguish:

- Passive files only: listed or quoted files without reviewer computation. This is supporting context.
- Reviewer-observed validation output: the run loaded, checked, aggregated, summarized, or otherwise produced checkable output from artifact-declared material. This can support D3/D4 when the rubric conditions hold.
- Fresh upstream execution: the artifact generator/pipeline produced paper-facing output in the current run.

Do not collapse reviewer-observed validation into passive evidence solely because upstream generation timed out or because files were deposited/precomputed.

## Required Packet

Write one JSON object with `schema: "final_scoring_packet_v1"` to `artifacts/final_scoring/final_scoring_packet.json`.

Keep these top-level fields:

- `schema`
- `status`
- `rubric_source`
- `available_gate`
- `dimension_checklists`
- `dimension_scores`
- `evidence_tier`
- `e2_sufficiency_check`
- `route_arbitration_check`
- `resource_boundary_assessment`
- `rubric_evidence_adjudication`
- `highest_intervention`
- `functional_decision`
- `reusable_decision`
- `d5_transfer_sufficiency_check`
- `badge_recommendation`
- `confidence`
- `limitations`
- `author_clarification_requests`
- `human_review_sensitive_points`
- `evidence_refs`

Use these enum values:

- `dimension_scores.*.score`: `R`, `WR`, `WA`, `A`
- `dimension_checklists.*[].judgment`: `yes`, `no`, `not_applicable`
- `evidence_tier.tier`: `E0`, `E1`, `E2`, `E3`
- `highest_intervention.level`: `I0`, `I1`, `I2`, `I3`, `I4`
- `confidence.level`: `low`, `medium`, `high`
- `badge_recommendation.badge`: `no badge`, `available`, `available + functional`, `available + functional + reusable`

`available_gate`, `functional_decision`, and `reusable_decision` must each contain:

- `holds`: boolean
- `reason`: concise evidence-grounded reason
- `conditions`: list of condition objects with `condition`, `satisfied`, `evidence`, and `refs`

`d5_transfer_sufficiency_check` must include the route-level fields used for Reusable:

- `reuse_target_level`
- `transfer_novelty`
- `relationship_to_d3`
- `validated_reuse_surface`
- `artifact_level_sufficiency`
- `transfer_failure_interpretation`
- `core_reuse_validation_result`
- `reviewer_created_validation`
- `same_workflow_variant_check`
- `field_conflicts`: list, empty when none

`same_workflow_variant_check` must include `same_script_or_mode_as_d3`, `only_parameter_or_config_change`, `only_path_or_relocation_change`, `only_same_task_new_input`, `reviewer_owned_new_context`, and `conclusion`. If any `only_*` field is true and `reviewer_owned_new_context` is false/unknown, Reusable must be false unless the artifact is a dataset/result package whose declared public surface is the deposited data/result contract.

`rubric_evidence_adjudication` must include:

- `strongest_functional_evidence`
- `functional_sufficiency`
- `strongest_reusable_evidence`
- `reusable_sufficiency`
- `final_alignment_check`

Use concrete refs. Do not add placeholder reasons.

Use this exact JSON shape for `final_scoring_packet.json`. Keep all keys present; use empty arrays/objects only when the evidence is genuinely absent.

```json
{
  "schema": "final_scoring_packet_v1",
  "status": "complete | limited",
  "rubric_source": {
    "rubric_ref": "artifactguide/rubric_overview.md",
    "scored_from_refs": []
  },
  "available_gate": {
    "holds": false,
    "reason": "",
    "conditions": [
      {"condition": "GC1", "satisfied": false, "evidence": "", "refs": []},
      {"condition": "GC2", "satisfied": false, "evidence": "", "refs": []},
      {"condition": "GC3", "satisfied": false, "evidence": "", "refs": []}
    ]
  },
  "dimension_checklists": {
    "D1": [{"item": "D1.1", "judgment": "yes | no | not_applicable", "evidence": "", "refs": []}],
    "D2": [{"item": "D2.1", "judgment": "yes | no | not_applicable", "evidence": "", "refs": []}],
    "D3": [{"item": "D3.1", "judgment": "yes | no | not_applicable", "evidence": "", "refs": []}],
    "D4": [{"item": "D4.1", "judgment": "yes | no | not_applicable", "evidence": "", "refs": []}],
    "D5": [{"item": "D5.1", "judgment": "yes | no | not_applicable", "evidence": "", "refs": []}]
  },
  "dimension_scores": {
    "D1": {"score": "R | WR | WA | A", "reason": "", "limiting_items": [], "refs": []},
    "D2": {"score": "R | WR | WA | A", "reason": "", "limiting_items": [], "refs": []},
    "D3": {"score": "R | WR | WA | A", "reason": "", "limiting_items": [], "refs": []},
    "D4": {"score": "R | WR | WA | A", "reason": "", "limiting_items": [], "refs": []},
    "D5": {"score": "R | WR | WA | A", "reason": "", "limiting_items": [], "refs": []}
  },
  "evidence_tier": {"tier": "E0 | E1 | E2 | E3", "reason": "", "refs": []},
  "e2_sufficiency_check": {
    "workflow_kind": "",
    "output_provenance": "",
    "artifact_specific_logic_exercised": "yes | limited | no | unknown",
    "paper_facing_output": "yes | limited | no | unknown",
    "generic_dependency_or_example": false,
    "component_only": false,
    "upstream_generation_attempted": "",
    "resource_boundary_position": "",
    "declared_artifact_type": "",
    "result_package_declared_by_artifact": false,
    "representative_workflow_assessment": {},
    "sufficiency_reason": "",
    "refs": []
  },
  "route_arbitration_check": {
    "d1_route_plan_ref": "artifacts/D1_documentation/readme_workflow.json",
    "d3_route_evidence_ref": "artifacts/D3_executability/route_evidence.json",
    "d3_selected_candidate_id": "",
    "d3_selected_source_ref": "",
    "d3_selected_route_decisiveness": "successful_representative | bounded_representative | result_package_only | component_or_helper_only | setup_only | not_decisive_alternative_exists | unclear",
    "unattempted_reviewer_realistic_alternatives": [],
    "final_effect": "supports_functional | limits_functional | unclear"
  },
  "resource_boundary_assessment": {
    "boundary_type": "api_gated | hardware_gated | data_gated | network_gated | toolchain_gated | time_gated | host_policy_gated | none | unknown",
    "position": "before_artifact_logic | after_bounded_artifact_logic | after_valid_bounded_output | not_resource_gated | unknown",
    "artifact_logic_reached": "yes | limited | no | unknown",
    "paper_facing_output_before_boundary": "yes | limited | no | unknown",
    "alternative_evidence_exists": "yes | no | unknown",
    "badge_effect": "blocks_functional | blocks_reusable | records_limitation | not_applicable | unclear",
    "reason": "",
    "refs": []
  },
  "rubric_evidence_adjudication": {
    "strongest_functional_evidence": {"evidence_type": "", "representative_strength": "", "refs": []},
    "functional_sufficiency": {"sufficient": false, "reason": "", "unmet_conditions": [], "downgrade_reason": "", "refs": []},
    "strongest_reusable_evidence": {"evidence_type": "", "transfer_strength": "", "transfer_novelty": "", "refs": []},
    "reusable_sufficiency": {"sufficient": false, "reason": "", "unmet_conditions": [], "downgrade_reason": "", "refs": []},
    "final_alignment_check": {"badge_matches_adjudication": false, "stage_final_consistency": "consistent | limited | inconsistent", "reason": ""}
  },
  "highest_intervention": {"level": "I0 | I1 | I2 | I3 | I4", "summary": "", "affected_stages": [], "depends_on_i4": false, "refs": []},
  "functional_decision": {
    "holds": false,
    "reason": "",
    "conditions": [
      {"condition": "Available", "satisfied": false, "evidence": "", "refs": []},
      {"condition": "D2>=WA", "satisfied": false, "evidence": "", "refs": []},
      {"condition": "D3>=WA", "satisfied": false, "evidence": "", "refs": []},
      {"condition": "D4>=WA", "satisfied": false, "evidence": "", "refs": []},
      {"condition": "RW1-RW5", "satisfied": false, "evidence": "", "refs": []},
      {"condition": "E2_or_above", "satisfied": false, "evidence": "", "refs": []},
      {"condition": "not_I4_dependent", "satisfied": false, "evidence": "", "refs": []}
    ]
  },
  "reusable_decision": {
    "holds": false,
    "reason": "",
    "conditions": [
      {"condition": "Functional", "satisfied": false, "evidence": "", "refs": []},
      {"condition": "D1>=WA", "satisfied": false, "evidence": "", "refs": []},
      {"condition": "D5>=WA", "satisfied": false, "evidence": "", "refs": []},
      {"condition": "transfer_completed", "satisfied": false, "evidence": "", "refs": []},
      {"condition": "outside_original_workflow", "satisfied": false, "evidence": "", "refs": []},
      {"condition": "explainable_output", "satisfied": false, "evidence": "", "refs": []},
      {"condition": "not_I4_dependent", "satisfied": false, "evidence": "", "refs": []}
    ]
  },
  "d5_transfer_sufficiency_check": {
    "reuse_target_level": "artifact_level | pipeline_level | documented_cli_or_api | module_or_function_level | utility_or_helper_level | component_level | dataset_package_level | result_package_level | packaged_example_level | unknown",
    "transfer_novelty": "new_input | new_configuration | new_project | new_scenario | independent_api_or_cli_call | new_summary_over_existing_files | same_as_d3_variant | no_transfer",
    "relationship_to_d3": "independent_of_d3 | same_workflow_with_new_input | post_processing_of_d3_outputs | data_reanalysis_of_deposited_files | unclear",
    "validated_reuse_surface": "intended_public_surface | documented_subsurface | component_or_helper_surface | dataset_or_result_surface | unclear_surface | no_validated_surface",
    "artifact_level_sufficiency": "sufficient | supporting_only | insufficient | unknown",
    "transfer_failure_interpretation": "route_or_config_choice | input_contract_mismatch | missing_dependency | resource_or_api_gated | artifact_behavior_failure | not_attempted_prerequisite_missing | not_applicable_success | unknown",
    "core_reuse_validation_result": "successful_core_reuse | resource_gated | fail_artifact_interface | fail_reviewer_harness | not_attempted_prerequisite_missing | no_core_reuse_surface | unknown",
    "reviewer_created_validation": {"created": false, "driver_or_test_ref": "", "input_ref": "", "command_ref": "", "output_refs": [], "modifies_core_logic": false, "harness_attribution": ""},
    "same_workflow_variant_check": {
      "same_script_or_mode_as_d3": false,
      "only_parameter_or_config_change": false,
      "only_path_or_relocation_change": false,
      "only_same_task_new_input": false,
      "reviewer_owned_new_context": false,
      "conclusion": ""
    },
    "field_conflicts": []
  },
  "badge_recommendation": {"badge": "no badge | available | available + functional | available + functional + reusable", "available": false, "functional": false, "reusable": false, "reason": ""},
  "confidence": {"level": "low | medium | high", "reason": ""},
  "limitations": [],
  "author_clarification_requests": [],
  "human_review_sensitive_points": [],
  "evidence_refs": []
}
```

## Finish Checks

Before `phase_finish`:

- Confirm every condition has a boolean and evidence.
- Confirm false Functional/Reusable names the failed rubric condition.
- Confirm D3/D4 resource-boundary structured facts were not erased.
- Confirm Reusable novelty can be recomputed from D5 route facts, not D5 summary prose.
- Confirm `highest_intervention.level` is at least the maximum intervention in the ledger and command ledger.
- Confirm `dimension_scores` match checklist judgments.
- Remember runtime derives final `badge_recommendation` from the three gate booleans.

Then call `phase_finish` in the same response after writing the packet.
