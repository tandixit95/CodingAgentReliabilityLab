# Durable execution boundary: an intentionally small experiment

## Failure and control

A tool effect may commit before a runtime records its acknowledgement. A retry
then cannot distinguish "not executed" from "executed, acknowledgement lost."
The naive baseline commits an inert effect row in one transaction, terminates its
own child process with `os._exit(73)`, and never records completion. The resumed
baseline repeats the operation and produces two rows.

The sandbox instead writes the effect and completion in one SQLite transaction.
The request identity and approval are persisted beforehand. Exact replay returns
`False` without repeating the effect. A reused identity with changed payload,
execution before approval, denial replay, and conflicting decisions fail closed.
Only `record_note` is supported; it does not invoke a real external tool.

## Executed test protocol

`tests/test_durable_sandbox.py` launches a fresh child interpreter and terminates
it at each named boundary: before effect, after effect, before commit, after
commit. A separate connection reopens the database. Before commit, no effect is
visible and recovery applies it once; after commit, recovery recognizes it and
does not reapply it. A second replay always leaves one effect.

The concurrency test launches six independent worker processes against one
file-backed database. All must exit successfully, exactly one must report a new
effect, and the persisted note count must be one. This tests concurrent local
writers; it is not a distributed-service experiment.

## Why the result is bounded

SQLite's transaction is the mechanism, not a new consensus or database algorithm.
See SQLite's [atomic commit explanation](https://sqlite.org/atomiccommit.html).
The experiment tests actual **process termination**, not operating-system crash,
power failure, filesystem faults, malicious database modification, or arbitrary
remote side effects. No claim is made that storing an idempotency key alone can
make an external API exactly-once.

Payload matching is exact string equality, not semantic JSON equivalence.
Decisions cannot be reversed by replay. A changed request needs a new operation
identity and decision; this is a chosen fail-closed contract, not a universal UX.

## Reproduce and inspect

```bash
python -m pytest tests/test_durable_sandbox.py -v
python -m pytest
ruff check .
ruff format --check .
```

The CI workflow runs only on push/pull-request events and requests read-only
repository contents. It does not schedule tasks, contact real tool providers, or
send messages. Published CI outcomes, rather than the workflow file alone, are
required evidence that a particular commit passed remotely.
