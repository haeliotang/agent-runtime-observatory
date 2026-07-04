# Object-model alignment: wutai · stillmirror-review · agent-runtime-observatory

This document is the field-level mapping the career report asked for: it reads
the actual object models of [`wutai`](https://github.com/haeliotang/wutai) and
[`stillmirror-review`](https://github.com/haeliotang/stillmirror-review) and
aligns them, field by field, with the `aro_schema` object model in this repo.
The goal is to make explicit that the three repos are one coherent thesis at
three altitudes — and to show precisely which fields would unify if they shared
a schema.

## Evidence and method

Everything below is grounded in source, not description:

- **wutai** — read from TypeScript domain source and JSON Schemas:
  `src/domain/task.ts`, `src/domain/evidence.ts`, `src/domain/workPacket.ts`,
  `src/runtime/trustVerdict.ts`, `src/storage/sqliteTaskStore.ts`,
  `schemas/*.schema.json`, and `examples/*.example.json`. wutai's model is
  fully readable, so wutai field names below are verbatim from code.
- **stillmirror-review** — its logic lives in packaged helper binaries
  (`bin/stillmirror-review`, `bin/stillmirror-capture`, `bin/stillmirror-mcp`),
  which are not readable source. Its object model is therefore reconstructed
  from three readable surfaces: the redacted state sample
  (`examples/stillmirror-review/redacted-sample.json`), the MCP tool contract
  (`plugins/stillmirror-review/manifest.json`), and the skill docs
  (`skills/{ledger,review,goals,init}/SKILL.md`). Field names are verbatim
  where the sample shows them; where only the skills describe a field, it is
  marked *(described)*.
- **agent-runtime-observatory** — `packages/schema/aro_schema/models.py` in
  this repo.

## The one-thesis, three-altitude picture

| Repo | Altitude | Core question it answers | Producer of record |
|---|---|---|---|
| `agent-runtime-observatory` | **Runtime** | What did the agent *do*, step by step, and was each step allowed? | the runtime itself, live |
| `stillmirror-review` | **Review** | Across a project's history, where did attention *go* vs. what was authorized, and who has stood behind it? | post-hoc, from Claude Code traces + git |
| `wutai` | **Trust boundary** | Can this finished packet of agentic work be *trusted*, and exactly which scope did a human ratify? | at the boundary, per delivered packet |

They chain: the observatory *generates* the per-step evidence; stillmirror
*aggregates* it into review debt and goal provenance; wutai *packages and gates*
a deliverable and captures a scoped human ratification. The objects below are
the same nouns seen from these three distances.

## Concept alignment (the spine)

| Concept | `aro_schema` | `wutai` | `stillmirror-review` |
|---|---|---|---|
| Unit of intent | `Goal` | `WutaiTask.userRequest` + `plan[]` | `AcceptedGoal` |
| Intent lifecycle | *(none — goal is static)* | `TaskStatus` | goal events: `introduced → reinforced → replaced → retired` |
| Unit of work | `AgentRun` | `WutaiTask` + `WorkPacketSession` | a capture *session* / ledger window |
| Atomic action | `StepRecord` | `TaskEvent` | allocation **ledger entry** |
| Guardrail verdict | `PolicyDecision` (`allow/deny/needs_review`) | `TrustPolicyRule.action` (`allow/review/block`) → `TrustVerdictCheck` | *(no live gate — review-time only)* |
| Risk flag | `RiskSignal` (severity + category) | evidence gaps: `highRiskGapCount`, `conflictCount`; verdict `blocked[]`/`reviewRequired[]` | `supports_mainline=no`, `ambiguity[]`, low `confidence` |
| Evidence pointer | `EvidenceItem` (sha256) | manifest `artifacts[].sha256`, `EvidenceVerification` | the ledger entry itself (evidence, "never a verdict") |
| Produced output | `Artifact` (sha256) | `ArtifactRecord` + manifest inventory (`role`, `sha256`, `bytes`) | git commits / files touched |
| Accountable human | `ReviewerSeat` | `ConsumerAttestation.reviewer` + `humanReview.attestation` | the **empty seat** + `attested_by` |
| Human verdict | *(none yet)* | `ConsumerAttestation.decision` + `declaredScope`/`excludedScope` | alignment attestation: `accept/amend/reject` |
| Aggregate rollup | *(none — single run)* | `WorkPacketManifest.audit` counts | `allocation_counts`, `review_due`, `fleet` |

The two rows with *(none)* in the `aro_schema` column are the real gaps this
mapping surfaces — see [Proposed schema deltas](#proposed-schema-deltas).

## Field-level mappings

### 1. Intent: `Goal` ↔ `WutaiTask` ↔ `AcceptedGoal`

| `aro_schema.Goal` | wutai | stillmirror |
|---|---|---|
| `id` | `WutaiTask.taskId` | goal id *(described; `goals list`)* |
| `statement` | `WutaiTask.userRequest` | accepted goal statement (e.g. `"Maintain hook reliability"`) |
| `constraints[]` | `PermissionRequest.scope[]` (as negative constraints) | — |
| `owner_seat_id` | — *(owner is implicit; attestation is separate)* | the human who ran `goals add` |
| — | `WutaiTask.plan[]` (ordered plan steps) | — |
| — | `WutaiTask.status` (`TaskStatus`) | goal lifecycle state *(introduced/reinforced/replaced/retired)* |

**Finding.** aro's `Goal` is *static* — it has an owner but no lifecycle.
stillmirror's goal-events log (`introduced/reinforced/replaced/retired`, kept
append-only in `goal-events.jsonl`) and its "how many allocations reinforced
this goal" provenance are the missing dimension. wutai adds an ordered `plan[]`
that aro folds into opaque scripted steps.

### 2. Unit of work: `AgentRun` ↔ `WutaiTask`/`WorkPacketSession` ↔ capture session

| `aro_schema.AgentRun` | wutai | stillmirror |
|---|---|---|
| `id` | `WorkPacketSession.sessionId` / `packetId` | session id *(implicit; `sessions`/`agents_touched`)* |
| `task_id` | `WutaiTask.taskId` | `related_goal` on each entry |
| `agent` | `WorkPacketProducer.adapter` | `resource_type` / real subagent identity |
| `model` | `WorkPacketProducer.runtime` | — |
| `status` (`RunStatus`) | `WutaiTask.status` (`TaskStatus`) | — |
| `started_at`/`finished_at` | `session.startedAt`/`completedAt` | `ledger.since` / `generated_at` |
| `steps[]` | `WutaiTask.events[]` | `ledger.example_entries[]` |
| `policy_decisions[]` | trust-verdict `checks[]` | — |
| `risk_signals[]` | evidence `checks[]` (status `warning`/`fail`) | entries with `supports_mainline=no` |
| `evidence[]` | manifest `artifacts[]` inventory | the whole ledger |
| `artifacts[]` | `WutaiTask.artifacts[]` | commits / files |
| — | `session.exitCode`, `session.command`, `session.workingDirectory` | — |
| — | `WorkPacketCoverage` (`captured`/`blindSpots`/`enforcement`) | Coverage & Blind Spots *(described)* |

**Finding.** Both siblings carry a **coverage / blind-spot** declaration that
aro lacks: an honest statement of *what the record does not see*. wutai encodes
it as `WorkPacketCoverage`; stillmirror as the review's "Coverage & Blind Spots"
section. This is a strong, cheap add for aro (a run should state its own
observability limits).

### 3. Atomic action: `StepRecord` ↔ `TaskEvent` ↔ ledger entry

| `aro_schema.StepRecord` | wutai `TaskEvent` | stillmirror ledger entry |
|---|---|---|
| `index` | ordinal in `events[]` | position in `example_entries[]` |
| `kind` (`StepKind`) | `type` (12 event types) | `source` (hook: `SessionStart`/`PostToolUse`/…) |
| `name` (tool) | — (type-encoded) | `resource_type` (`Bash`/`Read`/`Skill`) |
| `args` | `TaskEvent.details` | — |
| `input_digest` / `output_digest` | manifest artifact `sha256` (packet-level, not per-event) | — |
| `output_preview` | `TaskEvent.summary` | — |
| `decision_id` | → trust-verdict check `ruleId` | — |
| `started_at` | `TaskEvent.timestamp` | entry `timestamp` |
| `duration_ms` | — | — |
| `error` | `TaskFailed` event | — |
| — | `TaskEvent.visibility` (`user/expert/internal`) | — |
| — | — | `allocated_to[]` (rubric labels) |
| — | — | `related_goal` (goal linkage per action) |
| — | — | `supports_mainline` (`yes/no/unknown`) |
| — | — | `confidence` (float) + `ambiguity[]` |
| — | — | `review_state` (`unreviewed/reviewed`) |

**Finding — the richest divergence.** stillmirror's ledger entry carries four
fields aro's step has no home for, and they are exactly the review-substrate
fields: **`allocated_to`** (which goal-rubric bucket this action served),
**`supports_mainline`**, **`confidence`**, and **`review_state`**. aro records
*what happened* with cryptographic fidelity; stillmirror annotates *what it
meant and whether a human has looked*. wutai's `TaskEvent.visibility`
(user/expert/internal) is a third orthogonal axis aro also lacks.

### 4. Guardrail: `PolicyDecision` ↔ wutai trust policy/verdict

wutai is the only sibling with a policy engine, and it maps onto aro's almost
one-to-one — with a vocabulary skew and one extra tier:

| `aro_schema.PolicyDecision` | wutai |
|---|---|
| `policy_id` | `TrustPolicy.policyId` |
| `rule_id` | `TrustPolicyRule` key / `TrustVerdictCheck.ruleId` |
| `decision`: `allow` | `TrustPolicyAction.allow` → check `passed` |
| `decision`: `needs_review` | `TrustPolicyAction.review` → check `review_required` |
| `decision`: `deny` | `TrustPolicyAction.block` → check `blocked` |
| `reason` | `TrustVerdictCheck.message` (+ `evidence`) |
| — | `TrustVerdictArtifact.verdict` = roll-up (`trusted/review_required/blocked`) |
| — | rule modifiers: `requireRationale`, `requireTrustedProducer`, `requireReviewer` |

**Finding.** Same three-valued logic (`allow / review|needs_review /
block|deny`), same "matched rule wins, roll up to a verdict" shape. Two things
wutai has that aro doesn't: (a) a **run-level verdict** that aggregates
per-step decisions (aro leaves callers to derive it); (b) rule **modifiers**
that make a decision *conditional on human/producer facts*
(`requireReviewer`, `requireTrustedProducer`). aro's flat `deny/allow/
needs_review` is the right v0.1 core; wutai shows the natural next tier.

### 5. Accountability: `ReviewerSeat` ↔ wutai attestation ↔ stillmirror empty seat

This is where the three repos converge most tightly — all three treat *an
unfilled human seat* as the central object.

| aro | wutai `ConsumerAttestation` | stillmirror attestation |
|---|---|---|
| `ReviewerSeat.id` | `reviewer.id` | — |
| `ReviewerSeat.name` | `reviewer.name` | `attested_by` |
| `ReviewerSeat.role` | `reviewer.role` (`maintainer`…) | — |
| `ReviewerSeat.scope` | `declaredScope` (+ `excludedScope`) | review `labels[]` scope |
| *(no verdict field)* | `decision`: `ratified/rejected/needs_changes/refused` | `decision`: `accept/amend/reject` |
| *(none)* | `subject.manifestSha256` (what was ratified, by digest) | `ledger_entry_count` (what window was attested) |
| *(none)* | `proposed_by` vs `attested_by` split | `proposed_by` (assistant draft) vs `attested_by` (user) |

**Finding — the unfilled-seat invariant, stated three ways.** aro's runtime
default is `humanReview`-absent; wutai's manifest hard-codes
`humanReview.attestation = "not_recorded"` until a *separate* `ConsumerAttestation`
artifact is produced; stillmirror's `propose → ratify` flow *keeps the judgment
seat empty until the user ratifies* and its `fleet` view sorts **empty seats
first**. All three encode "nobody has stood behind this yet" as a first-class,
visible state rather than a silent default. aro models the *seat*; it does not
yet model the *act of filling it* (the attestation) — that is the single most
valuable object to port in.

Two design rules worth copying verbatim:

- **scoped ratification** (wutai): a human ratifies `declaredScope` and
  explicitly disclaims `excludedScope` ("I do not ratify trace completeness,
  runtime sandboxing, external side effects…"). Approval is never total.
- **draft ≠ attestation** (stillmirror): an assistant may *draft* a proposal
  (`proposed_by`), but only a named human `attested_by` turns it into an
  attestation; `reject` leaves the seat visibly empty. Silence is not assent.

### 6. Evidence & integrity: `EvidenceItem`/`Artifact` ↔ wutai manifest

| aro | wutai |
|---|---|
| `Artifact.digest` (sha256) | manifest `artifacts[].sha256` (same algorithm, same hex) |
| `Artifact.size_bytes` | manifest `artifacts[].bytes` |
| `Artifact.media_type` | `ArtifactRecord.type` (`markdown/json`) |
| `Artifact.path` | `ArtifactRecord.virtualPath` |
| `EvidenceItem.kind` | manifest `artifacts[].role` (12 roles: `source_ledger`, `claim_ledger`, `trust_verdict`…) |
| `EvidenceItem.digest` | artifact `sha256` |
| *(none)* | `EvidenceVerification` (claim/citation coverage, `readyForTrust`) |

**Finding.** Identical content-addressing discipline (sha256 hex over content).
wutai's `artifactRole` taxonomy is a ready-made vocabulary for
`EvidenceItem.kind`, and its `EvidenceVerification` (citation coverage, primary
source count, `readyForTrust`) is a domain-specific evidence check aro's generic
`EvidenceItem` could specialize into.

## Vocabulary reconciliation

Same idea, different words — the table a shared schema would have to pick from:

| Meaning | aro | wutai | stillmirror |
|---|---|---|---|
| allow / proceed | `allow` | `allow` / `passed` / `trusted` | *(implicit)* |
| soft-stop for a human | `needs_review` | `review` / `review_required` | *(review debt)* |
| hard-stop | `deny` | `block` / `blocked` | — |
| human accepted | — | `ratified` | `accept` |
| human corrected | — | `needs_changes` | `amend` |
| human refused | — | `rejected` / `refused` | `reject` |
| terminal-good status | `completed` | `completed` / `completed_with_warnings` | — |
| terminal-bad status | `failed` | `failed` / `cancelled` | — |

Recommended canonical set if unified: keep aro's `allow/needs_review/deny` for
**machine gate** decisions and adopt stillmirror's `accept/amend/reject` for
**human** decisions — the two are genuinely different acts and both siblings
keep them lexically distinct.

## Deliberate divergences (not gaps to close)

- **aro simulates tools in-process; the siblings observe real work.** aro's
  determinism is what makes replay a hard signal (see `docs/architecture.md`).
  It intentionally does *not* try to import real Claude Code traces the way
  stillmirror does or gate real deliverables the way wutai does.
- **aro compares by digest; stillmirror annotates by judgment.** aro's evidence
  is cryptographic and objective; stillmirror's ledger entries carry
  `confidence` and `supports_mainline` — deliberately *subjective, reviewable*
  classifications. These should stay distinct object kinds, not merge.
- **wutai's trust verdict is packet-terminal; aro's policy is step-live.** wutai
  judges a finished packet; aro gates each step before it runs. Same vocabulary,
  opposite point in time — a unified schema needs both, not one.

## Proposed schema deltas

Concrete, minimal additions to `aro_schema` that would make it a genuine
superset spine for all three — ordered by value, each traceable to a field
above. **Status: all six are now implemented** — #1 `Attestation` (with store
+ `POST /api/runs/{id}/attestations` + `aro_attestations_total`), #2
`GoalEvent` (schema + vocabulary; automatic tracking still roadmap), #3
`StepRecord.allocated_to`/`supports_goal`, #4 `AgentRun.coverage`, #5
`AgentRun.verdict` (computed), #6 `EVIDENCE_ROLES`. See
[object-model.md](object-model.md) for the resulting objects.

1. **`Attestation` object** — the highest-value port. Fields drawn from the
   union of wutai's `ConsumerAttestation` and stillmirror's ratify flow:
   `id`, `run_id`, `seat_id`, `decision` (`accept|amend|reject`),
   `declared_scope`, `excluded_scope`, `proposed_by`, `attested_by`,
   `subject_digest`, `note`, `attested_at`. Turns aro's *seat* (a place for a
   human) into a recorded *act* (a human standing behind a scope). Ties to
   roadmap issue on review debt.

2. **Goal lifecycle** — give `Goal` an append-only event log
   (`introduced/reinforced/replaced/retired`) mirroring stillmirror's
   `goal-events.jsonl`, plus a derived `reinforced_by` count of steps linked to
   the goal. Makes goal provenance queryable.

3. **`StepRecord.allocated_to` + `supports_goal`** — optional rubric labels and
   a `yes/no/unknown` mainline flag per step, so a run can express *what each
   step was for*, not just what it did. Directly ports stillmirror's ledger
   annotations; feeds a review-debt metric.

4. **Run-level coverage block** — an `AgentRun.coverage` with
   `captured`/`blind_spots`/`enforcement`, ported from `WorkPacketCoverage`. A
   run should declare its own observability limits.

5. **Run-level policy verdict** — a derived `AgentRun.verdict`
   (`trusted/review_required/blocked`) aggregating step decisions, matching
   wutai's `TrustVerdictArtifact.verdict`. Currently callers must roll this up
   themselves.

6. **`EvidenceItem.role` taxonomy** — adopt wutai's `artifactRole` names as a
   controlled vocabulary for `EvidenceItem.kind`.

## What should NOT be merged

Honesty per the repo's own threat-model discipline:

- **stillmirror's Claude Code capture spine and wutai's Tauri/keychain/OS
  integration** are platform-specific and out of scope for a runtime substrate.
  Only their *object models* align; their *implementations* should not be pulled
  in.
- **wutai's research-adapter and evidence-verification (claim/citation) logic**
  is domain-specific to research packets; aro should keep `EvidenceItem`
  generic and let a downstream layer specialize it.
- **stillmirror's `fleet` cross-project view** is an aggregation over many
  repos — a layer *above* a single runtime, not a schema change inside it.

## Bottom line

The three repos already share one ontology; they differ in altitude and in what
they can cryptographically prove vs. what they ask a human to judge. The single
most valuable unification is the **`Attestation` object** — all three treat the
unfilled human seat as central, and only wutai and stillmirror currently model
the *act* of filling it. Porting that into `aro_schema`, plus goal lifecycle and
per-step allocation, would make this repo the runtime-native spine the other two
aggregate and gate on top of.
