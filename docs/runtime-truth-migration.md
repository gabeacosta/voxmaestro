# Migration Path

Current users can keep importing `VoxMaestro` from `voxmaestro`.

New integrations should import `VoxMaestroRuntime` and create one `CallSession` per call:

```python
from voxmaestro import VoxMaestroRuntime

runtime = VoxMaestroRuntime.from_yaml(
    "agent.yaml",
    tool_executor=execute_tool,
    handoff_executor=deliver_handoff,
)

session = runtime.start_call(
    call_id="call-001",
    on_filler=play_filler,
)

result = await session.process_turn(
    "Thursday at three",
    intent="schedule_appointment",
)
```

The follow-up integration slice should adapt native Pipecat frames to `RuntimeStreamProcessor`, then migrate the existing alpha conductor into a compatibility wrapper.
