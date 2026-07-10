# Threat model

What this substrate defends against, what it only detects, and what it does
not attempt. The adversary here is not a nation-state — it is the realistic
trio of a *misbehaving agent*, a *compromised or prompt-injected agent*, and
an *over-trusting human* reading its report.

## Threats addressed

| # | Threat | Mechanism | Outcome |
|---|---|---|---|
| T1 | Agent exfiltrates secrets via shell (`curl --data @.env`, `scp`, `nc`) | policy rule `deny-destructive-shell`, evaluated before execution | step blocked, `PolicyDecision(deny)` + high RiskSignal recorded; run continues under observation |
| T2 | Agent contacts an unapproved endpoint | domain allowlist on `web_fetch` (`deny-unlisted-domain`) | blocked and recorded, same as T1 |
| T3 | Agent quietly reads credential-shaped files | `review-sensitive-read` → `needs_review` | executes, but creates recorded review debt (decision + signal + `aro_review_debt_total`) instead of silence |
| T4 | Post-hoc tampering with a recorded trace ("the run was clean") | replay re-derives every digest; `tests/replay/test_tamper_detection.py` proves an edited output digest is caught | divergence report, not a silent pass |
| T5 | Claims about a run that its evidence doesn't support | every output is an `EvidenceItem` with a digest; artifacts are content-addressed | claims are checkable against digests |
| T6 | Environment drift invalidating an audit ("same run, different world") | workspace digest in the trace header, verified at replay | `workspace_digest` divergence surfaced explicitly |

The `policy-violation-run` example is T1–T3 executed end-to-end; the replay
test suite is T4/T6 executed end-to-end.

## Threats detected but not prevented

- **needs_review abuse**: a rule set to `needs_review` lets the step run. If
  nobody consumes the review debt, the system has an honest record of
  unreviewed risk — but the risk happened. That is a deliberate design point:
  the substrate makes the debt visible and countable rather than pretending a
  headless system can adjudicate it.
- **Policy gaps**: a step no rule matches is default-allowed. The trace still
  records it fully, so a later, stricter policy can be evaluated against old
  traces (roadmap: counterfactual policy replay).

## Out of scope (v0.1)

- **Sandbox escape**: tools are simulated in-process; there is no real shell
  or network to escape to. Real tool adapters would need OS-level sandboxing
  (this repo's `wutai` sibling project covers the trust-boundary UX side).
- **Trace confidentiality/signing**: traces are plaintext JSONL and
  tamper-*evident* under replay, not tamper-*proof*. Signed work packets are
  a roadmap item.
- **Multi-tenant authz on the API**: the FastAPI surface is unauthenticated,
  intended for local/demo use.
- **Denial of service**: run creation is rate-limited
  (`ARO_RATE_LIMIT_PER_MINUTE`, coarse fixed-window), but there are no queue
  quotas, per-client limits, or payload-size caps yet.
- **Attestation identity**: `attested_by` is self-declared, not authenticated
  (see SECURITY.md). The substrate rejects blank identities, unknown seats, and
  digest-mismatched clearings, but cannot prove the named human is real.

## Residual assumptions

Replay integrity assumes the *code* doing the replay is the code in this
repo. An auditor should replay with a pinned, reviewed checkout — which is
exactly what CI does on every commit via the golden regression suite.
