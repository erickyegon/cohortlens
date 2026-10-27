// Issue #15: the results screen (docs/spec.md US-4, US-5, US-7, US-9, FR-15): the final count, a bar for every step of
// the attrition waterfall, the baseline table and the run history. Fetch is stubbed; nothing here needs a server.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Results } from "./Results";
import { REFERENCE_COHORT, mockApi } from "./test-utils";

const ID = REFERENCE_COHORT.id;
const COHORT = `GET /cohorts/${ID}`;
const RUNS = `GET /cohorts/${ID}/runs`;
const RUN = `POST /cohorts/${ID}/runs`;

afterEach(() => vi.unstubAllGlobals());

const steps = (counts: (number | string)[]) =>
  counts.map((persons, i) => ({ key: `step_${i}`, label: `Step ${i + 1}`, persons }));

const run = (over: Record<string, unknown> = {}) => ({
  id: "r1", cohort_id: ID, status: "success", step_counts: steps([920, 552, 481]), final_count: 481,
  duration_ms: 120, trace_id: "trace-r1", service_version: "abc1234", created_at: "2026-10-27T15:04:30+00:00", ...over,
});

const BASELINE = {
  age_sex: [
    { age_band: "50-59", sex: "F", persons: 120 }, { age_band: "50-59", sex: "M", persons: "<11" },
    { age_band: "50-59", sex: "other", persons: 0 },
    { age_band: "60-69", sex: "F", persons: 0 }, { age_band: "60-69", sex: "M", persons: 90 },
    { age_band: "60-69", sex: "other", persons: 0 },
  ],
  comorbidities: [
    { concept_id: 320128, name: "Essential hypertension", persons: 481 }, { concept_id: 1, name: null, persons: 30 },
  ],
  comorbidity_note: "Conditions with fewer than 11 patients are not shown.",
};

const show = () => render(<Results cohortId={ID} onBack={() => {}} />);

function loaded(runs: unknown[], extra: Record<string, unknown> = {}) {
  return mockApi({ [COHORT]: REFERENCE_COHORT, [RUNS]: runs, ...extra });
}

describe("Given a completed run, then the final count and a bar for each step are shown", () => {
  it("shows the cohort name and the final count of the latest run", async () => {
    loaded([run()]);
    show();
    expect(await screen.findByRole("heading", { name: REFERENCE_COHORT.name })).toBeInTheDocument();
    expect(screen.getByText("Final count", { selector: "strong" })).toBeInTheDocument(); // not the history column header
    expect(screen.getByTestId("final-count")).toHaveTextContent("481");
  });

  it("draws one bar per step, in order, scaled to the first step", async () => {
    loaded([run()]);
    show();
    const bars = await screen.findAllByRole("meter");
    expect(bars.map((bar) => bar.getAttribute("aria-label"))).toEqual(["Step 1", "Step 2", "Step 3"]);
    expect(bars.map((bar) => bar.getAttribute("aria-valuenow"))).toEqual(["920", "552", "481"]);
    expect(bars.every((bar) => bar.getAttribute("aria-valuemax") === "920")).toBe(true);
    expect((bars[1].firstElementChild as HTMLElement).style.width).toBe(`${(552 / 920) * 100}%`);
    expect((bars[0].firstElementChild as HTMLElement).style.width).toBe("100%");
  });

  it("lists each step's label and count beside its bar", async () => {
    loaded([run()]);
    show();
    const waterfall = await screen.findByRole("list", { name: "Attrition waterfall" });
    const items = within(waterfall).getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["Step 1920", "Step 2552", "Step 3481"]);
  });

  it("formats large counts with separators", async () => {
    loaded([run({ step_counts: steps([12345, 6789]), final_count: 6789 })]);
    show();
    expect(await screen.findByTestId("final-count")).toHaveTextContent("6,789");
    expect(screen.getByText("12,345")).toBeInTheDocument();
  });

  it("shows no 'removed' differences between steps (they could be small numbers)", async () => {
    loaded([run()]);
    show();
    await screen.findAllByRole("meter");
    expect(screen.queryByText(/removed/i)).not.toBeInTheDocument();
    expect(screen.queryByText("368")).not.toBeInTheDocument(); // 920 - 552
  });
});

describe("Given a step with 1 to 10 patients, then it displays <11", () => {
  const small = () => run({ step_counts: steps([920, 552, "<11"]), final_count: "<11" });

  it("shows <11 for the final count and for the step, never a number", async () => {
    loaded([small()]);
    show();
    expect(await screen.findByTestId("final-count")).toHaveTextContent("<11");
    const waterfall = screen.getByRole("list", { name: "Attrition waterfall" });
    expect(within(waterfall).getAllByRole("listitem")[2]).toHaveTextContent("Step 3<11");
  });

  it("still draws a bar for the suppressed step, described as <11 with no value", async () => {
    loaded([small()]);
    show();
    const bars = await screen.findAllByRole("meter");
    expect(bars).toHaveLength(3);
    expect(bars[2]).toHaveAttribute("aria-valuetext", "<11");
    expect(bars[2]).not.toHaveAttribute("aria-valuenow");
    expect((bars[2].firstElementChild as HTMLElement).style.width).not.toBe("0%");
  });

  it("scales the bars to the first step even when that step is the largest", async () => {
    loaded([run({ step_counts: steps(["<11", 0]), final_count: 0 })]);
    show();
    const bars = await screen.findAllByRole("meter");
    expect(bars[0]).toHaveAttribute("aria-valuetext", "<11");
    expect(bars[1]).toHaveAttribute("aria-valuenow", "0");
    expect(bars[1]).not.toHaveAttribute("aria-valuemax", "NaN");
  });
});

describe("Given a failed run, then the user sees the reference", () => {
  it("shows 'Something went wrong. Reference: <trace id>' when the latest run failed", async () => {
    loaded([run({ id: "r2", status: "failed", step_counts: null, final_count: null, trace_id: "deadbeef01" })]);
    show();
    expect(await screen.findByText("Something went wrong. Reference: deadbeef01")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Attrition waterfall" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("final-count")).not.toBeInTheDocument();
  });

  it("shows the reference when Run feasibility fails, and refreshes the history (the failure was recorded)", async () => {
    const api = loaded([], {
      [RUN]: { status: 503, body: { error: "The database is unavailable.", trace_id: "cafe0002", run_id: "r9", status: "failed" } },
    });
    show();
    await screen.findByRole("button", { name: "Run feasibility" });
    await userEvent.setup().click(screen.getByRole("button", { name: "Run feasibility" }));
    expect(await screen.findByText("Something went wrong. Reference: cafe0002")).toBeInTheDocument();
    await waitFor(() => expect(api.calls.filter((c) => c.path === `/cohorts/${ID}/runs` && c.method === "GET")).toHaveLength(2));
  });

  it("shows the API's clear message for a timeout, with the reference", async () => {
    const message = "This cohort took longer than 10 seconds to count and was cancelled. Try a narrower index event.";
    loaded([], { [RUN]: { status: 504, body: { error: message, trace_id: "feed0003", run_id: "r8", status: "timeout" } } });
    show();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Run feasibility" }));
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByText("Reference: feed0003")).toBeInTheDocument();
  });

  it("shows a timed-out run in the history with its status and reference", async () => {
    loaded([run({ id: "r7", status: "timeout", step_counts: null, final_count: null, trace_id: "feed0004" })]);
    show();
    const history = await screen.findByRole("table", { name: "Run history" });
    expect(within(history).getByText("Timed out")).toBeInTheDocument();
    expect(within(history).getByText("feed0004")).toBeInTheDocument();
  });
});

describe("Given a cohort with several runs, then history is shown newest first", () => {
  const runs = () => [
    run({ id: "old", created_at: "2026-10-25T08:00:00+00:00", final_count: 100, step_counts: steps([200, 100]) }),
    run({ id: "new", created_at: "2026-10-27T09:30:00+00:00", final_count: 300, step_counts: steps([400, 300]) }),
    run({ id: "mid", created_at: "2026-10-26T12:15:00+00:00", final_count: 200, step_counts: steps([300, 200]) }),
  ];

  it("orders the rows by time, newest first, whatever order they arrive in", async () => {
    loaded(runs());
    show();
    const history = await screen.findByRole("table", { name: "Run history" });
    const times = within(history).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].textContent);
    expect(times).toEqual(["2026-10-27 09:30 UTC", "2026-10-26 12:15 UTC", "2026-10-25 08:00 UTC"]);
  });

  it("shows status, final count and duration for each run", async () => {
    loaded(runs());
    show();
    const history = await screen.findByRole("table", { name: "Run history" });
    const first = within(history).getAllByRole("row")[1];
    expect(within(first).getAllByRole("cell").map((c) => c.textContent)).toEqual(
      ["2026-10-27 09:30 UTC", "Success", "300", "120 ms", "View"]);
  });

  it("shows the newest run's steps by default and another run's steps when its row is viewed", async () => {
    loaded(runs());
    show();
    expect(await screen.findByTestId("final-count")).toHaveTextContent("300");
    const history = screen.getByRole("table", { name: "Run history" });
    await userEvent.setup().click(within(within(history).getAllByRole("row")[3]).getByRole("button", { name: "View" }));
    expect(screen.getByTestId("final-count")).toHaveTextContent("100");
    expect(within(screen.getByRole("list", { name: "Attrition waterfall" })).getAllByRole("listitem")[0]).toHaveTextContent("200");
  });

  it("says so when the cohort has never been run", async () => {
    loaded([]);
    show();
    expect(await screen.findByText("This cohort has not been run yet.")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Run history" })).not.toBeInTheDocument();
  });

  it("shows a suppressed final count in the history as <11", async () => {
    loaded([run({ final_count: "<11", step_counts: steps([30, "<11"]) })]);
    show();
    const history = await screen.findByRole("table", { name: "Run history" });
    expect(within(history).getAllByRole("cell")[2]).toHaveTextContent("<11");
  });
});

describe("Run feasibility", () => {
  it("runs the cohort, shows the new result and the baseline, and refreshes the history", async () => {
    let listed = 0;
    const api = loaded([], {
      [RUNS]: () => (listed++ === 0 ? [] : [run({ id: "fresh" })]),
      [RUN]: { status: 201, body: { ...run({ id: "fresh" }), baseline: BASELINE } },
    });
    show();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Run feasibility" }));
    expect(await screen.findByTestId("final-count")).toHaveTextContent("481");
    expect(api.calls.filter((c) => c.method === "POST")).toHaveLength(1);
    expect(await screen.findByRole("table", { name: "Run history" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Baseline by age and sex" })).toBeInTheDocument();
  });

  it("disables the button while running, so a double click sends one request", async () => {
    let release: (value: unknown) => void = () => {};
    const gate = new Promise((resolve) => { release = resolve; });
    mockApi({ [COHORT]: REFERENCE_COHORT, [RUNS]: [] });
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        await gate;
        return new Response(JSON.stringify({ ...run(), baseline: BASELINE }), { status: 201, headers: { "Content-Type": "application/json" } });
      }
      return original(input, init);
    }));
    show();
    const user = userEvent.setup();
    const button = await screen.findByRole("button", { name: "Run feasibility" });
    await user.dblClick(button);
    expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();
    release(null);
    await screen.findByTestId("final-count");
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });
});

describe("Baseline table", () => {
  const withBaseline = async () => {
    loaded([run()], { [RUN]: { status: 201, body: { ...run({ id: "fresh" }), baseline: BASELINE } } });
    show();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Run feasibility" }));
    return screen.findByRole("table", { name: "Baseline by age and sex" });
  };

  it("shows age bands as rows and sexes as columns, with <11 and 0 as given", async () => {
    const table = await withBaseline();
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Age", "Female", "Male", "Other"]);
    const cells = (band: RegExp) =>
      within(within(table).getByRole("row", { name: band })).getAllByRole("cell").map((c) => c.textContent);
    expect(cells(/50-59/)).toEqual(["120", "<11", "0"]);
    expect(cells(/60-69/)).toEqual(["0", "90", "0"]);
  });

  it("lists the comorbidities with their counts and the always-present note", async () => {
    await withBaseline();
    const list = screen.getByRole("table", { name: "Most common conditions" });
    expect(within(list).getByRole("row", { name: /Essential hypertension/ })).toHaveTextContent("481");
    expect(within(list).getByRole("row", { name: /Concept 1/ })).toHaveTextContent("30"); // a concept without a name
    expect(screen.getByText("Conditions with fewer than 11 patients are not shown.")).toBeInTheDocument();
  });

  it("still shows the note when no condition is common enough to list", async () => {
    loaded([run()], { [RUN]: { status: 201, body: { ...run({ id: "fresh" }), baseline: { ...BASELINE, comorbidities: [] } } } });
    show();
    await userEvent.setup().click(await screen.findByRole("button", { name: "Run feasibility" }));
    expect(await screen.findByText("Conditions with fewer than 11 patients are not shown.")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Most common conditions" })).not.toBeInTheDocument();
  });

  it("is not stored with a run, so reopening the screen says to run the cohort to see it", async () => {
    loaded([run()]);
    show();
    await screen.findByTestId("final-count");
    expect(screen.queryByRole("table", { name: "Baseline by age and sex" })).not.toBeInTheDocument();
    expect(screen.getByText("Run the cohort to see the baseline table.")).toBeInTheDocument();
  });
});

describe("Loading and errors", () => {
  it("shows a loading message first", async () => {
    loaded([run()]);
    show();
    expect(screen.getByText("Loading results…")).toBeInTheDocument();
    await screen.findByTestId("final-count");
  });

  it("shows the reference when the cohort or its history cannot be loaded", async () => {
    mockApi({ [COHORT]: { status: 404, body: { error: "Cohort not found", trace_id: "abc999" } }, [RUNS]: [] });
    show();
    expect(await screen.findByText("Something went wrong. Reference: abc999")).toBeInTheDocument();
  });

  it("goes back to the list", async () => {
    loaded([]);
    const onBack = vi.fn();
    render(<Results cohortId={ID} onBack={onBack} />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Back to cohorts" }));
    expect(onBack).toHaveBeenCalled();
  });
});
