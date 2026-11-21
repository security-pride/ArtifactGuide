# Badge Rules

This file summarizes the badge-derivation rules used by ArtifactGuide.

## Available

`Available` holds only if all three gate conditions pass:

- `GC1`: the artifact is publicly accessible and retrievable;
- `GC2`: a reviewer-usable entry point exists, such as a README or equivalent
  evaluation entry; and
- `GC3`: the artifact is related to the paper and contains material beyond the
  paper text itself.

Failing any of these conditions blocks substantive review.

## Functional

`Functional` requires:

- `Available` already holds;
- `D2 >= WA`;
- `D3 >= WA`;
- `D4 >= WA`;
- the review includes reviewer-observed executable evidence at `E2` or above;
- no `I4` intervention is used.

In other words, `Functional` is not awarded merely because some command runs.
The run must correspond to a representative workflow and produce valid evidence
that can be interpreted against a paper-facing target.

## Reusable

`Reusable` requires:

- `Functional` already holds;
- `D1 >= WA`;
- `D5 >= WA`;
- transfer validation succeeds on a bounded new input, configuration, scenario,
  or independent call path;
- no `I4` intervention is used.

`Reusable` therefore depends not only on execution success, but also on whether
the artifact exposes a clear and repeatable reuse path.

## Disqualifying Conditions

The following conditions prevent higher-level badges even if some evidence is
positive:

- failing the `Availability` gate prevents all later badges;
- `I4` blocks both `Functional` and `Reusable`;
- static or precomputed material without reviewer-observed execution cannot
  satisfy the execution prerequisite for `Functional`;
- `Reusable` cannot hold unless `Functional` already holds.

## Practical Interpretation

Badge derivation should be conservative. Partial execution success, static
result inspection, undocumented route discovery, or reviewer-created semantic
changes should not be promoted into higher-level badge awards.
