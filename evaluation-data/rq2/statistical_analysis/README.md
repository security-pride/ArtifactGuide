# RQ2 Statistical Analysis

## Data

All 13 configurations (four baseline agents under three protocols, plus
ArtifactCopilot) were evaluated on 60 artifacts over three repeated runs,
yielding 2,340 run-level observations. A run is coded `correct = 1` when the
predicted badge equals the human-adjudicated badge, and `correct = 0`
otherwise; runs without a parseable final badge are counted as incorrect.

## Models

- Main model: logistic mixed-effects regression,
  `correct ~ configuration + (1 | artifact)`,
  with ArtifactCopilot as the reference level. Artifact-level random
  intercepts account for repeated observations of the same artifact across
  systems and runs. Pairwise contrasts against ArtifactCopilot are reported
  as odds ratios with 95% confidence intervals; odds ratios above 1 favor
  ArtifactCopilot. P values are Holm-adjusted over the 12 pairwise
  comparisons.
- Secondary model: `correct ~ system + (1 | artifact)`, pooling protocols
  within each baseline system (Holm adjustment over 4 comparisons).
- Sensitivity analysis: GEE with binomial family, logit link, exchangeable
  working correlation, and artifact-level clustering.

## Files

- `mixed_effects_results.csv`: all contrasts (odds ratio, 95% CI, raw and
  Holm-adjusted p values) for the three models.
- `run_mixed_effects.py`: script that reproduces the analysis from the
  case-level predictions file (see header for the required input columns).

## Software

Python 3, statsmodels 0.15 (BinomialBayesMixedGLM for the mixed-effects
models, GEE for the sensitivity analysis).
