import type { Step } from "./types";

// The API returns names alphabetically. Show prerequisites before dependents,
// keeping independent branches together instead of implying a linear chain.
export function orderSteps(steps: Step[]): Step[] {
  const remaining = new Map(steps.map((step) => [step.id, step]));
  const ordered: Step[] = [];
  while (remaining.size) {
    const ready = [...remaining.values()].filter((step) =>
      step.depends_on.every((parent) => !remaining.has(parent)),
    );
    if (!ready.length) return [...ordered, ...remaining.values()];
    for (const step of ready) {
      ordered.push(step);
      remaining.delete(step.id);
    }
  }
  return ordered;
}
