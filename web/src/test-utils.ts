import { vi } from "vitest";

export interface Call {
  method: string;
  path: string; // including the query string, without the /api prefix
  body: unknown;
}

type Reply = unknown | { status: number; body?: unknown; headers?: Record<string, string> };
type Handler = Reply | ((call: Call) => Reply);

const isStatusReply = (reply: unknown): reply is { status: number; body?: unknown; headers?: Record<string, string> } =>
  typeof reply === "object" && reply !== null && "status" in reply && typeof (reply as { status: unknown }).status === "number";

/**
 * Replace `fetch` with a stub that answers by "METHOD /path" (the /api prefix is dropped). A handler is a JSON body
 * (answered 200), `{status, body, headers}`, or a function of the call returning either. Unmatched calls fail the test.
 */
export function mockApi(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const stub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input).replace(/^\/api/, "");
    const call: Call = {
      method: (init?.method ?? "GET").toUpperCase(),
      path: url,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    };
    calls.push(call);
    const key = `${call.method} ${call.path}`;
    const handler = key in routes ? routes[key] : undefined;
    if (handler === undefined) throw new Error(`unexpected request: ${key}`);
    const reply = typeof handler === "function" ? (handler as (c: Call) => Reply)(call) : handler;
    const { status, body, headers } = isStatusReply(reply) ? reply : { status: 200, body: reply, headers: undefined };
    return new Response(status === 204 ? null : JSON.stringify(body ?? {}), {
      status,
      headers: { "Content-Type": "application/json", ...(headers ?? {}) },
    });
  });
  vi.stubGlobal("fetch", stub);
  return { calls, writes: () => calls.filter((c) => c.method !== "GET") };
}

/** The reference cohort of docs/spec.md section 7 as the API returns it (api/tests/fixtures/reference_cohort.json). */
export const REFERENCE_DEFINITION = {
  name: "New users of lisinopril with hypertension, aged 50+, no prior heart failure",
  index_event: {
    domain: "drug", concept_ids: [1308216], include_descendants: true, first_occurrence_only: true, date_range: null,
  },
  demographics: { age_min: 50, age_max: null, sex: ["F", "M"] },
  observation: { prior_days: 365, post_days: 0 },
  rules: [
    {
      label: "No ACE inhibitor in the prior 365 days", type: "exclude", domain: "drug", concept_ids: [21601784],
      include_descendants: true, window: { start_days: -365, end_days: -1 }, min_occurrences: 1,
    },
    {
      label: "Essential hypertension on or before index", type: "include", domain: "condition", concept_ids: [320128],
      include_descendants: true, window: { start_days: -36500, end_days: 0 }, min_occurrences: 1,
    },
    {
      label: "No prior heart failure", type: "exclude", domain: "condition", concept_ids: [316139],
      include_descendants: true, window: { start_days: -36500, end_days: -1 }, min_occurrences: 1,
    },
  ],
};

export const REFERENCE_COHORT = {
  id: "11111111-1111-4111-8111-111111111111",
  name: REFERENCE_DEFINITION.name,
  version: 3,
  definition: REFERENCE_DEFINITION,
  created_at: "2026-10-27T10:00:00+00:00",
  updated_at: "2026-10-27T12:00:00+00:00",
};

/** Non-default values in every field, with every exposure an index event (so at most 3 rules, occurrences 1). */
export const FULL_A = {
  name: "Hypertension, women 18-65",
  index_event: {
    domain: "condition", concept_ids: [320128, 317898], include_descendants: false, first_occurrence_only: false,
    date_range: { start: "2015-01-01", end: "2024-12-31" },
  },
  demographics: { age_min: 18, age_max: 65, sex: ["F"] },
  observation: { prior_days: 30, post_days: 90 },
  rules: [
    {
      label: "Statin before", type: "include", domain: "drug", concept_ids: [1539403], include_descendants: false,
      window: { start_days: -90, end_days: 0 }, min_occurrences: 1,
    },
    {
      label: "No stroke", type: "exclude", domain: "condition", concept_ids: [443454, 4043731],
      include_descendants: true, window: { start_days: -36500, end_days: -1 }, min_occurrences: 1,
    },
  ],
};

/** Non-default values the other way: first occurrence only, a one-sided date range, repeated use (3 occurrences). */
export const FULL_B = {
  name: "Men to 40 on repeat therapy",
  index_event: {
    domain: "drug", concept_ids: [1308216], include_descendants: true, first_occurrence_only: true,
    date_range: { start: "2020-06-01", end: null },
  },
  demographics: { age_min: null, age_max: 40, sex: ["M"] },
  observation: { prior_days: 0, post_days: 365 },
  rules: [
    {
      label: "Repeat use", type: "include", domain: "drug", concept_ids: [1308216], include_descendants: false,
      window: { start_days: 1, end_days: 365 }, min_occurrences: 3,
    },
  ],
};
