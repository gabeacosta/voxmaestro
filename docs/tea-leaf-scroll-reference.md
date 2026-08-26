# Tea Leaf Scroll World — Pattern Harvest

Reference: `amirmushichge/tea-leaf-scroll-world`

Status: architectural reference only

## Why this reference matters

The useful part of the reference is not the tea content. It is the interaction primitive: a fixed full-screen stage driven by document scroll, with ordinary MP4 clips scrubbed by scroll progress and scene copy layered over the media.

This is a good fit for the conversational microscroll landing-page direction because it produces a cinematic surface with very little runtime machinery.

## License boundary

As inspected on 2026-08-25, the reference repository reports no GitHub license and its root tree contains no `LICENSE` file. Do not copy source or media into VoxMaestro or the website. Reimplement the general interaction pattern independently.

## Pattern to harvest

The reference implementation uses:

1. a tall document track (`650vh` in the reference);
2. a fixed viewport shell;
3. one active scene/chapter index;
4. global scroll progress normalized to `0..1`;
5. segment-local progress for each transition;
6. `requestAnimationFrame` smoothing;
7. MP4 `currentTime` seeking instead of a 3D/rendering library;
8. short crossfades between adjacent clips;
9. still-image posters as visual fallback;
10. `prefers-reduced-motion` fallback;
11. a chapter rail and progress indicator;
12. presentation state owned entirely by the browser.

No GSAP, Three.js, React animation library, or canvas dependency is required for the core effect.

## What not to inherit

### Eager video blob loading

The reference fetches all five MP4 transitions on mount and converts them to Blob URLs. The checked-in videos total roughly 43.8 MB.

That is too aggressive for a conversion-focused landing page, especially on mobile Safari and constrained networks.

Our default should be:

- preload the current scene and the next scene;
- use `preload="metadata"` for later scenes;
- promote the next clip to full load before it becomes active;
- release media that is safely behind the visitor when memory pressure matters;
- always keep a still poster available;
- never block text/conversation readiness on cinematic media readiness.

### Scroll position as business state

The reference maps scroll directly to a linear story. Our page must remain non-authoritative.

VoxMaestro emits semantic conversation state. The browser chooses how that state affects the visible chapter.

Do not persist or infer business truth from chapter index or scroll position.

### One mandatory linear journey

A conversion page should permit the visitor's intent to shorten or reweight the journey.

Example:

```text
visitor asks pricing
  -> runtime semantic state: pricing_question
  -> frontend may advance/reveal PRICING/PROOF scene
  -> no need to force the visitor through every previous scene
```

Scroll remains user-controlled. Semantic events may suggest or reveal a destination, not covertly take authority over the session.

## Proposed browser architecture

```text
DocumentScroll
     |
     v
ScrollController
  - normalizedProgress
  - activeScene
  - localSceneProgress
     |
     +-------------------+
     |                   |
     v                   v
MediaScrubber       ScenePresenter
  - video refs         - copy
  - rAF smoothing      - proof cards
  - crossfade          - CTA
  - poster fallback    - conversation affordance
     ^                   ^
     |                   |
     +---------+---------+
               |
        PresentationState
               ^
               |
     RuntimeSemanticEvent
       from VoxMaestro gateway
```

The `ScrollController` never calls models or tools.

The VoxMaestro gateway never manipulates DOM scroll offsets.

## Initial scene model

Use five commercial scenes instead of six narrative chapters:

```text
00 ARRIVE
   hero + immediate talk/type affordance

01 UNDERSTAND
   visitor intent becomes visible

02 PROVE
   contextual proof, demonstration, or relevant capability

03 ACT
   qualification / availability / bounded tool request

04 CONFIRM
   verified next step / booking request / human handoff
```

Each scene should define presentation metadata independent of VoxMaestro state names.

Suggested interface:

```ts
interface MicroscrollScene {
  id: string;
  still: string;
  transitionClip?: string;
  headline: string;
  body?: string;
  semanticTags: string[];
}
```

Runtime events map to `semanticTags`, not DOM component names.

## Runtime-to-presentation bridge

Example normalized browser event:

```json
{
  "type": "semantic_state",
  "sessionId": "...",
  "state": "engage",
  "intent": "availability_question",
  "presentationHint": "act"
}
```

`presentationHint` is advisory. It must not be treated as workflow truth.

The frontend may respond by:

- revealing the ACT scene;
- highlighting availability UI;
- keeping the visitor at their current scroll position;
- offering a small "See availability" jump control.

## Performance envelope for the landing page

Initial targets to validate rather than assume:

- text/chat interactive before cinematic media is ready;
- first scene still available with no video dependency;
- current + next transition clips are the only eager media working set;
- reduced-motion mode uses stills and normal scene transitions;
- no scroll handler performs model/network work;
- scroll listener is passive;
- media seeking is performed from one `requestAnimationFrame` loop;
- scene-state React updates occur only when the active semantic/visual scene changes, not on every scroll frame.

## Product distinction

The reference is a cinematic linear story.

Our target is a **conversational cinematic decision surface**:

```text
scroll says: where the visitor is looking
conversation says: what the visitor needs
VoxMaestro says: what workflow state is legal
ToolExecutor says: what actually happened
```

Those four signals must remain separate.
