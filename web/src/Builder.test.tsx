// Issue #14: the cohort builder (docs/spec.md US-2, US-3, US-6, FR-15). Fetch is stubbed; nothing here needs a server.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Builder } from "./Builder";
import { FULL_A, FULL_B, REFERENCE_COHORT, mockApi } from "./test-utils";

const ID = REFERENCE_COHORT.id;
const open = () => render(<Builder cohortId={ID} onBack={() => {}} />);

afterEach(() => vi.unstubAllGlobals());

async function reopened() {
  const api = mockApi({ [`GET /cohorts/${ID}`]: REFERENCE_COHORT });
  open();
  await screen.findByDisplayValue(REFERENCE_COHORT.name);
  return api;
}

const ruleLabels = () =>
  screen.getAllByLabelText(/^Rule \d+ label$/).map((input) => (input as HTMLInputElement).value);

describe("Given the builder, when I enter age_min above age_max", () => {
  it("shows a field-level error next to the maximum age", async () => {
    mockApi({});
    render(<Builder cohortId={null} onBack={() => {}} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Minimum age"), "70");
    await user.type(screen.getByLabelText("Maximum age"), "40");
    expect(screen.getByText("The maximum age must not be below the minimum age")).toBeInTheDocument();
  });

  it("cannot save: no request is sent, and every other field problem is shown", async () => {
    const api = mockApi({});
    render(<Builder cohortId={null} onBack={() => {}} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Minimum age"), "70");
    await user.type(screen.getByLabelText("Maximum age"), "40");
    await user.click(screen.getByRole("button", { name: "Save cohort" }));
    expect(api.writes()).toHaveLength(0);
    expect(screen.getByText("Give the cohort a name")).toBeInTheDocument();
    expect(screen.getAllByText("Choose at least one concept").length).toBeGreaterThan(0);
  });

  it("can save again once the ages are fixed", async () => {
    const api = mockApi({ [`GET /cohorts/${ID}`]: REFERENCE_COHORT, [`PUT /cohorts/${ID}`]: REFERENCE_COHORT });
    open();
    await screen.findByDisplayValue(REFERENCE_COHORT.name);
    const user = userEvent.setup();
    const maximum = screen.getByLabelText("Maximum age");
    await user.type(maximum, "10"); // below the minimum of 50
    await user.click(screen.getByRole("button", { name: "Save cohort" }));
    expect(api.writes()).toHaveLength(0);
    await user.clear(maximum);
    await user.type(maximum, "80");
    await user.click(screen.getByRole("button", { name: "Save cohort" }));
    await waitFor(() => expect(api.writes()).toHaveLength(1));
  });
});

describe("Given a rule list, when I reorder rules or toggle include/exclude, then the definition updates", () => {
  it("moves a rule up and down", async () => {
    await reopened();
    const user = userEvent.setup();
    expect(ruleLabels()).toEqual([
      "No ACE inhibitor in the prior 365 days", "Essential hypertension on or before index", "No prior heart failure"]);
    await user.click(screen.getByRole("button", { name: "Move rule 3 up" }));
    expect(ruleLabels()[1]).toBe("No prior heart failure");
    await user.click(screen.getByRole("button", { name: "Move rule 1 down" }));
    expect(ruleLabels()).toEqual([
      "No prior heart failure", "No ACE inhibitor in the prior 365 days", "Essential hypertension on or before index"]);
  });

  it("cannot move the first rule up or the last rule down", async () => {
    await reopened();
    expect(screen.getByRole("button", { name: "Move rule 1 up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move rule 3 down" })).toBeDisabled();
  });

  it("keeps each rule's other inputs with it when it moves", async () => {
    await reopened();
    await userEvent.setup().click(screen.getByRole("button", { name: "Move rule 2 up" }));
    // the hypertension rule (window -36500..0) is now rule 1
    expect(screen.getByLabelText("Rule 1 label")).toHaveValue("Essential hypertension on or before index");
    expect(screen.getByLabelText("Rule 1 window end (days from the index date)")).toHaveValue("0");
  });

  it("saves the new order and the toggled type", async () => {
    const api = mockApi({ [`GET /cohorts/${ID}`]: REFERENCE_COHORT, [`PUT /cohorts/${ID}`]: REFERENCE_COHORT });
    open();
    await screen.findByDisplayValue(REFERENCE_COHORT.name);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Move rule 3 up" }));
    await user.selectOptions(screen.getByLabelText("Rule 1 type"), "include"); // was exclude
    await user.click(screen.getByRole("button", { name: "Save cohort" }));
    await waitFor(() => expect(api.writes()).toHaveLength(1));
    const rules = (api.writes()[0].body as { rules: { label: string; type: string }[] }).rules;
    expect(rules.map((r) => [r.label, r.type])).toEqual([
      ["No ACE inhibitor in the prior 365 days", "include"],
      ["No prior heart failure", "exclude"],
      ["Essential hypertension on or before index", "include"],
    ]);
  });

  it("adds and removes rules, with at most ten", async () => {
    mockApi({});
    render(<Builder cohortId={null} onBack={() => {}} />);
    const user = userEvent.setup();
    for (let i = 0; i < 10; i++) await user.click(screen.getByRole("button", { name: "Add rule" }));
    expect(screen.getAllByLabelText(/^Rule \d+ label$/)).toHaveLength(10);
    expect(screen.getByRole("button", { name: "Add rule" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Remove rule 4" }));
    expect(screen.getAllByLabelText(/^Rule \d+ label$/)).toHaveLength(9);
    expect(screen.getByRole("button", { name: "Add rule" })).toBeEnabled();
  });
});

describe("Given a saved cohort, when I reopen it, then all fields are restored", () => {
  it("restores the name, index event, demographics, observation and every rule", async () => {
    const api = await reopened();
    expect(api.calls[0]).toMatchObject({ method: "GET", path: `/cohorts/${ID}` });
    expect(screen.getByLabelText("Name")).toHaveValue(REFERENCE_COHORT.name);
    expect(screen.getByLabelText("Index event domain")).toHaveValue("drug");
    expect(screen.getByRole("list", { name: "Index concepts chosen" })).toHaveTextContent("Concept 1308216");
    expect(screen.getByLabelText("Include descendant concepts")).toBeChecked();
    expect(screen.getByLabelText("First occurrence only")).toBeChecked();
    expect(screen.getByLabelText("Minimum age")).toHaveValue("50");
    expect(screen.getByLabelText("Maximum age")).toHaveValue("");
    expect(screen.getByLabelText("Female")).toBeChecked();
    expect(screen.getByLabelText("Male")).toBeChecked();
    expect(screen.getByLabelText("Days of observation before the index date")).toHaveValue("365");
    expect(screen.getByLabelText("Days of observation after the index date")).toHaveValue("0");
    expect(ruleLabels()).toHaveLength(3);
    expect(screen.getByLabelText("Rule 1 type")).toHaveValue("exclude");
    expect(screen.getByLabelText("Rule 2 type")).toHaveValue("include");
    expect(screen.getByLabelText("Rule 2 domain")).toHaveValue("condition");
    expect(screen.getByLabelText("Rule 1 window start (days from the index date)")).toHaveValue("-365");
    expect(screen.getByLabelText("Rule 1 window end (days from the index date)")).toHaveValue("-1");
    expect(screen.getByLabelText("Rule 2 window start (days from the index date)")).toHaveValue("-36500");
    expect(screen.getByLabelText("Rule 3 minimum occurrences")).toHaveValue("1");
    expect(screen.getByRole("list", { name: "Rule 3 concepts chosen" })).toHaveTextContent("Concept 316139");
  });

  it("saves the restored cohort back unchanged as an update, not a new cohort", async () => {
    const api = mockApi({ [`GET /cohorts/${ID}`]: REFERENCE_COHORT, [`PUT /cohorts/${ID}`]: { ...REFERENCE_COHORT, version: 4 } });
    open();
    await screen.findByDisplayValue(REFERENCE_COHORT.name);
    await userEvent.setup().click(screen.getByRole("button", { name: "Save cohort" }));
    await waitFor(() => expect(api.writes()).toHaveLength(1));
    expect(api.writes()[0]).toMatchObject({ method: "PUT", path: `/cohorts/${ID}` });
    expect(api.writes()[0].body).toEqual(REFERENCE_COHORT.definition);
    expect(await screen.findByText("Saved as version 4.")).toBeInTheDocument();
  });

  it("says so when the cohort cannot be loaded, with the reference", async () => {
    mockApi({ [`GET /cohorts/${ID}`]: { status: 404, body: { error: "Cohort not found", trace_id: "abc123" } } });
    open();
    expect(await screen.findByText("Something went wrong. Reference: abc123")).toBeInTheDocument();
  });
});

describe("Choosing concepts", () => {
  it("searches as the user types (two letters or more) and adds the chosen concept", async () => {
    const api = mockApi({
      "GET /concepts?q=lisinopril&domain=drug": [
        { concept_id: 1308216, name: "lisinopril", domain: "drug" },
        { concept_id: 19080128, name: "lisinopril 10 MG Oral Tablet", domain: "drug" },
      ],
    });
    render(<Builder cohortId={null} onBack={() => {}} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Index concepts"), "l");
    expect(api.calls).toHaveLength(0); // one character: no search
    await user.type(screen.getByLabelText("Index concepts"), "isinopril");
    const results = await screen.findByRole("list", { name: "Index concepts search results" });
    await user.click(within(results).getByRole("button", { name: "lisinopril (1308216)" }));
    expect(screen.getByRole("list", { name: "Index concepts chosen" })).toHaveTextContent("lisinopril (1308216)");
    expect(screen.queryByRole("list", { name: "Index concepts search results" })).not.toBeInTheDocument();
  });

  it("removes a chosen concept", async () => {
    await reopened();
    await userEvent.setup().click(screen.getByRole("button", { name: "Remove Concept 1308216" }));
    expect(screen.queryByRole("list", { name: "Index concepts chosen" })).not.toBeInTheDocument();
  });

  it("says when nothing matches, and when search is unavailable", async () => {
    mockApi({ "GET /concepts?q=zzzz&domain=drug": [], "GET /concepts?q=boom&domain=drug": { status: 503, body: {} } });
    render(<Builder cohortId={null} onBack={() => {}} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Index concepts"), "zzzz");
    expect(await screen.findByText("No matching concepts.")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Index concepts"));
    await user.type(screen.getByLabelText("Index concepts"), "boom");
    expect(await screen.findByText("Concept search is unavailable right now.")).toBeInTheDocument();
  });
});

describe("Saving", () => {
  it("creates a new cohort with the exact definition and keeps the builder on it", async () => {
    const api = mockApi({
      "GET /concepts?q=lisinopril&domain=drug": [{ concept_id: 1308216, name: "lisinopril", domain: "drug" }],
      "POST /cohorts": { ...REFERENCE_COHORT, version: 1 },
    });
    const saved = vi.fn();
    render(<Builder cohortId={null} onBack={() => {}} onSaved={saved} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Name"), "  My cohort  ");
    await user.type(screen.getByLabelText("Index concepts"), "lisinopril");
    await user.click(await screen.findByRole("button", { name: "lisinopril (1308216)" }));
    await user.type(screen.getByLabelText("Minimum age"), "40");
    await user.click(screen.getByLabelText("Male")); // untick
    await user.click(screen.getByRole("button", { name: "Save cohort" }));
    await waitFor(() => expect(api.writes()).toHaveLength(1));
    expect(api.writes()[0]).toMatchObject({ method: "POST", path: "/cohorts" });
    expect(api.writes()[0].body).toEqual({
      name: "My cohort",
      index_event: { domain: "drug", concept_ids: [1308216], include_descendants: true, first_occurrence_only: true, date_range: null },
      demographics: { age_min: 40, age_max: null, sex: ["F"] },
      observation: { prior_days: 0, post_days: 0 },
      rules: [],
    });
    expect(await screen.findByText("Saved as version 1.")).toBeInTheDocument();
    expect(saved).toHaveBeenCalledWith(REFERENCE_COHORT.id);
    // a second save is an update of the same cohort
    mockApi({ [`PUT /cohorts/${REFERENCE_COHORT.id}`]: { ...REFERENCE_COHORT, version: 2 } });
    await user.click(screen.getByRole("button", { name: "Save cohort" }));
    expect(await screen.findByText("Saved as version 2.")).toBeInTheDocument();
  });

  it("shows the server's field-level errors on the matching fields", async () => {
    mockApi({
      [`GET /cohorts/${ID}`]: REFERENCE_COHORT,
      [`PUT /cohorts/${ID}`]: {
        status: 422,
        body: {
          error: "Invalid cohort definition", trace_id: "t1",
          errors: [
            { field: "demographics.age_min", message: "Server: age out of range" },
            { field: "rules.1.window.end_days", message: "Server: window problem" },
          ],
        },
      },
    });
    open();
    await screen.findByDisplayValue(REFERENCE_COHORT.name);
    await userEvent.setup().click(screen.getByRole("button", { name: "Save cohort" }));
    expect(await screen.findByText("Server: age out of range")).toBeInTheDocument();
    expect(screen.getByText("Server: window problem")).toBeInTheDocument();
    expect(screen.getByText("The server did not accept the cohort. Fix the marked fields.")).toBeInTheDocument();
  });

  it("shows the reference when saving fails for any other reason", async () => {
    mockApi({
      [`GET /cohorts/${ID}`]: REFERENCE_COHORT,
      [`PUT /cohorts/${ID}`]: { status: 503, body: { error: "The database is unavailable.", trace_id: "deadbeef" } },
    });
    open();
    await screen.findByDisplayValue(REFERENCE_COHORT.name);
    await userEvent.setup().click(screen.getByRole("button", { name: "Save cohort" }));
    expect(await screen.findByText("Something went wrong. Reference: deadbeef")).toBeInTheDocument();
  });

  it("applies the all-events cap in the form (at most 3 rules, occurrences 1)", async () => {
    await reopened();
    const user = userEvent.setup();
    await user.click(screen.getByLabelText("First occurrence only")); // every exposure becomes an index event
    await user.click(screen.getByRole("button", { name: "Add rule" }));
    expect(screen.getByText("With every exposure as an index event a cohort can have at most 3 rules")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Rule 1 minimum occurrences"));
    await user.type(screen.getByLabelText("Rule 1 minimum occurrences"), "2");
    expect(screen.getByText("With every exposure as an index event this must be 1")).toBeInTheDocument();
  });
});

describe("Reopening restores non-default values in every field", () => {
  const saved = (definition: typeof FULL_A | typeof FULL_B) => ({ ...REFERENCE_COHORT, name: definition.name, definition });

  it.each([["A", FULL_A], ["B", FULL_B]])("definition %s", async (_name, definition) => {
    mockApi({ [`GET /cohorts/${ID}`]: saved(definition) });
    open();
    await screen.findByDisplayValue(definition.name);
    const index = definition.index_event;
    expect(screen.getByLabelText("Index event domain")).toHaveValue(index.domain);
    const chosen = screen.getByRole("list", { name: "Index concepts chosen" });
    for (const id of index.concept_ids) expect(chosen).toHaveTextContent(`Concept ${id}`);
    expect(within(chosen).getAllByRole("listitem")).toHaveLength(index.concept_ids.length);
    expect(screen.getByLabelText("Include descendant concepts")).toHaveProperty("checked", index.include_descendants);
    expect(screen.getByLabelText("First occurrence only")).toHaveProperty("checked", index.first_occurrence_only);
    expect(screen.getByLabelText("Index date from")).toHaveValue(index.date_range?.start ?? "");
    expect(screen.getByLabelText("Index date to")).toHaveValue(index.date_range?.end ?? "");
    const demographics = definition.demographics;
    expect(screen.getByLabelText("Minimum age")).toHaveValue(demographics.age_min === null ? "" : String(demographics.age_min));
    expect(screen.getByLabelText("Maximum age")).toHaveValue(demographics.age_max === null ? "" : String(demographics.age_max));
    expect(screen.getByLabelText("Female")).toHaveProperty("checked", demographics.sex.includes("F"));
    expect(screen.getByLabelText("Male")).toHaveProperty("checked", demographics.sex.includes("M"));
    expect(screen.getByLabelText("Days of observation before the index date")).toHaveValue(String(definition.observation.prior_days));
    expect(screen.getByLabelText("Days of observation after the index date")).toHaveValue(String(definition.observation.post_days));
    definition.rules.forEach((rule, i) => {
      const n = i + 1;
      expect(screen.getByLabelText(`Rule ${n} label`)).toHaveValue(rule.label);
      expect(screen.getByLabelText(`Rule ${n} type`)).toHaveValue(rule.type);
      expect(screen.getByLabelText(`Rule ${n} domain`)).toHaveValue(rule.domain);
      expect(screen.getByLabelText(`Rule ${n} include descendants`)).toHaveProperty("checked", rule.include_descendants);
      expect(screen.getByLabelText(`Rule ${n} window start (days from the index date)`)).toHaveValue(String(rule.window.start_days));
      expect(screen.getByLabelText(`Rule ${n} window end (days from the index date)`)).toHaveValue(String(rule.window.end_days));
      expect(screen.getByLabelText(`Rule ${n} minimum occurrences`)).toHaveValue(String(rule.min_occurrences));
      const ruleChosen = screen.getByRole("list", { name: `Rule ${n} concepts chosen` });
      expect(within(ruleChosen).getAllByRole("listitem")).toHaveLength(rule.concept_ids.length);
    });
  });
});

describe("Pressing Enter in a concept search box", () => {
  it("does not submit the form", async () => {
    const api = mockApi({ "GET /concepts?q=lisinopril&domain=drug": [] });
    render(<Builder cohortId={null} onBack={() => {}} />);
    await userEvent.setup().type(screen.getByLabelText("Index concepts"), "lisinopril{Enter}");
    expect(api.writes()).toHaveLength(0);
    expect(screen.queryByText("Give the cohort a name")).not.toBeInTheDocument(); // not even treated as a save attempt
  });
});

describe("Errors are tied to their fields", () => {
  it("marks the input invalid and describes it with the message", async () => {
    mockApi({});
    render(<Builder cohortId={null} onBack={() => {}} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Minimum age"), "70");
    await user.type(screen.getByLabelText("Maximum age"), "40");
    const maximum = screen.getByLabelText("Maximum age");
    expect(maximum).toBeInvalid();
    expect(maximum).toHaveAccessibleDescription("The maximum age must not be below the minimum age");
    expect(screen.getByLabelText("Minimum age")).toBeValid();
  });

  it("does the same for a rule field, the concept picker and the name", async () => {
    mockApi({});
    render(<Builder cohortId={null} onBack={() => {}} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add rule" }));
    await user.type(screen.getByLabelText("Rule 1 window start (days from the index date)"), "5");
    await user.type(screen.getByLabelText("Rule 1 window end (days from the index date)"), "2");
    const end = screen.getByLabelText("Rule 1 window end (days from the index date)");
    expect(end).toBeInvalid();
    expect(end).toHaveAccessibleDescription("The window must not end before it starts");
    await user.click(screen.getByRole("button", { name: "Save cohort" }));
    expect(screen.getByLabelText("Index concepts")).toBeInvalid();
    expect(screen.getByLabelText("Index concepts")).toHaveAccessibleDescription("Choose at least one concept");
    expect(screen.getByLabelText("Name")).toHaveAccessibleDescription("Give the cohort a name");
  });

  it("does the same for an error that came from the server", async () => {
    mockApi({
      [`GET /cohorts/${ID}`]: REFERENCE_COHORT,
      [`PUT /cohorts/${ID}`]: {
        status: 422,
        body: { error: "Invalid", trace_id: "t", errors: [{ field: "demographics.age_min", message: "Server: age out of range" }] },
      },
    });
    open();
    await screen.findByDisplayValue(REFERENCE_COHORT.name);
    await userEvent.setup().click(screen.getByRole("button", { name: "Save cohort" }));
    await screen.findByText("Server: age out of range");
    expect(screen.getByLabelText("Minimum age")).toBeInvalid();
    expect(screen.getByLabelText("Minimum age")).toHaveAccessibleDescription("Server: age out of range");
  });
});
