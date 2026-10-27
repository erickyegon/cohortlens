import { useState } from "react";
import { Builder } from "./Builder";
import { CohortList } from "./CohortList";

type View = { name: "list" } | { name: "builder"; cohortId: string | null };

export function App() {
  const [view, setView] = useState<View>({ name: "list" });
  const toList = () => setView({ name: "list" });
  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 860, margin: "2rem auto", padding: "0 1rem" }}>
      <h1>CohortLens</h1>
      {view.name === "list" && (
        <CohortList onNew={() => setView({ name: "builder", cohortId: null })}
          onOpen={(id) => setView({ name: "builder", cohortId: id })} />
      )}
      {view.name === "builder" && <Builder key={view.cohortId ?? "new"} cohortId={view.cohortId} onBack={toList} />}
    </main>
  );
}
