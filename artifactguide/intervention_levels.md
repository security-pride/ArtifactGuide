# Intervention Levels

Intervention levels record how much reviewer-side assistance was required to
obtain evidence.

## I0

Run as documented, without repair.

## I1

Minor setup assistance, such as:

- installing missing packages or tools
- setting environment variables
- selecting compatible versions

## I2

Minor execution repair, such as:

- fixing paths
- correcting command forms
- adjusting startup order
- extending wait time

## I3

Semantic-preserving compatibility repair, such as:

- configuration changes
- wrapper changes
- non-core script changes
- lightweight compatibility fixes

`I3` may still support `Functional`, but it must be justified and recorded
carefully.

## I4

Semantic modification, such as changing:

- algorithms
- model logic
- metrics
- experiment semantics
- result-generation logic
- core data-processing logic

`I4` blocks both `Functional` and `Reusable`.

## Why Intervention Matters

Intervention levels separate original artifact quality from reviewer-assisted
success. They prevent a repaired reviewer-side execution from being treated as
if the original artifact had worked unaided.
