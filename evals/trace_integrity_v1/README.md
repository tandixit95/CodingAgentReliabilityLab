# Frozen trace-integrity mutation evaluation v1

This namespace records a trace-integrity mutation evaluation for Coding Agent Reliability Lab. The detector, canonical cross-layer trace contract, clean baselines, mutation corpus, and detection gates were frozen at commit `cee88ebde76d67e99721796bf292f553f88fb5a3` **before aggregate detection outcomes were measured**; the unchanged frozen surface has now been executed twice and published.

## Frozen surface

- `TRACE_SCHEMA.json` defines six record kinds: runtime event, approval, effect, checkpoint, provider evidence, and reconciliation completion.
- `BASELINE_TRACES.json` contains 3 repository-authored clean traces representing a completed lost-ack recovery, pending retry authority, and a scoped parent turn.
- `MUTATIONS.json` contains 15 deterministic corruptions across event identity, record order, approval/effect binding, checkpoint lineage, provider revisions, and reconciliation completion.
- `src/agent_reliability_lab/trace_integrity.py` contains the frozen generic detector, mutation applicator, and deterministic corpus evaluator.
- `PROTOCOL.json` predeclares five gates: clean-trace acceptance and mutation detection must each equal `1.0`; false positives, false negatives, and detector errors must each equal `0`.
- Two independent normalized executions must be byte-identical before an aggregate detection result may be published.

Frozen SHA-256 identities:

- detector source: `d481d451106dbcf3f7fad7e89ce5aed12bdded26c179553150092524af0cd0e7`
- trace schema: `2d1281fc3c16dacca9eb2136c090417adeea160531b812e340a8a3fc6065a7cc`
- clean baselines: `9b2b62d25d849be3123c3b0212351e89c2876dbd8e861675a2a084e666f9c4c5`
- mutation corpus: `ef4f7eb8bdc8170afe15eb6ae4bf59c5bd677477dde1c0d8e398ee8aeb448af2`

## Frozen boundary and published result

The immutable freeze commit records protocol status `frozen_unexecuted`, no `RESULTS.json`, all detector/input hashes, and every threshold before outcomes. On the later execution boundary, the exact remote freeze commit and frozen hashes were reverified before execution. `PROTOCOL.json`, `TRACE_SCHEMA.json`, `BASELINE_TRACES.json`, `MUTATIONS.json`, and the detector source were not changed after outcomes.

Two independent Python-process executions produced byte-identical normalized artifacts:

- `artifacts/run-1.json`: `4d1486472eada40441a407c827495dd5cee89a8f348749f05a42ec8291baf164`
- `artifacts/run-2.json`: `4d1486472eada40441a407c827495dd5cee89a8f348749f05a42ec8291baf164`

The published aggregate disposition is **pass** under the predeclared gates: all 3 clean traces were accepted, all 15 frozen mutations were detected, false positives = 0, false negatives = 0, and detector errors = 0. The machine-readable publication is `RESULTS.json`.

For the historical freeze boundary, inspect commit `cee88ebde76d67e99721796bf292f553f88fb5a3` and run `verify_protocol.py` there. In the current post-execution tree, verify the publication and unchanged frozen detector/inputs with:

```bash
python evals/trace_integrity_v1/verify_results.py
```

## Claim boundary

This protocol is a deterministic local mutation evaluation over repository-authored synthetic trace metadata. Detecting these 15 frozen corruptions does not establish completeness against arbitrary or adversarial corruption and does not establish production trace integrity. It makes no production reliability, exactly-once, distributed-consensus, real-provider, throughput, latency, model-quality, employer-data, private-telemetry, or third-party-dataset claim.
