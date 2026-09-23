# Outcome attestation v0

VoxMaestro distinguishes three claims that must not be collapsed:

```text
provider completion
        !=
workflow acceptance
        !=
semantic outcome attestation
```

A workflow can reach a legal terminal or accepted state while still satisfying only part of
the original task. Outcome attestation is the explicit post-acceptance seam for testing that
difference.

## Contract

The caller freezes a `TaskContract` before verification:

- objective;
- requirement IDs and statements;
- constraints;
- prohibited effects.

The verifier receives that contract plus the workflow result and explicit evidence bundle.
VoxMaestro hashes both sides and rejects an attestation that is bound to different bytes.

```python
from voxmaestro import (
    OutcomeGate,
    OutcomeStatus,
    TaskContract,
    TaskRequirement,
    VoxMaestroRuntime,
)

contract = TaskContract(
    task_id="move-meeting-001",
    objective="Move Sarah's meeting to Thursday afternoon and explain why.",
    requirements=(
        TaskRequirement("meeting", "Move the intended Sarah meeting to Thursday."),
        TaskRequirement("window", "Schedule the new time in the afternoon."),
        TaskRequirement("notice", "Tell Sarah the reason for the move."),
    ),
)

runtime = VoxMaestroRuntime(config, outcome_gate=OutcomeGate(my_verifier))
session = runtime.start_call("call-001")

attestation = await session.verify_outcome(
    contract,
    {"workflow_state": "accepted"},
    evidence=(
        {"ref": "calendar:event-42"},
        {"ref": "message:17"},
    ),
)

if attestation.status is not OutcomeStatus.SATISFIED:
    # repair, escalate, or require human adjudication
    ...
```

## Status is derived, not trusted

The verifier returns one finding per requirement. VoxMaestro derives the aggregate status
from those findings:

- `SATISFIED`
- `PARTIALLY_SATISFIED`
- `UNSATISFIED`
- `AMBIGUOUS`
- `INSUFFICIENT_EVIDENCE`

A verifier cannot simply return an overall "passed" flag and bypass requirement coverage.

## Boundary

Outcome verification is intentionally **not** part of the live turn path.

`process_turn()` never invokes the outcome verifier. The caller must explicitly invoke
`verify_outcome()` after its own workflow acceptance condition has been reached.

This keeps four concerns separate:

1. the state machine owns legal transitions;
2. tool/handoff adapters own external effects;
3. runtime evidence records what the system can truthfully claim happened;
4. the outcome verifier challenges whether those accepted results satisfy the frozen task.

The verifier receives no execution authority. Its output is an attestation for downstream
repair, escalation, or human adjudication.

## Adapter model

`OutcomeVerifier` is a protocol. A verifier may be a local deterministic checker, a
separate model, a human-review service, or an external independent-verification system.

No verifier vendor is a VoxMaestro dependency.

The `independence_basis` field records why the verifier is believed to be independent from
the execution path. This is evidence, not a cryptographic proof of independence.

## Fail-closed validation

VoxMaestro rejects an attestation when:

- the task-contract hash differs;
- the result/evidence hash differs;
- a requirement finding is missing;
- a verifier invents an unknown requirement;
- duplicate requirement findings are returned.

There is no fallback that converts verifier failure into `SATISFIED`.

## Current limit

This slice defines the contract and deterministic validation boundary. It does not decide
which workflows require verification, which verifier to use, or what authority a failed
attestation should have over repair/escalation. Those remain policy decisions above this
module.
