# Combined Evidence Release Gate v1

This directory freezes the first combined publication gate for the Coding Agent Reliability Lab.
It composes two already-published evidence packages without changing either package's own gates:

- `core_reliability_v1` at result commit `be63a2d88dc5537386dca70977bd2d258b80e1cc`;
- `trace_integrity_v1` at result commit `7d358de55cfbb50b8a5f8162d689f352a6b9c4f4`.

## Freeze boundary

The evidence manifest, evaluator source, source commit identities, source artifact hashes, and release-readiness criteria are frozen together. `RESULTS.json` is intentionally absent at this boundary and the combined evaluator has **not** been executed against the pinned evidence for an aggregate release-ready claim.

Protocol SHA-256: `3aea2286f57b832490bbe85479d042ffa525877dd99d521f69286e6494097ca1`
Evidence manifest SHA-256: `99438a2c2ab2cef784df4d3a51ad7f114d695509d657ca25c502f8d74d15397f`
Evaluator SHA-256: `a0a2ca7c771312d70b4bd5f582c0db0ec272855c67e2d95aa136c03745e106c4`

## Predeclared release criteria

A later aggregate run may report `release_ready=true` only if both exact pinned source publications remain available and byte-identical to their pinned commits, each source result matches its frozen protocol identity, each source's own published result verifier passes, every frozen source gate passes, each source records at least two required independent runs with byte-identical normalized output, and both source dispositions are `pass`.

The combined evaluator itself must then run twice independently with byte-identical normalized output before a combined result can be published.

## Fail-closed behavior

Missing files, changed current bytes, unavailable/mismatched pinned Git blobs, malformed result JSON, protocol/freeze identity drift, failed source verifiers, failed frozen source gates, insufficient reproduction, or a non-pass source disposition all prevent release readiness. A negative result is evidence and must not be repaired by changing this frozen protocol after observing it.

## Claim boundary

This gate can only support a claim about the readiness of these two deterministic local evidence publications as a combined evidence package. It does **not** establish production reliability, production trace integrity, exactly-once remote effects, distributed consensus, real-provider behavior, model quality, throughput, latency, or adoption.

## Verification

At the freeze commit, run:

```bash
python evals/release_gate_v1/verify_protocol.py
python -m pytest tests/test_release_gate_protocol.py -q
```

Do not execute `evaluate.py` against the pinned production evidence until a later run has independently reverified this frozen boundary.
