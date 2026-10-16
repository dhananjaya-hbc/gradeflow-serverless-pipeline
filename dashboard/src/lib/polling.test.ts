import { describe, expect, it } from "vitest";
import { FAST_MS, FAST_PERIOD_MS, GIVE_UP_MS, SLOW_MS, pollDelay } from "./polling";

describe("pollDelay", () => {
  it("is fast at first", () => {
    expect(pollDelay(0)).toBe(FAST_MS);
    expect(pollDelay(FAST_PERIOD_MS - 1)).toBe(FAST_MS);
  });
  it("slows down after the fast period", () => {
    expect(pollDelay(FAST_PERIOD_MS)).toBe(SLOW_MS);
    expect(pollDelay(GIVE_UP_MS - 1)).toBe(SLOW_MS);
  });
  it("stops after the give-up time", () => {
    expect(pollDelay(GIVE_UP_MS)).toBeNull();
    expect(pollDelay(GIVE_UP_MS * 10)).toBeNull();
  });
});
