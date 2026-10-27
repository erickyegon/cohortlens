import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

// Proves the test tooling itself works: jsdom, React rendering and the jest-dom matchers.
describe("test tooling", () => {
  it("renders a React element into jsdom and matches it with jest-dom", () => {
    render(<p>CohortLens</p>);
    expect(screen.getByText("CohortLens")).toBeInTheDocument();
  });

  it("cleans up between tests", () => {
    expect(screen.queryByText("CohortLens")).not.toBeInTheDocument();
  });
});
