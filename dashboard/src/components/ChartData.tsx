import type { ReactNode } from "react";

/** The numbers behind a chart as a table: for screen readers, and for reading exact values. */
export function ChartData({ caption, children }: { caption: string; children: ReactNode }) {
  return (
    <details className="chart-data">
      <summary>Show {caption} as a table</summary>
      <table>
        <caption className="visually-hidden">{caption}</caption>
        {children}
      </table>
    </details>
  );
}
