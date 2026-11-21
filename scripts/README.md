# scripts

This directory contains utility scripts for the submission package.

Included scripts:

- `setup_artifactcopilot_env.sh`: create the conda environment required for
  local ArtifactCopilot execution
- `download_final60_artifacts.py`: download a reviewer-selected subset of the
  60 public artifacts into a submission-local input layout
- `run_batch_reviews.py`: run ArtifactCopilot over a prepared subset manifest
- `audit_submission.sh`: scan the submission package for cache files,
  sensitive-path residues, unfinished wording, and non-ASCII text
