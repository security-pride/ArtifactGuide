# 🧭🤖 ArtifactGuide and ArtifactCopilot

This repository is the artifact for the ICSE 2027 paper **"Beyond Running Code:
Rubric-Driven Agentic Artifact Evaluation"**.

![ICSE 2027 Artifact](https://img.shields.io/badge/ICSE_2027-Artifact-6A1B9A)
![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)
![Conda](https://img.shields.io/badge/conda-recipe-44A8FE?logo=anaconda&logoColor=white)
![License](https://img.shields.io/badge/license-Apache--2.0-green)

The artifact has two components and a data layer:

- 📘 **ArtifactGuide** — an operational, evidence-based rubric that turns the ACM
  artifact badges (Available / Functional / Reusable) into concrete gate
  conditions, five scored dimensions, and explicit evidence and intervention
  levels.
- 🤖 **ArtifactCopilot** — a phase-driven review agent that executes the rubric
  on a paper–artifact pair: it reconstructs the environment, runs a
  representative workflow, records evidence and interventions, and derives a
  badge recommendation with a full derivation trace.
- 📊 **evaluation-data** — the compact, anonymized tables behind the paper's
  RQ1–RQ4 results, plus public manifests for the 60 evaluated artifacts.

## 📦 1. What Is in This Repository

```text
artifactguide/        The rubric (pure Markdown, no code required)
  rubric_overview.md    review structure: availability gate + five dimensions
  badge_rules.md        how Available / Functional / Reusable are derived
  dimensions.md         scoring criteria for D1–D5
  evidence_levels.md    E0–E3: what counts as executable evidence
  intervention_levels.md I0–I4: reviewer interventions and their limits
  examples/             worked examples for every scoring level
artifactcopilot/      The review agent (Python package, runs from this repo root)
  core/                 phase controller, runtime, memory, trajectory
  skills/               one instruction skill per review phase
  tools/                bounded tool surface (file, shell, browser, packet checks)
  llm/                  DeepSeek-compatible JSON client
  schemas/              evidence and report JSON schemas
  examples/             ae_config.example.yaml
  api.py                programmatic entry points
evaluation-data/      Paper-facing tables and manifests (RQ1–RQ4)
scripts/              Setup, download, and batch-run scripts
```

## ⚙️ 2. Requirements and Setup

Prerequisites: **conda** (Miniconda/Anaconda), **git**, and — only for live
agent runs — a **DeepSeek API key** (or any DeepSeek-compatible endpoint).
Everything else in this artifact (the rubric, all evaluation tables) can be
inspected with no model access at all.

### 🌱 2.1 One-command environment setup

```bash
bash scripts/setup_artifactcopilot_env.sh
```

This creates a conda environment `artifactcopilot-ae` (Python 3.11 +
`requests` + `pyyaml`, defined in `artifactcopilot/environment.yml`),
verifies the dependencies, and confirms the CLI starts. Manual equivalent:

```bash
conda --no-plugins env create -n artifactcopilot-ae -f artifactcopilot/environment.yml
conda activate artifactcopilot-ae
python -m artifactcopilot --help
```

### 🔑 2.2 Model configuration (only for live runs)

```bash
export DEEPSEEK_API_KEY=...
export DEEPSEEK_BASE_URL=https://api.deepseek.com   # default
export DEEPSEEK_MODEL=deepseek-v4-flash             # default
```

All knobs (artifact path, paper path, model, budgets) are also settable in
`artifactcopilot/examples/ae_config.example.yaml`. Based on the paper's runs,
one full review costs about **$0.13** of API budget on average.

### 🗂️ 2.3 Create a workspace

All examples below keep inputs and outputs inside one workspace directory of
your choice:

```bash
export AC_WORKSPACE="$HOME/artifactcopilot-workspace"
mkdir -p "$AC_WORKSPACE"
```

## 🚀 3. Running ArtifactCopilot

### ⚡ 3.1 Quick start on a real public artifact (~5 minutes)

The quick-start case is `S36` — the public `google/magika` repository with its
public paper PDF, recorded in
`evaluation-data/manifests/final60_quickstart_manifest.csv`.

```bash
# Step 1: download the quick-start artifact into the workspace
python scripts/download_final60_artifacts.py \
  --manifest evaluation-data/manifests/final60_quickstart_manifest.csv \
  --out-root "$AC_WORKSPACE/inputs" \
  --suite-ids S36

# Step 2: run the full review; results go to "$AC_WORKSPACE/runs/S36"
python scripts/run_batch_reviews.py \
  --prepared-manifest "$AC_WORKSPACE/inputs/prepared_manifest.json" \
  --run-root "$AC_WORKSPACE/runs" \
  --suite-ids S36 \
  --max-steps 150
```

Step 1 downloads the artifact and writes `prepared_manifest.json` into the
download directory — a record of what was downloaded and where it lives.
`--prepared-manifest` in Step 2 simply points the runner at that record, so it
knows which inputs to use. Step 2 requires `DEEPSEEK_API_KEY` (§2.2).

The run writes, under the run root: `agent_state.json`, `agent_memory.json`,
`trajectory.jsonl`, per-phase `artifacts/<phase>/stage_packet.json`, the final
`artifacts/final_scoring/final_scoring_packet.json`, and the review report in
`ae_report.md` / `ae_report.json`.

### 🧪 3.2 Running on your own paper–artifact pair

```bash
python -m artifactcopilot /path/to/artifact \
  --paper /path/to/paper.pdf \
  --config artifactcopilot/examples/ae_config.example.yaml \
  --run-root "$AC_WORKSPACE/my-run" \
  --max-steps 150
```

Useful options: `--start-phase` resumes from a review phase,
`--manual-paper-targets` supplies paper-claim targets when the PDF is not
machine-readable. Run `python -m artifactcopilot --help` for the full list.

## 📈 4. Evidence Backing the Paper (RQ1–RQ4)

There are two ways to check that the artifact behaves as the paper reports:

1. **Inspect the recorded evaluations.** `evaluation-data/` contains the
   paper-facing tables for all 60 artifacts × 3 runs (and all baseline
   configurations):

   | RQ | Files | Content |
   | --- | --- | --- |
   | RQ1 | `evaluation-data/rq1/` | ArtifactCopilot vs. human adjudication; per-case three-run results |
   | RQ2 | `evaluation-data/rq2/` | Baseline comparison: badge/functional/gate-level metrics, per-round counts, mixed-effects + GEE analysis |
   | RQ3 | `evaluation-data/rq3/` | Repeated-run stability, per-case badge sequences |
   | RQ4 | `evaluation-data/rq4/` | Reviewer-study responses and summary |
   | Suite | `evaluation-data/manifests/` | Public access list for the 60 artifacts + quick-start case |

2. **Generate fresh evidence yourself.** The §3.1 quick start produces a real
   review report for a public artifact end to end — the same procedure, on a
   new sample, that produced the paper's evaluation data.

## ♻️ 5. Reusing the Two Components

### 📐 5.1 Reusing ArtifactGuide

ArtifactGuide can be reused directly as **artifact-design guidance**: when
preparing an artifact for submission, authors can self-check against the same
gate conditions (GC1–GC3), dimensions (D1–D5), representative-workflow
checklist (RW1–RW5), and evidence/intervention levels (E0–E3, I0–I4), and fix
gaps before submission. It is self-contained Markdown — no code or setup is
required. `artifactguide/badge_rules.md` states how the levels compose into
badge recommendations, and `artifactguide/examples/` shows worked cases for
every scoring level. Venues or AECs that want a different badge policy only
need to edit `artifactguide/badge_rules.md` and `artifactguide/dimensions.md`.

### 🤖 5.2 Reusing ArtifactCopilot

`artifactcopilot/api.py` exposes stable entry points. A minimal review:

```python
from artifactcopilot import run_review

runtime = run_review(
    artifact_repo="/path/to/artifact",
    paper_source="/path/to/paper.pdf",
    run_root="/tmp/artifactcopilot-run",
    stop_after_phase="D3_executability",
    max_steps=120,
)
print(runtime.paths.run_root)
```

Reusable pieces inside `artifactcopilot/`: `core/runtime.py` (run-root and
state layout),
`core/agent.py` (phase controller), `skills/` (one plain-Markdown instruction
file per review phase — editable and swappable, e.g., to follow a
venue-specific rubric), `llm/llm_client.py` (any DeepSeek-compatible
endpoint), and `tools/router.py` + `tools/specs.py` (bounded tool surface).
Reviews can be sliced by phase via `start_phase` / `stop_after_phase` and
scripted over manifests with `scripts/run_batch_reviews.py`.

## 📄 License

This artifact is released under the [Apache License 2.0](LICENSE).
