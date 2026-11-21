# ArtifactCopilot

This directory contains the paper-facing snapshot of `ArtifactCopilot`, the
rubric-guided agent used in the paper for automated artifact evaluation.

## Purpose

ArtifactCopilot is designed as a reviewer-facing AE assistant. It follows the
rubric as the only scoring authority, collects evidence phase by phase, and
produces auditable review outputs instead of directly relying on a single
opaque execution outcome.

## Included Contents

- `core/`: main agent runtime, state, memory, and execution flow
- `prompts/`: global prompting components
- `schemas/`: output and packet schemas
- `shared/`: shared filesystem helpers
- `skills/`: stage-specific review skills
- `tools/`: execution, evidence, and guardrail utilities
- `api.py`: stable reuse entrypoints for creating a runtime, creating an
  agent, and running a review programmatically
- `examples/`: example configuration
- `environment.yml`: minimal conda environment for local execution

## Suggested Entry Points

For reviewers who want a quick code-level overview:

1. `__main__.py`
2. `core/runtime.py`
3. `core/agent.py`
4. `skills/orchestrator_global_skill.md`
5. `skills/final_scoring_skill.md`

## Environment Setup

ArtifactCopilot itself has a small runtime dependency surface. The provided
conda file installs the packages needed to inspect the code and run the agent
entrypoint.

`conda` must be installed and available in `PATH`.

From the repository root, automatic setup is:

```bash
bash artifact_submission/scripts/setup_artifactcopilot_env.sh
```

This creates a conda environment named `artifactcopilot-ae` and verifies that
the ArtifactCopilot CLI can start. The setup script calls `conda --no-plugins`
to reduce host-specific plugin interference.

The equivalent manual setup is:

```bash
conda --no-plugins env create -n artifactcopilot-ae -f artifact_submission/artifactcopilot/environment.yml
conda activate artifactcopilot-ae
python -m artifact_submission.artifactcopilot --help
```

## Required Configuration

Full agent execution beyond static inspection requires LLM API configuration.
ArtifactCopilot reads the following environment variables:

- `DEEPSEEK_API_KEY`: required for model calls
- `DEEPSEEK_BASE_URL`: optional, default is `https://api.deepseek.com`
- `DEEPSEEK_MODEL`: optional, default is `deepseek-v4-flash`
- `LLM_TIMEOUT_SECONDS`: optional request timeout override

The example run configuration is in `examples/ae_config.example.yaml`. The
fields a reviewer typically needs to set are:

- `inputs.artifact_repo`
- `inputs.paper_pdf`
- `inputs.artifact_url` when a public source link should be recorded
- `policy.llm_model` if a different compatible model endpoint is used

## Review Boundaries

Full ArtifactCopilot review execution requires a valid `DEEPSEEK_API_KEY` or a
compatible hosted endpoint.

Without model credentials, reviewers can still inspect the code, the rubric,
and the evaluation tables, but they cannot run the full agent.

## Minimal Run Pattern

After environment setup and API configuration:

```bash
python -m artifact_submission.artifactcopilot <artifact_repo> --paper <paper_source>
```

or with the helper wrapper:

```bash
bash artifact_submission/artifactcopilot/examples/run_artifactcopilot.sh <artifact_repo> <paper_source> [run_root]
```

To stop after the representative-workflow phase:

```bash
python -m artifact_submission.artifactcopilot <artifact_repo> \
  --paper <paper_source> \
  --stop-after-phase D3_executability
```

## Reuse API

`api.py` exposes a small stable surface intended for reuse by other reviewers
or tool builders:

- `create_runtime(...)`: prepare run directories and shared context
- `create_agent(...)`: create a configured `ArtifactEvaluationAgent`
- `run_review(...)`: execute a review and return the populated runtime

Example:

```python
from artifact_submission.artifactcopilot import run_review

runtime = run_review(
    artifact_repo="/path/to/artifact",
    paper_source="/path/to/paper.pdf",
    run_root="/tmp/artifactcopilot-run",
    stop_after_phase="D3_executability",
    max_steps=120,
)

print(runtime.paths.run_root)
```

Reusable implementation modules are:

- `core/runtime.py`: run-root construction and persistent path layout
- `core/agent.py`: phase controller and evidence-first review loop
- `llm/llm_client.py`: DeepSeek-compatible JSON LLM client
- `tools/router.py` and `tools/specs.py`: reusable tool registration surface
- `api.py`: programmatic entrypoint over the internal runtime

## Boundary

This snapshot intentionally excludes large runtime traces, transient workspaces,
earlier agent iterations, and internal design-process materials. It is meant to
support artifact inspection, small-scale execution, and paper-result
reproduction.
