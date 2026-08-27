# Veynit Ecosystem Boundary for VoxMaestro

Status: integration boundary v0

VoxMaestro is the deterministic conversation runtime inside the Veynit ecosystem. It is not a second authority or verification plane.

## Layering

```text
Veynit
  authority · qualification · verification · evidence · stop
     |
     +-- Wind Tunnel qualifies fleet/runtime configurations
     |
     +-- consequential ToolExecutor boundary
              ^
              |
         VoxMaestro
     conversation/session truth
              ^
              |
       WebSessionAdapter
              ^
              |
     microscroll browser UI
```

## Boundary rules

- Harmless conversation stays on the VoxMaestro + local-fleet hot path.
- Models can propose actions but never create authority.
- VoxMaestro determines whether a conversation transition/tool request is legal, but does not prove a real-world consequence happened.
- Consequential `ToolExecutor` calls must eventually be wrapped by the Veynit authority/effect boundary.
- A model response, tool callback, provider 200, or runtime state transition is not sufficient evidence for `booking_confirm`.
- The current web-session gateway intentionally never emits `booking_confirm`; that event remains reserved for a future verifier-backed effect path.
- Wind Tunnel qualification binds production fleet choices to tested model/runtime/configuration digests rather than mutable names.

The canonical ecosystem-level contract lives in the private Veynit repository at `docs/ECOSYSTEM_CONTROL_PLANE.md`.
