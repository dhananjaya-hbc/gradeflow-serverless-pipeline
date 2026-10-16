/** Formatting for display. Never prints "NaN" or "undefined": a missing value is a dash. */

const DASH = "-";

type Maybe = number | null | undefined;

function isNumber(value: Maybe): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** 62.43 -> "62.43", 80 -> "80", 62.5 -> "62.5": at most `decimals` decimals, no trailing zeros. */
export function formatNumber(value: Maybe, decimals = 2): string {
  return isNumber(value) ? String(Number(value.toFixed(decimals))) : DASH;
}

/** 78.34 -> "78.34%". */
export function formatPercent(value: Maybe, decimals = 2): string {
  return isNumber(value) ? `${formatNumber(value, decimals)}%` : DASH;
}

/** 524991 -> "524,991". */
export function formatCount(value: Maybe): string {
  return isNumber(value) ? new Intl.NumberFormat("en-US").format(value) : DASH;
}

/** "2026-10-15T10:09:30+00:00" -> "15 Oct 2026, 15:39" in the viewer's own time zone. */
export function formatDateTime(
  iso: string | null | undefined,
  options: { timeZone?: string; locale?: string } = {},
): string {
  if (!iso) return DASH;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return DASH;
  return new Intl.DateTimeFormat(options.locale ?? "en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: options.timeZone,
  }).format(date);
}
