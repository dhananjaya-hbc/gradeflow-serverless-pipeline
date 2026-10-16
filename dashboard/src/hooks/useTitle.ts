import { useEffect } from "react";

/** Sets the browser tab title (and what a screen reader announces for the page). */
export function useTitle(title: string | null | undefined): void {
  useEffect(() => {
    document.title = title ? `${title} · GradeFlow` : "GradeFlow";
    return () => {
      document.title = "GradeFlow";
    };
  }, [title]);
}
