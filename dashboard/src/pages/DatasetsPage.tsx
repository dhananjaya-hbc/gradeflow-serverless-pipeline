import { Link } from "react-router-dom";
import { useApi } from "../api/context";
import { ErrorBox } from "../components/ErrorBox";
import { Loading } from "../components/Loading";
import { StatusBadge } from "../components/StatusBadge";
import { TableScroll } from "../components/TableScroll";
import { useDatasetList } from "../hooks/useDatasetList";
import { useTitle } from "../hooks/useTitle";
import { formatCount, formatDateTime } from "../lib/format";

export function DatasetsPage() {
  useTitle("Datasets");
  const list = useDatasetList(useApi());

  if (list.status === "loading") return <Loading label="Loading datasets" />;
  if (list.status === "error") {
    return <ErrorBox title="Could not load datasets" message={list.error?.message ?? ""} onRetry={list.refresh} />;
  }

  return (
    <section>
      <div className="page-title">
        <h1>Datasets</h1>
        <button type="button" onClick={list.refresh}>
          Refresh
        </button>
      </div>

      {list.autoRefresh === "on" && <p className="hint">Some datasets are still processing. This list refreshes automatically.</p>}
      {list.autoRefresh === "gave-up" && (
        <p className="hint">Still processing after 10 minutes. Automatic refresh stopped: use Refresh to check again.</p>
      )}
      {list.refreshError && <ErrorBox title="Could not refresh" message={list.refreshError.message} />}

      {list.datasets.length === 0 ? (
        <p>
          No datasets yet. <Link to="/upload">Upload a file</Link> to get started.
        </p>
      ) : (
        <TableScroll label="Datasets">
        <table>
          <thead>
            <tr>
              <th>Dataset</th>
              <th>Uploaded</th>
              <th>Status</th>
              <th className="num">Rows</th>
              <th className="num">Clean rows</th>
              <th className="num">Anomalies</th>
            </tr>
          </thead>
          <tbody>
            {list.datasets.map((d) => (
              <tr key={d.dataset_id}>
                <td>
                  <Link to={`/datasets/${encodeURIComponent(d.dataset_id)}`}>{d.filename}</Link>
                  <div className="muted">{d.dataset_id}</div>
                </td>
                <td>{formatDateTime(d.uploaded_at)}</td>
                <td>
                  <StatusBadge status={d.status} />
                </td>
                <td className="num">{formatCount(d.total_rows)}</td>
                <td className="num">{formatCount(d.clean_rows)}</td>
                <td className="num">{formatCount(d.anomaly_count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </TableScroll>
      )}

      {list.hasMore && (
        <button type="button" onClick={list.loadMore} disabled={list.loadingMore}>
          {list.loadingMore ? "Loading…" : "Load more"}
        </button>
      )}
    </section>
  );
}
