# Run Initialization Skill

skill_id: orchestrator_global_skill
stage: run_initialization

## Mission

Start the AE review run. Load the configured inputs, confirm the artifact repository can be inspected, and make the paper text available for later D4 work.
This stage does not produce a stage packet and does not score anything.

## Allowed Tools

- `load_run_config`
- `list_repo_tree`
- `get_paper_info`
- `read_file`
- `write_file`
- `phase_finish`

## Workflow

1. Call `load_run_config`. Pass any configured paper/config/manual target values already present in state.
   Treat the returned `artifact_universe` and `artifact_universe_ref` as required initialization evidence. It records whether the configured repo root appears complete or whether sibling artifact-like components suggest that the root may be only a subdirectory of a multi-source artifact.
2. Use `list_repo_tree` at the repository root to confirm the artifact can be inspected.
3. If the artifact universe reports `root_scope` as `subdir_possible` or `ambiguous`, record this in global memory and evidence index. Do not resolve or score the boundary in initialization, but make the risk explicit for D1/D3/D5/final scoring.
4. If a paper source is configured, call `get_paper_info` without a page cap so the full paper text is written under `artifacts/paper_source/`.
   Do not infer paper extraction success from state prose alone. Treat paper extraction as complete only when the `get_paper_info` tool result reports `ok: true` and provides a `paper_text_ref`, or when `artifacts/paper_source/paper_text.txt` already exists in the current run root.
5. Write or update `artifacts/memory/global_memory.md` with the stable review identity: artifact repo path, run root, paper source status, configured budget/policy notes, artifact universe root scope, boundary warnings when any, and the stage sequence.
6. Write or update `artifacts/memory/evidence_index.md` with refs created during initialization, including `ae_config.yaml`, `artifacts/run_initialization/artifact_universe.json`, and paper extraction refs when present.
7. Write `artifacts/memory/current_stage_memory.md` for the next stage, containing a concise note that initialization is complete and `available_gate` should inspect local source/readability/entry point/paper relation. If artifact universe root scope is not `complete_root`, this note must carry forward that D1 should preserve route scope and avoid repo-local "all routes failed" conclusions.

   Use these stable Markdown sections so later stages can parse the memory consistently:

```text
# Global Memory

## Review Identity
- repo_root:
- run_root:
- paper_source:
- artifact_url:
- official_badge:

## Artifact Universe
- artifact_universe_ref: artifacts/run_initialization/artifact_universe.json
- root_scope:
- boundary_warnings:

## Budget And Policy
- reviewer_mode:
- max_intervention_level:
- network:
- gpu:
- paid_api:

## Stage Sequence
- run_initialization
- available_gate
- D1_documentation
- D2_environment
- D3_executability
- D4_claim_support
- D5_reusability
- final_scoring
- report_generation
```

```text
# Evidence Index

## run_initialization
- ae_config: ae_config.yaml
- artifact_universe: artifacts/run_initialization/artifact_universe.json
- paper_source_profile:
- paper_text:

## available_gate
## D1_documentation
## D2_environment
## D3_executability
## D4_claim_support
## D5_reusability
## final_scoring
## report_generation
```
8. In the same response as the final memory writes, call `phase_finish` for `run_initialization` when there were no write errors.

## Completion

Finish only after run configuration is loaded, `artifacts/run_initialization/artifact_universe.json` exists or was returned by `load_run_config`, the repository tree was inspected, and, when a paper source exists, paper text extraction is confirmed by a current-run `paper_text_ref` or `artifacts/paper_source/paper_text.txt`.
Do not treat "paper extraction was attempted" as sufficient when the paper text file is missing.
Use the state summary's `tool_facts` as completion evidence only when it shows successful `load_run_config`, `list_repo_tree`, and, if applicable, successful `get_paper_info` with a paper text ref. If the file is missing or the tool fact is ambiguous, call `get_paper_info` again before writing final memory files or calling `phase_finish`.
