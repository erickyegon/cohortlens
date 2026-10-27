// Shapes of the API (docs/architecture.md, docs/cohort-engine.md). Counts are numbers, or the string "<11".

export type Domain = "condition" | "drug";
export type Count = number | "<11";

export interface IndexEvent {
  domain: Domain;
  concept_ids: number[];
  include_descendants: boolean;
  first_occurrence_only: boolean;
  date_range: { start: string | null; end: string | null } | null;
}

export interface Demographics {
  age_min: number | null;
  age_max: number | null;
  sex: ("F" | "M")[];
}

export interface Observation {
  prior_days: number;
  post_days: number;
}

export interface Rule {
  label: string;
  type: "include" | "exclude";
  domain: Domain;
  concept_ids: number[];
  include_descendants: boolean;
  window: { start_days: number; end_days: number };
  min_occurrences: number;
}

export interface CohortDefinition {
  name: string;
  index_event: IndexEvent;
  demographics: Demographics;
  observation: Observation;
  rules: Rule[];
}

export interface LastRun {
  status: "success" | "failed" | "timeout";
  final_count: Count | null;
  created_at: string;
}

export interface CohortSummary {
  id: string;
  name: string;
  version: number;
  created_at: string;
  updated_at: string;
  last_run: LastRun | null;
}

export interface Cohort {
  id: string;
  name: string;
  version: number;
  definition: CohortDefinition;
  created_at: string;
  updated_at: string;
}

export interface Concept {
  concept_id: number;
  name: string;
  domain: Domain;
}

export interface StepCount {
  key: string;
  label: string;
  persons: Count;
}

export interface Baseline {
  age_sex: { age_band: string; sex: string; persons: Count }[];
  comorbidities: { concept_id: number; name: string | null; persons: number }[];
  comorbidity_note: string;
}

export interface Run {
  id: string;
  cohort_id: string;
  status: "success" | "failed" | "timeout";
  step_counts: StepCount[] | null;
  final_count: Count | null;
  baseline?: Baseline;
  duration_ms: number | null;
  trace_id: string | null;
  service_version: string | null;
  created_at: string;
}

export interface FieldError {
  field: string;
  message: string;
}
