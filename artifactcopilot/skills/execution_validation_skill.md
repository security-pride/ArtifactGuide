# D3 Executability Skill

skill_id: execution_validation_skill
stage: D3_executability

## Mission

Collect evidence about whether an artifact-documented reviewer workflow can run
under the D2 environment.

D3 is not setup, not paper-claim analysis, and not scoring. D3 records:

- what executable or data/result route was selected;
- why that route was selected from D1/D2 evidence;
- what command was run;
- what output, failure, resource boundary, or limitation was observed.

Final D3 score and badge predicates are decided only in `final_scoring`.

## Allowed Tools

- `read_file`
- `list_repo_tree`
- `run_command`
- `write_file`
- `phase_finish`

## Constraints

### C1 - Source Authority

The route source is artifact-authored documentation:

- D1 `documented_commands`;
- D1 `documented_files`;
- D1 observations/uncertainties;
- README or linked docs that D1 read;
- D2 environment handoff.

Do not create a workflow from arbitrary source-code browsing. Source files may
clarify documented placeholders, expected paths, or output contracts only after
the route is already grounded in documentation.

### C2 - Flat D1 Contract

D1 no longer provides `canonical_route_plan`, `route_portfolio`, or route ranks.
Do not require those fields and do not invent a ranked plan on D1's behalf.

D1 binds D3 by facts, not by route ranking:

- `documented_commands` are author-provided executable/action facts. Each
  command should carry `command_id`, `source_ref`, `command`, `purpose`,
  `command_kind`, `is_shell_command`, `working_directory`, `inputs`, `outputs`,
  `resource_requirements`, `placeholders`, and `notes`.
- `documented_files` are author-described data/result/script/notebook assets.
- `documentation_observations` and `uncertainties` define limitations.

Preserve D1 source order when two commands have the same reviewer-facing role.
Do not reinterpret D1 `install` commands as D3 workflow routes; they are setup
context unless no other executable or inspectable artifact route exists.

### C3 - Setup Is Not D3 Success

Installation, import checks, `--help`, version output, package build success,
container startup, and environment coherence checks are D2/supporting evidence.
They are not representative D3 workflow success.

### C4 - Preserve Original Semantics

If D1/README gives a concrete command, attempt that command or an environment
wrapped equivalent. Preserve command flags, order, input paths, and working
directory semantics. Environment wrapping from D2 is allowed; semantic
replacement is not.

### C5 - Preserve D2 Environment Context

Use the D2 handoff for activation, container entry, working directory, command
prefix, PATH/PYTHONPATH, and relevant environment variables.

If D2 says to use an activated shell, use that shell form. Do not replace it
with only an absolute interpreter path unless D2 explicitly says it is
equivalent or the command cannot spawn child commands.

### C6 - No Unjustified Downstream Shortcut

If documentation gives an ordered chain, do not skip upstream execution and use
only table-building, plotting, log inspection, result viewing, or postprocessing
as the representative workflow. Such downstream commands may be supporting
evidence after the upstream step is attempted, resource-gated, or clearly
impossible.

### C7 - Evidence-Grounded Placeholders

Fill placeholders only from README/docs, D1/D2 packets, packaged examples,
repository listings, or prior command evidence. If no grounded value exists,
record the unresolved placeholder instead of guessing.

### C8 - Bounded Execution Hygiene

Use a host timeout for long or interactive workflows unless the artifact
documents a native timeout/limit option. Do not pipe the primary workflow
through `head`, `tail`, `grep`, or failure-suppression constructs such as
`|| true`. Preserve real exit status.

After bounded execution, inspect the expected output contract: result file,
output directory, stdout marker, plot, notebook execution, service response,
checkpoint, log/stat file, or queue/artifact directory.

### C9 - Failure Is Evidence

If the selected route fails, diagnose the immediate cause from logs and targeted
file checks. Do not switch to an unrelated easy task. A second route is allowed
only if it is also documentation-grounded and the first route is attempted,
resource/time gated, missing input, or clearly not reviewer-realistic.

### C10 - Repairs Must Be Recorded

Repairs are allowed when they do not silently change workflow meaning. Record
what changed, why, command/log refs, and whether the result depends on the
repair.

### C11 - Controlled Toolchain Dependency Installs

Missing host-level toolchain dependencies may be installed only as a recorded
reviewer intervention when all of these are true:

- the dependency is required by a documentation-grounded workflow or by an
  immediately observed failure from that workflow;
- installing it does not change the artifact's algorithm, inputs, outputs, or
  evaluation target;
- the runtime explicitly permits system package installs.

Rules:

- Do not use `sudo`.
- Do not use `curl | sh`, `wget | sh`, or installer pipe patterns.
- Do not run package upgrades.
- Use non-interactive package-manager flags such as `apt-get install -y`.
- Include a structured `intervention` object in the `run_command` call.
- Do not pipe installer output through `head`, `tail`, or `grep`; preserve the
  full stdout/stderr logs for audit.
- If runtime rejects the install, record the dependency as a host-policy or
  toolchain gate rather than repeatedly retrying.

Generic allowed command form when runtime policy permits it:

```json
{
  "command": "apt-get update -qq && apt-get install -y -qq <package-name>",
  "log_label": "d3_install_toolchain_dependency",
  "cwd": ".",
  "phase_name": "D3_executability",
  "purpose": "Install a missing host-level toolchain dependency required by the selected documented workflow.",
  "timeout_sec": 600,
  "intervention": {
    "kind": "I2",
    "summary": "Install missing toolchain dependency required by the documented workflow.",
    "details": {
      "semantic_preserving": true,
      "dependency": "<package-name>",
      "reason": "The selected documented workflow cannot run without this dependency."
    }
  }
}
```

### C12 - Observe Before Concluding

Do not write final D3 artifacts immediately after an execution command. First
observe the command result, inspect output refs when needed, then write the
D3 artifacts.

## Route Selection Protocol

D3 route selection has one explicit checkpoint before execution:
`artifacts/D3_executability/route_selection_checkpoint.json`.

### Candidate Sources

Build candidate routes in this order:

1. Every non-setup D1 `documented_commands` item that is executable or required
   for reviewer evaluation. Use `command_kind`, `purpose`, `inputs`, `outputs`,
   `resource_requirements`, `placeholders`, and `notes` directly from D1.
2. D1 `install` commands only as prerequisites/environment context, not as the
   selected representative D3 route.
3. If there are no executable workflow commands, use D1 `documented_files` and README
   file descriptions to form a bounded data/result/notebook inspection route.
4. If D1 is incomplete or contradicts README, re-read the README and record a
   source correction in `source_correction`. Corrections require exact refs.

Do not include source-inferred commands as candidates. If the README only lists
script purposes, do not invent `python script.py`; record a file/data/notebook
route or limitation instead.

### Selection Priority

Select the highest rubric-relevant candidate by this deterministic priority:

1. Author-documented reproduce/evaluate/main workflow command that is intended
   to generate, validate, or inspect paper-facing artifact output.
2. Author-documented artifact-evaluation, quick-start, demo, test, or small
   reproduce command only when it exercises artifact-specific logic and the
   main paper-facing route is resource-gated, time-gated, missing input, absent,
   or not reviewer-realistic in the current environment.
3. Author-documented data download/loading/result-validation action when the
   artifact is a dataset/result/replication package or the main workflow is
   resource-gated.
4. README-described data/result/notebook file validation when no explicit run
   command exists.
5. No runnable workflow, with a documented limitation.

Do not prefer a route merely because it is easy, short, or likely to pass.
Do not treat setup/help/import as the selected D3 route unless the artifact
truly has no runnable or inspectable reviewer workflow; then classify it as
supporting evidence only.

When the selected route is a demo, test, mock, helper, result-file validation,
or post-processing action, the checkpoint and semantics must state why it is
bounded evidence rather than full paper-facing workflow execution.

### Override Protocol

Because D1 is flat, "override" means deviating from D1 facts, not from route
ranking. It is allowed only when one of these is true:

- D1 missed or distorted a README/documentation fact; cite README and the D1
  field being corrected.
- D2 proves the route cannot run in the prepared environment; cite the D2
  evidence.
- A selected route was attempted and failed, resource-gated, time-gated, or
  missing input; cite command/log evidence before moving to another route.

Record the reason in `route_selection_checkpoint.source_correction` or
`route_evidence.route_arbitration.override_reason`. Do not use "faster",
"simpler", "more likely", or "looks reasonable" as override reasons.

## Workflow

1. Read prior evidence:
   - `artifacts/D1_documentation/stage_packet.json`
   - `artifacts/D1_documentation/readme_workflow.json`
   - `artifacts/D2_environment/stage_packet.json`
   - `artifacts/run_initialization/artifact_universe.json` if present
   - `artifacts/memory/current_stage_memory.md`

2. Re-read README or linked docs only if route facts are unclear, D1 is empty,
   or a source correction may be needed.

3. Build route candidates and write
   `artifacts/D3_executability/route_selection_checkpoint.json`.

4. Prepare the command form using D2's execution context. Record original
   command, wrapped command, cwd, timeout, and expected output contract in the
   checkpoint or route evidence.

5. Execute the selected route with `run_command` when it involves command
   execution. If the selected command depends on an immediately preceding
   documented data/config/preparation action from the same README section or
   command block, attempt or explicitly account for that prerequisite first.
   For data/result validation with no author command, a reviewer-owned
   helper script or shell command may be used under `artifacts/D3_executability/`
   to load, summarize, or validate README-listed files. Do not place helper code
   in the artifact repo.

6. Observe outputs and preserve evidence. Copy small mutable outputs or write a
   compact summary under `artifacts/D3_executability/`.

7. Diagnose failures, resource gates, and limitations. Retry only when the retry
   preserves workflow meaning and addresses a concrete observed cause.

8. Write three D3 JSON files:
   - `stage_packet.json`: compact handoff;
   - `route_evidence.json`: route candidates, selection, attempts, command refs;
   - `evidence_semantics.json`: fact-first evidence interpretation for scoring.

9. Update memory files and call `phase_finish`.

## Evidence Recording Guide

Separate observed facts from judgments.

### Fact Fields

Record facts directly observed in commands/files/logs:

- original command/action and source refs;
- executed command and command refs;
- cwd/environment wrapper;
- exit status/failure class;
- stdout/stderr refs;
- output files/directories/snapshots;
- expected output contract and whether it was observed;
- interventions/repairs;
- limitations and resource boundaries.

### Judgment Fields

Judgment fields are descriptive, not final scores:

- `workflow_kind`
- `output_provenance`
- `artifact_specific_logic_exercised`
- `paper_facing_output`
- `generic_dependency_or_example`
- `component_only`
- `representativeness_boundary`
- RW1-RW5 fact table
- `evidence_tier_candidate`
- `resource_boundary_assessment`

Do not write Functional/Reusability/badge conclusions in D3.

### RW1-RW5 Facts

Fill RW facts for the selected route:

- RW1 source: route is grounded in artifact-authored documentation.
- RW2 execution target: route is more than setup/help/import/version.
- RW3 valid output: output contract is observed or failure is evidenced.
- RW4 paper mapping: output can plausibly be mapped to paper artifact goal.
- RW5 coverage boundary: D3 states what the bounded route covers and does not
  cover.

These are facts for final scoring, not the final decision.

## Output Schemas

### `route_selection_checkpoint.json`

```json
{
  "schema": "d3_route_selection_checkpoint_v1",
  "stage": "D3_executability",
  "candidate_source": {
    "d1_readme_workflow_ref": "artifacts/D1_documentation/readme_workflow.json",
    "d2_stage_ref": "artifacts/D2_environment/stage_packet.json",
    "readme_reread_refs": []
  },
  "source_correction": {
    "applied": false,
    "reason": "",
    "d1_field": "",
    "stronger_source_refs": []
  },
  "route_candidates": [
    {
      "candidate_id": "d3_candidate_01",
      "source_kind": "documented_command | documented_file | documented_data_result | documented_notebook | no_runnable_workflow",
      "source_ref": "",
      "original_command_or_action": "",
      "purpose": "",
      "expected_output_contract": {
        "contract_type": "output_dir | result_file | stdout_marker | generated_plot | executed_notebook | service_response | checkpoint | log_or_stats_file | data_summary | unknown",
        "expected_refs_or_paths": [],
        "inspection_method": ""
      },
      "reviewer_realistic": "yes | limited | no | unknown",
      "selection_priority_reason": ""
    }
  ],
  "selected_candidate_id": "",
  "selection_reason": "",
  "unselected_candidates": [
    {
      "candidate_id": "",
      "reason": "lower_priority | setup_only | resource_gated | time_gated | missing_input | not_reviewer_realistic | supporting_only | not_applicable"
    }
  ]
}
```

### `route_evidence.json`

```json
{
  "schema": "d3_route_evidence_v1",
  "stage": "D3_executability",
  "route_scope": "configured_repo_only | artifact_universe_incomplete | artifact_universe_complete",
  "route_scope_reason": "",
  "artifact_universe_ref": "artifacts/run_initialization/artifact_universe.json",
  "artifact_boundary_warnings": [],
  "route_selection_checkpoint_ref": "artifacts/D3_executability/route_selection_checkpoint.json",
  "route_arbitration": {
    "selected_candidate_id": "",
    "selection_reason": "",
    "override_reason": "",
    "unattempted_reviewer_realistic_alternatives": [],
    "selected_route_decisiveness": "successful_representative | bounded_representative | result_package_only | component_or_helper_only | setup_only | not_decisive_alternative_exists | unclear"
  },
  "route_attempts": [
    {
      "candidate_id": "",
      "source_ref": "",
      "original_command_or_action": "",
      "executed_command": "",
      "command_refs": [],
      "attempt_status": "attempted_succeeded | attempted_limited_progress | attempted_failed | not_attempted_resource_gated | not_attempted_time_gated | not_attempted_missing_input | not_attempted_out_of_scope | not_attempted_lower_priority | not_applicable",
      "failure_or_skip_cause": "",
      "failure_cause_class": "artifact_command_failure | evaluator_wrapper_error | environment_mismatch | missing_input | missing_dependency | resource_boundary | time_boundary | unsupported_platform_or_toolchain | post_processing_only | not_attempted | successful | undiagnosed",
      "wrapper_or_option_changes": [],
      "expected_output_contract": {
        "contract_type": "output_dir | result_file | stdout_marker | generated_plot | executed_notebook | service_response | checkpoint | log_or_stats_file | data_summary | unknown",
        "expected_refs_or_paths": [],
        "inspection_method": "",
        "observed": false,
        "observation_refs": []
      },
      "route_result_interpretation": "representative_success | limited_success | failed_with_confirmed_cause | failed_but_alternative_exists | setup_or_component_evidence | unclear",
      "evidence_refs": []
    }
  ],
  "environment_context_used": {},
  "interventions": [],
  "limitations": [],
  "evidence_refs": []
}
```

### `evidence_semantics.json`

```json
{
  "schema": "d3_evidence_semantics_v1",
  "stage": "D3_executability",
  "selected_candidate_id": "",
  "workflow_kind": "full_reproduction | near_full_reproduction | reduced_representative | documented_demo | artifact_evaluation_path | post_processing_only | result_validation_only | dataset_loading_or_aggregation | dry_run_or_plan | component_probe | no_runnable_workflow",
  "output_provenance": "newly_generated_by_reviewer | loaded_from_deposited_files | validated_precomputed_output | derived_from_existing_results | dry_run_no_artifact_output | no_valid_output",
  "artifact_specific_logic_exercised": "yes | limited | no | unknown",
  "paper_facing_output": "yes | limited | no | unknown",
  "generic_dependency_or_example": false,
  "component_only": false,
  "representativeness_boundary": "",
  "upstream_generation_attempted": "yes | no_resource_or_time_gated | no_not_documented | no_not_applicable_dataset_package | no_unattempted",
  "resource_boundary_position": "before_artifact_logic | after_bounded_artifact_logic | after_valid_bounded_output | not_resource_gated | unknown",
  "declared_artifact_type": "tool_repo | pipeline | notebook_package | dataset_package | result_package | replication_package | unknown",
  "result_package_declared_by_artifact": false,
  "entry_origin": "readme_declared | docs_declared | artifact_eval_path | documented_file_inventory | help_or_makefile_grounded | source_inferred | reviewer_constructed | unknown",
  "input_origin": "artifact_packaged_sample | readme_specified_input | reviewer_owned_valid_input | precomputed_only | missing_or_unknown | not_applicable",
  "output_kind": "paper_metric_or_table | artifact_report_or_log | transformed_artifact | setup_banner_or_help | precomputed_result_summary | no_valid_output | unknown",
  "paper_mapping_strength": "direct | bounded_direct | limited | none | unknown",
  "coverage_boundary": "full_or_near_full | bounded_representative | component_only | result_validation_only | setup_only | resource_blocked_before_logic | unknown",
  "rw1_rw5_fact_table": {
    "RW1_entry_source_satisfied": {"satisfied": false, "evidence": "", "refs": []},
    "RW2_execution_target_satisfied": {"satisfied": false, "evidence": "", "refs": []},
    "RW3_valid_output_satisfied": {"satisfied": false, "evidence": "", "refs": []},
    "RW4_paper_mapping_satisfied": {"satisfied": false, "evidence": "", "refs": []},
    "RW5_coverage_boundary_satisfied": {"satisfied": false, "evidence": "", "refs": []},
    "evidence_tier_candidate": "E0 | E1 | E2 | E3 | unknown"
  },
  "resource_boundary_assessment": {
    "boundary_type": "api_gated | hardware_gated | data_gated | network_gated | toolchain_gated | time_gated | host_policy_gated | none | unknown",
    "declared_by_artifact": "yes | no | unknown",
    "position": "before_artifact_logic | after_bounded_artifact_logic | after_valid_bounded_output | not_resource_gated | unknown",
    "artifact_logic_reached": "yes | limited | no | unknown",
    "paper_facing_output_before_boundary": "yes | limited | no | unknown",
    "blocks_all_reviewer_realistic_routes": "yes | no | unknown",
    "alternative_route_available": "yes | no | unknown",
    "alternative_evidence_exists": "yes | no | unknown",
    "evidence_effect": "paper_facing_output_observed_before_boundary | boundary_before_artifact_logic | limitation_only | not_applicable | unclear",
    "reason": "",
    "evidence_refs": []
  },
  "reviewer_observed_output_note": "",
  "evidence_refs": []
}
```

### `stage_packet.json`

This is the compact D3 handoff. Keep it small and point to the two detailed
files.

```json
{
  "schema": "d3_stage_packet_v2",
  "stage": "D3_executability",
  "stage_goal": "Attempt and record a documented reviewer-realistic workflow route.",
  "status": "success | limited | failed | blocked",
  "summary": "",
  "route_scope": "configured_repo_only | artifact_universe_incomplete | artifact_universe_complete",
  "route_scope_reason": "",
  "artifact_boundary_warnings": [],
  "selected_workflow": {
    "candidate_id": "",
    "source_ref": "",
    "original_command_or_action": "",
    "executed_command_refs": [],
    "result": "succeeded | limited | failed | blocked"
  },
  "route_evidence_ref": "artifacts/D3_executability/route_evidence.json",
  "evidence_semantics_ref": "artifacts/D3_executability/evidence_semantics.json",
  "route_selection_checkpoint_ref": "artifacts/D3_executability/route_selection_checkpoint.json",
  "environment_context_used": {},
  "outputs_observed": [],
  "output_snapshots": [],
  "interventions": [],
  "limitations": [],
  "handoff": {
    "d4_should_use": [],
    "d5_should_use": [],
    "known_boundaries": [],
    "refs": []
  },
  "evidence_refs": []
}
```

For compatibility with D4/final scoring, also copy these objects from
`evidence_semantics.json` into `stage_packet.json`:

- `evidence_semantics`
- `rw1_rw5_fact_table`
- `resource_boundary_assessment`
- `route_arbitration`

These copied objects should be concise mirrors, not a second independent
interpretation.

## Completion Criteria

D3 may finish after one selected documentation-grounded route has been attempted
or explicitly classified as blocked/no-runnable-workflow, and all required D3
JSON files are written.

Do not finish D3 merely because the environment works. Do not mark workflow
success based only on setup/help/import/version output.
