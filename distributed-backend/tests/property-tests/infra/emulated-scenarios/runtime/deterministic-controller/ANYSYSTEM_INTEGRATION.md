# AnySystem integration boundary

- Upstream: `https://github.com/systems-group/anysystem`
- Inspected branch: `main`
- `main` at task start: `74613a368c73fb12f25778ce33ca11c9a833da96`
- Pinned Git revision: `74613a368c73fb12f25778ce33ca11c9a833da96`
- Upstream crate version at that revision: `0.1.2`

The upstream revision matches the revision noted when the task prompt was authored. Relevant seeded `System`, `Process`, `Context::rand`, local-message, and logical-event APIs remain available.

This crate creates exactly one AnySystem node and one process: the scenario controller. The process stores declared controller state, emits the next declared action, consumes real-world barrier outcomes supplied by Dagger, and increments logical phase ticks. A seeded choice is permitted only among variants declared in the scenario JSON.

It intentionally does not import or call AnySystem model-checking APIs, network-fault APIs, node crash/recovery APIs, or distributed-system simulation behavior. EVE Trade components and business state are never represented in AnySystem. Physical actions occur in the real deployment through Dagger and scenario-specific Litmus adapters, and pytest/oracles decide business correctness independently.
