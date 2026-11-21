# Representative Workflow

Not every runnable command counts as valid execution evidence. A workflow counts
as representative only if it satisfies all of the following conditions.

## RW1: Grounded Entry

The workflow must come from artifact-authored material such as:

- README
- artifact documentation
- paper appendix
- author-specified script
- official workflow file

If the reviewer must guess the workflow purely by browsing source code, the
workflow is not representative.

## RW2: Evaluation-Relevant Target

The workflow must invoke an evaluation-relevant program, script, notebook,
service, demo, benchmark, analysis, inference, test, or replication path.

Pure setup commands, version checks, help commands, import checks, or unrelated
utility commands do not satisfy this condition.

## RW3: Valid Output

The workflow must produce valid output. Valid output excludes:

- empty files
- pure error logs
- semantically empty prints
- dependency installation logs
- help text

## RW4: Paper-Facing Mapping

The output must be mappable to at least one paper-facing target, such as:

- a method step
- an artifact goal
- an experiment
- a claim
- a table or figure
- a case study

Execution that only proves the software starts is not enough.

## RW5: Coverage Boundary

If the workflow is reduced, bounded, or partial, the reviewer must record what
it covers and what it does not cover.

This prevents demo paths or small validation runs from being silently promoted
into full reproduction claims.
