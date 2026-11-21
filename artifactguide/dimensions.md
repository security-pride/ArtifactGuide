# Dimensions

This file summarizes the meaning of `D1-D5` and what the minimum positive
threshold (`WA`) means for each dimension.

## D1 Documentation

`D1` evaluates whether the artifact's documentation gives the reviewer a usable
evaluation path.

At `WA`, the documentation should make it possible to identify:

- the artifact's goal or scope;
- how to set up the environment;
- how to run the intended workflow;
- what outputs or observations to expect; and
- how the artifact relates to a paper-facing target.

The reviewer may still need minor inference, but should not need to reconstruct
the workflow from source code.

## D2 Environment Reconstruction

`D2` evaluates whether the reviewer can establish an evaluation-usable
environment.

At `WA`, the reviewer can reach an evaluable state using:

- documented setup steps; and
- at most bounded intervention (`I0-I2`, or justified semantic-preserving
  `I3`).

If setup success depends on semantic modification (`I4`), the dimension cannot
be positive.

## D3 Workflow Executability

`D3` evaluates whether at least one representative workflow can be executed and
produce valid output.

At `WA`, the reviewer can:

- identify a representative workflow;
- execute it;
- obtain valid output; and
- record evidence at `E2` or above without `I4`.

Running installation commands, help commands, import checks, or semantically
empty utilities is not sufficient.

## D4 Claim Support

`D4` evaluates whether reviewer-observed output supports a locatable
paper-facing claim, method step, figure, table, experiment, or result object.

At `WA`, the reviewer can:

- explain the observed output;
- map it to at least one paper-facing target; and
- state the coverage boundary if the run is only partial or reduced.

`D4` is the main safeguard against treating runnable output alone as sufficient
review evidence.

## D5 Reusability

`D5` evaluates whether the artifact can be reused or transferred beyond the
original paper workflow.

At `WA`, the reviewer can:

- identify a reuse entry point;
- perform bounded transfer validation; and
- obtain explainable output on the new input, configuration, scenario, or
  independent call path.

`D5` requires `Functional` first. A nominal API or notebook is not enough by
itself if the reviewer must invent an unverifiable reuse scenario.
