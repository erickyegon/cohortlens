// Issue #14: the API client: URLs, methods, bodies, and how errors carry the trace id (FR-12).
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "./api";
import { mockApi } from "./test-utils";

afterEach(() => vi.unstubAllGlobals());

describe("requests", () => {
  it("calls the right method, path and body under /api", async () => {
    const mock = mockApi({
      "GET /cohorts": [], "GET /cohorts/x": {}, "POST /cohorts": {}, "PUT /cohorts/x": {}, "DELETE /cohorts/x": { status: 204 },
      "POST /cohorts/x/runs": {}, "GET /cohorts/x/runs": [],
    });
    const definition = { name: "n" } as never;
    await api.listCohorts();
    await api.getCohort("x");
    await api.createCohort(definition);
    await api.updateCohort("x", definition);
    expect(await api.deleteCohort("x")).toBeUndefined();
    await api.runCohort("x");
    await api.listRuns("x");
    expect(mock.calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      "GET /cohorts", "GET /cohorts/x", "POST /cohorts", "PUT /cohorts/x", "DELETE /cohorts/x", "POST /cohorts/x/runs",
      "GET /cohorts/x/runs"]);
    expect(mock.calls[2].body).toEqual({ name: "n" });
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/cohorts");
  });

  it("url-encodes concept searches and passes the domain only when given", async () => {
    const mock = mockApi({ "GET /concepts?q=heart+failure%2F1": [], "GET /concepts?q=ab&domain=drug": [] });
    await api.searchConcepts("heart failure/1");
    await api.searchConcepts("ab", "drug");
    expect(mock.calls.map((c) => c.path)).toEqual(["/concepts?q=heart+failure%2F1", "/concepts?q=ab&domain=drug"]);
  });
});

describe("errors", () => {
  it("carries the message, field errors and the trace id from the body", async () => {
    mockApi({ "GET /cohorts": { status: 422, body: { error: "Invalid", trace_id: "t-body", errors: [{ field: "a", message: "b" }] } } });
    const error = await api.listCohorts().catch((e: unknown) => e as ApiError);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, message: "Invalid", traceId: "t-body", errors: [{ field: "a", message: "b" }] });
  });

  it("falls back to the X-Trace-Id header when the body has none", async () => {
    mockApi({ "GET /cohorts": { status: 502, body: {}, headers: { "X-Trace-Id": "t-header" } } });
    const error = (await api.listCohorts().catch((e: unknown) => e)) as ApiError;
    expect(error.traceId).toBe("t-header");
    expect(error.message).toBe("Something went wrong.");
  });

  it("copes with a non-JSON error page", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>Bad gateway</html>", { status: 502 })));
    const error = (await api.listCohorts().catch((e: unknown) => e)) as ApiError;
    expect(error).toMatchObject({ status: 502, traceId: null, errors: [] });
  });

  it("turns a network failure into status 0 with a plain message", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const error = (await api.listCohorts().catch((e: unknown) => e)) as ApiError;
    expect(error).toMatchObject({ status: 0 });
    expect(error.message).toMatch(/Could not reach the server/);
  });

  it("returns the body of a timeout answer so the UI can show it", async () => {
    mockApi({ "POST /cohorts/x/runs": { status: 504, body: { error: "took longer than 10 seconds", run_id: "r1", status: "timeout", trace_id: "t" } } });
    const error = (await api.runCohort("x").catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(504);
    expect(error.body).toMatchObject({ run_id: "r1", status: "timeout" });
  });
});
