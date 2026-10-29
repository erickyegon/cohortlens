import type { Count } from "./types";

export interface Drop {
  persons: number;
  /** Percent of the previous step that was lost, to one decimal. */
  percent: number;
}

/**
 * What a step removed from the one before it. Null whenever a number would be derived from, or would reveal, a
 * suppressed ("<11") count, when there is no previous step, or when the previous step was empty.
 */
export function stepDrop(previous: Count | null, current: Count): Drop | null {
  if (typeof previous !== "number" || typeof current !== "number") return null;
  if (previous === 0 || current > previous) return null;
  const persons = previous - current;
  return { persons, percent: Math.round((persons / previous) * 1000) / 10 };
}
