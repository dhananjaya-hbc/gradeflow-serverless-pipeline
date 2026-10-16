/** Turns the API's per-subject-and-term statistics into the numbers the charts draw. */

import type { SubjectTermStats } from "../api/types";

export interface SubjectAverage {
  subject: string;
  average: number; // mean score across all terms, weighted by how many scores each term has
  passRate: number; // % of scores at or above the pass mark, weighted the same way
  count: number; // scores behind the bar
}

/** One row per term; each subject is a column holding the term's mean, or null if it has none. */
export interface TermTrendRow {
  term: string;
  [subject: string]: string | number | null;
}

export interface TermTrends {
  terms: TermTrendRow[];
  subjects: string[];
}

const round2 = (value: number): number => Math.round(value * 100) / 100;

/**
 * One bar per subject. The mean is weighted by each term's score count: a term with 60 scores
 * must count more than one with 5, so a plain average of the three term means would be wrong.
 */
export function subjectAverages(groups: SubjectTermStats[]): SubjectAverage[] {
  const totals = new Map<string, { count: number; scoreSum: number; passSum: number }>();
  for (const group of groups) {
    if (group.count <= 0) continue;
    const total = totals.get(group.subject) ?? { count: 0, scoreSum: 0, passSum: 0 };
    total.count += group.count;
    total.scoreSum += group.count * group.mean;
    total.passSum += group.count * group.pass_rate;
    totals.set(group.subject, total);
  }
  return [...totals.entries()]
    .map(([subject, t]) => ({
      subject,
      average: round2(t.scoreSum / t.count),
      passRate: round2(t.passSum / t.count),
      count: t.count,
    }))
    .sort((a, b) => a.subject.localeCompare(b.subject));
}

/**
 * Data for a line chart with one line per subject across the terms. A subject with no scores in
 * a term gets null (the line breaks) instead of a made-up 0.
 */
export function termTrends(groups: SubjectTermStats[]): TermTrends {
  const subjects = [...new Set(groups.filter((g) => g.count > 0).map((g) => g.subject))].sort((a, b) =>
    a.localeCompare(b),
  );
  const terms = [...new Set(groups.filter((g) => g.count > 0).map((g) => g.term))].sort();
  const rows: TermTrendRow[] = terms.map((term) => {
    const row: TermTrendRow = { term };
    for (const subject of subjects) {
      const group = groups.find((g) => g.term === term && g.subject === subject && g.count > 0);
      row[subject] = group ? round2(group.mean) : null;
    }
    return row;
  });
  return { terms: rows, subjects };
}
