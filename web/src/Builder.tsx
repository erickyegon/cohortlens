import { useEffect, useState } from "react";
import { ApiError, api } from "./api";
import { ConceptPicker } from "./ConceptPicker";
import {
  MAX_RULES, emptyForm, moveRule, newRule, toDefinition, toForm, validate,
  type FormState, type RuleForm,
} from "./form";
import type { Domain } from "./types";

interface Props {
  cohortId: string | null; // null: a new cohort
  onBack: () => void;
  onSaved?: (id: string) => void;
}

/** The id an error message gets, so its input can point at it with aria-describedby. */
const errorId = (path: string) => `error-${path.replace(/[^a-zA-Z0-9]+/g, "-")}`;

function Message({ path, text }: { path: string; text?: string }) {
  return text ? <span id={errorId(path)} role="alert" className="error">{text}</span> : null;
}

export function Builder({ cohortId, onBack, onSaved }: Props) {
  const [form, setForm] = useState<FormState>(emptyForm);
  const [savedId, setSavedId] = useState<string | null>(cohortId);
  const [version, setVersion] = useState<number | null>(null);
  const [loading, setLoading] = useState(cohortId !== null);
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const [submitted, setSubmitted] = useState(false);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (cohortId === null) return;
    let current = true;
    api
      .getCohort(cohortId)
      .then((cohort) => {
        if (!current) return;
        setForm(toForm(cohort.definition));
        setVersion(cohort.version);
        setLoading(false);
      })
      .catch((error: unknown) => {
        if (!current) return;
        setBanner(failureText(error));
        setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [cohortId]);

  const clientErrors = validate(form);
  const errorFor = (path: string): string | undefined =>
    serverErrors[path] ?? (submitted || touched.has(path) ? clientErrors[path] : undefined);
  /** Attributes that tie an input to its error message (read by screen readers, and by the tests). */
  const a11y = (path: string) =>
    errorFor(path) ? { "aria-invalid": true as const, "aria-describedby": errorId(path) } : {};
  const message = (path: string) => <Message path={path} text={errorFor(path)} />;

  const change = (patch: Partial<FormState>, ...paths: string[]) => {
    setForm((f) => ({ ...f, ...patch }));
    setTouched((t) => new Set([...t, ...paths]));
    setServerErrors({});
  };
  const changeRule = (index: number, patch: Partial<RuleForm>, ...fields: string[]) => {
    setForm((f) => ({ ...f, rules: f.rules.map((r, i) => (i === index ? { ...r, ...patch } : r)) }));
    setTouched((t) => new Set([...t, ...fields.map((field) => `rules.${index}.${field}`)]));
    setServerErrors({});
  };

  const save = async () => {
    setSubmitted(true);
    setBanner(null);
    if (Object.keys(clientErrors).length > 0) return; // cannot save while a field has an error
    setSaving(true);
    try {
      const definition = toDefinition(form);
      const cohort = savedId ? await api.updateCohort(savedId, definition) : await api.createCohort(definition);
      setSavedId(cohort.id);
      setVersion(cohort.version);
      setServerErrors({});
      setBanner(`Saved as version ${cohort.version}.`);
      onSaved?.(cohort.id);
    } catch (error) {
      if (error instanceof ApiError && error.status === 422 && error.errors.length > 0) {
        setServerErrors(Object.fromEntries(error.errors.map((e) => [e.field, e.message])));
        setBanner("The server did not accept the cohort. Fix the marked fields.");
      } else {
        setBanner(failureText(error));
      }
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <p role="status">Loading cohort…</p>;

  return (
    <form onSubmit={(e) => { e.preventDefault(); void save(); }} aria-label="Cohort builder">
      <h2>{savedId ? `Edit cohort${version ? ` (version ${version})` : ""}` : "New cohort"}</h2>
      {banner && <p role="status">{banner}</p>}

      <label>
        Name
        <input value={form.name} onChange={(e) => change({ name: e.target.value }, "name")} {...a11y("name")} />
      </label>
      {message("name")}

      <fieldset>
        <legend>Index event</legend>
        <label>
          Index event domain
          <select
            value={form.domain}
            onChange={(e) => change({ domain: e.target.value as Domain, concepts: [] }, "index_event.concept_ids")}
          >
            <option value="drug">Drug exposure</option>
            <option value="condition">Condition</option>
          </select>
        </label>
        <ConceptPicker
          label="Index concepts"
          domain={form.domain}
          selected={form.concepts}
          onChange={(concepts) => change({ concepts }, "index_event.concept_ids")}
          error={errorFor("index_event.concept_ids")}
          errorId={errorId("index_event.concept_ids")}
        />
        <label>
          <input type="checkbox" checked={form.include_descendants}
            onChange={(e) => change({ include_descendants: e.target.checked })} />
          Include descendant concepts
        </label>
        <label>
          <input type="checkbox" checked={form.first_occurrence_only}
            onChange={(e) => change({ first_occurrence_only: e.target.checked }, "rules")} />
          First occurrence only
        </label>
        <label>
          Index date from
          <input type="date" value={form.date_start} {...a11y("index_event.date_range.end")}
            onChange={(e) => change({ date_start: e.target.value }, "index_event.date_range.end")} />
        </label>
        <label>
          Index date to
          <input type="date" value={form.date_end} {...a11y("index_event.date_range.end")}
            onChange={(e) => change({ date_end: e.target.value }, "index_event.date_range.end")} />
        </label>
        {message("index_event.date_range.end")}
      </fieldset>

      <fieldset>
        <legend>Demographics</legend>
        <label>
          Minimum age
          <input value={form.age_min} inputMode="numeric" {...a11y("demographics.age_min")}
            onChange={(e) => change({ age_min: e.target.value }, "demographics.age_min", "demographics.age_max")} />
        </label>
        {message("demographics.age_min")}
        <label>
          Maximum age
          <input value={form.age_max} inputMode="numeric" {...a11y("demographics.age_max")}
            onChange={(e) => change({ age_max: e.target.value }, "demographics.age_min", "demographics.age_max")} />
        </label>
        {message("demographics.age_max")}
        <label>
          <input type="checkbox" checked={form.sexF} {...a11y("demographics.sex")}
            onChange={(e) => change({ sexF: e.target.checked }, "demographics.sex")} />
          Female
        </label>
        <label>
          <input type="checkbox" checked={form.sexM} {...a11y("demographics.sex")}
            onChange={(e) => change({ sexM: e.target.checked }, "demographics.sex")} />
          Male
        </label>
        {message("demographics.sex")}
      </fieldset>

      <fieldset>
        <legend>Observation</legend>
        <label>
          Days of observation before the index date
          <input value={form.prior_days} inputMode="numeric" {...a11y("observation.prior_days")}
            onChange={(e) => change({ prior_days: e.target.value }, "observation.prior_days")} />
        </label>
        {message("observation.prior_days")}
        <label>
          Days of observation after the index date
          <input value={form.post_days} inputMode="numeric" {...a11y("observation.post_days")}
            onChange={(e) => change({ post_days: e.target.value }, "observation.post_days")} />
        </label>
        {message("observation.post_days")}
      </fieldset>

      <fieldset>
        <legend>Rules</legend>
        {message("rules")}
        {form.rules.map((rule, i) => {
          const n = i + 1;
          const at = (field: string) => `rules.${i}.${field}`;
          return (
            <fieldset key={rule.key}>
              <legend>{`Rule ${n}`}</legend>
              <label>
                {`Rule ${n} label`}
                <input value={rule.label} {...a11y(at("label"))}
                  onChange={(e) => changeRule(i, { label: e.target.value }, "label")} />
              </label>
              {message(at("label"))}
              <label>
                {`Rule ${n} type`}
                <select value={rule.type}
                  onChange={(e) => changeRule(i, { type: e.target.value as RuleForm["type"] })}>
                  <option value="include">Include</option>
                  <option value="exclude">Exclude</option>
                </select>
              </label>
              <label>
                {`Rule ${n} domain`}
                <select value={rule.domain}
                  onChange={(e) => changeRule(i, { domain: e.target.value as Domain, concepts: [] }, "concept_ids")}>
                  <option value="condition">Condition</option>
                  <option value="drug">Drug exposure</option>
                </select>
              </label>
              <ConceptPicker
                label={`Rule ${n} concepts`}
                domain={rule.domain}
                selected={rule.concepts}
                onChange={(concepts) => changeRule(i, { concepts }, "concept_ids")}
                error={errorFor(at("concept_ids"))}
                errorId={errorId(at("concept_ids"))}
              />
              <label>
                <input type="checkbox" checked={rule.include_descendants}
                  onChange={(e) => changeRule(i, { include_descendants: e.target.checked })} />
                {`Rule ${n} include descendants`}
              </label>
              <label>
                {`Rule ${n} window start (days from the index date)`}
                <input value={rule.start_days} inputMode="numeric" {...a11y(at("window.start_days"))}
                  onChange={(e) => changeRule(i, { start_days: e.target.value }, "window.start_days", "window.end_days")} />
              </label>
              {message(at("window.start_days"))}
              <label>
                {`Rule ${n} window end (days from the index date)`}
                <input value={rule.end_days} inputMode="numeric" {...a11y(at("window.end_days"))}
                  onChange={(e) => changeRule(i, { end_days: e.target.value }, "window.start_days", "window.end_days")} />
              </label>
              {message(at("window.end_days"))}
              <label>
                {`Rule ${n} minimum occurrences`}
                <input value={rule.min_occurrences} inputMode="numeric" {...a11y(at("min_occurrences"))}
                  onChange={(e) => changeRule(i, { min_occurrences: e.target.value }, "min_occurrences")} />
              </label>
              {message(at("min_occurrences"))}
              <button type="button" aria-label={`Move rule ${n} up`} disabled={i === 0}
                onClick={() => change({ rules: moveRule(form.rules, i, -1) }, "rules")}>↑</button>
              <button type="button" aria-label={`Move rule ${n} down`} disabled={i === form.rules.length - 1}
                onClick={() => change({ rules: moveRule(form.rules, i, 1) }, "rules")}>↓</button>
              <button type="button" aria-label={`Remove rule ${n}`}
                onClick={() => change({ rules: form.rules.filter((r) => r.key !== rule.key) }, "rules")}>Remove</button>
            </fieldset>
          );
        })}
        <button type="button" disabled={form.rules.length >= MAX_RULES}
          onClick={() => change({ rules: [...form.rules, newRule()] }, "rules")}>Add rule</button>
      </fieldset>

      <button type="submit" disabled={saving}>Save cohort</button>{" "}
      <button type="button" onClick={onBack}>Back to cohorts</button>
    </form>
  );
}

export function failureText(error: unknown): string {
  if (error instanceof ApiError) {
    return error.traceId
      ? `Something went wrong. Reference: ${error.traceId}`
      : error.status === 0 ? error.message : "Something went wrong.";
  }
  return "Something went wrong.";
}
