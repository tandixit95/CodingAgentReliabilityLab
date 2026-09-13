# Frozen trace-integrity mutation evaluation v1

This namespace freezes the next Coding Agent Reliability Lab evaluation **before aggregate mutation-detection outcomes are measured**. It asks a narrower question than the core reliability score: can one canonical cross-layer trace contract detect deterministic corruption of identity, approval/effect binding, checkpoint lineage, provider revisions, and reconciliation completion without rejecting clean traces?

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

## Freeze boundary

`PROTOCOL.json` is `frozen_unexecuted`. `RESULTS.json` and aggregate run artifacts are intentionally absent. The freeze verifier checks artifact identities, corpus membership/order, detector bytes, mutation applicability, fixed gates, claim limits, and absence of an aggregate result, but it does **not** execute the frozen corpus through the detector.

Verify the freeze with:

```bash
python evals/trace_integrity_v1/verify_protocol.py
```

A later run must first verify the exact remote freeze commit and hashes, execute the unchanged corpus twice in independent Python processes, require byte-identical normalized output, and apply only the gates already frozen here. A negative result must be published rather than weakening the protocol after outcomes.

## Claim boundary

This protocol is a deterministic local mutation evaluation. Freezing a detector and mutation corpus does not establish that every corruption will be detected, and it makes no production trace-integrity, production reliability, exactly-once, distributed-consensus, real-provider, throughput, latency, or model-quality claim. The baseline data is repository-authored synthetic trace metadata; no employer data, private telemetry, credentials, or third-party dataset payloads are included.
