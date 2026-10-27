import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library does not unmount between tests when globals are off.
afterEach(() => cleanup());
