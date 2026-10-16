/** One table of words and colours, so the list and the detail page can never disagree. */

import type { AnomalyMethod, DatasetStatus, DatasetSummary, JumpPattern } from "../api/types";

export type Tone = "success" | "danger" | "warning" | "info" | "neutral";

export interface StatusInfo {
  label: string;
  tone: Tone;
  description: string;
}

const STATUSES: Record<DatasetStatus, StatusInfo> = {
  COMPLETED: { label: "Completed", tone: "success", description: "Processed successfully." },
  FAILED: { label: "Failed", tone: "danger", description: "Rejected or crashed: see the reason on the dataset page." },
  PROCESSING: { label: "Processing", tone: "warning", description: "Being processed right now." },
  RECEIVED: { label: "Received", tone: "info", description: "Waiting to be processed." },
  DUPLICATE: { label: "Duplicate", tone: "neutral", description: "Identical to an earlier upload, so it was not processed again." },
};

/** Unknown statuses (the API may grow new ones) are shown as they are, never crash the page. */
export function statusInfo(status: string): StatusInfo {
  return STATUSES[status as DatasetStatus] ?? { label: status, tone: "neutral", description: "" };
}

const UNFINISHED = new Set(["PROCESSING", "RECEIVED"]);

/** Finished datasets will not change any more. An unknown status counts as finished, so a
 * new status can never make the page poll forever. */
export function isFinished(status: string): boolean {
  return !UNFINISHED.has(status);
}

/** True while at least one dataset is still being processed: the list should keep refreshing. */
export function needsRefresh(datasets: Pick<DatasetSummary, "status">[]): boolean {
  return datasets.some((dataset) => !isFinished(dataset.status));
}

const PATTERNS: Record<JumpPattern, { label: string; hint: string }> = {
  spike: { label: "Spike", hint: "The score jumped and then went back: more suspicious." },
  sustained: { label: "Sustained", hint: "The score jumped and stayed: possibly a genuine change." },
  latest: { label: "Latest term", hint: "The change is in the last term, so it is too early to tell." },
};

export function patternInfo(pattern: JumpPattern | null | undefined): { label: string; hint: string } {
  return pattern ? (PATTERNS[pattern] ?? { label: pattern, hint: "" }) : { label: "-", hint: "" };
}

const METHODS: Record<AnomalyMethod, string> = {
  term_jump: "Term jump",
  duplicate_id: "Duplicate ID",
  zscore: "Score outlier",
  isolation_forest: "Unusual student",
};

export function methodLabel(method: string): string {
  return METHODS[method as AnomalyMethod] ?? method;
}

/** The methods in the order the filter lists them. */
export const METHOD_OPTIONS: AnomalyMethod[] = ["term_jump", "duplicate_id", "zscore", "isolation_forest"];
