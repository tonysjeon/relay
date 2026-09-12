import type { Usage } from "./types";

export function cost(value: string | null | undefined): string {
  if (value == null) return "Unknown";
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount < 0) return "Unknown";
  if (amount > 0 && amount < 0.00000001) return "<$0.00000001";
  return "$" + amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 8 });
}
export function tokenTotal(usage?: Usage): string {
  if (!usage || !usage.calls) return "—";
  const total = usage.input_tokens + usage.output_tokens;
  if (!total && usage.missing_token_calls === usage.calls) return "Unknown";
  return total.toLocaleString("en-US") + (usage.missing_token_calls ? " + unknown" : "");
}
export function usageCost(usage?: Usage): string {
  if (!usage || !usage.calls) return "—";
  if (usage.unpriced_calls === usage.calls) return "Unknown";
  return cost(usage.estimated_cost_usd) + (usage.unpriced_calls ? " + unknown" : "");
}
