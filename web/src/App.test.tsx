// Issue #14: the app shell moves between the cohort list and the builder.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { REFERENCE_COHORT, mockApi } from "./test-utils";

afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("starts on the cohort list and opens a new builder and back", async () => {
    mockApi({ "GET /cohorts": [] });
    render(<App />);
    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { name: "Cohorts" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New cohort" }));
    expect(screen.getByRole("heading", { name: "New cohort" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Back to cohorts" }));
    expect(await screen.findByRole("heading", { name: "Cohorts" })).toBeInTheDocument();
  });

  it("opens a saved cohort from the list and shows its fields", async () => {
    const summary = { id: REFERENCE_COHORT.id, name: "Saved one", version: 3, created_at: "2026-10-27T10:00:00+00:00",
      updated_at: "2026-10-27T12:00:00+00:00", last_run: null };
    mockApi({ "GET /cohorts": [summary], [`GET /cohorts/${REFERENCE_COHORT.id}`]: REFERENCE_COHORT });
    render(<App />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Open Saved one" }));
    expect(await screen.findByDisplayValue(REFERENCE_COHORT.name)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Edit cohort (version 3)" })).toBeInTheDocument();
  });

  it("opens the results screen from the list and goes back", async () => {
    const summary = { id: REFERENCE_COHORT.id, name: "Saved one", version: 3, created_at: "2026-10-27T10:00:00+00:00",
      updated_at: "2026-10-27T12:00:00+00:00", last_run: null };
    mockApi({
      "GET /cohorts": [summary], [`GET /cohorts/${REFERENCE_COHORT.id}`]: REFERENCE_COHORT,
      [`GET /cohorts/${REFERENCE_COHORT.id}/runs`]: [],
    });
    render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Results for Saved one" }));
    expect(await screen.findByRole("button", { name: "Run feasibility" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Back to cohorts" }));
    expect(await screen.findByRole("heading", { name: "Cohorts" })).toBeInTheDocument();
  });
});
