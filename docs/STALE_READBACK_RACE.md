# Stale provider readback race

## Failure and control

A provider can truthfully report that an operation is absent and still become unsafe
to retry a moment later. Another writer may commit the same logical effect between
the readback and the replay. A timestamp or a locally recent read does not close that
time-of-check/time-of-use race.

The lab now binds an `absent` readback to a provider revision. Reconciliation may
produce retry authority only when exact idempotency evidence is also present, and
the retry carries that revision as a precondition. `apply_conditional_retry` models
the provider enforcing the precondition atomically with the write:

- unchanged revision -> apply the exact retry once and advance the revision;
- changed revision -> fail closed and require a new reconciliation;
- same revision plus a contradictory existing operation -> fail closed;
- absent evidence without a revision -> never authorize retry.

## Executed race

The regression case observes absence at revision `4`. Before replay, another writer
commits the same logical operation and advances provider state to revision `5`.
An unconditional retry would create a duplicate. The conditional retry instead sees
that revision `4` is stale and raises `StaleProviderReadback`; no second effect is
applied.

A companion test starts from an unchanged revision and proves that the exact retry is
applied once, producing one effect and advancing the provider revision.

## Reproduce

```bash
python -m pytest tests/test_stale_readback_race.py -v
python -m pytest tests/test_cross_system_reconciliation.py -v
python -m pytest
ruff check .
ruff format --check .
```

## Limitations

`VersionedProviderState` is a deterministic in-memory model, not an HTTP service or
distributed datastore. The revision stands in for a provider-enforced compare-and-set,
ETag/`If-Match`, transaction version, or equivalent conditional-write contract.
Checking a revision locally and then issuing an unconditional remote write would still
be racy and is not represented as safe.

The experiment does not establish exactly-once delivery, real provider idempotency,
authentication, distributed consensus, production durability, throughput, or model
quality. A real integration must document the provider's revision semantics and make
the condition part of the atomic remote mutation itself.
