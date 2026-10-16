import { useCallback, useEffect, useState } from "react";
import { ApiError, type ApiClient } from "../api/client";
import type { DatasetDetail, StatsResponse } from "../api/types";
import { isFinished } from "../lib/labels";
import { pollDelay } from "../lib/polling";

export type DetailStatus = "loading" | "ready" | "not-found" | "error";

export interface DatasetDetailState {
  detail: DatasetDetail | null;
  /** Only loaded for a COMPLETED dataset: other statuses have no statistics. */
  stats: StatsResponse | null;
  statsError: Error | null;
  status: DetailStatus;
  error: Error | null;
  /** True while the dataset is unfinished and the page is checking again by itself. */
  autoRefresh: "off" | "on" | "gave-up";
  refresh: () => void;
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error));
}

/** One dataset: its detail, refreshed on a schedule while processing, and its statistics. */
export function useDatasetDetail(client: ApiClient, id: string): DatasetDetailState {
  const [detail, setDetail] = useState<DatasetDetail | null>(null);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [statsError, setStatsError] = useState<Error | null>(null);
  const [status, setStatus] = useState<DetailStatus>("loading");
  const [error, setError] = useState<Error | null>(null);
  const [gaveUp, setGaveUp] = useState(false);
  const [pollStart, setPollStart] = useState(() => Date.now());
  const [tick, setTick] = useState(0); // bumped after every background check: schedules the next
  const [reloadKey, setReloadKey] = useState(0); // bumped by "Refresh": reload detail and stats

  // Load the detail (first load, a new id, or Refresh).
  useEffect(() => {
    const controller = new AbortController();
    client
      .getDataset(id, controller.signal)
      .then((loaded) => {
        setDetail(loaded);
        setError(null);
        setStatus("ready");
        setPollStart(Date.now());
        setGaveUp(false);
      })
      .catch((e: unknown) => {
        if (isAbort(e)) return;
        setError(asError(e));
        setStatus(e instanceof ApiError && e.status === 404 ? "not-found" : "error");
      });
    return () => controller.abort();
  }, [client, id, reloadKey]);

  // A different dataset must not show the previous one's data while it loads.
  useEffect(() => {
    setDetail(null);
    setStats(null);
    setStatsError(null);
    setStatus("loading");
    setError(null);
  }, [id]);

  // While unfinished, check again on the schedule, then stop.
  const waiting = detail !== null && !isFinished(detail.status);
  useEffect(() => {
    if (!waiting) return;
    const delay = pollDelay(Date.now() - pollStart);
    if (delay === null) {
      setGaveUp(true);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      if (document.hidden) return setTick((t) => t + 1);
      client
        .getDataset(id, controller.signal)
        .then(setDetail)
        .catch(() => undefined) // a failed background check keeps what is on screen
        .finally(() => {
          if (!controller.signal.aborted) setTick((t) => t + 1);
        });
    }, delay);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [waiting, pollStart, tick, client, id]);

  // Statistics exist only for a COMPLETED dataset; load them when it becomes (or is) COMPLETED.
  const completed = detail?.status === "COMPLETED";
  useEffect(() => {
    if (!completed) return;
    const controller = new AbortController();
    setStatsError(null);
    client
      .getStats(id, controller.signal)
      .then(setStats)
      .catch((e: unknown) => {
        if (!isAbort(e)) setStatsError(asError(e));
      });
    return () => controller.abort();
  }, [completed, client, id, reloadKey]);

  const refresh = useCallback(() => {
    setGaveUp(false);
    setPollStart(Date.now());
    setReloadKey((k) => k + 1);
  }, []);

  let autoRefresh: DatasetDetailState["autoRefresh"] = "off";
  if (waiting) autoRefresh = gaveUp ? "gave-up" : "on";
  return { detail, stats, statsError, status, error, autoRefresh, refresh };
}
