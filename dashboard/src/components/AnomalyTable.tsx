import { useState } from "react";
import type { AnomalyMethod } from "../api/types";
import { useApi } from "../api/context";
import { useAnomalies } from "../hooks/useAnomalies";
import { formatNumber } from "../lib/format";
import { filterAnomalies } from "../lib/anomalies";
import { METHOD_OPTIONS, methodLabel, patternInfo } from "../lib/labels";
import { ErrorBox } from "./ErrorBox";
import { Loading } from "./Loading";

/** The findings, with a method filter (asked of the server) and a student search (on what is loaded). */
export function AnomalyTable({ datasetId, enabled }: { datasetId: string; enabled: boolean }) {
  const [method, setMethod] = useState<AnomalyMethod | "">("");
  const [search, setSearch] = useState("");
  const list = useAnomalies(useApi(), datasetId, method || null, enabled);
  const visible = filterAnomalies(list.items, { search });

  return (
    <section>
      <h2>Anomalies</h2>
      <div className="filters">
        <label>
          Method{" "}
          <select value={method} onChange={(event) => setMethod(event.target.value as AnomalyMethod | "")}>
            <option value="">All methods</option>
            {METHOD_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {methodLabel(option)}
              </option>
            ))}
          </select>
        </label>{" "}
        <label>
          Student{" "}
          <input
            type="search"
            placeholder="ID or name"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </div>

      {list.mlSkipped && <p className="hint">The machine-learning check was skipped: {list.mlSkipped}.</p>}
      {list.status === "loading" && <Loading label="Loading anomalies" />}
      {list.status === "error" && <ErrorBox title="Could not load anomalies" message={list.error?.message ?? ""} onRetry={list.reload} />}
      {list.status === "ready" && list.error && <ErrorBox title="Could not load more" message={list.error.message} />}

      {list.status === "ready" && list.items.length === 0 && (
        <p>{method ? "No anomalies were found by this method." : "No anomalies were found."}</p>
      )}
      {list.status === "ready" && list.items.length > 0 && (
        <>
          {visible.length === 0 ? (
            <p>
              No loaded anomaly matches “{search.trim()}”.
              {list.hasMore && " Load more to search further."}
            </p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Method</th>
                  <th>Student</th>
                  <th>Subject / term</th>
                  <th className="num">Score</th>
                  <th>Pattern</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((a) => {
                  const pattern = patternInfo(a.pattern);
                  return (
                    <tr key={a.rank}>
                      <td>{a.rank}</td>
                      <td>{methodLabel(a.method)}</td>
                      <td>
                        {a.student_name ?? "-"} <span className="muted">{a.student_id}</span>
                      </td>
                      <td>{a.subject ? `${a.subject}${a.term ? ` ${a.term}` : ""}` : "-"}</td>
                      <td className="num">{formatNumber(a.score)}</td>
                      <td title={pattern.hint || undefined}>{pattern.label}</td>
                      <td>{a.details}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          <p className="muted">
            Showing {visible.length === list.items.length ? list.items.length : `${visible.length} of ${list.items.length} loaded`}
            {!method && ` of ${list.stored} stored`}
            {!method && list.total > list.stored && ` (${list.total} found in total)`}.
          </p>
          {list.hasMore && (
            <button type="button" onClick={list.loadMore} disabled={list.loadingMore}>
              {list.loadingMore ? "Loading…" : "Load more"}
            </button>
          )}
        </>
      )}
    </section>
  );
}
