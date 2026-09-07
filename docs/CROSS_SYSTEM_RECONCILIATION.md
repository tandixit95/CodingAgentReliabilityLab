# Cross-system reconciliation after a lost acknowledgement

## Failure and control

An external provider can commit a side effect while the caller loses the
acknowledgement. Local state then says "pending" even though remote state may say
"committed". Blind retry is unsafe: if the first effect committed, the retry may
duplicate it.

`reconcile_lost_ack` models the evidence boundary before replay:

- a provider readback that confirms the exact effect is present suppresses retry;
- a provider readback that confirms absence can produce retry authority only when the
  provider also supplies exact idempotency evidence bound to the same operation id and
  exact effect fingerprint, plus a revision that can be enforced as a conditional write;
- an ambiguous/unavailable readback fails closed even if an idempotency key is
  claimed;
- conflicting provider, operation, tool, payload, or idempotency evidence fails
  closed instead of guessing.

## Executed failure case

The regression test starts from the lost-acknowledgement state: the provider-side
effect exists, but local completion is absent. A naive retry would append the same
effect a second time. Explicit provider readback instead proves that the exact
effect already committed, so reconciliation returns `accept_remote_commit` and
forbids retry.

A second test covers the retryable branch in this model. The provider must
explicitly confirm that no effect exists, bind the same operation id as an idempotency
key to the exact provider/tool/payload fingerprint, and supply a revision that the
retry can carry as an atomic provider-side precondition. Missing or mismatched
idempotency or revision evidence is insufficient.

## Reproduce

```bash
python -m pytest tests/test_cross_system_reconciliation.py -v
python -m pytest
ruff check .
ruff format --check .
```

## Limitations

This is deterministic evidence modeling, not a real provider integration. It does
not implement an HTTP client, provider authentication, webhooks, distributed
transactions, exactly-once delivery, or provider-specific idempotency semantics.
The SHA-256 fingerprint is an inspectable exact-binding mechanism for this lab, not
a substitute for a provider's documented contract.

A real integration must define what "authoritative readback" means, the provider's
idempotency scope and retention window, and a conditional mutation mechanism that
closes the race between absence readback and retry. A merely recent timestamp is not
enough. It must also define how local completion is durably reconciled after the
remote state is accepted. Until those properties are known, ambiguity remains a hard
stop.
