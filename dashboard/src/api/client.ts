/**
 * The Analytics API client.
 *
 * Built for a small AWS account: Lambda is limited to 10 concurrent executions in total, so
 * under load the API answers 429 or a generic 500. The client therefore (1) never has more
 * than a few requests in flight and (2) retries 429, 5xx and network failures with an
 * exponential backoff, honouring Retry-After. Client errors (400, 404, ...) are never retried.
 */

import type { AppConfig } from "../config";
import type {
  AnomaliesResponse,
  AnomalyMethod,
  DatasetDetail,
  DatasetsPage,
  StatsResponse,
  UploadForm,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NetworkError";
  }
}

export interface ClientOptions {
  fetch?: typeof fetch;
  /** Waits `ms`; rejects with an AbortError if `signal` aborts. Injectable for tests. */
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
  random?: () => number;
  maxAttempts?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  maxConcurrent?: number;
}

const RETRYABLE_STATUS = new Set([429, 500, 502, 503, 504]);

export function abortError(): DOMException {
  return new DOMException("The request was cancelled", "AbortError");
}

function defaultSleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(abortError());
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** Lets at most `limit` tasks run at once; the rest wait their turn, first come first served. */
export class Limiter {
  private active = 0;
  private waiting: Array<() => void> = [];
  constructor(private readonly limit: number) {}

  async run<T>(task: () => Promise<T>): Promise<T> {
    if (this.active < this.limit) {
      this.active += 1;
    } else {
      // The slot is handed over by whoever finishes next, so `active` never dips below the
      // true count (a newcomer cannot slip in between and exceed the limit).
      await new Promise<void>((resolve) => this.waiting.push(resolve));
    }
    try {
      return await task();
    } finally {
      const next = this.waiting.shift();
      if (next) next(); // pass our slot on: `active` stays the same
      else this.active -= 1;
    }
  }
}

export class ApiClient {
  private readonly fetchFn: typeof fetch;
  private readonly sleep: (ms: number, signal?: AbortSignal) => Promise<void>;
  private readonly random: () => number;
  private readonly maxAttempts: number;
  private readonly baseDelayMs: number;
  private readonly maxDelayMs: number;
  private readonly limiter: Limiter;

  constructor(
    private readonly config: AppConfig,
    options: ClientOptions = {},
  ) {
    this.fetchFn = options.fetch ?? ((...args) => fetch(...args));
    this.sleep = options.sleep ?? defaultSleep;
    this.random = options.random ?? Math.random;
    this.maxAttempts = options.maxAttempts ?? 4;
    this.baseDelayMs = options.baseDelayMs ?? 300;
    this.maxDelayMs = options.maxDelayMs ?? 5000;
    this.limiter = new Limiter(options.maxConcurrent ?? 4);
  }

  listDatasets(
    params: { limit?: number; cursor?: string | null } = {},
    signal?: AbortSignal,
  ): Promise<DatasetsPage> {
    return this.get("/datasets", params, signal);
  }

  getDataset(id: string, signal?: AbortSignal): Promise<DatasetDetail> {
    return this.get(`/datasets/${encodeURIComponent(id)}`, {}, signal);
  }

  getStats(id: string, signal?: AbortSignal): Promise<StatsResponse> {
    return this.get(`/datasets/${encodeURIComponent(id)}/stats`, {}, signal);
  }

  getAnomalies(
    id: string,
    params: { method?: AnomalyMethod | null; limit?: number; cursor?: string | null } = {},
    signal?: AbortSignal,
  ): Promise<AnomaliesResponse> {
    return this.get(`/datasets/${encodeURIComponent(id)}/anomalies`, params, signal);
  }

  createUpload(filename: string, signal?: AbortSignal): Promise<UploadForm> {
    return this.request("POST", "/uploads", {}, JSON.stringify({ filename }), signal);
  }

  // --- internals -------------------------------------------------------------------

  private get<T>(path: string, params: object, signal?: AbortSignal): Promise<T> {
    return this.request("GET", path, params, undefined, signal);
  }

  private request<T>(
    method: string,
    path: string,
    params: object,
    body: string | undefined,
    signal?: AbortSignal,
  ): Promise<T> {
    const query = new URLSearchParams();
    for (const [name, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== "") query.set(name, String(value));
    }
    const url = `${this.config.apiUrl}${path}${query.size > 0 ? `?${query}` : ""}`;
    const headers: Record<string, string> = { "x-api-key": this.config.apiKey };
    if (body !== undefined) headers["Content-Type"] = "application/json";

    return this.limiter.run(() => this.attempt<T>(url, { method, headers, body, signal }));
  }

  private async attempt<T>(url: string, init: RequestInit): Promise<T> {
    const signal = init.signal ?? undefined;
    for (let attempt = 1; ; attempt += 1) {
      if (signal?.aborted) throw abortError();
      let response: Response;
      try {
        response = await this.fetchFn(url, init);
      } catch (error) {
        if (signal?.aborted) throw abortError();
        if (attempt >= this.maxAttempts) {
          throw new NetworkError("Could not reach the API. Check your connection and try again.");
        }
        await this.sleep(this.backoff(attempt, null), signal);
        continue;
      }

      if (response.ok) return (await response.json()) as T;
      if (!RETRYABLE_STATUS.has(response.status) || attempt >= this.maxAttempts) {
        throw await toApiError(response);
      }
      await this.sleep(this.backoff(attempt, response.headers.get("Retry-After")), signal);
    }
  }

  /** Exponential backoff with a little jitter; a numeric Retry-After header wins. */
  private backoff(attempt: number, retryAfter: string | null): number {
    const seconds = retryAfter === null ? NaN : Number(retryAfter);
    if (Number.isFinite(seconds) && seconds >= 0) return Math.min(seconds * 1000, this.maxDelayMs);
    const exponential = this.baseDelayMs * 2 ** (attempt - 1);
    return Math.min(exponential * (0.75 + 0.5 * this.random()), this.maxDelayMs);
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // not JSON: fall through to the generic message
  }
  const error = (body as { error?: { code?: unknown; message?: unknown } } | null)?.error;
  if (typeof error?.code === "string" && typeof error.message === "string") {
    return new ApiError(response.status, error.code, error.message);
  }
  if (response.status === 403) {
    // API Gateway's own answer: no key, a wrong key, or a route that does not exist.
    return new ApiError(403, "forbidden", "The API rejected the request: check the API key in config.json.");
  }
  if (response.status === 429) {
    return new ApiError(429, "too_many_requests", "The API is busy. Please wait a moment and try again.");
  }
  return new ApiError(response.status, "http_error", `The API answered with HTTP ${response.status}.`);
}
