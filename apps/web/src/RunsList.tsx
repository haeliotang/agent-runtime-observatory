import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { StatusBadge } from "./badges";
import { fmtDate, fmtMs } from "./format";
import type { ExampleInfo, RunSummary } from "./types";

interface Props {
  onSelectRun: (id: string) => void;
}

export default function RunsList({ onSelectRun }: Props) {
  const [examples, setExamples] = useState<ExampleInfo[] | null>(null);
  const [examplesError, setExamplesError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [launching, setLaunching] = useState<string | null>(null);
  const [launchError, setLaunchError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .examples()
      .then((data) => {
        if (!cancelled) setExamples(data);
      })
      .catch((err) => {
        if (!cancelled) setExamplesError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadRuns = useCallback(async () => {
    try {
      const data = await api.runs();
      setRuns(data);
      setRunsError(null);
    } catch (err) {
      setRunsError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void loadRuns();
    const timer = setInterval(() => void loadRuns(), 2000);
    return () => clearInterval(timer);
  }, [loadRuns]);

  const startRun = async (name: string) => {
    setLaunching(name);
    setLaunchError(null);
    try {
      await api.createRun(name);
      await loadRuns();
    } catch (err) {
      setLaunchError(`Run of "${name}" failed to start: ${errorMessage(err)}`);
    } finally {
      setLaunching(null);
    }
  };

  return (
    <div className="stack">
      <section className="panel">
        <h2>Examples</h2>
        {examplesError && <p className="error-text">Failed to load examples: {examplesError}</p>}
        {launchError && <p className="error-text">{launchError}</p>}
        {examples === null && !examplesError && <p className="muted">Loading examples…</p>}
        {examples !== null && examples.length === 0 && <p className="muted">No examples available.</p>}
        {examples !== null && examples.length > 0 && (
          <ul className="example-list">
            {examples.map((ex) => (
              <li key={ex.name} className="example-item">
                <div className="example-info">
                  <div className="example-title">{ex.title}</div>
                  <div className="example-goal">{ex.goal}</div>
                  <div className="muted small">
                    {ex.steps} step{ex.steps === 1 ? "" : "s"} · policy <code>{ex.policy_id}</code>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => void startRun(ex.name)}
                  disabled={launching !== null}
                >
                  {launching === ex.name ? "Running…" : "Run"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="panel">
        <h2>Runs</h2>
        {runsError && <p className="error-text">Failed to load runs: {runsError}</p>}
        {runs === null && !runsError && <p className="muted">Loading runs…</p>}
        {runs !== null && runs.length === 0 && (
          <p className="muted">No runs yet — start one from an example above.</p>
        )}
        {runs !== null && runs.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>id</th>
                <th>example</th>
                <th>status</th>
                <th className="num">steps</th>
                <th className="num">denials</th>
                <th className="num">duration</th>
                <th>created</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id} className="clickable" onClick={() => onSelectRun(run.id)}>
                  <td>
                    <code className="mono" title={run.id}>
                      {run.id}
                    </code>
                  </td>
                  <td>{run.example ?? "—"}</td>
                  <td>
                    <StatusBadge status={run.status} />
                  </td>
                  <td className="num">{run.steps}</td>
                  <td className={`num${run.denials > 0 ? " error-text" : ""}`}>{run.denials}</td>
                  <td className="num">{fmtMs(run.duration_ms)}</td>
                  <td>{fmtDate(run.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
