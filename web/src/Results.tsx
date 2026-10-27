import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "./api";
import { failureText } from "./Builder";
import { formatCount, formatTime } from "./CohortList";
import type { Baseline, Cohort, Count, Run, StepCount } from "./types";

interface Props {
  cohortId: string;
  onBack: () => void;
}

const SEXES: [string, string][] = [["F", "Female"], ["M", "Male"], ["other", "Other"]];
const STATUS_TEXT: Record<Run["status"], string> = { success: "Success", failed: "Failed", timeout: "Timed out" };

const newestFirst = (runs: Run[]) =>
  [...runs].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)); // stable: ties keep the API order

function duration(ms: number | null): string {
  if (ms === null) return "-";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** What to tell the user about a run that did not finish. The reference is the trace id they can give to support. */
function RunProblem({ run }: { run: Run }) {
  return (
    <p role="alert">
      {run.status === "timeout"
        ? "This cohort took too long to count and was cancelled."
        : `Something went wrong. Reference: ${run.trace_id ?? "unavailable"}`}
      {run.status === "timeout" && <> Reference: {run.trace_id ?? "unavailable"}</>}
    </p>
  );
}

/** One bar per step, scaled to the first step. A suppressed step ("<11") gets a small bar and no numeric value. */
export function Waterfall({ steps }: { steps: StepCount[] }) {
  const first = steps[0]?.persons;
  const scale = typeof first === "number" && first > 0 ? first : null;
  const widthOf = (persons: Count): string => {
    if (persons === "<11") return "3%"; // visible, but it carries no number
    if (scale === null) return persons === 0 ? "0%" : "100%";
    return `${Math.min(100, (persons / scale) * 100)}%`;
  };
  return (
    <ol aria-label="Attrition waterfall" style={{ listStyle: "none", padding: 0 }}>
      {steps.map((step) => (
        <li key={step.key} style={{ display: "grid", gridTemplateColumns: "16rem 1fr 6rem", gap: "0.5rem", alignItems: "center" }}>
          <span>{step.label}</span>
          <div
            role="meter"
            aria-label={step.label}
            aria-valuemin={0}
            {...(typeof step.persons === "number"
              ? { "aria-valuenow": step.persons, "aria-valuemax": scale ?? step.persons }
              : { "aria-valuetext": step.persons })}
            style={{ background: "#e6e6e6", height: "1rem" }}
          >
            <div style={{ width: widthOf(step.persons), height: "100%", background: "#3b6ea5" }} />
          </div>
          <span style={{ textAlign: "right" }}>{formatCount(step.persons)}</span>
        </li>
      ))}
    </ol>
  );
}

function BaselineTables({ baseline }: { baseline: Baseline }) {
  const bands = [...new Set(baseline.age_sex.map((cell) => cell.age_band))];
  const cell = (band: string, sex: string): Count =>
    baseline.age_sex.find((c) => c.age_band === band && c.sex === sex)?.persons ?? 0;
  return (
    <>
      <table aria-label="Baseline by age and sex">
        <thead>
          <tr><th>Age</th>{SEXES.map(([, name]) => <th key={name}>{name}</th>)}</tr>
        </thead>
        <tbody>
          {bands.map((band) => (
            <tr key={band}>
              <th scope="row">{band}</th>
              {SEXES.map(([sex]) => <td key={sex}>{formatCount(cell(band, sex))}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {baseline.comorbidities.length > 0 && (
        <table aria-label="Most common conditions">
          <thead><tr><th>Condition</th><th>Patients</th></tr></thead>
          <tbody>
            {baseline.comorbidities.map((c) => (
              <tr key={c.concept_id}>
                <th scope="row">{c.name ?? `Concept ${c.concept_id}`}</th>
                <td>{formatCount(c.persons)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p>{baseline.comorbidity_note}</p>
    </>
  );
}

export function Results({ cohortId, onBack }: Props) {
  const [cohort, setCohort] = useState<Cohort | null>(null);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<{ runId: string; baseline: Baseline } | null>(null);
  const [problem, setProblem] = useState<string | null>(null); // why loading failed
  const [runProblem, setRunProblem] = useState<{ message: string; reference: string | null } | null>(null);
  const [running, setRunning] = useState(false);

  /** Reload the history; `keep` (the run just made) stays in the list even if the server has not returned it yet. */
  const refreshRuns = useCallback(async (keep?: Run) => {
    const listed = await api.listRuns(cohortId);
    setRuns(newestFirst(keep && !listed.some((r) => r.id === keep.id) ? [keep, ...listed] : listed));
  }, [cohortId]);

  useEffect(() => {
    let current = true;
    Promise.all([api.getCohort(cohortId), api.listRuns(cohortId)])
      .then(([loadedCohort, loadedRuns]) => {
        if (!current) return;
        setCohort(loadedCohort);
        setRuns(newestFirst(loadedRuns));
      })
      .catch((error: unknown) => current && setProblem(failureText(error)));
    return () => {
      current = false;
    };
  }, [cohortId]);

  const runNow = async () => {
    if (running) return;
    setRunning(true);
    setRunProblem(null);
    try {
      const fresh = await api.runCohort(cohortId);
      setSelectedId(fresh.id);
      if (fresh.baseline) setBaseline({ runId: fresh.id, baseline: fresh.baseline });
      const record: Run = { ...fresh, baseline: undefined }; // the history holds counts only; the baseline is kept apart
      setRuns((previous) => newestFirst([record, ...(previous ?? []).filter((r) => r.id !== fresh.id)]));
      await refreshRuns(record).catch(() => undefined);
    } catch (error) {
      if (error instanceof ApiError && error.status === 504) {
        setRunProblem({ message: error.message, reference: error.traceId });
      } else {
        setRunProblem({ message: failureText(error), reference: null });
      }
      await refreshRuns().catch(() => undefined); // a failed or timed-out run is recorded: show it in the history
    } finally {
      setRunning(false);
    }
  };

  if (problem) return <p role="alert">{problem}</p>;
  if (cohort === null || runs === null) return <p role="status">Loading results…</p>;

  const shown = runs.find((r) => r.id === selectedId) ?? runs[0] ?? null;
  const shownBaseline = shown && baseline?.runId === shown.id ? baseline.baseline : null;

  return (
    <section aria-label="Results">
      <h2>{cohort.name}</h2>
      <button type="button" onClick={() => void runNow()} disabled={running}>
        {running ? "Running…" : "Run feasibility"}
      </button>{" "}
      <button type="button" onClick={onBack}>Back to cohorts</button>

      {runProblem && (
        <p role="alert">
          <span>{runProblem.message}</span>
          {runProblem.reference && <span> Reference: {runProblem.reference}</span>}
        </p>
      )}

      {shown === null && <p>This cohort has not been run yet.</p>}
      {shown !== null && shown.status !== "success" && <RunProblem run={shown} />}
      {shown !== null && shown.status === "success" && shown.step_counts && (
        <>
          <p>
            <strong>Final count</strong>: <span data-testid="final-count">{shown.final_count === null ? "-" : formatCount(shown.final_count)}</span>
          </p>
          <Waterfall steps={shown.step_counts} />
        </>
      )}

      <h3>Baseline</h3>
      {shownBaseline ? <BaselineTables baseline={shownBaseline} /> : <p>Run the cohort to see the baseline table.</p>}

      <h3>History</h3>
      {runs.length > 0 && (
        <table aria-label="Run history">
          <thead>
            <tr><th>Time</th><th>Status</th><th>Final count</th><th>Duration</th><th>Reference</th></tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>{formatTime(run.created_at)}</td>
                <td>{STATUS_TEXT[run.status]}</td>
                <td>{run.final_count === null ? "-" : formatCount(run.final_count)}</td>
                <td>{duration(run.duration_ms)}</td>
                <td>{run.status === "success" ? <button type="button" onClick={() => setSelectedId(run.id)}>View</button> : run.trace_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
