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

## Third milestone: ambiguous terminal states

A turn can also receive contradictory terminal events after retries, reconnects, or competing runtime paths. Silently accepting whichever event arrives last makes the final result order-dependent.

The scoped replay model now makes terminal finality explicit:

- the first active-turn terminal event establishes the terminal outcome;
- an exact replay of that terminal event is treated as a duplicate and ignored;
- a conflicting terminal event fails closed instead of overwriting the first outcome;
- any later same-turn output after termination also fails closed; foreign run/turn events remain ignored by identity.

This is a strict deterministic orchestration contract, not a claim that every production event protocol must use the same terminal semantics.

## Fourth milestone: approval interruptions

Approval-gated tool calls can be interrupted between request, human decision, and execution. A replay must not treat an old approval as permission for a changed operation.

The approval replay model now enforces that boundary explicitly:

- execution before an approval decision fails closed;
- approval or denial is bound to the exact `operation_id`, tool, and payload requested;
- denial remains authoritative on replay;
- changing the tool or payload after approval invalidates the binding and fails closed;
- conflicting approval decisions and ambiguous event ordering are rejected.

This models an orchestration safety contract only. It does not claim to implement identity, authentication, authorization policy, or human-review UX for a production agent system.

## Fifth milestone: checkpoint lineage and partial-progress recovery

A multi-step run can recover from the wrong checkpoint after a restart. If an older
checkpoint is accepted after later progress already committed, the scheduler can
reissue work that belongs to the current lineage.

The file-backed recovery model makes that boundary explicit:

- each run is bound to the exact ordered operation plan, including tool and payload;
- checkpoints form one persisted parent/sequence lineage with a single authoritative head;
- exact replay of an already committed checkpoint advance is idempotent;
- resume is allowed only from the current checkpoint and returns only unfinished operations;
- stale parents, forked lineage, changed plans, reused checkpoint identities, and impossible progress fail closed.

The failure test demonstrates that naively resuming an older checkpoint would schedule
an operation already completed in the current lineage. The model prevents that replay;
it does not claim distributed consensus or atomicity across remote systems.

See `docs/CHECKPOINT_RECOVERY.md` for the bounded experiment and limitations.

## Sixth milestone: cross-system reconciliation

A lost acknowledgement at a remote boundary leaves local state unable to distinguish
"not committed" from "committed, acknowledgement lost." Blind replay can duplicate
the side effect.

The provider-evidence model now makes the retry boundary explicit:

- matching authoritative readback suppresses retry and accepts the remote commit;
- confirmed absence is retryable only with exact idempotency evidence bound to the
  same operation and exact effect fingerprint;
- ambiguous readback remains a hard stop even when an idempotency key is claimed;
- conflicting provider, operation, payload, or idempotency evidence fails closed.

The model does not contact a real service or claim exactly-once execution. See
`docs/CROSS_SYSTEM_RECONCILIATION.md` for the bounded experiment and limitations.

## Seventh milestone: stale provider readback race

An authoritative `absent` readback can become stale before retry if another writer
commits the operation in the gap. A merely recent read therefore cannot safely
authorize an unconditional replay.

The versioned-provider model adds an atomic retry boundary:

- absent readback must carry a provider revision in addition to exact idempotency evidence;
- retry authority carries that revision as a provider-side precondition;
- an unchanged revision permits exactly one modeled retry and advances provider state;
- a changed revision fails closed and requires reconciliation again;
- contradictory same-revision state and missing revision evidence also fail closed.

This models compare-and-set/ETag-style semantics, not a real provider API. See
`docs/STALE_READBACK_RACE.md` for the executable race and limitations.

## Eighth milestone: repeated lost-ack convergence

A retry can satisfy the provider-side conditional write and still lose its acknowledgement. Restarting from the old local decision must not issue the same effect again.

The reconciliation model now closes that loop explicitly:

- a conditional retry can commit once while local completion remains unresolved;
- replay before a fresh readback fails on the advanced provider revision;
- a restarted runtime persists a newer PRESENT observation and supersedes the pre-crash retry authority;
- only the current PRESENT decision can durably mark reconciliation complete;
- an already reconciled operation cannot regain retry authority from a later absence observation.

The provider side remains a deterministic versioned-state model and completion lineage remains local SQLite state. See `docs/REPEATED_LOST_ACK_LOOP.md` for the executable case and limitations.

## Ninth milestone: competing reconcilers

Two restarted workers can load the same retry authority before either observes the other's provider commit. A local freshness check is insufficient because both workers can pass it.

The provider-race model adds an independently persisted conditional-write boundary:

- two real child processes load the same durable retry decision before a start barrier;
- a separate SQLite provider store serializes revision-conditional writes;
- exactly one worker advances the provider revision and records the exact effect;
- the losing worker fails stale, re-reads the provider as PRESENT, and converges instead of retrying again;
- equivalent same-revision provider observations from independent workers converge to one evidence head and one durable completion.

This is a single-host provider simulation, not a distributed consensus or exactly-once claim. See `docs/COMPETING_RECONCILERS.md` for the executable race and limitations.


## Tenth milestone: frozen reliability evaluation protocol

The repository now has enough independent failure-mode evidence that another narrow retry variant would add less value than a fixed cross-milestone evaluation surface. The first aggregate protocol therefore freezes **before outcomes**:

- `evals/reliability_v1/SCENARIOS.json` selects 11 representative executable scenarios from the implemented boundaries;
- `PROTOCOL.json` fixes safety, convergence, fail-closed, and duplicate-effect gates before aggregate execution;
- two independent normalized runs must be byte-identical before an aggregate result can be published;
- `RESULTS.json` is intentionally absent at this milestone, and the verifier fails if it already exists at the freeze boundary.

The harness is executable, but no aggregate score is claimed by the freeze commit. A later run must verify the remote frozen commit first, execute the unchanged suite twice, and apply the predeclared gates. Even then, the result remains a deterministic local-system evaluation rather than production or exactly-once evidence.

### Later execution result

The later execution boundary reverified remote freeze commit `f11c5d441261fbbd07be23b806c53d7c685d8560`, preserved `PROTOCOL.json` and `SCENARIOS.json` byte-for-byte, and executed the exact manifest in two independent Python processes. Both normalized outputs have SHA-256 `31390b50cb0761a1c4c06f3c5783367f52fb9d4c15722f409bb5efcc1a24c203`. All predeclared gates pass: scenario 11/11, safety 11/11, convergence 8/8, fail-closed 7/7, duplicate-effect violations 0. `evals/reliability_v1/RESULTS.json` records the result and `verify_results.py` re-derives the disposition from the frozen thresholds. The claim boundary remains unchanged.


## Run

```bash
python -m pytest
```

Or without installing the package:

```bash
PYTHONPATH=src pytest
```

## Roadmap

Further milestones include richer failure injection, trace evaluation, and release metrics. Each milestone should add a reproducible failure case and tests before making a public claim.
