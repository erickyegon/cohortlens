import { useState } from "react";
import { Builder } from "./Builder";
import { CohortList } from "./CohortList";
import styles from "./App.module.css";
import { Logo } from "./Logo";
import { Results } from "./Results";

type View = { name: "list" } | { name: "builder"; cohortId: string | null } | { name: "results"; cohortId: string };

export function App() {
  const [view, setView] = useState<View>({ name: "list" });
  const toList = () => setView({ name: "list" });
  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <Logo />
        <h1>CohortLens</h1>
      </header>
      {view.name === "list" && (
        <CohortList onNew={() => setView({ name: "builder", cohortId: null })}
          onOpen={(id) => setView({ name: "builder", cohortId: id })}
          onResults={(cohortId) => setView({ name: "results", cohortId })} />
      )}
      {view.name === "builder" && <Builder key={view.cohortId ?? "new"} cohortId={view.cohortId} onBack={toList} />}
      {view.name === "results" && <Results key={view.cohortId} cohortId={view.cohortId} onBack={toList} />}
    </main>
  );
}
