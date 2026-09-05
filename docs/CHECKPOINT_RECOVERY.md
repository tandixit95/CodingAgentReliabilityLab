# Checkpoint lineage: bounded partial-progress recovery

## Failure and control

A long-running agent may persist a checkpoint, complete more work, and then restart
with an older checkpoint reference. A naive scheduler that trusts the supplied
checkpoint can reissue operations already represented by the current persisted
lineage.

`CheckpointStore` persists one authoritative checkpoint head per run in SQLite. The
run is bound to the exact ordered operation plan (operation id, tool, and payload).
Each committed checkpoint names its parent, receives the next sequence number, and
records how much of that exact plan has completed. Resume succeeds only when the
caller presents the exact current checkpoint and exact bound plan.

## Executed failure case

The regression test creates checkpoints after operation 1 and operation 2. Slicing
the plan from the older checkpoint would schedule operation 2 again even though the
current lineage says it has completed. `CheckpointStore.resume` rejects that older
checkpoint as stale and resumes the current checkpoint at operation 3.

Additional tests cover restart recovery, idempotent replay of the exact same
checkpoint advance, forked stale parents, checkpoint-id conflicts, changed payloads,
cross-run checkpoint forgery, progress regression, and progress beyond the plan.

## Reproduce

```bash
python -m pytest tests/test_checkpoint_recovery.py -v
python -m pytest
ruff check .
ruff format --check .
```

## Limitations

This is a deterministic local recovery contract backed by one SQLite database. It
does not coordinate checkpoints across databases, services, filesystems, remote tool
providers, or multiple independent schedulers. The stored plan is compared exactly;
there is no semantic equivalence layer for payloads. SHA-256 is included as an
inspectable plan fingerprint, while the persisted canonical plan itself is also
compared exactly.

The experiment establishes stale-lineage rejection and deterministic partial-progress
resume behavior in this bounded model. It is not a production durability, distributed
consensus, disaster-recovery, model-quality, throughput, or exactly-once guarantee.
