import type { DatasetDetail, StatsResponse } from "../api/types";
import { formatCount, formatNumber, formatPercent } from "../lib/format";

function Card({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="card">
      <dt>{label}</dt>
      <dd>{value}</dd>
      {note && <small>{note}</small>}
    </div>
  );
}

/** The headline numbers. Statistics are missing (shown as "-") until the dataset is complete. */
export function SummaryCards({ detail, stats }: { detail: DatasetDetail; stats: StatsResponse | null }) {
  const overall = stats?.overall ?? null;
  const anomalies = detail.anomalies;
  const capped = anomalies.stored < anomalies.count;
  return (
    <dl className="cards">
      <Card label="Rows in file" value={formatCount(detail.validation.total_rows)} />
      <Card
        label="Valid rows"
        value={formatCount(detail.validation.valid_rows)}
        note={`${formatCount(detail.validation.invalid_rows)} rejected`}
      />
      <Card
        label="Clean rows"
        value={formatCount(detail.cleaning.clean_rows)}
        note={`${formatCount(detail.cleaning.duplicates_removed)} duplicates removed`}
      />
      <Card label="Students" value={formatCount(stats?.student_count)} />
      <Card label="Average score" value={formatNumber(overall?.mean)} note={`median ${formatNumber(overall?.median)}`} />
      <Card label="Pass rate" value={formatPercent(overall?.pass_rate)} />
      <Card
        label="Anomalies"
        value={formatCount(anomalies.count)}
        note={capped ? `only the ${formatCount(anomalies.stored)} most important are stored` : undefined}
      />
    </dl>
  );
}
