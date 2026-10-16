import { useCallback, useEffect, useState } from "react";
import type { ApiClient } from "../api/client";
import type { Anomaly, AnomalyMethod } from "../api/types";

export interface AnomalyList {
  items: Anomaly[];
  status: "idle" | "loading" | "ready" | "error";
  error: Error | null;
  hasMore: boolean;
  loadingMore: boolean;
  /** Findings for the whole dataset (not just the filtered or loaded ones). */
  total: number;
  stored: number;
  mlSkipped: string | null;
  loadMore: () => void;
  reload: () => void;
}

const PAGE = 25;

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error));
}

/** The anomalies of a dataset, filtered by method on the server and paged with a cursor.
 * `enabled` is false when there is nothing to load (not completed, or no findings). */
export function useAnomalies(
  client: ApiClient,
  id: string,
  method: AnomalyMethod | null,
  enabled: boolean,
): AnomalyList {
  const [items, setItems] = useState<Anomaly[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [status, setStatus] = useState<AnomalyList["status"]>("idle");
  const [error, setError] = useState<Error | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [totals, setTotals] = useState({ total: 0, stored: 0, mlSkipped: null as string | null });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setItems([]);
    setCursor(null);
    setError(null);
    if (!enabled) return setStatus("idle");
    setStatus("loading");
    const controller = new AbortController();
    client
      .getAnomalies(id, { method, limit: PAGE }, controller.signal)
      .then((page) => {
        setItems(page.items);
        setCursor(page.next_cursor);
        setTotals({ total: page.anomaly_count, stored: page.anomalies_stored, mlSkipped: page.ml_skipped });
        setStatus("ready");
      })
      .catch((e: unknown) => {
        if (isAbort(e)) return;
        setError(asError(e));
        setStatus("error");
      });
    return () => controller.abort();
  }, [client, id, method, enabled, reloadKey]);

  const loadMore = useCallback(() => {
    if (cursor === null || loadingMore) return;
    setLoadingMore(true);
    client
      .getAnomalies(id, { method, limit: PAGE, cursor })
      .then((page) => {
        setItems((current) => {
          const known = new Set(current.map((a) => a.rank));
          return [...current, ...page.items.filter((a) => !known.has(a.rank))];
        });
        setCursor(page.next_cursor);
      })
      .catch((e: unknown) => setError(asError(e)))
      .finally(() => setLoadingMore(false));
  }, [client, id, method, cursor, loadingMore]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  return { items, status, error, hasMore: cursor !== null, loadingMore, ...totals, loadMore, reload };
}
