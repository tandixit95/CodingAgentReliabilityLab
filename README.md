# Coding Agent Reliability Lab

A small, evidence-first lab for reproducing and preventing orchestration failures in long-running coding agents.

## First milestone: stale child completion

Nested agents often share an event transport. If the parent runtime treats every `completed` event as terminal, a child completion can end the parent turn early or overwrite the parent's final output.

This repository starts with a deterministic replay model that makes that failure explicit:

- `naive` reducer: accepts every event on the shared stream;
- `scoped` reducer: accepts output/terminal events only from the active run and turn identity;
- immutable ordered events make a scenario exactly replayable;
- ambiguous event ordering fails closed rather than silently changing the outcome.

The tests demonstrate the failure before the fix and the expected scoped behavior afterward. This is a simulation of an orchestration failure mode, not a claim about model quality or production-scale performance.

## Second milestone: retry/replay idempotency

Coding agents often retry after a crash or transport failure. A dangerous window occurs when a tool side effect succeeds but its acknowledgement is lost: replay can emit the same logical operation again.

The second deterministic model makes that boundary explicit:

- `naive` replay applies every tool request, so an exact retry duplicates the side effect;
- `idempotent` replay records a stable `operation_id` and suppresses exact retries;
- reusing one operation identity for a different tool or payload fails closed;
- request ordering remains explicit and deterministic.

The model does not claim that every real tool can be made idempotent by keying alone. It isolates the orchestration contract a runtime needs before retry/replay can be safe.

## Run

```bash
python -m pytest
```

Or without installing the package:

```bash
PYTHONPATH=src pytest
```

## Roadmap

Planned evidence-backed milestones include retry/replay idempotency, ambiguous terminal states, approval interruptions, partial-progress recovery, failure injection, trace lineage, and evaluation metrics. Each milestone should add a reproducible failure case and tests before making a public claim.
