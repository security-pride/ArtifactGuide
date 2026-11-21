# ArtifactCopilot V4 Skill Contract

Each V4 stage skill is a stage-scoped AE evidence protocol. Skills guide the
collector agent; they are not separate scoring agents and they do not own phase
transitions.

The injected Artifact Evaluation Rubric is the scoring authority. Evidence
stages may organize rubric-relevant facts, but final GC/D1-D5 scores, Reusable,
confidence, and badge recommendation are assigned only by
`final_scoring_skill.md`.

D4 has one explicit exception: `claim_support_skill.md` must apply the rubric
Functional predicate and write `functional_predicate` plus `d5_entry_gate` in
`artifacts/D4_claim_support/stage_packet.json`. This predicate controls whether
D5 should run. It is not a final badge recommendation and does not assign a D4
score or Reusable status.

## Active Runtime Model

- The main collector agent runs the current stage skill and writes required
  artifacts under the run root.
- `phase_finish` triggers runtime checks before a phase commit.
- When enabled, `evidence_auditor_skill.md` runs as a separate lightweight
  auditor loop before the phase commit.
- The auditor may inspect supplied artifacts and bounded refs with `read_ref`,
  but it does not run commands, edit artifacts, score, or recommend badges.
- If the auditor returns `revise_required`, the collector stays in the same
  phase, receives `<evidence_audit_context>` in the next prompt, updates the
  current-stage artifacts, writes `audit_response_round_XX.json`, and calls
  `phase_finish` again.
- The auditor has at most three phase-finish audit rounds by default. At the
  round limit, unresolved findings are carried forward instead of blocking
  forever.

`report_generation` is not audited by the evidence auditor. It only renders the
already accepted final scoring packet.

## Standard Stage Closing Protocol

For `available_gate` and D1-D5 stages:

1. Write the required stage packet and any required supporting artifact.
2. Update stage memory and evidence index.
3. Call `phase_finish`.
4. If runtime or auditor rejects the finish attempt, address the concrete
   finding in the same phase and write the requested audit response.

`phase_finish` is never a storage mechanism. If required artifacts are missing
or invalid JSON, write only the missing/fixed artifacts before calling it again.

## Shared Evidence Rules

- Cite evidence refs, command ids, log refs, paper refs, or repository refs.
- Distinguish observed facts from inferences and limitations.
- Preserve route/surface boundaries instead of converting them into scores.
- Do not silently replace artifact-authored workflows with reviewer-invented
  workflows.
- Record interventions and reviewer-created D5 material explicitly.
- JSON templates in stage skills are output contracts. Keep the keys and nesting.
  Strings containing `|` list allowed enum choices; replace them with exactly
  one allowed value in the actual JSON file. Do not write the pipe-separated
  placeholder literally.

## D1/D3/D5 Evidence Contract

- D1 writes a source-grounded documentation inventory in
  `readme_workflow.json`: documents read, author-documented commands,
  workflows, inputs, outputs, resources, placeholders, and limitations.
- D1 does not decide D3 route order or D5 reuse candidates.
- D3 consumes D1's documented commands/workflows and records its own route
  selection, attempts, skips, outputs, and execution interpretation.
- D5 writes `core_surface_analysis.json` when Functional holds and D5 is not
  skipped, plus `reuse_plan.json` when it plans or attempts transfer
  validation. Its `stage_packet.json` carries `route_accounting`,
  `d5_transfer_sufficiency_check`, and same-workflow variant facts for final
  scoring.
- Changing only parameters, config values, seeds, paths, working directory,
  benchmark subset, resource limits, or same-task input is same-workflow
  evidence, not independent reuse, unless the artifact is a dataset/result
  package whose documented public surface is that data/result contract.

## Active Skill Set

```text
orchestrator_global_skill.md
available_gate_skill.md
documentation_review_skill.md
environment_review_skill.md
execution_validation_skill.md
claim_support_skill.md
reuse_validation_skill.md
evidence_auditor_skill.md
final_scoring_skill.md
report_synthesis_skill.md
```
