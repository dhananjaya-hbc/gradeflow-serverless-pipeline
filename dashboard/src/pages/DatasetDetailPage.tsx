import { Link, useParams } from "react-router-dom";
import { useApi } from "../api/context";
import { AnomalyTable } from "../components/AnomalyTable";
import { ErrorBox } from "../components/ErrorBox";
import { Loading } from "../components/Loading";
import { StatusBadge } from "../components/StatusBadge";
import { StudentTables } from "../components/StudentTables";
import { SubjectBarChart } from "../components/SubjectBarChart";
import { SummaryCards } from "../components/SummaryCards";
import { TermLineChart } from "../components/TermLineChart";
import { ValidationReport } from "../components/ValidationReport";
import { useDatasetDetail } from "../hooks/useDatasetDetail";
import { formatDateTime } from "../lib/format";

export function DatasetDetailPage() {
  const { id = "" } = useParams();
  const { detail, stats, statsError, status, error, autoRefresh, refresh } = useDatasetDetail(useApi(), id);

  if (status === "loading") return <Loading label="Loading dataset" />;
  if (status === "not-found") {
    return (
      <section>
        <h1>Dataset not found</h1>
        <p>There is no dataset with the ID “{id}”.</p>
        <p>
          <Link to="/">Back to datasets</Link>
        </p>
      </section>
    );
  }
  if (status === "error" || !detail) {
    return <ErrorBox title="Could not load the dataset" message={error?.message ?? ""} onRetry={refresh} />;
  }

  return (
    <section>
      <p>
        <Link to="/">← All datasets</Link>
      </p>
      <div className="page-title">
        <h1>{detail.filename ?? detail.dataset_id}</h1>
        <StatusBadge status={detail.status} />
        <button type="button" onClick={refresh}>
          Refresh
        </button>
      </div>
      <p className="muted">
        {detail.dataset_id} · uploaded {formatDateTime(detail.uploaded_at)}
      </p>

      {autoRefresh === "on" && <p className="hint">This dataset is still being processed. The page refreshes automatically.</p>}
      {autoRefresh === "gave-up" && (
        <p className="hint">Still processing after 10 minutes. Automatic refresh stopped: use Refresh to check again.</p>
      )}

      {detail.status === "DUPLICATE" && (
        <p>
          This file is identical to an earlier upload, so it was not processed again.
          {detail.duplicate_of && (
            <>
              {" "}
              See <Link to={`/datasets/${encodeURIComponent(detail.duplicate_of)}`}>{detail.duplicate_of}</Link>.
            </>
          )}
        </p>
      )}

      {detail.status === "FAILED" && (
        <>
          <ErrorBox title="This dataset failed" message={detail.error_message ?? "No reason was recorded."} />
          {detail.quarantine_key && <p className="muted">A copy of the file was kept in quarantine for inspection.</p>}
          {detail.validation.invalid_rows > 0 && <ValidationReport detail={detail} />}
        </>
      )}

      {detail.status === "COMPLETED" && (
        <>
          <SummaryCards detail={detail} stats={stats} />
          {statsError && <ErrorBox title="Could not load statistics" message={statsError.message} onRetry={refresh} />}
          {!stats && !statsError && <Loading label="Loading statistics" />}
          {stats && (
            <>
              <SubjectBarChart groups={stats.by_subject_term} />
              <TermLineChart groups={stats.by_subject_term} />
              <StudentTables stats={stats} />
            </>
          )}
          <ValidationReport detail={detail} />
          <AnomalyTable datasetId={detail.dataset_id} enabled={detail.anomalies.stored > 0} />
        </>
      )}
    </section>
  );
}
