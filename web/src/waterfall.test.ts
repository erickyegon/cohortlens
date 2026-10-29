// Issue #71: the drop between two waterfall steps. A suppressed ("<11") count must never leak through a computed number.
import { describe, expect, it } from "vitest";
import { stepDrop } from "./waterfall";

describe("stepDrop", () => {
  it("returns the absolute drop and the percent of the previous step", () => {
    expect(stepDrop(920, 552)).toEqual({ persons: 368, percent: 40 });
    expect(stepDrop(1930, 1512)).toEqual({ persons: 418, percent: 21.7 });
  });

  it("rounds the percent to one decimal", () => {
    expect(stepDrop(3, 2)).toEqual({ persons: 1, percent: 33.3 });
    expect(stepDrop(1512, 988)).toEqual({ persons: 524, percent: 34.7 });
  });

  it("reports no drop when nobody was lost", () => {
    expect(stepDrop(500, 500)).toEqual({ persons: 0, percent: 0 });
  });

  it("reports a full drop to zero", () => {
    expect(stepDrop(40, 0)).toEqual({ persons: 40, percent: 100 });
  });

  it("has no drop for the first step (no previous step)", () => {
    expect(stepDrop(null, 920)).toBeNull();
    expect(stepDrop(null, "<11")).toBeNull();
  });

  it("does not divide by zero when the previous step was empty", () => {
    expect(stepDrop(0, 0)).toBeNull();
  });

  it("computes nothing when the step is suppressed, so the hidden count cannot be recovered", () => {
    expect(stepDrop(552, "<11")).toBeNull();
    expect(stepDrop(11, "<11")).toBeNull();
  });

  it("computes nothing when the previous step is suppressed", () => {
    expect(stepDrop("<11", 0)).toBeNull();
    expect(stepDrop("<11", 5)).toBeNull();
  });

  it("computes nothing when both steps are suppressed", () => {
    expect(stepDrop("<11", "<11")).toBeNull();
  });

  it("computes nothing for a step larger than the one before it (not a drop)", () => {
    expect(stepDrop(10, 20)).toBeNull();
  });
});
