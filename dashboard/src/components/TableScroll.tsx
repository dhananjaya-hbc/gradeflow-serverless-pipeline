import type { ReactNode } from "react";

/** Lets a wide table scroll sideways on a phone instead of stretching the page. It is focusable
 * so keyboard users can scroll it too. */
export function TableScroll({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="table-scroll" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}
