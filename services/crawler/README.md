# crawler

Fetches listings from Iranian seller sites and keeps the immutable raw archive that the entire pipeline can be rebuilt from.

**Your instructions are in this folder.** [`BRIEF.md`](./BRIEF.md) is the shared
project brief — read it first, every time — and [`SPEC.md`](./SPEC.md) is the
full specification for this service, including its acceptance criteria. Together
they are everything you need: you own `services/crawler/` and nothing else, you
never read or modify another service's folder or database, and you talk to other
services only over Kafka (plus the few synchronous HTTP pairs the brief allows).
If something you need is missing from a spec, stop and report it rather than
inventing it.

Both files are copies, distributed from `docs/specs/` by `make sync-specs`.
**Do not edit them here** — CI compares them byte-for-byte against the source and
will fail the build. Spec changes happen in `docs/specs/`, reviewed by a human.
