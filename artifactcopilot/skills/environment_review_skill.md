# D2 Environment Skill

skill_id: environment_review_skill
stage: D2_environment

## Mission

Collect evidence about whether the artifact's documented environment can be reconstructed for later AE workflow execution.

D2 is an environment stage. It may install dependencies, build containers, start containers, and run lightweight environment checks. It must not execute the artifact's representative experiment workflow, reproduction workflow, benchmark workflow, or paper-result command. Those belong to D3.

D2 does not assign the final D2 score. It records evidence for final scoring.

## Allowed Tools

- `list_repo_tree`
- `read_file`
- `run_command`
- `write_file`
- `phase_finish`

## Workflow

1. Start from D1.
   Read:
   - `artifacts/D1_documentation/stage_packet.json`
   - `artifacts/D1_documentation/readme_workflow.json`

2. Identify the documented environment route from D1 evidence.
   The primary D2 environment route must come from the README-derived setup/environment instructions captured by D1.
   Repository files can support, confirm, or clarify that README-derived route, but they must not override it by themselves.

   If D1/README says to use Docker, use that Docker route.
   If D1/README says to use pip/venv/conda, use that route.
   If D1/README gives multiple setup routes, follow the route described as default, recommended, easiest, or primary. If no route is clearly primary, choose the route that best supports the README workflow and explain why.
   If D1/README does not describe any setup route, then inspect repository environment files as fallback evidence.

   Do not choose a Dockerfile, requirements file, setup script, or build script as the primary D2 route merely because it exists in the repository. The route must be grounded in README/D1 evidence, or the packet must explicitly state that the README route was missing, ambiguous, or impossible.

3. Inspect only targeted environment files.
   Read files only when they are part of the documented environment path or needed to choose between documented setup routes, such as:
   - `Dockerfile`
   - `.devcontainer/devcontainer.json`
   - `docker-compose.yml`
   - `environment.yml`
   - `requirements.txt`
   - `setup.py`
   - `pyproject.toml`
   - setup scripts explicitly named by README or D1.

4. Choose the environment route by this priority:
   Apply this priority only among routes that are documented by README/D1 or needed because README/D1 is missing or ambiguous:
   - First, use the README/D1 primary or recommended route.
   - If README/D1 documents both `docker pull` and `docker build`, try `docker pull` first because it is the author-provided ready environment. Use `docker build` if pull is unavailable, not documented as sufficient, or the artifact explicitly asks reviewers to build locally.
   - If README/D1 documents a container route, use that route before manually reconstructing a local environment.
   - If README/D1 documents conda, venv, package manifest, or setup script as the setup route, use that route.
   - If no author-provided isolated environment exists and setup is still needed, create an evaluator-side isolated environment, such as a run-root virtual environment.

      Dataset/result/notebook package route:
      If D1 identifies the artifact as a dataset or result package and README lists inspectable files (`.csv`, `.json`, `.txt`, `.xlsx`, `.tar.xz`, `.zip`, logs, tables, models, or precomputed result objects), D2 should establish the minimal bounded environment needed to inspect those packaged files. This may be standard shell tools, `tar`/`unzip`, Python, and a run-root virtual environment with small data-analysis dependencies such as pandas when needed.
      If D1 identifies a notebook or spreadsheet analysis package, D2 should establish the minimal environment needed to execute or inspect the documented notebook/workbook route when the dependencies are explicit in notebook imports, workbook format, README file descriptions, or package metadata. Record missing version pins and undeclared packages as limitations; do not treat them as fatal when the bounded notebook/data route can be reconstructed in an isolated reviewer environment.

      Do not treat missing heavy raw archives as an automatic D2 blocker when the artifact also provides smaller README-listed result files or archives that can be loaded for bounded review. Record the missing heavy archives as a resource/data limitation and coverage boundary. The D2 question is whether a reviewer can establish an environment for the bounded evaluation path that is actually available in the artifact.

5. Do not create a Dockerfile when the artifact does not provide one.

6. Treat installing artifact-declared dependencies inside the chosen isolated environment as ordinary environment setup, not as an intervention by itself.
   Record intervention evidence when the agent adds undeclared dependencies, modifies repository files, changes permissions, edits config, bypasses author workflow, or makes semantic changes to get setup working.

7. Execute only environment setup commands in D2.
   Allowed examples:
   - `docker pull ...`
   - `docker build ...`
   - `docker run ...` for container startup or environment inspection
   - `conda env create ...`
   - `python -m venv ...`
   - `pip install -r ...`
   - README-documented install/setup scripts

   Not allowed in D2:
   - experiment reproduction commands
   - benchmark execution commands
   - paper table generation commands
   - representative workflow commands extracted for D3

   For dataset/result packages, extracting or analyzing data belongs to D3, not D2. D2 may only check that the required generic tools are available or install lightweight data-analysis dependencies in an isolated environment.

8. Lightweight environment checks are allowed.
   After setup, it is appropriate to run commands such as:
   - `python --version`
   - `which python`
   - `pip list` or targeted import checks
   - `docker run ... python --version`
   - `ls` inside the expected working directory

   These checks only establish that the environment can be entered and has expected tools. They are not D3 workflow execution.

   For Python virtualenv/conda routes, verify interpreter coherence before calling the environment ready:
   - run the check inside the exact activation/prefix that D3 will inherit;
   - include `which python`, `python --version`, `python -c "import sys; print(sys.executable)"`, and `pip -V` or equivalent;
   - confirm the observed executable path and version match the environment route you will hand off.

   If the check shows a different interpreter, version, or pip environment than the route claims, do not write that the environment is fully confirmed. Record it as an unresolved runtime-context mismatch, retry with a corrected activation-preserving command when feasible, and carry the limitation into the D2 handoff.

9. For Docker or container checks, inspect and respect `ENTRYPOINT` and `CMD`.
   Do not assume that `docker run IMAGE python --version` runs `python --version` inside the image. If the Dockerfile defines an `ENTRYPOINT`, arguments after the image name are passed to that entrypoint.

   Before running post-build checks, read the Dockerfile content and summarize:
   - the image name used by D2;
   - the `ENTRYPOINT`, if any;
   - the `CMD`, if any;
   - the expected container working directory.

   Then choose checks accordingly:
   - If the entrypoint supports `--help` or a documented dry-run/status mode, use that entrypoint-aware command as an environment check.
   - If the goal is only to inspect installed tools or files inside the image, use `docker run --entrypoint ...` to run a shell or tool directly, and record this as evaluator-side environment inspection rather than an artifact repair.
   - If the entrypoint immediately runs a dataset-gated or workflow command, do not treat that as a Docker build failure. Record the entrypoint behavior and provide a D3 handoff with the exact entrypoint-aware or entrypoint-override command needed.
   - Use the exact image tag observed from the build command, setup script, or `docker images`. Do not normalize or rewrite image names. For example, `dylin_project:latest` and `dylin:project` are different images.

10. Use `run_command` with `phase_name: D2_environment`, a clear `purpose`, and logs enabled by default.
   Long environment builds are allowed. For container or toolchain builds, use a practical timeout up to three hours when needed. If the build or pull cannot complete within the run budget, record that as an environment limitation or blocker instead of silently switching to an unrelated route.

11. Do not write the D2 stage packet in the same response as a final environment check command.
    First run the check command, observe its result, and only then write the packet. The packet must not claim a check succeeded unless the corresponding `run_command` result shows success. If a check fails, record the failed command, exact image/tag/entrypoint used, and the reason shown by stdout/stderr.

12. Record the environment execution context for later stages.
    D2 must not only say that setup succeeded. It must explain how D3 and D5 should enter and reuse the environment.

    The execution context should include, when applicable:
    - required working directory;
    - shell activation command such as `source path/to/venv/bin/activate` or `conda activate ...`;
    - command prefix for artifact workflows;
    - relevant environment variables such as `PATH`, `PYTHONPATH`, `CUDA_VISIBLE_DEVICES`, API-key placeholders, dataset paths, or model paths;
    - container image, container name, mount paths, working directory, entrypoint/CMD behavior, and the exact form for running a command inside the container;
    - whether README semantics require an activated shell rather than only an absolute interpreter path.

    Do not assume `/path/to/venv/bin/python command` is equivalent to `source venv/bin/activate && command`. They differ when the artifact workflow starts child processes or calls tools such as `python`, `pip`, `pytest`, `Rscript`, `node`, `java`, `make`, or shell scripts. If README uses shell activation, preserve that activation in the D2 handoff unless you have evidence that the workflow never invokes child commands.

13. Write `artifacts/D2_environment/stage_packet.json` in reviewer-readable natural language.
    Use natural-language field values. Prefer summary fields over machine-like route labels.
    Include at least:
    - `stage`
    - `stage_goal`
    - `status`
    - `summary`
    - `environment_source_summary`
    - `environment_route_summary`
    - `readme_route_grounding`
    - `environment_execution_context`
    - `command_summary`
    - `environment_check_summary`
    - `container_entrypoint_summary`
    - for dataset/result/notebook packages, `data_package_environment_summary`, including available generic tools, chosen data-inspection or notebook execution environment, dependency source, missing version pins, and large/raw archive limitations
    - `interventions`
    - `limitations`
    - `handoff`
    - `evidence_refs`
    The `handoff` must state exactly how D3 should execute the README workflow in the prepared environment. For example, if the README route used a virtualenv activation and a working directory, write a natural-language handoff such as: "D3 should run artifact workflow commands in a shell that first sources `<venv>/bin/activate`, then changes to `<artifact_workdir>`, then runs the original README command. This preserves PATH for child `python` calls."

    Use this exact JSON shape for `stage_packet.json`. Keep all keys present.

```json
{
  "schema": "d2_stage_packet_v1",
  "stage": "D2_environment",
  "stage_goal": "Prepare and record the documented environment route.",
  "status": "ready | limited | blocked",
  "summary": "",
  "environment_source_summary": {
    "source_kind": "README | D1_route | manifest_fallback | none",
    "source_refs": [],
    "documented_route": "",
    "fallback_reason": ""
  },
  "environment_route_summary": {
    "route_id": "",
    "route_type": "docker_pull | docker_build | docker_run | conda | venv | package_manager | native | dataset_result_inspection | notebook | unknown",
    "selected_reason": "",
    "attempt_status": "succeeded | limited | failed | not_attempted",
    "command_refs": []
  },
  "readme_route_grounding": {
    "d1_readme_workflow_ref": "artifacts/D1_documentation/readme_workflow.json",
    "grounded_in_d1": false,
    "grounded_in_readme": false,
    "deviation_reason": ""
  },
  "environment_execution_context": {
    "working_directory": "",
    "activation_command": "",
    "command_prefix": "",
    "container_image": "",
    "container_name": "",
    "container_workdir": "",
    "entrypoint": "",
    "environment_variables": {},
    "child_process_context_note": ""
  },
  "command_summary": [
    {"command_id": "", "purpose": "", "final_status": "", "refs": []}
  ],
  "environment_check_summary": {
    "checks_run": [],
    "interpreter_or_toolchain_confirmed": "yes | limited | no | unknown",
    "ready_for_d3": "yes | limited | no | unknown",
    "evidence_refs": []
  },
  "container_entrypoint_summary": {
    "image": "",
    "entrypoint": "",
    "cmd": "",
    "entrypoint_implication": "",
    "refs": []
  },
  "data_package_environment_summary": {
    "applies": false,
    "tools_available": [],
    "dependency_source": "",
    "large_or_raw_archive_limitations": []
  },
  "interventions": [],
  "limitations": [],
  "handoff": {
    "d3_environment_entry": "",
    "working_directory": "",
    "command_prefix": "",
    "activation_required": false,
    "container_run_form": "",
    "known_limitations": [],
    "refs": []
  },
  "evidence_refs": []
}
```

14. Update memory after the stage packet is written:
    - append a concise D2 summary to `artifacts/memory/stage_summaries.md`;
    - update `artifacts/memory/evidence_index.md`;
    - replace `artifacts/memory/current_stage_memory.md` with a D3 starting note that clearly states how D3 should enter or use the environment, including exact command prefixes, shell activation command, container names, image names, virtualenv paths, working directory, environment variables, and any setup limitations.
    The D3 starting note should explicitly say when child processes must inherit the activated environment.

15. In the same response as the final writes, call `phase_finish` when there were no write errors.

## Completion

Finish D2 when the documented environment route has been attempted and the result is recorded.

D2 can finish with:

- an environment ready for D3;
- a limited-readiness environment with clear limitations;
- a failed environment setup with a clear blocker and log refs.

Do not keep exploring unrelated environment routes after the documented primary route has produced enough evidence. Do not run D3 workflow commands before finishing D2.
