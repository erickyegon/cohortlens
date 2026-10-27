// The builder's editing state, its conversion to and from the API definition, and client-side validation.
// The client checks only what is cheap and obvious so the user gets a field-level message before saving; the API
// (parse_definition) stays the source of truth and its field errors are shown by the same field paths.
import type { CohortDefinition, Domain } from "./types";

export const MAX_RULES = 10;
export const MAX_RULES_ALL_EVENTS = 3; // when every exposure is an index event (docs/cohort-engine.md, issue #37)

export interface ConceptRef {
  id: number;
  name?: string; // known only for concepts picked in this session
}

export interface RuleForm {
  key: number; // stable React key, so reordering keeps each rule's inputs
  label: string;
  type: "include" | "exclude";
  domain: Domain;
  concepts: ConceptRef[];
  include_descendants: boolean;
  start_days: string;
  end_days: string;
  min_occurrences: string;
}

export interface FormState {
  name: string;
  domain: Domain;
  concepts: ConceptRef[];
  include_descendants: boolean;
  first_occurrence_only: boolean;
  date_start: string;
  date_end: string;
  age_min: string;
  age_max: string;
  sexF: boolean;
  sexM: boolean;
  prior_days: string;
  post_days: string;
  rules: RuleForm[];
}

let nextKey = 1;

export function newRule(): RuleForm {
  return {
    key: nextKey++, label: "", type: "include", domain: "condition", concepts: [], include_descendants: true,
    start_days: "-365", end_days: "-1", min_occurrences: "1",
  };
}

export function emptyForm(): FormState {
  return {
    name: "", domain: "drug", concepts: [], include_descendants: true, first_occurrence_only: true,
    date_start: "", date_end: "", age_min: "", age_max: "", sexF: true, sexM: true,
    prior_days: "0", post_days: "0", rules: [],
  };
}

const text = (value: number | null | undefined) => (value === null || value === undefined ? "" : String(value));

export function toForm(definition: CohortDefinition): FormState {
  const { index_event: index, demographics, observation } = definition;
  return {
    name: definition.name,
    domain: index.domain,
    concepts: index.concept_ids.map((id) => ({ id })),
    include_descendants: index.include_descendants,
    first_occurrence_only: index.first_occurrence_only,
    date_start: index.date_range?.start ?? "",
    date_end: index.date_range?.end ?? "",
    age_min: text(demographics.age_min),
    age_max: text(demographics.age_max),
    sexF: demographics.sex.includes("F"),
    sexM: demographics.sex.includes("M"),
    prior_days: text(observation.prior_days),
    post_days: text(observation.post_days),
    rules: definition.rules.map((rule) => ({
      key: nextKey++, label: rule.label, type: rule.type, domain: rule.domain,
      concepts: rule.concept_ids.map((id) => ({ id })), include_descendants: rule.include_descendants,
      start_days: text(rule.window.start_days), end_days: text(rule.window.end_days),
      min_occurrences: text(rule.min_occurrences),
    })),
  };
}

const INTEGER = /^-?\d+$/;
const isInteger = (value: string) => INTEGER.test(value.trim());
const toInt = (value: string) => Number.parseInt(value.trim(), 10);
const orNull = (value: string) => (value.trim() === "" ? null : toInt(value));

/** Convert validated form state to the API definition. Call only when `validate` found no errors. */
export function toDefinition(form: FormState): CohortDefinition {
  const dateRange =
    form.date_start || form.date_end ? { start: form.date_start || null, end: form.date_end || null } : null;
  return {
    name: form.name.trim(),
    index_event: {
      domain: form.domain,
      concept_ids: form.concepts.map((c) => c.id),
      include_descendants: form.include_descendants,
      first_occurrence_only: form.first_occurrence_only,
      date_range: dateRange,
    },
    demographics: {
      age_min: orNull(form.age_min),
      age_max: orNull(form.age_max),
      sex: [...(form.sexF ? (["F"] as const) : []), ...(form.sexM ? (["M"] as const) : [])],
    },
    observation: { prior_days: toInt(form.prior_days || "0"), post_days: toInt(form.post_days || "0") },
    rules: form.rules.map((rule) => ({
      label: rule.label.trim(),
      type: rule.type,
      domain: rule.domain,
      concept_ids: rule.concepts.map((c) => c.id),
      include_descendants: rule.include_descendants,
      window: { start_days: toInt(rule.start_days), end_days: toInt(rule.end_days) },
      min_occurrences: toInt(rule.min_occurrences),
    })),
  };
}

export type Errors = Record<string, string>;

function checkInteger(errors: Errors, field: string, value: string, min: number, max: number, blankOk: boolean) {
  if (value.trim() === "") {
    if (!blankOk) errors[field] = "Enter a whole number";
    return null;
  }
  if (!isInteger(value)) {
    errors[field] = "Enter a whole number";
    return null;
  }
  const number = toInt(value);
  if (number < min || number > max) {
    errors[field] = `Enter a number from ${min} to ${max}`;
    return null;
  }
  return number;
}

/** Field-level errors keyed by the API's dotted field paths (for example `rules.1.window.end_days`). */
export function validate(form: FormState): Errors {
  const errors: Errors = {};
  const name = form.name.trim();
  if (name === "") errors["name"] = "Give the cohort a name";
  else if (name.length > 200) errors["name"] = "The name can have at most 200 characters";

  if (form.concepts.length === 0) errors["index_event.concept_ids"] = "Choose at least one concept";
  if (form.date_start && form.date_end && form.date_end < form.date_start) {
    errors["index_event.date_range.end"] = "The end date must not be before the start date";
  }

  const ageMin = checkInteger(errors, "demographics.age_min", form.age_min, 0, 130, true);
  const ageMax = checkInteger(errors, "demographics.age_max", form.age_max, 0, 130, true);
  if (ageMin !== null && ageMax !== null && ageMin > ageMax) {
    errors["demographics.age_max"] = "The maximum age must not be below the minimum age";
  }
  if (!form.sexF && !form.sexM) errors["demographics.sex"] = "Choose at least one sex";

  checkInteger(errors, "observation.prior_days", form.prior_days, 0, 36500, true);
  checkInteger(errors, "observation.post_days", form.post_days, 0, 36500, true);

  if (form.rules.length > MAX_RULES) errors["rules"] = `A cohort can have at most ${MAX_RULES} rules`;
  if (!form.first_occurrence_only && form.rules.length > MAX_RULES_ALL_EVENTS) {
    errors["rules"] = `With every exposure as an index event a cohort can have at most ${MAX_RULES_ALL_EVENTS} rules`;
  }
  const seen = new Map<string, number>();
  form.rules.forEach((rule, i) => {
    const label = rule.label.trim();
    if (label === "") errors[`rules.${i}.label`] = "Give the rule a label";
    else if (label.length > 100) errors[`rules.${i}.label`] = "The label can have at most 100 characters";
    else if (seen.has(label.toLowerCase())) errors[`rules.${i}.label`] = "Labels must be different";
    seen.set(label.toLowerCase(), i);
    if (rule.concepts.length === 0) errors[`rules.${i}.concept_ids`] = "Choose at least one concept";
    const start = checkInteger(errors, `rules.${i}.window.start_days`, rule.start_days, -36500, 36500, false);
    const end = checkInteger(errors, `rules.${i}.window.end_days`, rule.end_days, -36500, 36500, false);
    if (start !== null && end !== null && start > end) {
      errors[`rules.${i}.window.end_days`] = "The window must not end before it starts";
    }
    const occurrences = checkInteger(errors, `rules.${i}.min_occurrences`, rule.min_occurrences, 1, 100, false);
    if (!form.first_occurrence_only && occurrences !== null && occurrences !== 1) {
      errors[`rules.${i}.min_occurrences`] = "With every exposure as an index event this must be 1";
    }
  });
  return errors;
}

/** A new array with the rule at `index` moved by `delta` (-1 up, +1 down); unchanged at the ends. */
export function moveRule(rules: RuleForm[], index: number, delta: -1 | 1): RuleForm[] {
  const target = index + delta;
  if (target < 0 || target >= rules.length) return rules;
  const copy = [...rules];
  [copy[index], copy[target]] = [copy[target], copy[index]];
  return copy;
}
