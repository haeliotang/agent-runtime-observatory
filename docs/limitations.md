# Limitations — what this substrate does *not* guarantee

This document exists because the honest failure mode of a governance reference
implementation is claiming closure. Governance and accountability semantics are
effectively unbounded (real IAM/audit systems have teams on seat authorization,
delegation, supersession, contested review). So rather than say "structural
accountability is closed" — a claim the next reviewer can always falsify by
finding the next edge — this repo states exactly what holds and what does not.

Each item below is a deliberate boundary of the current design, not an
undiscovered bug. Where there is a tracking issue, it is linked.

## Review-debt / attestation

| Boundary | What holds | What does **not** | Tracking |
|---|---|---|---|
| **Identity is self-declared, not authenticated** | `attested_by` is non-blank; a `seat_id` (required to clear) must reference a non-blank, unique, declared seat | The API is unauthenticated ([SECURITY.md](../SECURITY.md)); nothing proves the named human is who they say. Verified identity needs an authenticating gateway in front | — |
| **Seat *reference*, not seat *authorization*** | clearing a specific item requires a declared seat | **any** declared seat may clear **any** item — there is no per-scope authorization mapping a seat to the debt it is entitled to clear | [#29](https://github.com/haeliotang/agent-runtime-observatory/issues/29) |
| **First valid clearing wins; no supersession** | debt is cleared/open/stale derived from attestations | there is no revoke, supersede, or *contested* state — a later reviewer disagreeing cannot change the derived result; the only lever is content drift (which reopens) | [#30](https://github.com/haeliotang/agent-runtime-observatory/issues/30) |
| **Subject digest binds the run, not the goal text** | `v2` binds run id, task id, agent, reviewer seats, per-step digests, and full policy decisions | the `Goal`/`Task` *statements* are on the `Script`, not the `AgentRun`, so their prose is not bound; changing a goal's wording after review does not reopen debt | [#31](https://github.com/haeliotang/agent-runtime-observatory/issues/31) |
| **Version deprecation is a manual decision** | new attestations use `v2`; `v1` clearing power is **revoked** (it under-bound the record) | there is no automated migration — a future `v3` requires an explicit decision about whether `v2` keeps its clearing power | folded into subject-schema policy |

## Scale

| Boundary | Note | Tracking |
|---|---|---|
| **`/metrics` does a full table read** | each scrape deserializes every run and queries its attestations (N+1) to derive the debt gauges. Fine at reference scale; would need a materialized/incremental gauge at production scale | [#32](https://github.com/haeliotang/agent-runtime-observatory/issues/32) |
| **SQLite/Postgres, single worker class** | the queue scales to multiple workers (`SKIP LOCKED`), but there is no sharding, HA, or backpressure beyond rate limiting | — |

## Integration

| Boundary | Note | Tracking |
|---|---|---|
| **clinic ↔ ARO is a narrative relation, not a code interface** | `packages/clinic` shares this repo's null-reporting discipline and CI, but no ARO package imports it and there is no integration test binding clinic's audit protocol to the runtime object model. "The audit-protocol layer" is a description, not yet an executable interface | [#33](https://github.com/haeliotang/agent-runtime-observatory/issues/33) |

## Release governance

| Boundary | What holds | What does **not** | Tracking |
|---|---|---|---|
| **Version consistency is detected, not prevented** | `version-consistency` CI (a required check) fails if any package/app/web version disagrees with root, and on a tag push if the tag name ≠ the version | it runs *after* the tag is pushed; a wrong tag (e.g. `v0.2.6` on `0.2.5` source) turns CI red but the no-delete ruleset then makes that tag permanent. GitHub Cloud has no pre-receive hook to reject the push — the guarantee is process + a loud red gate, not prevention | — |
| **Canonical release predates immutability** | the evidence tag is delete/rewrite-protected (ruleset); the packet SHA is pinned in CI; the packet's GPG signature is verified as a standing gate | the GitHub `immutable` flag on the `credential-packet-v1` release is `false` — the setting only protects releases published *after* it was enabled. Substantive tamper-resistance is the SHA + signature + tag ruleset, not that flag (see [signing.md](signing.md)) | — |

## Out of scope by design

Signed/tamper-proof traces (traces are tamper-*evident* via replay, not signed),
LLM-step recording (the runtime is a deterministic scripted runner), and
production infrastructure (k8s manifests are illustrative). See
[README](../README.md#failure-cases-honestly) and
[threat-model](threat-model.md).
