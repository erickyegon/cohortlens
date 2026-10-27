// Issue #14: the builder's form model: definition <-> form conversion and client-side validation.
import { describe, expect, it } from "vitest";
import { emptyForm, moveRule, newRule, toDefinition, toForm, validate } from "./form";
import type { CohortDefinition } from "./types";
import { FULL_A, FULL_B, REFERENCE_DEFINITION } from "./test-utils";

const reference = () => REFERENCE_DEFINITION as unknown as CohortDefinition;
const valid = () => ({ ...toForm(reference()) });

describe("definition <-> form", () => {
  it("round-trips the reference cohort exactly", () => {
    expect(toDefinition(toForm(reference()))).toEqual(REFERENCE_DEFINITION);
  });

  it.each([["A (every exposure, women, bounded dates)", FULL_A], ["B (first occurrence, repeat use)", FULL_B]])(
    "round-trips a definition with non-default values in every field: %s",
    (_name, definition) => {
      const asDefinition = definition as unknown as CohortDefinition;
      expect(toDefinition(toForm(asDefinition))).toEqual(definition);
      expect(validate(toForm(asDefinition))).toEqual({});
    },
  );

  it("turns blank ages into null and a one-sided date range into a range with a null end", () => {
    const form = { ...valid(), age_min: "", age_max: "", date_start: "2020-01-01", date_end: "" };
    const definition = toDefinition(form);
    expect(definition.demographics.age_min).toBeNull();
    expect(definition.demographics.age_max).toBeNull();
    expect(definition.index_event.date_range).toEqual({ start: "2020-01-01", end: null });
    expect(toDefinition({ ...form, date_start: "" }).index_event.date_range).toBeNull();
  });

  it("lists the chosen sexes in F, M order", () => {
    expect(toDefinition({ ...valid(), sexF: false }).demographics.sex).toEqual(["M"]);
    expect(toDefinition({ ...valid(), sexM: false }).demographics.sex).toEqual(["F"]);
  });

  it("trims the name and the labels", () => {
    const form = valid();
    form.name = "  padded  ";
    form.rules[0] = { ...form.rules[0], label: "  spaced  " };
    const definition = toDefinition(form);
    expect(definition.name).toBe("padded");
    expect(definition.rules[0].label).toBe("spaced");
  });
});

describe("validate", () => {
  it("accepts the reference cohort", () => {
    expect(validate(valid())).toEqual({});
  });

  it("requires a name and an index concept", () => {
    const errors = validate(emptyForm());
    expect(errors["name"]).toBeDefined();
    expect(errors["index_event.concept_ids"]).toBeDefined();
  });

  it("rejects age_min above age_max on the maximum age, but allows equal ages", () => {
    expect(validate({ ...valid(), age_min: "70", age_max: "40" })["demographics.age_max"]).toMatch(/must not be below/);
    expect(validate({ ...valid(), age_min: "40", age_max: "40" })["demographics.age_max"]).toBeUndefined();
  });

  it.each(["abc", "1.5", "-1", "131", " "])("rejects the age %j or treats blank as no limit", (age) => {
    const errors = validate({ ...valid(), age_min: age });
    if (age.trim() === "") expect(errors["demographics.age_min"]).toBeUndefined();
    else expect(errors["demographics.age_min"]).toBeDefined();
  });

  it("requires at least one sex", () => {
    expect(validate({ ...valid(), sexF: false, sexM: false })["demographics.sex"]).toBeDefined();
  });

  it("rejects an end date before the start date", () => {
    const errors = validate({ ...valid(), date_start: "2021-01-01", date_end: "2020-01-01" });
    expect(errors["index_event.date_range.end"]).toBeDefined();
  });

  it("checks each rule: label, concepts, window order, occurrences", () => {
    const form = valid();
    form.rules = [{ ...newRule(), label: "", start_days: "0", end_days: "-5", min_occurrences: "0" }];
    const errors = validate(form);
    expect(errors["rules.0.label"]).toBeDefined();
    expect(errors["rules.0.concept_ids"]).toBeDefined();
    expect(errors["rules.0.window.end_days"]).toMatch(/must not end before/);
    expect(errors["rules.0.min_occurrences"]).toBeDefined();
  });

  it("rejects duplicate labels ignoring case, on the later rule", () => {
    const form = valid();
    form.rules[1] = { ...form.rules[1], label: form.rules[0].label.toUpperCase() };
    expect(validate(form)["rules.1.label"]).toBe("Labels must be different");
  });

  it("allows a window that starts and ends on the same day", () => {
    const form = valid();
    form.rules[0] = { ...form.rules[0], start_days: "0", end_days: "0" };
    expect(validate(form)["rules.0.window.end_days"]).toBeUndefined();
  });

  it("caps rules at 10, and at 3 with occurrence 1 when every exposure is an index event", () => {
    const eleven = { ...valid(), rules: Array.from({ length: 11 }, (_, i) => ({ ...newRule(), label: `r${i}` })) };
    expect(validate(eleven)["rules"]).toMatch(/at most 10/);
    const four = { ...valid(), first_occurrence_only: false, rules: valid().rules.concat({ ...newRule(), label: "x" }) };
    expect(validate(four)["rules"]).toMatch(/at most 3 rules/);
    const twice = valid();
    twice.first_occurrence_only = false;
    twice.rules[0] = { ...twice.rules[0], min_occurrences: "2" };
    expect(validate(twice)["rules.0.min_occurrences"]).toBeDefined();
    const uncapped = valid();
    uncapped.rules[0] = { ...uncapped.rules[0], min_occurrences: "2" };
    expect(validate(uncapped)["rules.0.min_occurrences"]).toBeUndefined();
  });
});

describe("moveRule", () => {
  const rules = () => ["a", "b", "c"].map((label) => ({ ...newRule(), label }));
  const labels = (list: { label: string }[]) => list.map((r) => r.label);

  it("moves a rule up or down without changing the others' order", () => {
    expect(labels(moveRule(rules(), 2, -1))).toEqual(["a", "c", "b"]);
    expect(labels(moveRule(rules(), 0, 1))).toEqual(["b", "a", "c"]);
  });

  it("does nothing at the ends and does not mutate its input", () => {
    const original = rules();
    expect(moveRule(original, 0, -1)).toBe(original);
    expect(moveRule(original, 2, 1)).toBe(original);
    moveRule(original, 1, 1);
    expect(labels(original)).toEqual(["a", "b", "c"]);
  });
});
