# Repeated lost-ack reconciliation loop

## Failure and convergence rule

A conditional retry can commit successfully and still lose its acknowledgement. After a restart, replaying the old retry decision is unsafe: the provider may already contain the exact effect even though local completion was never recorded. A robust loop must reconcile again instead of treating the pre-crash retry authority as reusable.

The lab now models the complete deterministic loop:

1. provider revision `4` reports the exact operation absent and supplies exact idempotency binding;
2. the revision-bound conditional retry commits once and advances provider state to revision `5`;
3. the retry acknowledgement is lost, so local reconciliation is still incomplete;
4. a restarted runtime reads revision `5`, observes the exact effect present, and persists that newer evidence;
5. the newer PRESENT observation supersedes the old retry authority;
6. local reconciliation is durably marked complete without applying another provider effect.

`ReconciliationEvidenceStore` now persists a completion marker only from the current `accept_remote_commit` decision. Completion is terminal for retry authority: later absence evidence cannot silently reopen an already reconciled operation. Exact completion replay across restart is idempotent.

## Executed failure case

The regression test first performs the successful conditional retry while deliberately omitting any local completion record, modeling a lost acknowledgement. Before a fresh provider readback, replaying the old decision against revision `5` fails with `StaleProviderReadback`.

After restart, the provider's revision-5 PRESENT observation is recorded. The old revision-4 decision then fails as `SupersededReconciliationDecision`, while the completion marker survives another restart. The provider-state model still contains exactly one effect.

## Reproduce

```bash
python -m pytest tests/test_repeated_lost_ack_loop.py -v
python -m pytest tests/test_reconciliation_evidence_lineage.py tests/test_stale_readback_race.py tests/test_cross_system_reconciliation.py -v
python -m pytest
ruff check .
ruff format --check .
```

## Limitations

This remains a deterministic local model. Provider state is in memory and reconciliation lineage/completion are stored in SQLite; there is no real HTTP provider, queue, cross-host transaction, webhook, authentication layer, or provider-specific idempotency/ETag contract. The experiment demonstrates fail-closed orchestration semantics under the modeled revision contract; it does not establish exactly-once delivery or production durability.
