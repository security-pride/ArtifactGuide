# D1 Documentation Skill

skill_id: documentation_review_skill
stage: D1_documentation

## Mission

Read the artifact's author-written documentation and extract every setup, run, data, configuration, result-viewing, and evaluation instruction the authors tell a reviewer/user to follow.

D1 is a pure extraction stage. It does not execute commands, rank routes, decide reuse, score artifacts, or predict badges. It produces a flat inventory of documented executable instructions and referenced files.

Important boundary: a README section that only lists files and describes what each
file is for is not a command list. Record those entries in `documented_files`,
not in `documented_commands`.

## Allowed Tools

- `list_repo_tree`
- `read_file`
- `write_file`
- `phase_finish`

## Workflow

### Step 1 - Collect Context

Try to read these files. If either is missing, continue and note the limitation:

- `artifacts/available_gate/stage_packet.json`
- `artifacts/run_initialization/artifact_universe.json`

### Step 2 - Find And Read Entry Documentation

1. Call `list_repo_tree` with shallow depth.
2. Identify the primary entry document using this priority:
   `README.md` -> `README.rst` -> `README.txt` -> `README` -> any file whose name starts with `README`.
3. Read the primary entry document completely.

### Step 3 - Read Linked Documentation, Bounded

Read only files that the entry document explicitly references by path/link and that match these categories:

- install/setup guides, such as `INSTALL.md` or `docs/setup.md`;
- reproduce/evaluation guides, such as `REPRODUCE.md`, `EXPERIMENTS.md`, `ARTIFACT.md`;
- requirements/environment files, such as `requirements.txt`, `environment.yml`, `environment.yaml`;
- config examples, such as `config.yaml`, `params.json`;
- data/result READMEs, such as `data/README.md`, `results/README.md`.

Read at most 8 linked documents. If more relevant docs exist, record the un-read docs in `uncertainties`.

### Step 4 - What Not To Read

Do not open source code or executable script files in D1, including `.py`, `.java`, `.js`, `.ts`, `.sh`, `.R`, `.jl`, `.cpp`, or code notebooks.

If documentation tells the reviewer to run a script, record the documented command verbatim and cite the documentation source. Do not open the script to infer internal commands.

### Step 5 - Extract A Flat Command List

For every author-documented command or required reviewer action, add one entry to `documented_commands`.

Extraction rules:

- A `documented_commands` entry requires imperative reviewer intent from the
  documentation: a shell/code block, a command line, or wording such as "run",
  "execute", "install", "download", "set", "edit", "open", "launch", "use
  this command", or "to reproduce, do ...".
- Do not convert file-purpose inventory into commands. If the README says
  "`analysis.py`: calculates metrics" or "`notebook.ipynb`: plots Figure 2",
  record the file in `documented_files`; do not write "Run analysis.py" or
  "Open notebook.ipynb" unless the README explicitly instructs the reviewer to
  run/open it.
- Copy command/action text exactly enough to preserve author spelling, flags, placeholders, and typos.
- Use one `command_id` per independent command line.
- If a fenced shell block contains multiple independent command lines, split them into separate entries in source order.
- If a command uses backslash continuation lines, keep the continued block as one command.
- If a single command line contains `&&`, keep it as one command.
- Record conditional branches separately, such as Docker path versus local path.
- Record non-shell required actions, such as editing `.env`, setting API keys in `config.json`, downloading a dataset manually, placing files under `result/`, or changing model/dataset variables.
- Record result-viewing commands such as `cat result.txt`.
- Do not infer commands from filenames, directory structure, package entry points, source code, or a file-purpose list.
- If no documented command/action exists, leave `documented_commands` as `[]`
  and explain in `documentation_observations` and `uncertainties` that the
  README only provides a file/purpose map or passive data/result description.

For Python/API snippets:

- If the README presents a multi-line Python code block as API usage, record it as one command entry with `is_shell_command: false` and `command_kind: "run"` or `"other"`.
- If the README explicitly says to run a file such as `python example.py`, record that command.
- Do not invent `python example.py` merely because `example.py` exists.

## Command Field Rules

- `source_ref`: use `repo:<relative_path>#<section-heading>` when there is a heading, for example `repo:README.md#Installation`.
- If no heading exists, use `repo:<relative_path>#L<line-or-area>` when possible, for example `repo:README.md#L45`.
- `command_kind`: use only `install`, `data`, `run`, `evaluate`, `test`, or `other`.
- `is_shell_command`: true only for shell/CLI commands. Use false for required actions like editing config or manually downloading data.
- `working_directory`: use the documented working directory if stated; otherwise use `"unknown"`.

## Required `readme_workflow.json`

This is the full D1 inventory. Keep it flat. Do not add `documented_workflows`.

```json
{
  "schema": "d1_readme_workflow_v2",
  "stage": "D1_documentation",
  "status": "complete | limited | blocked",
  "status_reason": "",
  "documents_read": [
    {
      "path": "README.md",
      "read_completely": true,
      "why": "primary entry document"
    }
  ],
  "artifact_identity": {
    "name": "",
    "type": "tool | pipeline | library | dataset | notebook | replication_package | unknown",
    "paper_relationship": ""
  },
  "documented_commands": [
    {
      "command_id": "cmd_01",
      "source_ref": "repo:README.md#Installation",
      "command": "pip install -r requirements.txt",
      "purpose": "Install Python dependencies",
      "command_kind": "install",
      "is_shell_command": true,
      "working_directory": "unknown",
      "inputs": [],
      "outputs": [],
      "resource_requirements": [],
      "placeholders": [],
      "notes": ""
    }
  ],
  "documented_files": [
    {
      "path": "data/train.csv",
      "source_ref": "repo:README.md#Data",
      "role": "input | output | dataset | model | result | figure | config | other",
      "description": ""
    }
  ],
  "documentation_observations": [],
  "uncertainties": []
}
```

## Required `stage_packet.json`

This is a minimal handoff pointer. Do not duplicate the full command list.

```json
{
  "schema": "d1_stage_packet_v2",
  "stage": "D1_documentation",
  "status": "complete | limited | blocked",
  "summary": "One-paragraph summary of what the documentation tells a reviewer to do.",
  "documents_read_count": 1,
  "commands_extracted_count": 0,
  "resource_warnings": [],
  "readme_workflow_ref": "artifacts/D1_documentation/readme_workflow.json"
}
```

## Worked Example

Input README excerpt:

````text
## Installation
```bash
conda create -n myenv python=3.9
conda activate myenv
pip install -r requirements.txt
```

## Data
Download the dataset from https://example.com/data.zip and unzip it into `data/`:
```bash
wget https://example.com/data.zip && unzip data.zip -d data/
```

## Reproduce Results
To reproduce Table 1:
```bash
python run_experiment.py --config configs/table1.yaml
python scripts/build_table.py results.csv table1.txt
cat table1.txt
```
Results will be saved to `results/table1.csv`.
````

Expected `readme_workflow.json`:

```json
{
  "schema": "d1_readme_workflow_v2",
  "stage": "D1_documentation",
  "status": "complete",
  "status_reason": "",
  "documents_read": [
    {
      "path": "README.md",
      "read_completely": true,
      "why": "primary entry document"
    }
  ],
  "artifact_identity": {
    "name": "myenv experiment",
    "type": "replication_package",
    "paper_relationship": "Reproduces Table 1 from the associated paper"
  },
  "documented_commands": [
    {
      "command_id": "cmd_01",
      "source_ref": "repo:README.md#Installation",
      "command": "conda create -n myenv python=3.9",
      "purpose": "Create conda environment",
      "command_kind": "install",
      "is_shell_command": true,
      "working_directory": "unknown",
      "inputs": [],
      "outputs": [],
      "resource_requirements": [],
      "placeholders": [],
      "notes": ""
    },
    {
      "command_id": "cmd_02",
      "source_ref": "repo:README.md#Installation",
      "command": "conda activate myenv",
      "purpose": "Activate conda environment",
      "command_kind": "install",
      "is_shell_command": true,
      "working_directory": "unknown",
      "inputs": [],
      "outputs": [],
      "resource_requirements": [],
      "placeholders": [],
      "notes": ""
    },
    {
      "command_id": "cmd_03",
      "source_ref": "repo:README.md#Installation",
      "command": "pip install -r requirements.txt",
      "purpose": "Install Python dependencies",
      "command_kind": "install",
      "is_shell_command": true,
      "working_directory": "unknown",
      "inputs": ["requirements.txt"],
      "outputs": [],
      "resource_requirements": [],
      "placeholders": [],
      "notes": ""
    },
    {
      "command_id": "cmd_04",
      "source_ref": "repo:README.md#Data",
      "command": "wget https://example.com/data.zip && unzip data.zip -d data/",
      "purpose": "Download and extract dataset",
      "command_kind": "data",
      "is_shell_command": true,
      "working_directory": "unknown",
      "inputs": [],
      "outputs": ["data/"],
      "resource_requirements": ["network"],
      "placeholders": [],
      "notes": ""
    },
    {
      "command_id": "cmd_05",
      "source_ref": "repo:README.md#Reproduce Results",
      "command": "python run_experiment.py --config configs/table1.yaml",
      "purpose": "Reproduce Table 1",
      "command_kind": "run",
      "is_shell_command": true,
      "working_directory": "unknown",
      "inputs": ["configs/table1.yaml"],
      "outputs": [],
      "resource_requirements": [],
      "placeholders": [],
      "notes": ""
    },
    {
      "command_id": "cmd_06",
      "source_ref": "repo:README.md#Reproduce Results",
      "command": "python scripts/build_table.py results.csv table1.txt",
      "purpose": "Build Table 1 output",
      "command_kind": "evaluate",
      "is_shell_command": true,
      "working_directory": "unknown",
      "inputs": ["results.csv"],
      "outputs": ["table1.txt"],
      "resource_requirements": [],
      "placeholders": [],
      "notes": ""
    },
    {
      "command_id": "cmd_07",
      "source_ref": "repo:README.md#Reproduce Results",
      "command": "cat table1.txt",
      "purpose": "View Table 1 output",
      "command_kind": "evaluate",
      "is_shell_command": true,
      "working_directory": "unknown",
      "inputs": ["table1.txt"],
      "outputs": [],
      "resource_requirements": [],
      "placeholders": [],
      "notes": ""
    }
  ],
  "documented_files": [
    {
      "path": "requirements.txt",
      "source_ref": "repo:README.md#Installation",
      "role": "config",
      "description": "Python dependency list"
    },
    {
      "path": "configs/table1.yaml",
      "source_ref": "repo:README.md#Reproduce Results",
      "role": "config",
      "description": "Experiment config for Table 1"
    },
    {
      "path": "results/table1.csv",
      "source_ref": "repo:README.md#Reproduce Results",
      "role": "result",
      "description": "Documented output of Table 1 reproduction"
    },
    {
      "path": "data/",
      "source_ref": "repo:README.md#Data",
      "role": "dataset",
      "description": "Dataset directory populated by external download"
    }
  ],
  "documentation_observations": [
    "Dataset requires external download from https://example.com/data.zip"
  ],
  "uncertainties": []
}
```

Expected `stage_packet.json`:

```json
{
  "schema": "d1_stage_packet_v2",
  "stage": "D1_documentation",
  "status": "complete",
  "summary": "The README documents a three-part workflow: create and activate a conda environment, download and extract an external dataset, then run experiment and table-building commands to reproduce Table 1.",
  "documents_read_count": 1,
  "commands_extracted_count": 7,
  "resource_warnings": [
    "External data download required: https://example.com/data.zip"
  ],
  "readme_workflow_ref": "artifacts/D1_documentation/readme_workflow.json"
}
```

## Negative Example: File Inventory Is Not A Command List

Input README excerpt:

````text
## Where is what
- **thresholds.py**: Measuring agreement to human judgements.
- **best_metric_thresh.ipynb**: Searching for the best metric threshold.
- **results/**: Raw result JSON files.
````

Expected behavior:

- `documented_commands`: `[]`
- `documented_files`: include `thresholds.py`, `best_metric_thresh.ipynb`,
  and `results/` with their descriptions.
- `documentation_observations`: state that the README describes file purposes
  but does not provide explicit run/install/reproduce commands.
- Do not output `Run thresholds.py`, `Run best_metric_thresh.ipynb`, or any
  invented `python thresholds.py` command.

## Edge Cases

| Situation | Required behavior |
|---|---|
| README is empty or missing | Set `status: "blocked"`, explain in `status_reason`, still write both JSON files and call `phase_finish`. |
| Conditional branches, such as Docker vs local | Record each branch as separate command entries and label the branch in `notes`. |
| Documentation references a script but gives no command line | Record the script path in `documented_files`; do not fabricate a command. |
| Author says "see paper" with no concrete command | Add an uncertainty; do not invent a command. |
| Multiple OS/platform variants | Record all variants and label each in `notes`. |
| Artifact universe lists multiple repos/components | Record a limitation/uncertainty if only current repo docs were read. |

## Completion Criteria

Call `phase_finish` only after:

1. the primary entry document was read completely or its absence is recorded;
2. `readme_workflow.json` and `stage_packet.json` are written;
3. every command/action extracted from documentation has a `source_ref`;
4. D1 contains no route ranking, D3/D5 decision, score, badge, or final judgment.
