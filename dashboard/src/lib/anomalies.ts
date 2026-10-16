import type { Anomaly, AnomalyMethod } from "../api/types";

export interface AnomalyFilter {
  method?: AnomalyMethod | "" | null;
  /** Matches the student id or name, ignoring case and surrounding spaces. */
  search?: string;
}

export function filterAnomalies(items: Anomaly[], filter: AnomalyFilter): Anomaly[] {
  const needle = (filter.search ?? "").trim().toLowerCase();
  return items.filter((item) => {
    if (filter.method && item.method !== filter.method) return false;
    if (!needle) return true;
    return (
      item.student_id.toLowerCase().includes(needle) ||
      (item.student_name ?? "").toLowerCase().includes(needle)
    );
  });
}
