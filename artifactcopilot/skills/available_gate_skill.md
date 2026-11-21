# Available Gate Skill

skill_id: available_gate_skill
stage: available_gate

## Mission

Collect structured evidence for the Available gate. Record whether the artifact has readable source material, a reviewer-usable evaluation entry point, and a paper connection sufficient for later final scoring.
For GC2, ask what a reviewer can actually do from artifact-authored entry material. A valid entry point helps the reviewer obtain, inspect, run, load, check, or evaluate the artifact. Paper text, repository structure, source code alone, passive deposited files, anonymous placeholder pages, title-only entries, source-discovered commands, help output, and paper-summary-only pages are not enough unless artifact-authored material gives a usable review path.

For file-oriented artifacts, the entry path may be an inspect/load/check surface rather than a command-oriented workflow. A README, artifact index, manifest, notebook, workbook, or package description can satisfy GC2 when it identifies reviewer-facing material and states its purpose clearly enough that a reviewer can open, execute, load, inspect, or validate it. Do not require a full setup command or reproduction script at the Available gate.

A passive deposited file by itself is not automatically an evaluation entry. Such material satisfies GC2 only when artifact-authored entry material explicitly presents it as the reviewable artifact surface or gives a concrete inspection/validation path. If the reviewer must infer the review purpose from the paper, file contents, names, or reviewer-created parsing, fail GC2 or mark it uncertain instead of promoting the file itself into an entry document.

Still fail GC2 when the listing is only names with no usable purpose, or when the reviewer would have to reverse-engineer arbitrary source code or infer the artifact's review path from paper claims alone.
Do not assign D1-D5 scores.

## Allowed Tools

- `list_repo_tree`
- `read_file`
- `get_paper_info`
- `write_file`
- `phase_finish`

## Workflow

1. Read `artifacts/memory/global_memory.md` and the current state summary.
2. Use `list_repo_tree` to inspect the repository root.
   Preserve path prefixes exactly as returned by `list_repo_tree`. If the tree
   lists `repo/README.md`, `src/main.py`, `artifact/README.md`, or any other
   prefixed path, use that exact relative path in later `read_file` or
   `list_repo_tree` calls. Do not drop the prefix and then conclude the file is
   missing. A failed read of `README.md` is not evidence that `repo/README.md`
   is missing.
   When `artifact_universe.json` reports a child component or nested README
   under the configured root, treat that child path as a valid artifact surface
   to inspect. Before marking GC1 or GC2 false because files are unreadable or
   absent, retry the strongest listed candidates using their exact listed
   relative paths and cite those exact-path attempts.
3. Identify likely entry documents such as README, INSTALL, REPRODUCE, artifact appendix, docs, manifests, indexes, notebooks, and other artifact-authored guides.
   Separate true evaluation entries from weak identity-only material. Treat title-only pages, paper summaries, passive files without evaluation instructions, placeholder/error pages, paper-only material, source trees, source-discovered commands, help text, or file names as GC2 failures unless artifact-authored material provides a usable evaluation path.
4. Read the strongest entry document candidates, with README preferred when present. README must be read completely; do not request a deliberately small `max_chars` for README.
   A minimal but valid GC2 entry may be file-oriented when it identifies the deposited material and gives its review purpose clearly enough to inspect, load, execute, or validate it. Record the exact entry evidence rather than inferring it from file extensions or paper relation. Use `dataset_or_result_index` only when artifact-authored text provides an index or description of the review surface; otherwise use `source_tree_only`, `placeholder_or_error_page`, or `none` as appropriate.
5. Write GC2 as a structured evidence observation, not only prose.
   In `gc2_entry_document_assessment`, include:
   - `evidence_status`: `pass`, `fail`, or `uncertain`;
   - `entry_source_type`: one of `artifact_document`, `notebook_or_workbook`, `dataset_or_result_index`, `source_discovered_cli`, `source_tree_only`, `placeholder_or_error_page`, or `none`;
   - `reviewer_action_from_entry`: the concrete action the entry material tells a reviewer to take, or an empty string when no such action exists;
   - `why_source_or_help_is_not_enough`: required when the only apparent action comes from source code, argparse/help text, or file names.
   If `entry_source_type` is `source_discovered_cli`, `source_tree_only`, `placeholder_or_error_page`, or `none`, record GC2 evidence as `fail` unless another artifact document is cited that gives a usable review path.
6. Confirm paper text availability from memory/state/evidence refs. If paper extraction was already done in `run_initialization`, do not call `get_paper_info` again. Only call it when there is a configured paper source and no paper extraction ref exists.
7. Write `artifacts/available_gate/stage_packet.json` in natural language. Include:
   - `stage`
   - `stage_goal`
   - `status`
   - `summary`
   - `source_accessibility`
   - `primary_entry_document`
   - `gc2_entry_document_assessment`
   - `paper_connection`
   - `limitations`
   - `handoff`
   - `evidence_refs`

   Use this exact JSON shape. Keep all keys present; use empty strings, empty arrays, or `unknown` when evidence is unavailable.
   If GC1 or GC2 fails because files are inaccessible, the packet must list the
   exact candidate paths observed in `list_repo_tree`, the exact candidate paths
   attempted with `read_file`, and whether any path-prefix mismatch remains. Do
   not write "all files are unreadable" unless exact listed paths, including
   nested prefixes such as `repo/`, were attempted and failed.

```json
{
  "schema": "available_gate_stage_packet_v1",
  "stage": "available_gate",
  "stage_goal": "Collect GC1-GC3 availability evidence.",
  "status": "complete | limited | blocked",
  "summary": "Reviewer-readable summary.",
  "source_accessibility": {
    "gc1_holds": false,
    "source_status": "accessible | inaccessible | partial | unknown",
    "source_location": "repo path or URL",
    "evidence": "What was observed.",
    "refs": []
  },
  "primary_entry_document": {
    "path": "",
    "entry_source_type": "artifact_document | notebook_or_workbook | dataset_or_result_index | source_discovered_cli | source_tree_only | placeholder_or_error_page | none",
    "read_completely": false,
    "reviewer_action_from_entry": "",
    "refs": []
  },
  "gc2_entry_document_assessment": {
    "evidence_status": "pass | fail | uncertain",
    "entry_source_type": "artifact_document | notebook_or_workbook | dataset_or_result_index | source_discovered_cli | source_tree_only | placeholder_or_error_page | none",
    "reviewer_action_from_entry": "",
    "why_source_or_help_is_not_enough": "",
    "refs": []
  },
  "paper_connection": {
    "gc3_holds": false,
    "paper_source_status": "available | unavailable | not_configured | unknown",
    "connection_evidence": "",
    "paper_refs": [],
    "artifact_refs": []
  },
  "gc_conditions": [
    {"condition": "GC1", "satisfied": false, "evidence": "", "refs": []},
    {"condition": "GC2", "satisfied": false, "evidence": "", "refs": []},
    {"condition": "GC3", "satisfied": false, "evidence": "", "refs": []}
  ],
  "limitations": [],
  "handoff": {
    "d1_starting_points": [],
    "entry_document_refs": [],
    "artifact_scope_notes": [],
    "paper_refs": []
  },
  "evidence_refs": []
}
```
8. Append a concise Available gate summary to `artifacts/memory/stage_summaries.md`.
9. Update `artifacts/memory/evidence_index.md` with the stage packet and key refs.
10. Replace `artifacts/memory/current_stage_memory.md` with the D1 starting note.
11. In the same response as the final writes, call `phase_finish` when there were no write errors.

## Completion

Finish only after `artifacts/available_gate/stage_packet.json` exists and the memory files reflect the completed stage.
Use state `tool_facts` and memory refs to avoid repeating repository listing, README reads, or paper extraction that already happened in this stage.
