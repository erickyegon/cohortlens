import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

type Ready = { status: string; cdm_persons?: number };

function App() {
  const [ready, setReady] = useState<Ready | null>(null);
  useEffect(() => {
    fetch("/api/readyz")
      .then((r) => r.json())
      .then(setReady)
      .catch(() => setReady({ status: "unavailable" }));
  }, []);
  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 720, margin: "4rem auto", padding: "0 1rem" }}>
      <h1>CohortLens</h1>
      <p>Cohort feasibility for OMOP data. The builder and results screens land in epic E3.</p>
      <p data-testid="api-status">
        API: <strong>{ready ? ready.status : "checking…"}</strong>
        {ready?.cdm_persons !== undefined && ` · ${ready.cdm_persons.toLocaleString()} patients in the demo database`}
      </p>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
