import { useEffect, useMemo, useState } from "react";
import { api, errorMessage } from "./api";
import { DecisionBadge, SeverityBadge, StatusBadge } from "./badges";
import { fmtBytes, fmtDate, fmtMs, fmtValue, shortDigest } from "./format";
import type { PolicyDecision, ReplayReport, RunDetailResponse } from "./types";

function ReviewDebtSection({
  detail,
  onAttested,
}: {
  detail: RunDetailResponse;
  onAttested: (next: RunDetailResponse) => void;
}) {
  const debt = detail.review_debt;
  const open = debt.filter((d) => d.status === "open");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [attestedBy, setAttestedBy] = useState("");
  const [scope, setScope] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const clear = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await api.attest(detail.run.id, {
        decision: "accept",
        declared_scope: scope,
        attested_by: attestedBy,
        clears_decisions: [...selected],
      });
      onAttested(await api.run(detail.run.id));
      setSelected(new Set());
      setScope("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="panel">
      <h2>
        Review debt{" "}
        {open.length > 0 ? (
          <span className="badge badge-amber">{open.length} open</span>
        ) : (
          debt.length > 0 && <span className="badge badge-green">all cleared</span>
        )}
      </h2>
      {debt.length === 0 ? (
        <p className="muted">No review debt — no step was flagged needs_review.</p>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th />
                <th className="num">step</th>
                <th>rule</th>
                <th>reason</th>
                <th>status</th>
                <th>cleared by</th>
              </tr>
            </thead>
            <tbody>
              {debt.map((item) => (
                <tr key={item.decision_id}>
                  <td>
                    {item.status === "open" && (
                      <input
                        type="checkbox"
                        checked={selected.has(item.decision_id)}
                        onChange={() => toggle(item.decision_id)}
                        aria-label={`select debt item at step ${item.step_index}`}
                      />
                    )}
                  </td>
                  <td className="num">{item.step_index}</td>
                  <td>
                    <code className="mono">{item.rule_id}</code>
                  </td>
                  <td>{item.reason}</td>
                  <td>
                    {item.status === "cleared" ? (
                      <span className="badge badge-green">cleared</span>
                    ) : (
                      <span className="badge badge-amber">open</span>
                    )}
                  </td>
                  <td>{item.attested_by ?? <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {open.length > 0 && (
            <div className="attest-form">
              <input
                type="text"
                placeholder="your name (attested_by)"
                value={attestedBy}
                onChange={(e) => setAttestedBy(e.target.value)}
              />
              <input
                type="text"
                className="attest-scope"
                placeholder="declared scope — what exactly did you review?"
                value={scope}
                onChange={(e) => setScope(e.target.value)}
              />
              <button
                type="button"
                disabled={submitting || selected.size === 0 || !attestedBy || !scope}
                onClick={() => void clear()}
              >
                {submitting
                  ? "Attesting…"
                  : `Clear ${selected.size} item${selected.size === 1 ? "" : "s"}`}
              </button>
            </div>
          )}
          {error && <p className="error-text">Attestation failed: {error}</p>}
          <p className="muted small">
            Clearing records an <em>accept</em> attestation naming these decisions. A named human
            is required; approval is scoped, never total.
          </p>
        </>
      )}
    </section>
  );
}

interface Props {
  runId: string;
  onBack: () => void;
}

function runDurationMs(started: string | null, finished: string | null): number | null {
  if (!started || !finished) return null;
  const a = new Date(started).getTime();
  const b = new Date(finished).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.max(0, Math.round(b - a));
}

function DigestCell({ digest }: { digest: string | null }) {
  if (!digest) return <span className="muted">—</span>;
  return (
    <code className="mono" title={digest}>
      {shortDigest(digest)}
    </code>
  );
}

export default function RunDetail({ runId, onBack }: Props) {
  const [detail, setDetail] = useState<RunDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<ReplayReport | null>(null);
  const [replaying, setReplaying] = useState(false);
  const [replayError, setReplayError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    setReport(null);
    setReplayError(null);
    api
      .run(runId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const decisionByStep = useMemo(() => {
    const map = new Map<number, PolicyDecision>();
    for (const d of detail?.run.policy_decisions ?? []) {
      map.set(d.step_index, d);
    }
    return map;
  }, [detail]);

  const replay = async () => {
    setReplaying(true);
    setReplayError(null);
    try {
      setReport(await api.replay(runId));
    } catch (err) {
      setReplayError(errorMessage(err));
    } finally {
      setReplaying(false);
    }
  };

  if (error) {
    return (
      <div className="stack">
        <a className="back-link" href="#" onClick={(e) => (e.preventDefault(), onBack())}>
          ← Back to runs
        </a>
        <p className="error-text">Failed to load run {runId}: {error}</p>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="stack">
        <a className="back-link" href="#" onClick={(e) => (e.preventDefault(), onBack())}>
          ← Back to runs
        </a>
        <p className="muted">Loading run…</p>
      </div>
    );
  }

  const { run } = detail;
  const duration = runDurationMs(run.started_at, run.finished_at);

  return (
    <div className="stack">
      <a className="back-link" href="#" onClick={(e) => (e.preventDefault(), onBack())}>
        ← Back to runs
      </a>

      <section className="panel">
        <div className="detail-header">
          <div>
            <h2 className="mono">{run.id}</h2>
            <div className="detail-meta">
              <StatusBadge status={run.status} />
              <span>
                example: <strong>{detail.example ?? "—"}</strong>
              </span>
              <span>
                agent: <strong>{run.agent}</strong>
              </span>
              <span>duration: {fmtMs(duration)}</span>
            </div>
          </div>
          <button type="button" onClick={() => void replay()} disabled={replaying}>
            {replaying ? "Replaying…" : "Replay"}
          </button>
        </div>

        {replayError && <p className="error-text">Replay failed: {replayError}</p>}
        {report && report.divergences.length === 0 && (
          <p className="replay-clean">
            replay clean — {report.steps_compared} steps compared, 0 divergences
          </p>
        )}
        {report && report.divergences.length > 0 && (
          <div className="replay-dirty">
            <p className="error-text">
              {report.divergences.length} divergence
              {report.divergences.length === 1 ? "" : "s"} across {report.steps_compared} steps
              compared
            </p>
            <table>
              <thead>
                <tr>
                  <th className="num">step</th>
                  <th>field</th>
                  <th>recorded</th>
                  <th>replayed</th>
                </tr>
              </thead>
              <tbody>
                {report.divergences.map((d, i) => (
                  <tr key={i}>
                    <td className="num">{d.step_index}</td>
                    <td>{d.field}</td>
                    <td>
                      <code className="mono">{fmtValue(d.recorded)}</code>
                    </td>
                    <td>
                      <code className="mono">{fmtValue(d.replayed)}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Steps</h2>
        {run.steps.length === 0 ? (
          <p className="muted">No steps recorded.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th className="num">#</th>
                <th>tool</th>
                <th>decision</th>
                <th className="num">duration</th>
                <th>output / error</th>
                <th>in digest</th>
                <th>out digest</th>
              </tr>
            </thead>
            <tbody>
              {run.steps.map((step) => {
                const decision = decisionByStep.get(step.index);
                return (
                  <tr key={step.index}>
                    <td className="num">{step.index}</td>
                    <td>
                      <code className="mono">{step.name}</code>
                    </td>
                    <td>
                      <DecisionBadge decision={decision?.decision ?? "allow"} />
                    </td>
                    <td className="num">{fmtMs(step.duration_ms)}</td>
                    <td className="preview">
                      {step.error ? (
                        <span className="error-text">{step.error}</span>
                      ) : (
                        step.output_preview ?? <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <DigestCell digest={step.input_digest} />
                    </td>
                    <td>
                      <DigestCell digest={step.output_digest} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <h2>Policy decisions</h2>
        {run.policy_decisions.length === 0 ? (
          <p className="muted">No policy decisions recorded.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th className="num">step</th>
                <th>rule</th>
                <th>decision</th>
                <th>reason</th>
              </tr>
            </thead>
            <tbody>
              {run.policy_decisions.map((d) => (
                <tr key={d.id}>
                  <td className="num">{d.step_index}</td>
                  <td>
                    <code className="mono">{d.rule_id}</code>
                  </td>
                  <td>
                    <DecisionBadge decision={d.decision} />
                  </td>
                  <td>{d.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <ReviewDebtSection detail={detail} onAttested={setDetail} />

      <section className="panel">
        <h2>Risk signals</h2>
        {run.risk_signals.length === 0 ? (
          <p className="muted">No risk signals.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th className="num">step</th>
                <th>severity</th>
                <th>category</th>
                <th>message</th>
              </tr>
            </thead>
            <tbody>
              {run.risk_signals.map((s) => (
                <tr key={s.id}>
                  <td className="num">{s.step_index}</td>
                  <td>
                    <SeverityBadge severity={s.severity} />
                  </td>
                  <td>{s.category}</td>
                  <td>{s.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <h2>Artifacts</h2>
        {run.artifacts.length === 0 ? (
          <p className="muted">No artifacts.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>path</th>
                <th className="num">size</th>
                <th>digest</th>
              </tr>
            </thead>
            <tbody>
              {run.artifacts.map((a) => (
                <tr key={a.id}>
                  <td>
                    <code className="mono">{a.path}</code>
                  </td>
                  <td className="num">{fmtBytes(a.size_bytes)}</td>
                  <td>
                    <DigestCell digest={a.digest} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <p className="muted small">
        started {fmtDate(run.started_at)} · finished {fmtDate(run.finished_at)} · model{" "}
        {run.model}
      </p>
    </div>
  );
}
