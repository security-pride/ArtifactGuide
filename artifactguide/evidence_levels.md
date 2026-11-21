# Evidence Levels

Evidence levels distinguish static material from reviewer-observed execution
evidence.

## E0

No executable evidence.

There is no runnable workflow, no checkable execution state, and no valid
reviewer-observed output.

## E1

Static or precomputed material only.

This may include logs, result files, models, tables, or figures that are
present in the artifact, but not generated or validated through reviewer
execution in the current review.

`E1` can provide context, but it is not enough by itself for `Functional`.

## E2

Representative executable evidence.

The reviewer runs a representative workflow and obtains valid output.

This is the minimum execution evidence needed for `Functional`.

## E3

Full or near-full reproduction evidence.

The reviewer runs the full or near-full main workflow and obtains paper-facing
result objects.

## Why Evidence Levels Matter

Evidence levels prevent static deposited material from being treated as if it
were equivalent to reviewer-observed execution. In this rubric, higher-level
badges depend on evidence that the reviewer actually obtained or validated
during the review.
