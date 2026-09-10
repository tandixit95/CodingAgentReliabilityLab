# Competing reconciler race

## Failure and convergence rule

After a restart, two workers can load the same persisted retry authority before either sees the other's progress. If each performs a local freshness check followed by an unconditional provider write, both can commit the same logical operation. The provider boundary therefore has to make the revision precondition atomic.

This experiment uses two real Python child processes, one SQLite database for persisted reconciliation evidence, and a separate SQLite database as a modeled external provider. Both workers load the same revision-0 retry decision before a start barrier opens. The provider serializes the conditional write under one transaction:

1. exactly one worker sees revision `0`, commits the effect, and advances the provider to revision `1`;
2. the other worker reaches the provider after that commit and fails with `StaleProviderReadback`;
3. both workers re-read revision `1` as PRESENT;
4. equivalent same-revision readbacks converge to one persisted evidence head even when the workers use different local evidence IDs;
5. reconciliation completion is idempotent, and the provider contains exactly one effect after another restart.

The important boundary is not worker election. Both workers are allowed to race. Safety comes from the provider-side conditional write plus mandatory re-read after losing the race.

## Reproduce

```bash
python -m pytest tests/test_competing_reconcilers.py -v
python -m pytest tests/test_repeated_lost_ack_loop.py tests/test_reconciliation_evidence_lineage.py tests/test_stale_readback_race.py -v
python -m pytest
ruff check .
ruff format --check .
```

## What this proves

Under this bounded model, two independent restarted workers cannot both satisfy the same provider revision precondition. One conditional write commits, the stale competitor is rejected, and both can converge from a fresh PRESENT observation to one durable reconciliation completion.

## Limitations

`VersionedProviderStore` is a SQLite-backed provider simulator on one machine, not a real SaaS API, queue, object store, or multi-region database. SQLite transaction serialization stands in for a provider CAS/ETag/conditional-create contract. The test does not establish distributed consensus, exactly-once delivery, lock-freedom, fairness, production durability, or behavior under network partitions and host failure.
