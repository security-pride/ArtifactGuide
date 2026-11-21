# Report Generation Skill

skill_id: report_synthesis_skill
stage: report_generation

## Mission

Write the reviewer-facing AE report from the canonical final scoring packet.

This stage is presentation only. It does not evaluate the artifact, does not run commands, does not repair evidence, and does not create a second scoring source. The report should help a human reviewer understand what the agent observed, what badge it recommends, why that recommendation follows from the rubric, and where the evidence files are.

## Tools

Use only:

- `read_file`
- `write_file`
- `phase_finish`

Do not use `run_command` in report generation.

## Source of Truth

The canonical source is:

- `artifacts/final_scoring/final_scoring_packet.json`

All report scores, badge conclusions, Functional/Reusability decisions, confidence, evidence tier, highest intervention, limitations, author clarification requests, and human-review-sensitive points must be copied from that packet.

Supporting stage packets may be read only to clarify prose or evidence refs:

- `artifacts/available_gate/stage_packet.json`
- `artifacts/D1_documentation/stage_packet.json`
- `artifacts/D2_environment/stage_packet.json`
- `artifacts/D3_executability/stage_packet.json`
- `artifacts/D4_claim_support/stage_packet.json`
- `artifacts/D5_reusability/stage_packet.json`

If the final scoring packet and a stage packet disagree, report the final scoring packet as the decision source and mention the stage packet only as underlying evidence. Do not reconcile by re-scoring.

## Report Rules

1. Do not rescore.
   Never derive a new D1-D5 score, badge, Functional decision, Reusable decision, evidence tier, intervention level, or confidence value. Copy those fields from `final_scoring_packet.json`.
   The report stage is not allowed to fix, reinterpret, or normalize a score contradiction by choosing the value that looks more plausible. If `final_scoring_packet.json` is internally contradictory, report generation must preserve the canonical fields in JSON and make the contradiction visible as a limitation/sensitive point rather than inventing a different D1-D5 score.

2. Preserve exact final decisions.
   The Markdown report and JSON companion must match the final scoring packet for:
   - Available decision;
   - D1-D5 scores;
   - evidence tier;
   - highest intervention;
   - Functional;
   - Reusable;
   - badge recommendation;
   - confidence.
   For the JSON companion, preserve the value shape from the final scoring packet. If `badge_recommendation`, `confidence`, `evidence_tier`, `highest_intervention`, `functional_decision`, or `reusable_decision` is an object in the final scoring packet, copy that object. Do not collapse it into a string or boolean.
   The JSON companion must be a field-for-field presentation copy for the canonical decision fields. In particular, `report_json["dimension_scores"]` must equal `final_scoring_packet["dimension_scores"]` byte-for-byte after JSON parsing; do not rewrite score reasons, checklist summaries, limiting items, or refs.

3. Explain, do not reinterpret.
   Use natural language to summarize `score_reason`, `functional_decision.reason`, `reusable_decision.reason`, `badge_reason`, limitations, and sensitive points. Do not add new criteria or stronger/weaker conclusions.
   Do not reconstruct checklist items in Markdown unless you copy them directly from the final scoring packet. It is safer to report each dimension's score, score reason, limiting items, and refs than to restate every checklist item from memory.

4. Keep D3/D4 evidence separate from D5 reuse evidence.
   The `Execution and Claim Evidence` section must summarize only D3 workflow evidence and D4 claim-support evidence. Do not use D5 transfer-validation inputs, toy examples, reuse commands, or generated reuse outputs as the representative D3 workflow.
   The `Reuse Evidence` section must summarize only D5 reuse evidence. If D3 and D5 both mention executable behavior, keep their input names, command refs, and output refs in separate paragraphs.
   When in doubt, copy the D3 workflow description from `artifacts/D3_executability/stage_packet.json` and the D5 transfer description from `artifacts/D5_reusability/stage_packet.json`.

5. Keep author clarification selective.
   Include author clarification requests only if they are present in the final scoring packet. Do not invent author questions for ordinary missing dependencies, failed commands, unclear docs, timeouts, hardware absence, or missing files; those should be presented as limitations/blockers unless the final scoring packet already marks them as clarification requests.

6. Evidence refs are part of the report.
   Every major section should point to concrete refs from the final scoring packet or stage packets. Prefer run-root relative paths such as `artifacts/D3_executability/stage_packet.json`.
   Evidence refs must be raw paths or command ids only. Do not append explanatory text inside a ref, for example write `repo/README.md` rather than `repo/README.md (full read, 44686 chars)`. Put explanatory notes in nearby prose.

7. Reviewer-facing style.
   Write concise Markdown. The reader is an AE reviewer, so the report should foreground the recommendation, the decisive evidence, blockers/limitations, and evidence refs. Avoid internal implementation commentary.

8. Add a reviewer verification checklist.
   Include a short `## Reviewer Verification Checklist` section before `## Human Review Notes`. This checklist is not a new scoring source. It should translate existing uncertainty, boundary, and limitation facts from the final scoring packet into 1-5 concrete checks a human reviewer may want to verify.
   Use it for issues that could plausibly affect Functional, Reusable, confidence, or author clarification, such as partial execution boundaries, dataset/package-level reuse vs tool-level reuse, resource-gated coverage gaps, intervention-level ambiguity, missing/corrupt data, or stage-packet contradictions.
   Do not invent ordinary troubleshooting tasks. Do not ask the reviewer to rerun every failed command. Do not change the badge or D1-D5 scores based on this checklist.
   Each checklist item should state:
   - what to verify;
   - why it matters;
   - affected decision area (`available`, `functional`, `reusable`, `confidence`, or `author_clarification`);
   - evidence refs.

## Markdown Structure

Write `ae_report.md` and `artifacts/report_generation/ae_report.md` with this structure:

1. `# Artifact Evaluation Report`
2. `## Recommendation`
   - badge recommendation;
   - confidence;
   - one-paragraph rationale from the final scoring packet.
3. `## Score Summary`
   - Available result;
   - D1-D5 scores in a compact table;
   - evidence tier;
   - highest intervention;
   - Functional and Reusable status.
4. `## Dimension Findings`
   - D1-D5 subsections;
   - each subsection includes score, reason, blocking/limiting items, and refs.
5. `## Execution and Claim Evidence`
   - representative workflow evidence from D3 only;
   - D4 claim-support summary from D4 only;
   - limitations on coverage or evidence tier.
6. `## Reuse Evidence`
   - D5 summary and transfer validation status from final scoring.
7. `## Interventions and Limitations`
   - highest intervention;
   - key limitations and blockers.
8. `## Reviewer Verification Checklist`
   - 1-5 reviewer-facing checks derived from limitations, author clarification requests, human-review-sensitive points, and final scoring reasons;
   - omit this section only if there are no material uncertainties or boundary checks.
9. `## Human Review Notes`
   - author clarification requests if present;
   - human-review-sensitive points if present.
10. `## Evidence Appendix`
   - bullet list of evidence refs as raw paths or command ids only.

## JSON Companion

Write `ae_report.json` and `artifacts/report_generation/ae_report.json`.

The JSON companion is a normalized presentation copy, not a new scoring artifact. It must include:

- `schema`: `ae_report_json_v1`
- `source_final_scoring_packet`: `artifacts/final_scoring/final_scoring_packet.json`
- `available_gate`
- `dimension_scores`
- `evidence_tier`
- `highest_intervention`
- `functional_decision`
- `reusable_decision`
- `badge_recommendation`
- `confidence`
- `limitations`
- `author_clarification_requests`
- `human_review_sensitive_points`
- optional `reviewer_verification_checklist`
- `evidence_refs`

These values must be copied from `final_scoring_packet.json`. You may add `report_summary` and `report_sections`, but they must not contain new scores or revised badge conclusions.

Use this exact top-level shape:
The machine-readable schema is also stored at `artifactcopilot/schemas/ae_report.schema.json`.

```json
{
  "schema": "ae_report_json_v1",
  "source_final_scoring_packet": "artifacts/final_scoring/final_scoring_packet.json",
  "available_gate": {},
  "dimension_scores": {},
  "evidence_tier": {},
  "highest_intervention": {},
  "functional_decision": {},
  "reusable_decision": {},
  "badge_recommendation": {},
  "confidence": {},
  "limitations": [],
  "author_clarification_requests": [],
  "human_review_sensitive_points": [],
  "reviewer_verification_checklist": [
    {
      "item": "Verify whether the D5 transfer evidence should count as artifact-level reuse.",
      "why_it_matters": "The current evidence validates a dataset/package-level reuse surface rather than the primary tool workflow.",
      "affects": ["reusable", "confidence"],
      "evidence_refs": []
    }
  ],
  "evidence_refs": [],
  "report_summary": "Short reviewer-facing summary.",
  "report_sections": {}
}
```

The object-valued fields above must preserve the shape used in `final_scoring_packet.json`. Do not convert:

- `available_gate` to a prose string;
- `evidence_tier` to `"E2"` when the packet uses `{"tier": "E2", ...}`;
- `highest_intervention` to `"I2 (reason...)"` when the packet uses `{"level": "I2", ...}`;
- `badge_recommendation` to `"available + functional"` when the packet uses `{"badge": "available + functional", ...}`;
- `functional_decision` or `reusable_decision` to booleans or short prose.

Use this construction rule:

- `report_json["available_gate"] = final_scoring_packet["available_gate"]`
- `report_json["dimension_scores"] = final_scoring_packet["dimension_scores"]`
- `report_json["evidence_tier"] = final_scoring_packet["evidence_tier"]`
- `report_json["highest_intervention"] = final_scoring_packet["highest_intervention"]`
- `report_json["functional_decision"] = final_scoring_packet["functional_decision"]`
- `report_json["reusable_decision"] = final_scoring_packet["reusable_decision"]`
- `report_json["badge_recommendation"] = final_scoring_packet["badge_recommendation"]`
- `report_json["confidence"] = final_scoring_packet["confidence"]`
- `report_json["limitations"] = final_scoring_packet["limitations"]`
- `report_json["author_clarification_requests"] = final_scoring_packet["author_clarification_requests"]`
- `report_json["human_review_sensitive_points"] = final_scoring_packet["human_review_sensitive_points"]`
- `report_json["evidence_refs"] = final_scoring_packet["evidence_refs"]`

For `reviewer_verification_checklist`, derive items only from copied final-scoring fields and cited stage evidence. Use an empty list when there are no material reviewer checks.

Do not replace these copied values with shorter summaries.
Before writing, compare the copied JSON companion fields against the source packet in memory. If any canonical field differs, rebuild the JSON companion from the source packet rather than editing the difference by hand.

## Workflow

1. Read `artifacts/final_scoring/final_scoring_packet.json`.
2. If needed for readable details, read the relevant D1-D5 stage packets referenced by the final scoring packet.
3. Draft the Markdown report by presenting the final scoring packet.
4. Draft the JSON companion as a copy/normalization of the final scoring packet fields.
   Do this before drafting optional summaries, so summaries cannot overwrite canonical fields.
5. In the same tool-call response, write:
   - `ae_report.md`
   - `artifacts/report_generation/ae_report.md`
   - `ae_report.json`
   - `artifacts/report_generation/ae_report.json`
   - optionally `artifacts/memory/stage_summaries.md`, `artifacts/memory/evidence_index.md`, and `artifacts/memory/current_stage_memory.md`
   - then call `phase_finish`

The `phase_finish` call should happen in the same response as the report writes. The runtime may stop when it sees `ae_report.md`; do not postpone `phase_finish` to a later step.

## Completion

Finish only after both Markdown report paths and both JSON report paths are written.

Do not finish if:

- the final scoring packet was not read;
- the report contains any score or badge that differs from the final scoring packet;
- the report invents author clarification requests;
- the report uses stage packets to override final scoring;
- evidence refs are missing.
