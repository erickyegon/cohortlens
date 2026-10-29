import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "./api";
import { failureText } from "./Builder";
import { formatCount, formatTime } from "./CohortList";
import styles from "./Results.module.css";
import type { Baseline, Cohort, Count, Run, StepCount } from "./types";
import ui from "./ui.module.css";
import { stepDrop } from "./waterfall";

interface Props {
  cohortId: string;
  onBack: () => void;
}

const SEXES: [string, string][] = [["F", "Female"], ["M", "Male"], ["other", "Other"]];
const STATUS_TEXT: Record<Run["status"], string> = { success: "Success", failed: "Failed", timeout: "Timed out" };
const HISTORY_PREVIEW = 5; // the history shows this many of the newest runs until "Show all" is pressed
const OMITTED_NOTE = "Conditions with fewer than 11 patients are not shown."; // the API's note, shown until it has sent one

const newestFirst = (runs: Run[]) =>
  [...runs].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)); // stable: ties keep the API order

function duration(ms: number | null): string {
  if (ms === null) return "-";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** What to tell the user about a run that did not finish. The reference is the trace id they can give to support. */
function RunProblem({ run }: { run: Run }) {
  return (
    <p role="alert" className={styles.problem}>
      {run.status === "timeout"
        ? "This cohort took too long to count and was cancelled."
        : `Something went wrong. Reference: ${run.trace_id ?? "unavailable"}`}
      {run.status === "timeout" && <> Reference: {run.trace_id ?? "unavailable"}</>}
    </p>
  );
}

/** The status in words, with an icon from CSS, so colour is never the only signal. */
function StatusBadge({ status, testId }: { status: Run["status"]; testId?: string }) {
  return <span className={`${ui.badge} ${ui[status]}`} data-testid={testId}>{STATUS_TEXT[status]}</span>;
}

const SUPPRESSED_MAX = 10; // "<11" means at most 10 patients

/** "−368 (40%)", or null where a suppressed count is involved (see stepDrop). */
function dropText(drop: ReturnType<typeof stepDrop>): string | null {
  return drop === null ? null : `−${drop.persons.toLocaleString("en-US")} (${drop.percent}%)`;
}

/**
 * One row per step: label, remaining count, the drop from the previous step and a bar scaled to the first step. A step
 * with a real number is a `meter`. A suppressed step ("<11") is an image with a text label (a meter needs a number)
 * drawn as a dashed outline that is at most 10 patients wide: it never looks bigger than a real step of 11, and it
 * carries no number. With no scale (the first step is suppressed) it is a fixed small outline, so no scale is implied.
 */
export function Waterfall({ steps }: { steps: StepCount[] }) {
  const first = steps[0]?.persons;
  const scale = typeof first === "number" && first > 0 ? first : null;
  const widthOf = (persons: Count): string => {
    if (persons === "<11") return scale === null ? "3%" : `${Math.min(100, (SUPPRESSED_MAX / scale) * 100)}%`;
    if (scale === null) return persons === 0 ? "0%" : "100%";
    return `${Math.min(100, (persons / scale) * 100)}%`;
  };
  return (
    <ol aria-label="Attrition waterfall" className={styles.steps}>
      {steps.map((step, i) => {
        const text = i === 0 ? null : dropText(stepDrop(steps[i - 1].persons, step.persons));
        return (
          <li key={step.key} className={styles.step}>
            <span className={styles.label}>{step.label}</span>
            <span className={styles.count}>{formatCount(step.persons)}</span>
            <span className={styles.drop}>
              {i === 0 ? (
                <span className={ui.srOnly}>First step, no previous step</span>
              ) : text === null ? (
                "Not shown"
              ) : (
                <>
                  <span className={ui.srOnly}>Drop from the previous step: </span>
                  {text}
                </>
              )}
            </span>
            {typeof step.persons === "number" ? (
              <div role="meter" aria-label={step.label} aria-valuemin={0} aria-valuenow={step.persons}
                aria-valuemax={scale ?? step.persons} className={styles.track}>
                <div className={styles.fill} style={{ width: widthOf(step.persons) }} />
              </div>
            ) : (
              <div role="img" aria-label={`${step.label}: ${step.persons}`} className={styles.track}>
                <div data-suppressed="true" className={styles.suppressedFill}
                  style={{ width: widthOf(step.persons), minWidth: "4px" }} />
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function BaselineTables({ baseline }: { baseline: Baseline }) {
  const bands = [...new Set(baseline.age_sex.map((cell) => cell.age_band))];
  const cell = (band: string, sex: string): Count =>
    baseline.age_sex.find((c) => c.age_band === band && c.sex === sex)?.persons ?? 0;
  return (
    <>
      <table aria-label="Baseline by age and sex" className={styles.table}>
        <thead>
          <tr><th scope="col">Age</th>{SEXES.map(([, name]) => <th scope="col" key={name}>{name}</th>)}</tr>
        </thead>
        <tbody>
          {bands.map((band) => (
            <tr key={band}>
              <th scope="row">{band}</th>
              {SEXES.map(([sex]) => <td key={sex} className={styles.numeric}>{formatCount(cell(band, sex))}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {baseline.comorbidities.length > 0 && (
        <table aria-label="Most common conditions" className={styles.table}>
          <thead><tr><th scope="col">Condition</th><th scope="col">Patients</th></tr></thead>
          <tbody>
            {baseline.comorbidities.map((c) => (
              <tr key={c.concept_id}>
                <th scope="row">{c.name ?? `Concept ${c.concept_id}`}</th>
                <td className={styles.numeric}>{formatCount(c.persons)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

export function Results({ cohortId, onBack }: Props) {
  const [cohort, setCohort] = useState<Cohort | null>(null);
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [problem, setProblem] = useState<string | null>(null); // why loading failed
  const [runProblem, setRunProblem] = useState<{ message: string; reference: string | null; runId: string | null } | null>(null);
  const [running, setRunning] = useState(false);

  /** Reload the history; `keep` (the run just made) stays in the list even if the server has not returned it yet. */
  const refreshRuns = useCallback(async (keep?: Run) => {
    const fetched = await api.listRuns(cohortId);
    // an API that does not list baselines yet (an older revision) must not hide the one the run just returned
    const listed = keep?.baseline ? fetched.map((r) => (r.id === keep.id && !r.baseline ? { ...r, baseline: keep.baseline } : r)) : fetched;
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
      setRuns((previous) => newestFirst([fresh, ...(previous ?? []).filter((r) => r.id !== fresh.id)]));
      await refreshRuns(fresh).catch(() => undefined);
    } catch (error) {
      // the API records a failed or timed-out run and names it in the error body
      const recordedId = error instanceof ApiError && typeof error.body.run_id === "string" ? error.body.run_id : null;
      if (recordedId) setSelectedId(recordedId);
      if (error instanceof ApiError && error.status === 504) {
        setRunProblem({ message: error.message, reference: error.traceId, runId: recordedId });
      } else {
        setRunProblem({ message: failureText(error), reference: null, runId: recordedId });
      }
      await refreshRuns().catch(() => undefined); // a failed or timed-out run is recorded: show it in the history
    } finally {
      setRunning(false);
    }
  };

  if (problem) return <p role="alert">{problem}</p>;
  if (cohort === null || runs === null) return <p role="status">Loading results…</p>;

  const shown = runs.find((r) => r.id === selectedId) ?? runs[0] ?? null;
  const shownBaseline = shown?.baseline ?? null; // stored with the run (already suppressed), so it survives a reload
  // collapsed, the run being shown stays in the table even when it is older than the newest 5
  const visibleRuns = runs.filter((r, i) => showAll || i < HISTORY_PREVIEW || r.id === shown?.id);

  return (
    <section aria-label="Results">
      <header className={styles.header}>
        <button type="button" className={styles.backLink} onClick={onBack}>
          <span aria-hidden="true">←</span> Cohort list
        </button>
        <div className={styles.titleRow}>
          <h2 className={styles.title}>{cohort.name}</h2>
          <div className={styles.actions}>
            <button type="button" className={`${ui.btn} ${ui.primary}`} onClick={() => void runNow()} disabled={running}>
              {running ? "Running…" : "Run feasibility"}
            </button>
            <button type="button" className={`${ui.btn} ${ui.outline}`} onClick={onBack}>Back to cohorts</button>
          </div>
        </div>
      </header>

      {runProblem && (
        <p role="alert" className={styles.problem}>
          <span>{runProblem.message}</span>
          {runProblem.reference && <span> Reference: {runProblem.reference}</span>}
        </p>
      )}

      {/* the message about the run just attempted is already shown above; do not say it twice */}
      {shown !== null && shown.status !== "success" && runProblem?.runId !== shown.id && <RunProblem run={shown} />}

      <section aria-labelledby="results-overview" className={ui.card}>
        <h3 id="results-overview" className={ui.cardTitle}>Overview</h3>
        {shown === null && <p className={styles.muted}>This cohort has not been run yet.</p>}
        {shown !== null && (
          <div className={styles.overview}>
            <div>
              {shown.status === "success" && (
                <>
                  <strong className={styles.eyebrow}>Final count</strong>
                  <span className={styles.bigNumber} data-testid="final-count">
                    {shown.final_count === null ? "-" : formatCount(shown.final_count)}
                  </span>
                  <p className={styles.subtitle}>Final eligible population</p>
                </>
              )}
              <p className={styles.meta}>Run {formatTime(shown.created_at)} · {duration(shown.duration_ms)}</p>
            </div>
            <StatusBadge status={shown.status} testId="run-status" />
          </div>
        )}
      </section>

      <section aria-labelledby="results-waterfall" className={ui.card}>
        <h3 id="results-waterfall" className={ui.cardTitle}>Attrition waterfall</h3>
        {shown !== null && shown.status === "success" && shown.step_counts ? (
          <Waterfall steps={shown.step_counts} />
        ) : (
          <p className={styles.muted}>No steps to show.</p>
        )}
      </section>

      <section aria-labelledby="results-baseline" className={ui.card}>
        <h3 id="results-baseline" className={ui.cardTitle}>Baseline</h3>
        {shownBaseline ? (
          <BaselineTables baseline={shownBaseline} />
        ) : shown === null ? (
          <p className={styles.muted}>Run the cohort to see the baseline table.</p>
        ) : shown.status === "success" ? (
          <p className={styles.muted}>
            No baseline was kept for this run, which was made before baselines were stored. Run the cohort again to get one.
          </p>
        ) : (
          <p className={styles.muted}>A failed or timed-out run has no baseline.</p>
        )}
        <p className={styles.note}>{shownBaseline?.comorbidity_note ?? OMITTED_NOTE}</p>
      </section>

      <section aria-labelledby="results-history" className={ui.card}>
        <h3 id="results-history" className={ui.cardTitle}>Run history</h3>
        {runs.length > 0 ? (
          <table aria-label="Run history" className={styles.table}>
            <thead>
              <tr>
                <th scope="col">Time</th><th scope="col">Status</th><th scope="col">Final count</th>
                <th scope="col">Duration</th><th scope="col">Reference</th>
              </tr>
            </thead>
            <tbody>
              {visibleRuns.map((run) => (
                <tr key={run.id}>
                  <td id={`run-time-${run.id}`}>{formatTime(run.created_at)}</td>
                  <td><StatusBadge status={run.status} /></td>
                  <td className={styles.numeric}>{run.final_count === null ? "-" : formatCount(run.final_count)}</td>
                  <td className={styles.numeric}>{duration(run.duration_ms)}</td>
                  <td className={styles.reference}>
                    {run.status === "success" ? (
                      <button type="button" className={ui.pill} aria-describedby={`run-time-${run.id}`}
                        onClick={() => setSelectedId(run.id)}>View</button>
                    ) : run.trace_id}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className={styles.muted}>No runs yet.</p>
        )}
        {runs.length > HISTORY_PREVIEW && (
          <button type="button" className={`${ui.btn} ${ui.outline}`} aria-expanded={showAll}
            onClick={() => setShowAll((value) => !value)}>
            {showAll ? "Show fewer" : `Show all (${runs.length})`}
          </button>
        )}
      </section>
    </section>
  );
}
