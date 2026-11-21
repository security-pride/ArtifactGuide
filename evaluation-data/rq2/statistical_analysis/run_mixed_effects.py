#!/usr/bin/env python3
"""Run-level logistic mixed-effects analysis for RQ2.

Input: a case-level predictions CSV with columns
    system, protocol, run, suite_id, gold_badge, pred_badge
where each row is one artifact x configuration x run observation
(60 artifacts x 13 configurations x 3 runs = 2340 rows). A run is correct
when pred_badge equals gold_badge; runs without a parseable final badge are
counted as incorrect.

Models
------
Main        : correct ~ configuration + (1 | artifact), reference = ArtifactCopilot
Secondary   : correct ~ system + (1 | artifact)
Sensitivity : GEE binomial/logit, exchangeable correlation, artifact clusters

Usage
-----
    python3 run_mixed_effects.py <path-to-case-level-predictions.csv>

Outputs mixed_effects_results.csv with odds ratios (ArtifactCopilot vs
baseline; OR > 1 favors ArtifactCopilot), 95% CIs, and Holm-adjusted p values.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy.stats import norm
from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM


def holm(pvals: np.ndarray) -> np.ndarray:
    m = len(pvals)
    order = np.argsort(pvals)
    ranked = pvals[order]
    adj = np.maximum.accumulate((m - np.arange(m)) * ranked)
    out = np.empty(m)
    out[order] = np.minimum(adj, 1.0)
    return out


def contrasts_bayes(res, labels, term):
    names = list(res.model.exog_names)
    rows, pvals = [], []
    for lab in labels:
        name = f"{term}[T.{lab}]"
        i = names.index(name)
        beta = float(res.fe_mean[i])
        se = float(res.fe_sd[i])
        p = float(2 * norm.sf(abs(beta / se)))
        # report as ArtifactCopilot vs baseline: invert the baseline-vs-reference contrast
        rows.append(dict(comparison=lab, or_=float(np.exp(-beta)),
                         ci_low=float(np.exp(-(beta + 1.96 * se))),
                         ci_high=float(np.exp(-(beta - 1.96 * se))),
                         p_raw=p))
        pvals.append(p)
    for r, a in zip(rows, holm(np.asarray(pvals))):
        r["p_holm"] = float(a)
    return rows


def contrasts_gee(res, labels):
    rows, pvals = [], []
    for lab in labels:
        name = f"C(config)[T.{lab}]"
        beta = float(res.params[name])
        se = float(res.bse[name])
        p = float(res.pvalues[name])
        rows.append(dict(comparison=lab, or_=float(np.exp(-beta)),
                         ci_low=float(np.exp(-(beta + 1.96 * se))),
                         ci_high=float(np.exp(-(beta - 1.96 * se))),
                         p_raw=p))
        pvals.append(p)
    for r, a in zip(rows, holm(np.asarray(pvals))):
        r["p_holm"] = float(a)
    return rows


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    data_path = Path(sys.argv[1])

    df = pd.read_csv(data_path).rename(columns={"suite_id": "artifact"})
    df["correct"] = (df["pred_badge"] == df["gold_badge"]).astype(int)
    proto_paper = {"Full System": "Full System", "ACM v1.1": "ACM v1.1",
                   "Rubric": "ARTIFACTGUIDE", "Skill+Rubric": "Skills+ARTIFACTGUIDE"}
    df["protocol"] = df["protocol"].map(proto_paper).fillna(df["protocol"])
    df["system_label"] = df["system"].replace({"ArtifactCopilot": "ARTIFACTCOPILOT"})
    df["config"] = df["system_label"] + " | " + df["protocol"]
    df["config"] = pd.Categorical(df["config"], categories=sorted(
        df["config"].unique(), key=lambda c: (not c.startswith("ARTIFACTCOPILOT"), c)))
    df["system_cat"] = pd.Categorical(df["system_label"], categories=[
        "ARTIFACTCOPILOT", "OpenHands", "Cline", "Codex", "ExecutionAgent"])
    assert len(df) == 2340, len(df)

    # Main: configuration level
    res = BinomialBayesMixedGLM.from_formula(
        "correct ~ C(config)", {"artifact": "0 + C(artifact)"}, df).fit_map()
    config_labels = [str(c) for c in df["config"].cat.categories
                     if not c.startswith("ARTIFACTCOPILOT")]
    main_rows = contrasts_bayes(res, config_labels, "C(config)")

    # Secondary: system level
    res2 = BinomialBayesMixedGLM.from_formula(
        "correct ~ C(system_cat)", {"artifact": "0 + C(artifact)"}, df).fit_map()
    sys_rows = contrasts_bayes(res2, ["OpenHands", "Cline", "Codex", "ExecutionAgent"],
                               "C(system_cat)")

    # Sensitivity: GEE
    gee = smf.gee("correct ~ C(config)", groups="artifact", data=df,
                  family=sm.families.Binomial(),
                  cov_struct=sm.cov_struct.Exchangeable()).fit()
    gee_rows = contrasts_gee(gee, config_labels)

    out = []
    for model, rows in (("mixed-effects logistic (configuration level)", main_rows),
                        ("mixed-effects logistic (system level)", sys_rows),
                        ("GEE sensitivity (configuration level)", gee_rows)):
        for r in rows:
            lab = r["comparison"]
            comp = (f"ArtifactCopilot vs {lab} (protocols pooled)"
                    if lab in ("OpenHands", "Cline", "Codex", "ExecutionAgent")
                    else "ArtifactCopilot vs " + lab.replace(" | ", " + "))
            out.append({"model": model, "comparison": comp,
                        "odds_ratio": f"{r['or_']:.2f}",
                        "ci_low": f"{r['ci_low']:.2f}", "ci_high": f"{r['ci_high']:.2f}",
                        "p_value": f"{r['p_raw']:.4g}", "p_holm": f"{r['p_holm']:.4g}"})

    out_path = Path(__file__).parent / "mixed_effects_results.csv"
    pd.DataFrame(out).to_csv(out_path, index=False)
    print(f"wrote {out_path} ({len(out)} rows)")


if __name__ == "__main__":
    main()
