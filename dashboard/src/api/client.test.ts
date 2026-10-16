import { afterEach, describe, expect, it, vi } from "vitest";
import { API, deferred, jsonResponse } from "../test/helpers";
import { abortError, ApiClient, ApiError, NetworkError, type ClientOptions } from "./client";

type Step = Response | Error;

/** A client whose fetch plays back `steps` in order; sleeping is instant and recorded. */
function setup(steps: Step[], options: ClientOptions = {}) {
  const fetchMock = vi.fn(async (..._args: Parameters<typeof fetch>) => {
    const step = steps.shift();
    if (step === undefined) throw new Error("the test ran out of planned responses");
    if (step instanceof Error) throw step;
    return step;
  });
  const sleep = vi.fn(async (_ms: number) => {});
  const client = new ApiClient(API, { fetch: fetchMock as unknown as typeof fetch, sleep, random: () => 0.5, ...options });
  return { client, fetchMock, sleep };
}

const urlOf = (fetchMock: ReturnType<typeof setup>["fetchMock"], call = 0) => String(fetchMock.mock.calls[call]?.[0]);
const initOf = (fetchMock: ReturnType<typeof setup>["fetchMock"], call = 0) => fetchMock.mock.calls[call]?.[1] as RequestInit;

afterEach(() => vi.useRealTimers());

describe("requests", () => {
  it("sends the key in x-api-key and parses the JSON answer", async () => {
    const { client, fetchMock } = setup([jsonResponse(200, { datasets: [], next_cursor: null })]);

    await expect(client.listDatasets()).resolves.toEqual({ datasets: [], next_cursor: null });

    expect(urlOf(fetchMock)).toBe("https://api.example.com/v1/datasets");
    expect(initOf(fetchMock).method).toBe("GET");
    expect(initOf(fetchMock).headers).toEqual({ "x-api-key": API.apiKey });
    expect(initOf(fetchMock).body).toBeUndefined();
  });

  it("builds the query string and leaves out empty values", async () => {
    const { client, fetchMock } = setup([jsonResponse(200, {}), jsonResponse(200, {}), jsonResponse(200, {})]);

    await client.listDatasets({ limit: 5, cursor: "abc" });
    await client.listDatasets({ limit: undefined, cursor: null });
    await client.getAnomalies("ds-1", { method: "term_jump", limit: 50, cursor: "" });

    expect(urlOf(fetchMock, 0)).toBe("https://api.example.com/v1/datasets?limit=5&cursor=abc");
    expect(urlOf(fetchMock, 1)).toBe("https://api.example.com/v1/datasets");
    expect(urlOf(fetchMock, 2)).toBe("https://api.example.com/v1/datasets/ds-1/anomalies?method=term_jump&limit=50");
  });

  it("URL-encodes cursors, which contain base64 characters", async () => {
    const { client, fetchMock } = setup([jsonResponse(200, {})]);
    await client.listDatasets({ cursor: "eyJhIjoiYisvPSJ9+/=" });
    expect(urlOf(fetchMock)).toContain("cursor=eyJhIjoiYisvPSJ9%2B%2F%3D");
  });

  it("encodes dataset ids in the path", async () => {
    const { client, fetchMock } = setup([jsonResponse(200, {}), jsonResponse(200, {}), jsonResponse(200, {})]);
    await client.getDataset("a b/../c");
    await client.getStats("demo-030");
    await client.getAnomalies("x?y#z");
    expect(urlOf(fetchMock, 0)).toBe("https://api.example.com/v1/datasets/a%20b%2F..%2Fc");
    expect(urlOf(fetchMock, 1)).toBe("https://api.example.com/v1/datasets/demo-030/stats");
    expect(urlOf(fetchMock, 2)).toBe("https://api.example.com/v1/datasets/x%3Fy%23z/anomalies");
  });

  it("asks for an upload form with a JSON body", async () => {
    const form = { dataset_id: "ds-1", key: "uploads/ds-1/a.csv", upload: { method: "POST", url: "https://s3", fields: {} }, max_bytes: 1, expires_in: 900 };
    const { client, fetchMock } = setup([jsonResponse(201, form)]);

    await expect(client.createUpload("a.csv")).resolves.toEqual(form);

    expect(initOf(fetchMock).method).toBe("POST");
    expect(urlOf(fetchMock)).toBe("https://api.example.com/v1/uploads");
    expect(initOf(fetchMock).body).toBe('{"filename":"a.csv"}');
    expect(initOf(fetchMock).headers).toEqual({ "x-api-key": API.apiKey, "Content-Type": "application/json" });
  });
});

describe("retrying", () => {
  it("retries a 500 and succeeds, waiting 300 ms then 600 ms", async () => {
    const { client, fetchMock, sleep } = setup([jsonResponse(500, { message: "Internal server error" }), jsonResponse(500, {}), jsonResponse(200, { ok: true })]);

    await expect(client.getStats("ds-1")).resolves.toEqual({ ok: true });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(sleep.mock.calls.map(([ms]) => ms)).toEqual([300, 600]);
  });

  it("gives up after four attempts with the API's own error", async () => {
    const body = { error: { code: "internal_error", message: "something went wrong on our side" } };
    const { client, fetchMock, sleep } = setup([500, 500, 500, 500].map((s) => jsonResponse(s, body)));

    const error = await client.getStats("ds-1").catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 500, code: "internal_error", message: "something went wrong on our side" });
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(sleep.mock.calls.map(([ms]) => ms)).toEqual([300, 600, 1200]);
  });

  it.each([429, 502, 503, 504])("retries HTTP %i", async (status) => {
    const { client, fetchMock } = setup([jsonResponse(status, {}), jsonResponse(200, { ok: 1 })]);
    await expect(client.getStats("ds-1")).resolves.toEqual({ ok: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("follows Retry-After and caps it", async () => {
    const { client, sleep } = setup([
      jsonResponse(429, {}, { "Retry-After": "2" }),
      jsonResponse(429, {}, { "Retry-After": "60" }),
      jsonResponse(200, {}),
    ]);
    await client.getStats("ds-1");
    expect(sleep.mock.calls.map(([ms]) => ms)).toEqual([2000, 5000]);
  });

  it("adds jitter between 75% and 125% of the backoff", async () => {
    const low = setup([jsonResponse(500, {}), jsonResponse(200, {})], { random: () => 0 });
    await low.client.getStats("x");
    const high = setup([jsonResponse(500, {}), jsonResponse(200, {})], { random: () => 1 });
    await high.client.getStats("x");
    expect(low.sleep.mock.calls[0]?.[0]).toBe(225);
    expect(high.sleep.mock.calls[0]?.[0]).toBe(375);
  });

  it("never waits longer than the maximum delay", async () => {
    const { client, sleep } = setup([500, 500, 500, 200].map((s) => jsonResponse(s, {})), { baseDelayMs: 4000, maxDelayMs: 5000, maxAttempts: 4 });
    await client.getStats("x");
    expect(sleep.mock.calls.map(([ms]) => ms)).toEqual([4000, 5000, 5000]);
  });

  it("retries network failures, then reports a NetworkError", async () => {
    const down = () => new TypeError("Failed to fetch");
    const recovering = setup([down(), down(), jsonResponse(200, { ok: 1 })]);
    await expect(recovering.client.getStats("x")).resolves.toEqual({ ok: 1 });

    const dead = setup([down(), down(), down(), down()]);
    const error = await dead.client.getStats("x").catch((e) => e);
    expect(error).toBeInstanceOf(NetworkError);
    expect(dead.fetchMock).toHaveBeenCalledTimes(4);
  });

  it("works with the real timers too", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(500, {})).mockResolvedValueOnce(jsonResponse(200, { ok: 1 }));
    const client = new ApiClient(API, { fetch: fetchMock as unknown as typeof fetch, random: () => 0.5 });

    const result = client.getStats("x");
    await vi.advanceTimersByTimeAsync(299);
    expect(fetchMock).toHaveBeenCalledTimes(1); // still waiting
    await vi.advanceTimersByTimeAsync(1);
    await expect(result).resolves.toEqual({ ok: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("errors that are not retried", () => {
  it.each([
    [400, { error: { code: "invalid_limit", message: "limit must be a whole number from 1 to 100" } }, "invalid_limit"],
    [404, { error: { code: "not_found", message: "dataset 'nope' does not exist" } }, "not_found"],
    [413, { error: { code: "body_too_large", message: "too big" } }, "body_too_large"],
  ])("HTTP %i is reported at once with the API's code", async (status, body, code) => {
    const { client, fetchMock, sleep } = setup([jsonResponse(status, body)]);
    const error = await client.getDataset("nope").catch((e) => e);
    expect(error).toMatchObject({ status, code });
    expect(error.message).toBe(body.error.message);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sleep).not.toHaveBeenCalled();
  });

  it("explains API Gateway's 403 (no key, wrong key or unknown route)", async () => {
    const { client, fetchMock } = setup([jsonResponse(403, { message: "Forbidden" })]);
    const error = await client.listDatasets().catch((e) => e);
    expect(error).toMatchObject({ status: 403, code: "forbidden" });
    expect(error.message).toMatch(/API key/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("explains a throttled request that never recovered", async () => {
    const { client } = setup([429, 429, 429, 429].map(() => jsonResponse(429, { message: "Too Many Requests" })));
    const error = await client.listDatasets().catch((e) => e);
    expect(error).toMatchObject({ status: 429, code: "too_many_requests" });
    expect(error.message).toMatch(/busy/);
  });

  it("copes with an error body that is not JSON", async () => {
    const { client } = setup([new Response("<html>bad gateway</html>", { status: 404 })]);
    const error = await client.getDataset("x").catch((e) => e);
    expect(error).toMatchObject({ status: 404, code: "http_error", message: "The API answered with HTTP 404." });
  });

  it("never puts the API key in an error message", async () => {
    const failures = [
      setup([jsonResponse(403, { message: "Forbidden" })]),
      setup([jsonResponse(500, {}), jsonResponse(500, {}), jsonResponse(500, {}), jsonResponse(500, {})]),
      setup([new TypeError("x"), new TypeError("x"), new TypeError("x"), new TypeError("x")]),
    ];
    for (const { client } of failures) {
      const error = await client.listDatasets().catch((e) => e);
      expect(String(error.message)).not.toContain(API.apiKey);
    }
  });
});

describe("cancelling", () => {
  it("does not even call the API if already cancelled", async () => {
    const { client, fetchMock } = setup([jsonResponse(200, {})]);
    const controller = new AbortController();
    controller.abort();
    await expect(client.listDatasets({}, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("stops waiting between retries when cancelled", async () => {
    const controller = new AbortController();
    const sleep = (_ms: number, signal?: AbortSignal) =>
      new Promise<void>((_, reject) => signal?.addEventListener("abort", () => reject(abortError())));
    const { client, fetchMock } = setup([jsonResponse(500, {}), jsonResponse(200, {})], { sleep });

    const result = client.getStats("x", controller.signal).catch((e) => e);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    controller.abort();

    expect(await result).toMatchObject({ name: "AbortError" });
    expect(fetchMock).toHaveBeenCalledTimes(1); // no further attempt
  });

  it("treats a fetch that fails because of the cancel as a cancel, not a network error", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn(async () => {
      controller.abort();
      throw new TypeError("aborted");
    });
    const client = new ApiClient(API, { fetch: fetchMock as unknown as typeof fetch, sleep: async () => {} });
    await expect(client.getStats("x", controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("limiting concurrency (Lambda allows few at once on a new account)", () => {
  /** A fetch whose answers are released by the test, so requests overlap. */
  function holdingClient(maxConcurrent: number) {
    const pending: Array<{ release: () => void }> = [];
    let inFlight = 0;
    let peak = 0;
    const fetchMock = vi.fn(async (url: RequestInfo | URL) => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      const gate = deferred<void>();
      pending.push({ release: () => gate.resolve() });
      await gate.promise;
      inFlight -= 1;
      return jsonResponse(200, { url: String(url) });
    });
    const client = new ApiClient(API, { fetch: fetchMock as unknown as typeof fetch, sleep: async () => {}, maxConcurrent });
    return { client, pending, peak: () => peak, started: () => fetchMock.mock.calls.length };
  }

  it("runs at most N requests at once and queues the rest", async () => {
    const { client, pending, peak, started } = holdingClient(2);
    const results = Array.from({ length: 6 }, (_, i) => client.getStats(`ds-${i}`));

    await vi.waitFor(() => expect(started()).toBe(2));
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(started()).toBe(2); // the other four are still waiting for a free slot

    while (started() < 6 || pending.length > 0) {
      pending.splice(0).forEach((p) => p.release());
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    await Promise.all(results);

    expect(peak()).toBeLessThanOrEqual(2);
    expect(started()).toBe(6);
  });

  it("never exceeds the limit, even with 40 requests finishing in a scrambled order", async () => {
    const { client, pending, peak, started } = holdingClient(3);
    const results = Array.from({ length: 40 }, (_, i) => client.getStats(`ds-${i}`).then((r) => (r as unknown as { url: string }).url));

    let seed = 7;
    const nextRandom = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    while (started() < 40 || pending.length > 0) {
      await new Promise((r) => setTimeout(r, 0));
      if (pending.length > 0) pending.splice(Math.floor(nextRandom() * pending.length), 1)[0]?.release();
    }
    const urls = await Promise.all(results);

    expect(peak()).toBeLessThanOrEqual(3);
    expect(urls).toHaveLength(40);
    expect(urls.every((url, i) => url.endsWith(`/datasets/ds-${i}/stats`))).toBe(true); // each got its own answer
  });

  it("serves queued requests in the order they were made", async () => {
    const { client, pending, started } = holdingClient(1);
    const results = ["a", "b", "c"].map((id) => client.getStats(id));
    const order: string[] = [];
    results.forEach((r, i) => void r.then(() => order.push("abc"[i] ?? "?")));

    while (order.length < 3) {
      await new Promise((r) => setTimeout(r, 0));
      pending.splice(0).forEach((p) => p.release());
    }
    expect(order).toEqual(["a", "b", "c"]);
    expect(started()).toBe(3);
  });

  it("frees the slot when a request fails, so the queue keeps moving", async () => {
    const { client, fetchMock } = setup([jsonResponse(404, { error: { code: "not_found", message: "x" } }), jsonResponse(200, { ok: 1 })], { maxConcurrent: 1 });
    const first = client.getDataset("missing").catch((e) => e);
    const second = client.getStats("ds-1");
    expect(await first).toBeInstanceOf(ApiError);
    await expect(second).resolves.toEqual({ ok: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
