import type { Cohort, CohortDefinition, CohortSummary, Concept, Domain, FieldError, Run } from "./types";

/** An error answer from the API (or a network failure, status 0). Carries the trace id the user can quote. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly errors: FieldError[] = [],
    readonly traceId: string | null = null,
    readonly body: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, "Could not reach the server. Check your connection and try again.");
  }
  if (response.status === 204) return undefined as T;
  let body: Record<string, unknown> = {};
  try {
    body = await response.json();
  } catch {
    // not JSON (for example a proxy error page): fall through with an empty body
  }
  if (!response.ok) {
    const traceId = (body.trace_id as string | undefined) ?? response.headers.get("X-Trace-Id");
    throw new ApiError(
      response.status,
      (body.error as string | undefined) ?? "Something went wrong.",
      (body.errors as FieldError[] | undefined) ?? [],
      traceId ?? null,
      body,
    );
  }
  return body as T;
}

export const api = {
  listCohorts: () => request<CohortSummary[]>("/cohorts"),
  getCohort: (id: string) => request<Cohort>(`/cohorts/${id}`),
  createCohort: (definition: CohortDefinition) =>
    request<Cohort>("/cohorts", { method: "POST", body: JSON.stringify(definition) }),
  updateCohort: (id: string, definition: CohortDefinition) =>
    request<Cohort>(`/cohorts/${id}`, { method: "PUT", body: JSON.stringify(definition) }),
  deleteCohort: (id: string) => request<void>(`/cohorts/${id}`, { method: "DELETE" }),
  searchConcepts: (q: string, domain?: Domain) =>
    request<Concept[]>(`/concepts?${new URLSearchParams({ q, ...(domain ? { domain } : {}) })}`),
  runCohort: (id: string) => request<Run>(`/cohorts/${id}/runs`, { method: "POST" }),
  listRuns: (id: string) => request<Run[]>(`/cohorts/${id}/runs`),
};
