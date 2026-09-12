# Frozen reliability evaluation v1

This namespace records the first aggregate reliability evaluation for Coding Agent Reliability Lab. The protocol and 11-scenario manifest were frozen at commit `f11c5d441261fbbd07be23b806c53d7c685d8560` **before any aggregate execution**; the unchanged suite has now been executed twice and published.

## What is frozen

- **11 machine-readable scenarios** in `SCENARIOS.json`, covering event identity, replay idempotency, terminal finality, approval binding, durable restart, checkpoint lineage, cross-system readback, stale-readback revision control, evidence supersession across restart, repeated lost-ack convergence, and competing retry authority.
- **Five aggregate gates** in `PROTOCOL.json`: scenario pass rate, safety pass rate, convergence pass rate, fail-closed pass rate must each equal `1.0`; duplicate-effect violations must equal `0`.
- **Two independent normalized executions** are required, and their result JSON must be byte-identical before an aggregate result can be published.
- The scenario manifest is pinned by SHA-256: `5ca2ab5cd388037d056420e678004dd9909e479c11b84fbd6424b7a7d06c9044`.

## Frozen boundary and published result

The immutable freeze commit records protocol status `frozen_unexecuted`, no `RESULTS.json`, the scenario-manifest hash, and all aggregate thresholds before outcomes. On the later execution boundary, that exact remote commit and both frozen input hashes were reverified before each write. `PROTOCOL.json` and `SCENARIOS.json` were not changed after outcomes.

Two independent process executions produced byte-identical normalized artifacts:

- `artifacts/run-1.json`: `31390b50cb0761a1c4c06f3c5783367f52fb9d4c15722f409bb5efcc1a24c203`
- `artifacts/run-2.json`: `31390b50cb0761a1c4c06f3c5783367f52fb9d4c15722f409bb5efcc1a24c203`

The published aggregate disposition is **pass** under the predeclared gates: 11/11 scenarios passed, safety 11/11, convergence 8/8, fail-closed 7/7, and duplicate-effect violations 0. The machine-readable publication is `RESULTS.json`.

For the historical freeze boundary, inspect commit `f11c5d441261fbbd07be23b806c53d7c685d8560` and run `verify_protocol.py` there. In the current post-execution tree, verify the publication and unchanged frozen inputs with:

```bash
python evals/reliability_v1/verify_results.py
```

## Claim boundary

This evaluation is a deterministic local reliability lab. The perfect result on these 11 frozen scenarios does **not** establish production reliability, exactly-once remote effects, distributed consensus, model quality, throughput, latency, or real-provider behavior. SQLite-backed and in-memory provider models remain bounded simulations.
