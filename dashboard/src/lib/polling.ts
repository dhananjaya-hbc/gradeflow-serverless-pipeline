/**
 * When to refresh a list that is waiting for datasets to finish.
 *
 * The API allows 5,000 calls a day, so the dashboard must not poll forever if a dataset is
 * stuck: every 5 s at first (a dataset normally finishes within a minute), then every 15 s,
 * then it stops and leaves a Refresh button.
 */
export const FAST_MS = 5_000;
export const SLOW_MS = 15_000;
export const FAST_PERIOD_MS = 120_000; // 2 minutes
export const GIVE_UP_MS = 600_000; // 10 minutes

/** Milliseconds to wait before the next refresh, or null to stop. `elapsedMs` counts from the
 * last time the user loaded or refreshed the list. */
export function pollDelay(elapsedMs: number): number | null {
  if (elapsedMs < FAST_PERIOD_MS) return FAST_MS;
  if (elapsedMs < GIVE_UP_MS) return SLOW_MS;
  return null;
}
