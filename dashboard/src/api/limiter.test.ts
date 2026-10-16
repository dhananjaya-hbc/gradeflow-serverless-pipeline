import { describe, expect, it } from "vitest";
import { Limiter } from "./client";
import { deferred } from "../test/helpers";

/** Wait `hops` microtask turns: lets a newcomer arrive at one exact moment. */
async function afterMicrotasks(hops: number): Promise<void> {
  for (let i = 0; i < hops; i += 1) await Promise.resolve();
}

describe("Limiter", () => {
  it("never runs more tasks at once than its limit", async () => {
    const limiter = new Limiter(2);
    let running = 0;
    let peak = 0;
    const gates = Array.from({ length: 8 }, () => deferred<void>());
    const results = gates.map((gate) =>
      limiter.run(async () => {
        running += 1;
        peak = Math.max(peak, running);
        await gate.promise;
        running -= 1;
      }),
    );
    for (const gate of gates) {
      await new Promise((resolve) => setTimeout(resolve, 0));
      gate.resolve();
    }
    await Promise.all(results);
    expect(peak).toBeLessThanOrEqual(2);
  });

  it("starts waiting tasks first come, first served", async () => {
    const limiter = new Limiter(1);
    const started: number[] = [];
    const gates = [deferred<void>(), deferred<void>(), deferred<void>()];
    const results = gates.map((gate, i) =>
      limiter.run(async () => {
        started.push(i);
        await gate.promise;
      }),
    );
    for (const gate of gates) {
      await new Promise((resolve) => setTimeout(resolve, 0));
      gate.resolve();
    }
    await Promise.all(results);
    expect(started).toEqual([0, 1, 2]);
  });

  // A newcomer arriving at the instant a slot is passed on must not jump ahead of the waiter.
  // We cannot see that instant, so we try every arrival time from 0 to 12 microtask turns
  // after the running task is released.
  it.each(Array.from({ length: 13 }, (_, hops) => hops))(
    "does not exceed the limit when a newcomer arrives %i microtask turns after a release",
    async (hops) => {
      const limiter = new Limiter(1);
      let running = 0;
      let peak = 0;
      const track = async (gate: Promise<void>) => {
        running += 1;
        peak = Math.max(peak, running);
        await gate;
        running -= 1;
      };
      const first = deferred<void>();
      const second = deferred<void>();
      const third = deferred<void>();

      const a = limiter.run(() => track(first.promise));
      const b = limiter.run(() => track(second.promise)); // waits for a's slot
      await afterMicrotasks(2);

      first.resolve(); // a finishes: its slot is handed to b
      const c = afterMicrotasks(hops).then(() => limiter.run(() => track(third.promise)));
      await afterMicrotasks(20);
      second.resolve();
      await afterMicrotasks(20);
      third.resolve();
      await Promise.all([a, b, c]);

      expect(peak).toBe(1);
    },
  );

  it("frees its slot when a task throws", async () => {
    const limiter = new Limiter(1);
    await expect(limiter.run(async () => Promise.reject(new Error("boom")))).rejects.toThrow("boom");
    await expect(limiter.run(async () => "still works")).resolves.toBe("still works");
  });
});
