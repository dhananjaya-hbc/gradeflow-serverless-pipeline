import type { DatasetDetail } from "../api/types";
import { formatCount } from "../lib/format";

/** What the validation step found. Only the first rejected rows are kept by the pipeline. */
export function ValidationReport({ detail }: { detail: DatasetDetail }) {
  const { validation, cleaning } = detail;
  const shown = validation.row_errors.length;
  return (
    <section>
      <h2>Validation report</h2>
      <p>
        {formatCount(validation.valid_rows)} of {formatCount(validation.total_rows)} rows were valid;{" "}
        {formatCount(validation.invalid_rows)} were rejected.
        {cleaning.duplicates_removed > 0 && ` Cleaning then removed ${formatCount(cleaning.duplicates_removed)} exact duplicates.`}
      </p>
      {shown > 0 && (
        <>
          <p>
            {shown < validation.invalid_rows
              ? `The first ${shown} rejected rows:`
              : "The rejected rows:"}
          </p>
          <ul className="row-errors">
            {validation.row_errors.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
