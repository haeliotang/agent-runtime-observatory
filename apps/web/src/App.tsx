import { useState } from "react";
import RunDetail from "./RunDetail";
import RunsList from "./RunsList";

export default function App() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  return (
    <div className="app">
      <header className="app-header">
        <h1>Agent Runtime Observatory</h1>
        <span className="app-subtitle">runs · policy decisions · risk signals · replay</span>
      </header>
      {selectedRunId === null ? (
        <RunsList onSelectRun={setSelectedRunId} />
      ) : (
        <RunDetail runId={selectedRunId} onBack={() => setSelectedRunId(null)} />
      )}
    </div>
  );
}
