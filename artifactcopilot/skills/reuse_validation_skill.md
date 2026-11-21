# D5 Reusability Skill

skill_id: reuse_validation_skill
stage: D5_reusability

## Mission

Identify the artifact's core reusable surface, attempt to validate it outside
the original paper workflow with a reviewer-owned driver, and package the
evidence for final scoring.

D5 collects reuse evidence only. It does not assign R/WR/WA/A scores, Reusable
status, Functional status, or badge conclusions. Final scoring later applies
the rubric to the evidence written here.

## Tools

- `read_file`
- `list_repo_tree`
- `run_command`
- `write_file`
- `phase_finish`

## Core Principles

### P1. Functional is a prerequisite

Read `artifacts/D4_claim_support/stage_packet.json` first. If
`functional_predicate.holds` is not true, do not attempt D5 transfer
validation. Write a skipped D5 packet with
`core_reuse_validation_result=not_attempted_prerequisite_missing`,
`transfer_novelty=no_transfer`, and call `phase_finish`.

### P2. Core Surface Analysis is the primary deliverable

Before any D5 execution, trace from the paper contribution to the artifact
surface that future users could reuse:

- module, class, function, package entry point, CLI subcommand, API endpoint;
- or, for dataset/result artifacts, the documented file organization and schema.

Write this analysis to
`artifacts/D5_reusability/core_surface_analysis.json`. This file is useful
evidence even when transfer validation is infeasible or fails.

### P3. Reviewer-owned driver is the default validation method

The default D5 validation is a reviewer-created script, test, or integration
wrapper under `artifacts/D5_reusability/` that imports or calls the artifact's
core surface on a new reviewer-owned task or scenario.

Running the artifact's own paper scripts, demos, notebooks, or CLI commands
with different arguments is not the default D5 path.

Fallback order when direct import is not feasible:

1. Reviewer-created shell/Python wrapper that prepares a novel input, invokes a
   documented artifact CLI, and inspects the output.
2. Reviewer-created data/result analysis script, only when the artifact is
   itself a dataset or result package and the public data/result contract is the
   reuse surface.
3. Record the grounded surface and why validation is infeasible.

### P4. Transfer must pass the Transfer Gate

All three gates below must pass before a validation can be called transfer
evidence. If any gate fails, finish with `status=limited` and record the core
surface plus the gate failure reason.

### P5. No stable interface means no forced reuse

If no grounded public or source-observed reuse surface can be identified, record
`core_reuse_validation_result=no_core_reuse_surface` and finish. Do not invent
an interface by stitching together arbitrary internal functions.

## Transfer Gate

Before any D5 `run_command`, the selected plan must pass all three gates and
write a `d3_d5_separation` statement:

`D3 did X; D5 validates Y; the difference is Z.`

If you cannot write this statement with a real difference in task, scenario, or
integration, the plan has not passed the gate.

### Gate 1 - Execution Entry Novelty

The D5 execution entry point must be reviewer-created material under
`artifacts/D5_reusability/`: a driver script, test file, tiny project, or
integration wrapper that calls into the artifact's code.

This does not pass Gate 1:

- running the artifact's own script directly;
- running the same paper CLI command with different arguments;
- running a README demo or notebook as supplied;
- copying the D3 workflow to a new directory.

CLI fallback: if the artifact only exposes a CLI, Gate 1 may pass when the
reviewer-created wrapper does all of:

- creates or prepares a new reviewer-owned input/project/config;
- invokes the artifact CLI as a reusable surface;
- checks the output contract programmatically or with an explicit inspection.

Merely wrapping `cd <repo> && <same command> <different_arg>` is not sufficient.

### Gate 2 - Task or Scenario Novelty

The validation task must differ meaningfully from D3, D4, and the paper. A
valid D5 task is a plausible future-user use case, new project/scenario, new
integration, new API/module call, or new downstream analysis not already
demonstrated by the paper workflow.

Valid examples:

- Paper analyzes its benchmark suite; D5 creates a 10-line program and analyzes
  it for a specific property.
- Paper trains a model; D5 loads the model via public API and runs inference on
  a reviewer-authored input.
- Paper processes dataset A with script X; D5 imports the core parser/module and
  calls it on a reviewer-created three-record input.
- For a declared dataset package, D5 loads the public data schema and computes a
  new check or summary not present in the paper.

Invalid examples:

- D3 ran `tool analyze projectA`; D5 runs the same mode and flags on projectB.
- D3 generated Table 1; D5 regenerates Table 1 with a different seed.
- D3 ran the full pipeline; D5 runs the same pipeline on a subset.
- D3 ran in directory X; D5 copies the same workflow to directory Y and reruns.
- D5 only post-processes D3 outputs for a software/tool/pipeline artifact.

Checklist:

- Same script/mode/notebook as D3? Usually fail.
- Only parameter, config value, seed, path, or working directory changes? Fail.
- Only the input file changes within the same original task? Fail.
- Reviewer exercises a genuinely different use case through the core surface?
  Pass.

### Gate 3 - Core Logic Exercise

The D5 driver must reach the artifact's core implementation surface identified
in `core_surface_analysis.json`. Setup utilities, I/O helpers, config parsers,
data loaders, logging wrappers, and generic CLI dispatch do not satisfy this
gate by themselves.

Verification: trace from the driver's import/call through at most two levels of
indirection. If the trace reaches the module/class/function/subcommand
identified as implementing the paper contribution, Gate 3 passes.

For dataset/result artifacts with no core code, Gate 3 passes when the driver
loads and processes the documented public files in a way that exercises the
artifact's schema or data organization.

## Workflow

### Step 1. Read prior evidence

Read:

- `artifacts/D1_documentation/stage_packet.json`
- `artifacts/D1_documentation/readme_workflow.json`
- `artifacts/D2_environment/stage_packet.json`
- `artifacts/D3_executability/stage_packet.json`
- `artifacts/D3_executability/route_evidence.json`
- `artifacts/D3_executability/evidence_semantics.json`
- `artifacts/D4_claim_support/stage_packet.json`
- `artifacts/run_initialization/artifact_universe.json` when present
- `artifacts/paper_source/paper_text.txt` when present
- `artifacts/memory/current_stage_memory.md`

If `artifact_universe.json` says the configured repo may be one component of a
larger artifact, record `route_scope` and keep conclusions scoped to the
configured component unless all relevant components are covered.

### Step 2. Check Functional prerequisite

If `functional_predicate.holds` from D4 is not true, write a skipped
`stage_packet.json`, update memory for final scoring, and call `phase_finish`.
Do not inspect code or run commands for D5.

### Step 3. Core Surface Analysis

Without running D5 commands:

1. Inherit the paper contribution and claim mapping from D4.
2. Locate the implementation or reusable surface. Read README/docs/examples,
   package metadata, tests, `__init__` exports, and the most relevant small
   source snippets as needed.
3. Characterize the public interface: inputs, outputs, dependencies,
   invocation method, and unknowns.
4. Record documentation quality: docs, examples, docstrings, type hints, tests,
   exports, CLI help, data schema.
5. Decide whether the surface is a clear public surface, observable but
   undocumented, no stable interface, or unknown.

Write `artifacts/D5_reusability/core_surface_analysis.json`.

If no stable surface can be grounded, skip validation and finish with
`status=blocked`.

### Step 4. Decide feasibility

Validation is feasible only when:

- a core surface is identified;
- D4 Functional prerequisite is true;
- a small reviewer-created input or scenario can exercise that surface;
- the run does not require private data, credentials, unavailable hardware,
  external services, or heavy runtime;
- the interface contract is clear enough to avoid inventing behavior.

If blocked, still write the stage packet with core surface evidence and the
validation blocker.

### Step 5. Select candidate and write driver

Consider 2-4 plausible reuse targets. Prefer:

1. documented public API/module/function/class;
2. documented CLI or package entry point exposing the core method;
3. core component with source-observed signature and explainable output;
4. declared dataset/result package loading contract;
5. full pipeline only when no smaller grounded surface exists and the pipeline
   is the intended reuse surface.

Create reviewer-owned material under `artifacts/D5_reusability/`:

- driver/test/wrapper;
- tiny input/project/config when needed;
- expected output inspection plan.

Do not edit artifact code, author data, author configs, or environment files.

### Step 6. Apply Transfer Gate and write reuse plan

Apply all three gates. If the selected candidate fails and another grounded
candidate exists, switch candidate before execution. If no candidate can pass,
write the failed gate reasons and finish with `status=limited`.

Before any D5 command, write
`artifacts/D5_reusability/reuse_plan.json`.

### Step 7. Execute at most four attempts

Attempt 1: run the planned bounded validation.

Attempts 2-4: allowed only for reviewer-side repairs, such as driver import
path, mounted path, toy input shape, command arguments, cwd, environment
activation, or output inspection path.

Use the extra repair budget when the failure is visibly in the reviewer-owned
harness or invocation wrapper and a concrete non-semantic fix remains. For
example, if a Python driver under `artifacts/D5_reusability/` cannot import a
repo-root module because only the driver directory is on `sys.path`, try a
bounded harness repair such as adding the repo root to `sys.path` or changing
the invocation/mount/cwd. Do not stop after a failed first repair when the next
repair is obvious from the error message and does not alter artifact logic.

Never repair by editing artifact implementation, author data/configs, declared
environment files, or algorithmic semantics.

Execution hygiene:

- use D2 activation/container context when relevant;
- use bounded timeout when needed;
- do not use `|| true`;
- do not pipe the primary command through `head`, `tail`, or filters;
- preserve D5 outputs under `artifacts/D5_reusability/` with D5-specific names.

### Step 8. Diagnose result

If successful, verify the output is explainable and matches the expected
contract. Stop after one grounded successful transfer.

If failed, classify the failure location:

- `before_artifact_code`: reviewer driver/input/cwd/import/command shape failed
  before reaching artifact logic;
- `reviewer_harness`: reviewer-created harness reached the artifact boundary
  incorrectly or violated a visible contract;
- `artifact_interface`: valid harness and environment reached the artifact
  surface, then the artifact failed;
- `resource_or_api_boundary`: private data, credentials, hardware, network,
  time, toolchain, or host policy blocked validation;
- `unclear_contract`: evidence is insufficient to distinguish the above.

Only `before_artifact_code` or `reviewer_harness` can justify reviewer-side
repairs, up to the Step 7 attempt budget. After each failed repair, reassess
the failure location and continue only when the next fix is concrete, visible
from the evidence, and limited to the reviewer driver/input/invocation wrapper.
`artifact_interface` and `resource_or_api_boundary` should be recorded without
repair.

### Step 9. Write stage packet, update memory, finish

Write `artifacts/D5_reusability/stage_packet.json`.

Then:

- append a concise D5 summary to `artifacts/memory/stage_summaries.md`;
- update `artifacts/memory/evidence_index.md`;
- replace `artifacts/memory/current_stage_memory.md` with a final-scoring
  starting note listing packet refs, key evidence, and limitations;
- call `phase_finish`.

## Operational Rules

1. Environment preservation: use D2's activation context when calling artifact
   code. A missing-module error may mean the D2 context was lost.
2. File boundaries: reviewer-created files live under
   `artifacts/D5_reusability/`, not inside the artifact repo.
3. Contamination: prior reviewer-generated material inside the artifact repo is
   not author evidence. Treat it as contamination.
4. Mutation: if a temporary artifact-file mutation is unavoidable, back up to
   `artifacts/D5_reusability/original_backups/` first, restore after, and record
   the mutation as an intervention.
5. Interventions: reviewer-created drivers/inputs are not interventions.
   Modifying artifact code/config/data/environment is an intervention.

## Schema: core_surface_analysis.json

Use this exact JSON shape. Keep all keys present.

```json
{
  "schema": "d5_core_surface_v1",
  "stage": "D5_reusability",
  "status": "complete | limited | blocked",
  "paper_contribution_summary": "",
  "core_implementation": {
    "module_or_file": "",
    "class_or_function": "",
    "entry_point_type": "importable_module | cli_subcommand | api_endpoint | data_schema | result_schema | none_identified",
    "why_core": "",
    "evidence_refs": []
  },
  "public_interface": {
    "signature_or_usage": "",
    "inputs": "",
    "outputs": "",
    "dependencies": "",
    "unknowns": []
  },
  "documentation_quality": {
    "docstrings": false,
    "type_hints": false,
    "examples": false,
    "api_docs": false,
    "exported_in_init": false,
    "has_tests": false,
    "cli_help_or_schema": false,
    "refs": []
  },
  "candidate_surfaces": [
    {
      "candidate_id": "surface_01",
      "name": "",
      "level": "module_or_function | cli_command | api_endpoint | data_schema | result_schema | pipeline | unknown",
      "path": "",
      "is_core": false,
      "selected": false,
      "reason": "",
      "evidence_refs": []
    }
  ],
  "reuse_assessment": "clear_public_surface | observable_but_undocumented | no_stable_interface | unknown",
  "reuse_assessment_reason": "",
  "validation_feasible": false,
  "validation_blockers": [],
  "evidence_refs": []
}
```

## Schema: reuse_plan.json

Use this exact JSON shape. Keep all keys present.

```json
{
  "schema": "d5_reuse_plan_v2",
  "stage": "D5_reusability",
  "target": {
    "surface_id": "",
    "name": "",
    "level": "module_or_function | cli_command | api_endpoint | data_schema | result_schema | pipeline | unknown",
    "path": "",
    "is_core": true,
    "source_evidence": []
  },
  "driver": {
    "path": "",
    "description": "",
    "imports_or_calls": "",
    "new_input_description": "",
    "new_input_path": ""
  },
  "transfer_gate": {
    "gate_1_execution_entry_novelty": {"passes": false, "reason": "", "fallback_used": false},
    "gate_2_task_scenario_novelty": {"passes": false, "reason": ""},
    "gate_3_core_logic_exercise": {"passes": false, "reason": ""},
    "all_gates_pass": false
  },
  "d3_d5_separation": "",
  "command": "",
  "cwd": "",
  "timeout_sec": 0,
  "environment_entry": "",
  "expected_output": {
    "description": "",
    "inspection_method": "",
    "expected_paths": []
  },
  "artifact_modification_required": false,
  "candidates_considered": [
    {
      "name": "",
      "level": "",
      "path": "",
      "is_core": false,
      "selected": false,
      "reason": ""
    }
  ],
  "route_scope": "configured_repo_only | artifact_universe_incomplete | artifact_universe_complete",
  "route_scope_reason": "",
  "feasibility": "feasible | limited | blocked",
  "feasibility_reason": "",
  "evidence_refs": []
}
```

## Schema: stage_packet.json

Use this exact JSON shape. Keep all keys present. The
`d5_transfer_sufficiency_check` object is required for final scoring
compatibility.

```json
{
  "schema": "d5_stage_packet_v2",
  "stage": "D5_reusability",
  "stage_goal": "Collect core-code reuse evidence without scoring.",
  "status": "success | limited | failed | blocked | skipped",
  "summary": "",
  "functional_prerequisite_evidence": {
    "d4_stage_ref": "artifacts/D4_claim_support/stage_packet.json",
    "functional_predicate_holds": false,
    "prerequisite_status": "satisfied | blocked | unknown",
    "blocking_reasons": []
  },
  "route_scope": "configured_repo_only | artifact_universe_incomplete | artifact_universe_complete",
  "artifact_universe_ref": "artifacts/run_initialization/artifact_universe.json",
  "artifact_boundary_warnings": [],
  "core_surface_ref": "artifacts/D5_reusability/core_surface_analysis.json",
  "core_surface_summary": "",
  "reuse_information": {
    "input_documented": "yes | limited | no | unknown",
    "output_documented": "yes | limited | no | unknown",
    "dependencies_documented": "yes | limited | no | unknown",
    "invocation_documented": "yes | limited | no | unknown",
    "reuse_boundary_documented": "yes | limited | no | unknown",
    "refs": []
  },
  "reuse_plan_ref": "artifacts/D5_reusability/reuse_plan.json",
  "transfer_validation": {
    "attempted": false,
    "gate_passed": false,
    "succeeded": false,
    "selected_candidate": "",
    "driver_ref": "",
    "input_refs": [],
    "command": "",
    "command_refs": [],
    "output_refs": [],
    "d3_d5_separation": "",
    "failure_location": "not_attempted | before_artifact_code | reviewer_harness | artifact_interface | resource_or_api_boundary | unclear_contract | not_applicable_success",
    "failure_cause": "",
    "attempts": 0,
    "repair_attempted": false,
    "repair_description": "",
    "repair_result": "not_needed | succeeded | failed | not_attempted"
  },
  "resource_boundary": {
    "type": "none | api_gated | hardware_gated | data_gated | network_gated | time_gated | toolchain_gated | host_policy_gated | unknown",
    "blocks_all_routes": false,
    "artifact_logic_reached": "yes | limited | no | not_applicable | unknown",
    "reason": "",
    "evidence_refs": []
  },
  "classifications": {
    "reuse_target_level": "artifact_level | pipeline_level | documented_cli_or_api | module_or_function_level | utility_or_helper_level | component_level | dataset_package_level | result_package_level | packaged_example_level | unknown",
    "transfer_novelty": "new_input | new_configuration | new_project | new_scenario | independent_api_or_cli_call | new_summary_over_existing_files | same_as_d3_variant | no_transfer",
    "validated_reuse_surface": "intended_public_surface | documented_subsurface | component_or_helper_surface | dataset_or_result_surface | unclear_surface | no_validated_surface",
    "relationship_to_d3": "independent_of_d3 | same_workflow_with_new_input | post_processing_of_d3_outputs | data_reanalysis_of_deposited_files | unclear",
    "core_reuse_validation_result": "successful_core_reuse | fail_artifact_interface | fail_reviewer_harness | resource_gated | not_attempted_prerequisite_missing | no_core_reuse_surface | unknown"
  },
  "d5_transfer_sufficiency_check": {
    "reuse_target_level": "artifact_level | pipeline_level | documented_cli_or_api | module_or_function_level | utility_or_helper_level | component_level | dataset_package_level | result_package_level | packaged_example_level | unknown",
    "transfer_novelty": "new_input | new_configuration | new_project | new_scenario | independent_api_or_cli_call | new_summary_over_existing_files | same_as_d3_variant | no_transfer",
    "relationship_to_d3": "independent_of_d3 | same_workflow_with_new_input | post_processing_of_d3_outputs | data_reanalysis_of_deposited_files | unclear",
    "validated_reuse_surface": "intended_public_surface | documented_subsurface | component_or_helper_surface | dataset_or_result_surface | unclear_surface | no_validated_surface",
    "artifact_level_sufficiency": "sufficient | supporting_only | insufficient | unknown",
    "transfer_failure_interpretation": "route_or_config_choice | input_contract_mismatch | missing_dependency | resource_or_api_gated | artifact_behavior_failure | not_attempted_prerequisite_missing | not_applicable_success | unknown",
    "core_reuse_validation_result": "successful_core_reuse | resource_gated | fail_artifact_interface | fail_reviewer_harness | not_attempted_prerequisite_missing | no_core_reuse_surface | unknown",
    "reviewer_created_validation": {
      "created": false,
      "driver_or_test_ref": "",
      "input_ref": "",
      "command_ref": "",
      "output_refs": [],
      "modifies_core_logic": false,
      "harness_attribution": ""
    },
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
  "route_accounting": [
    {
      "route_id": "",
      "status": "selected_for_d5 | skipped_same_workflow | skipped_resource_gated | skipped_lower_priority | not_applicable",
      "d5_candidate": "",
      "reason": ""
    }
  ],
  "commands": [],
  "output_snapshots": [],
  "interventions": [],
  "limitations": [],
  "handoff": {
    "core_surface": "",
    "transfer_attempted": false,
    "transfer_succeeded": false,
    "d3_d5_separation": "",
    "validated_surface": "",
    "failure_summary": "",
    "unvalidated_surfaces": [],
    "confirmed_limitations": [],
    "transfer_sufficiency_semantics": {},
    "same_workflow_variant_check": {}
  },
  "evidence_refs": []
}
```

## Field Guidance

For a strong software core reuse success:

- `transfer_novelty`: `independent_api_or_cli_call`, `new_project`, or
  `new_scenario`;
- `relationship_to_d3`: `independent_of_d3`;
- `validated_reuse_surface`: `intended_public_surface` or
  `documented_subsurface`;
- `artifact_level_sufficiency`: `sufficient` only when the selected surface is
  the intended reusable surface and output is explainable;
- `core_reuse_validation_result`: `successful_core_reuse`.

For same-workflow variants:

- `transfer_novelty`: `same_as_d3_variant` or `new_input`;
- `relationship_to_d3`: `same_workflow_with_new_input`;
- `artifact_level_sufficiency`: `supporting_only` or `insufficient`;
- set the relevant `same_workflow_variant_check.only_*` field to true.

For skipped or blocked D5:

- `transfer_novelty`: `no_transfer`;
- `validated_reuse_surface`: `no_validated_surface`;
- `artifact_level_sufficiency`: `insufficient`;
- `core_reuse_validation_result`: `not_attempted_prerequisite_missing`,
  `no_core_reuse_surface`, or `resource_gated`.

## Completion

D5 finishes when one of these is true:

- Core Surface Analysis is written, all gates pass, validation succeeds, and
  output is explainable: `status=success`.
- Core Surface Analysis is written, validation reaches the artifact interface,
  and the artifact interface fails: `status=failed`.
- Core Surface Analysis is written, but validation is infeasible, resource
  gated, or fails a transfer gate: `status=limited`.
- No core surface can be identified: `status=blocked`.
- D4 Functional prerequisite is false: `status=skipped`.

Do not finish by assigning scores, Reusable status, or badge recommendations.
