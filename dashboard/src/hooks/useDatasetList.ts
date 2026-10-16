import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient } from "../api/client";
import type { DatasetSummary } from "../api/types";
import { needsRefresh } from "../lib/labels";
import { pollDelay } from "../lib/polling";

const MAX_PAGE = 100; // the API's largest page

export type ListStatus = "loading" | "ready" | "error";
export type AutoRefresh = "off" | "on" | "gave-up";

export interface DatasetList {
  datasets: DatasetSummary[];
  status: ListStatus;
  error: Error | null;
  /** A background refresh failed. The list on screen is still the last good one. */
  refreshError: Error | null;
  hasMore: boolean;
  loadingMore: boolean;
  /** "on" while something is processing, "gave-up" after the polling schedule ran out. */
  autoRefresh: AutoRefresh;
  loadMore: () => void;
  /** Reload everything now (and restart the auto-refresh schedule). */
  refresh: () => void;
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error));
}

/** The datasets list: first page, "load more", and auto-refresh while datasets are processing. */
export function useDatasetList(client: ApiClient, pageSize = 20): DatasetList {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [status, setStatus] = useState<ListStatus>("loading");
  const [error, setError] = useState<Error | null>(null);
  const [refreshError, setRefreshError] = useState<Error | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [gaveUp, setGaveUp] = useState(false);
  const [pollStart, setPollStart] = useState(() => Date.now());
  const [tick, setTick] = useState(0); // bumped after every refresh attempt: schedules the next one
  const [reloadKey, setReloadKey] = useState(0);
  const loadedCount = useRef(0);
  loadedCount.current = datasets.length;

  /** Fetch up to `count` datasets from the start, at most `maxPages` requests (a refresh needs
   * as many pages as the user has already loaded; the first load needs just one). */
  const fetchFirst = useCallback(
    async (count: number, signal: AbortSignal, maxPages = Infinity) => {
      let pages = 0;
      const items: DatasetSummary[] = [];
      let next: string | null = null;
      do {
        const page = await client.listDatasets(
          { limit: Math.min(MAX_PAGE, Math.max(1, count - items.length)), cursor: next },
          signal,
        );
        items.push(...page.datasets);
        next = page.next_cursor;
        pages += 1;
      } while (next !== null && items.length < count && pages < maxPages);
      return { items, next };
    },
    [client],
  );

  // First load (and "Try again" after a failed first load).
  useEffect(() => {
    const controller = new AbortController();
    setStatus("loading");
    setError(null);
    fetchFirst(pageSize, controller.signal, 1)
      .then(({ items, next }) => {
        setDatasets(items);
        setCursor(next);
        setStatus("ready");
        setPollStart(Date.now());
        setGaveUp(false);
      })
      .catch((e: unknown) => {
        if (isAbort(e)) return;
        setError(asError(e));
        setStatus("error");
      });
    return () => controller.abort();
  }, [fetchFirst, pageSize, reloadKey]);

  // Auto-refresh: while anything is processing, refresh on the schedule, then stop.
  const waiting = status === "ready" && needsRefresh(datasets);
  useEffect(() => {
    if (!waiting) return;
    const delay = pollDelay(Date.now() - pollStart);
    if (delay === null) {
      setGaveUp(true);
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      if (document.hidden) {
        setTick((t) => t + 1); // a hidden tab does not poll; look again after the next delay
        return;
      }
      fetchFirst(Math.max(loadedCount.current, pageSize), controller.signal)
        .then(({ items, next }) => {
          setDatasets(items);
          setCursor(next);
          setRefreshError(null);
        })
        .catch((e: unknown) => {
          if (!isAbort(e)) setRefreshError(asError(e));
        })
        .finally(() => {
          if (!controller.signal.aborted) setTick((t) => t + 1);
        });
    }, delay);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [waiting, pollStart, tick, fetchFirst, pageSize]);

  const loadMore = useCallback(() => {
    if (cursor === null || loadingMore) return;
    setLoadingMore(true);
    client
      .listDatasets({ limit: pageSize, cursor })
      .then((page) => {
        setDatasets((current) => {
          const known = new Set(current.map((d) => d.dataset_id));
          return [...current, ...page.datasets.filter((d) => !known.has(d.dataset_id))];
        });
        setCursor(page.next_cursor);
      })
      .catch((e: unknown) => setRefreshError(asError(e)))
      .finally(() => setLoadingMore(false));
  }, [client, cursor, loadingMore, pageSize]);

  const refresh = useCallback(() => {
    setGaveUp(false);
    setRefreshError(null);
    setPollStart(Date.now());
    if (status === "error") {
      setReloadKey((k) => k + 1);
      return;
    }
    const controller = new AbortController();
    fetchFirst(Math.max(loadedCount.current, pageSize), controller.signal)
      .then(({ items, next }) => {
        setDatasets(items);
        setCursor(next);
      })
      .catch((e: unknown) => {
        if (!isAbort(e)) setRefreshError(asError(e));
      });
  }, [fetchFirst, pageSize, status]);

  let autoRefresh: AutoRefresh = "off";
  if (waiting) autoRefresh = gaveUp ? "gave-up" : "on";

  return { datasets, status, error, refreshError, hasMore: cursor !== null, loadingMore, autoRefresh, loadMore, refresh };
}
