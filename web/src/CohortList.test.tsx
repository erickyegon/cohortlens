// Issue #14: the cohort list (docs/spec.md US-2, US-6, FR-15): each row shows the last run's count and time.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CohortList } from "./CohortList";
import { mockApi } from "./test-utils";

afterEach(() => vi.unstubAllGlobals());

const summary = (id: string, name: string, last_run: unknown) => ({
  id, name, version: 2, created_at: "2026-10-27T09:00:00+00:00", updated_at: "2026-10-27T09:30:00+00:00", last_run,
});

const COHORTS = [
  summary("a", "Ran fine", { status: "success", final_count: 4812, created_at: "2026-10-27T15:04:30+00:00" }),
  summary("b", "Tiny result", { status: "success", final_count: "<11", created_at: "2026-10-26T08:05:00+00:00" }),
  summary("c", "Never run", null),
  summary("d", "Too slow", { status: "timeout", final_count: null, created_at: "2026-10-25T23:59:00+00:00" }),
  summary("e", "Broke", { status: "failed", final_count: null, created_at: "2026-10-24T01:00:00+00:00" }),
];

const row = (name: string) => screen.getByRole("row", { name: new RegExp(name) });

describe("Given the cohort list, then each row shows last run count and time", () => {
  it("shows the count with thousands separators and the time in a fixed UTC format", async () => {
    mockApi({ "GET /cohorts": COHORTS });
    render(<CohortList onNew={() => {}} onOpen={() => {}} />);
    await screen.findByText("Ran fine");
    const cells = within(row("Ran fine")).getAllByRole("cell").map((c) => c.textContent);
    expect(cells.slice(0, 4)).toEqual(["Ran fine", "2", "4,812 patients", "2026-10-27 15:04 UTC"]);
  });

  it("shows a suppressed count as <11, never a number", async () => {
    mockApi({ "GET /cohorts": COHORTS });
    render(<CohortList onNew={() => {}} onOpen={() => {}} />);
    await screen.findByText("Tiny result");
    expect(within(row("Tiny result")).getByText("<11 patients")).toBeInTheDocument();
  });

  it("says so for a cohort that was never run, timed out or failed", async () => {
    mockApi({ "GET /cohorts": COHORTS });
    render(<CohortList onNew={() => {}} onOpen={() => {}} />);
    await screen.findByText("Never run");
    expect(within(row("Never run")).getByText("Not run yet")).toBeInTheDocument();
    expect(within(row("Never run")).getByText("-")).toBeInTheDocument();
    expect(within(row("Too slow")).getByText("Timed out")).toBeInTheDocument();
    expect(within(row("Too slow")).getByText("2026-10-25 23:59 UTC")).toBeInTheDocument();
    expect(within(row("Broke")).getByText("Failed")).toBeInTheDocument();
  });
});

describe("An API that predates last_run", () => {
  it("reads a summary without a last_run as not run yet instead of breaking the page", async () => {
    const { last_run: _omitted, ...withoutLastRun } = summary("z", "Old API", null);
    mockApi({ "GET /cohorts": [withoutLastRun] });
    render(<CohortList onNew={() => {}} onOpen={() => {}} />);
    expect(await screen.findByText("Old API")).toBeInTheDocument();
    expect(within(row("Old API")).getByText("Not run yet")).toBeInTheDocument();
  });
});

describe("Opening and creating", () => {
  it("opens a cohort by its id", async () => {
    mockApi({ "GET /cohorts": COHORTS });
    const onOpen = vi.fn();
    render(<CohortList onNew={() => {}} onOpen={onOpen} />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Open Tiny result" }));
    expect(onOpen).toHaveBeenCalledWith("b");
  });

  it("starts a new cohort", async () => {
    mockApi({ "GET /cohorts": [] });
    const onNew = vi.fn();
    render(<CohortList onNew={onNew} onOpen={() => {}} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "New cohort" }));
    expect(onNew).toHaveBeenCalled();
  });
});

describe("States", () => {
  it("shows a loading message first, and an empty-state message for no cohorts", async () => {
    mockApi({ "GET /cohorts": [] });
    render(<CohortList onNew={() => {}} onOpen={() => {}} />);
    expect(screen.getByText("Loading cohorts…")).toBeInTheDocument();
    expect(await screen.findByText("No cohorts yet. Create one to check feasibility.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows the reference when the list cannot be loaded", async () => {
    mockApi({ "GET /cohorts": { status: 503, body: { error: "The database is unavailable.", trace_id: "cafe01" } } });
    render(<CohortList onNew={() => {}} onOpen={() => {}} />);
    expect(await screen.findByText("Something went wrong. Reference: cafe01")).toBeInTheDocument();
  });

  it("says the server cannot be reached when the network fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network"); }));
    render(<CohortList onNew={() => {}} onOpen={() => {}} />);
    expect(await screen.findByText(/Could not reach the server/)).toBeInTheDocument();
  });
});
