# Reconciliation evidence lineage across restart

## Failure and control

A revision-bound retry is still unsafe if the runtime forgets that newer provider evidence arrived. For example, revision `4` can truthfully show an operation as absent and authorize a conditional retry. If revision `5` later shows the effect present, replaying the older revision-4 decision after a process restart must not regain authority simply because its original precondition is still available in local memory or a stale queue.

The lab now persists one monotonic reconciliation-evidence head per exact provider operation. Each record binds the provider, operation identity, exact effect hash, provider revision, evidence identity, and resulting reconciliation decision. Newer evidence replaces the head; older evidence cannot move it backward; conflicting evidence at the same revision fails closed.

Before a retry is applied, `ReconciliationEvidenceStore` verifies that the supplied evidence identity and decision are still the durable head. The check survives process restart because the head is stored in a file-backed SQLite database.

## Executed restart case

The regression test records an `absent` observation at revision `4`, producing retry authority. A new process opens the same evidence store and records a `present` observation at revision `5`. A third process then tries to apply the old revision-4 retry. The store raises `SupersededReconciliationDecision` before the retry reaches the provider-state simulation.

A companion test proves the non-conflict path: when no newer evidence exists, the current revision-bound retry authority survives restart and can apply exactly at its observed provider revision.

## Reproduce

```bash
python -m pytest tests/test_reconciliation_evidence_lineage.py -v
python -m pytest tests/test_stale_readback_race.py tests/test_cross_system_reconciliation.py -v
python -m pytest
ruff check .
ruff format --check .
```

## Limitations

This is a deterministic SQLite-backed evidence-lineage model layered over the existing in-memory versioned-provider simulation. It does not implement a real provider API, distributed consensus, provider-specific ETags, exactly-once delivery, authentication, cross-host replication, or production durability. A real integration must durably couple provider observations, retry queues, and provider-enforced conditional writes under a documented external contract.
