import { useEffect, useState } from "react";
import { api } from "./api";
import { failureText } from "./Builder";
import type { CohortSummary, Count, LastRun } from "./types";

interface Props {
  onNew: () => void;
  onOpen: (id: string) => void;
}

/** "2026-10-27 15:04 UTC": fixed format, so it reads the same everywhere. */
export function formatTime(iso: string): string {
  return `${new Date(iso).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

export function formatCount(count: Count): string {
  return typeof count === "number" ? count.toLocaleString("en-US") : count;
}

function lastRunText(run: LastRun | null): string {
  if (run === null) return "Not run yet";
  if (run.status === "success") return `${run.final_count === null ? "-" : formatCount(run.final_count)} patients`;
  return run.status === "timeout" ? "Timed out" : "Failed";
}

export function CohortList({ onNew, onOpen }: Props) {
  const [cohorts, setCohorts] = useState<CohortSummary[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    api
      .listCohorts()
      .then((list) => current && setCohorts(list))
      .catch((error: unknown) => current && setProblem(failureText(error)));
    return () => {
      current = false;
    };
  }, []);

  return (
    <section aria-label="Cohorts">
      <h2>Cohorts</h2>
      <button type="button" onClick={onNew}>New cohort</button>
      {problem && <p role="alert">{problem}</p>}
      {!problem && cohorts === null && <p role="status">Loading cohorts…</p>}
      {cohorts !== null && cohorts.length === 0 && <p>No cohorts yet. Create one to check feasibility.</p>}
      {cohorts !== null && cohorts.length > 0 && (
        <table>
          <thead>
            <tr><th>Name</th><th>Version</th><th>Last run</th><th>Last run time</th><th /></tr>
          </thead>
          <tbody>
            {cohorts.map((cohort) => (
              <tr key={cohort.id}>
                <td>{cohort.name}</td>
                <td>{cohort.version}</td>
                <td>{lastRunText(cohort.last_run)}</td>
                <td>{cohort.last_run ? formatTime(cohort.last_run.created_at) : "-"}</td>
                <td><button type="button" onClick={() => onOpen(cohort.id)} aria-label={`Open ${cohort.name}`}>Open</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
