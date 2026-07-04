import type {
  CreateRunResponse,
  ExampleInfo,
  ReplayReport,
  RunDetailResponse,
  RunSummary,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.text()).slice(0, 300);
    } catch {
      // ignore body read failures
    }
    throw new Error(`${res.status} ${res.statusText}${detail ? ` — ${detail}` : ""}`);
  }
  return (await res.json()) as T;
}

export const api = {
  examples: () => request<ExampleInfo[]>("/api/examples"),

  runs: (limit = 50) => request<RunSummary[]>(`/api/runs?limit=${limit}`),

  run: (id: string) => request<RunDetailResponse>(`/api/runs/${encodeURIComponent(id)}`),

  createRun: (example: string) =>
    request<CreateRunResponse>("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ example }),
    }),

  replay: (id: string) =>
    request<ReplayReport>(`/api/runs/${encodeURIComponent(id)}/replay`, {
      method: "POST",
    }),
};

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
