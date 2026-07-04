import type { Decision, RunStatus } from "./types";

function Badge({ tone, label }: { tone: "green" | "amber" | "red" | "gray"; label: string }) {
  return <span className={`badge badge-${tone}`}>{label}</span>;
}

export function StatusBadge({ status }: { status: RunStatus }) {
  const tone = status === "completed" ? "green" : status === "failed" ? "red" : "amber";
  return <Badge tone={tone} label={status} />;
}

export function DecisionBadge({ decision }: { decision: Decision }) {
  const tone = decision === "deny" ? "red" : decision === "needs_review" ? "amber" : "green";
  return <Badge tone={tone} label={decision} />;
}

export function SeverityBadge({ severity }: { severity: string }) {
  const s = severity.toLowerCase();
  const tone =
    s === "high" || s === "critical" ? "red" : s === "medium" ? "amber" : "gray";
  return <Badge tone={tone} label={severity} />;
}
