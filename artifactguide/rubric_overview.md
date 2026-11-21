# Rubric Overview

ArtifactGuide operationalizes artifact evaluation as evidence-based review
rather than as badge assignment from a single execution outcome.

The rubric is organized in two layers:

1. an `Availability` gate that determines whether the artifact contains enough
   material for substantive review; and
2. five scored dimensions, `D1-D5`, that evaluate documentation, environment
   reconstruction, workflow execution, claim support, and reusability.

## Scope

This rubric evaluates whether a public research artifact satisfies the intended
semantics of:

- `Available`
- `Functional`
- `Reusable`

It is designed for both human reviewers and automated reviewers that must
collect evidence, record interventions, and derive badge recommendations in a
traceable way.

## Review Structure

The review proceeds in the following order:

1. Check the `Availability` gate (`GC1-GC3`).
2. If the gate fails, stop substantive scoring and record only explanatory
   notes.
3. If the gate passes, evaluate `D1-D5`.
4. Record the representative workflow, evidence level, intervention level, and
   review limitations.
5. Apply the badge rules.

## Dimensions

- `D1 Documentation`: whether the artifact's documentation provides a usable
  evaluation path.
- `D2 Environment reconstruction`: whether the reviewer can reconstruct an
  evaluation-usable environment within acceptable intervention bounds.
- `D3 Workflow executability`: whether a representative workflow can be run and
  produce valid output.
- `D4 Claim support`: whether reviewer-observed output supports a locatable
  paper-facing claim, method step, or result object.
- `D5 Reusability`: whether the artifact exposes a usable reuse path that
  survives bounded transfer validation.

## Rating Scale

Each scored dimension uses the same four-level scale:

- `R`: reject
- `WR`: weak reject
- `WA`: weak accept
- `A`: accept

`WA` is the minimum positive threshold for badge derivation unless a stricter
rule is stated explicitly.

## Badge Dependency Structure

The dimensions are grouped by badge tier:

- `Functional` depends on `D2`, `D3`, and `D4`.
- `Reusable` depends on `Functional` first, and then additionally on `D1` and
  `D5`.

This reflects the intended evaluation order: an artifact must first be
reviewable and executable before stronger reuse claims can be established.

## Additional Control Layers

The rubric also defines three supporting mechanisms:

- `Representative workflow`: prevents arbitrary runnable commands from counting
  as valid execution evidence.
- `Intervention levels (I0-I4)`: distinguish original artifact quality from
  reviewer-assisted success.
- `Evidence levels (E0-E3)`: distinguish static materials from
  reviewer-observed execution evidence.

These mechanisms are critical for interpreting success conservatively and
auditing how a badge decision was reached.
