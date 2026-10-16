import { statusInfo } from "../lib/labels";

/** The coloured status label. The description is the hover text, and the text itself carries the
 * meaning, so colour is never the only signal. */
export function StatusBadge({ status }: { status: string }) {
  const info = statusInfo(status);
  return (
    <span className={`badge badge-${info.tone}`} title={info.description || undefined}>
      {info.label}
    </span>
  );
}
