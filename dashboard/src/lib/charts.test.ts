import { describe, expect, it } from "vitest";
import type { StatsResponse, SubjectTermStats } from "../api/types";
import stats030 from "../test/fixtures/stats.demo-030.json";
import { subjectAverages, termTrends } from "./charts";

const real = stats030 as unknown as StatsResponse;

function group(subject: string, term: string, count: number, mean: number, passRate = 50): SubjectTermStats {
  return { subject, term, count, mean, median: mean, std: 1, min: 0, max: 100, pass_rate: passRate };
}

describe("subjectAverages", () => {
  it("weights each term by how many scores it has", () => {
    // (60 x 63 + 5 x 50) / 65 = 4030 / 65 = 62.0. A plain average of 63 and 50 would be 56.5.
    const [maths] = subjectAverages([group("Maths", "T1", 60, 63), group("Maths", "T2", 5, 50)]);
    expect(maths).toMatchObject({ subject: "Maths", average: 62, count: 65 });
  });

  it("weights the pass rate the same way", () => {
    // (60 x 80 + 5 x 40) / 65 = 5000 / 65 = 76.923...
    const [maths] = subjectAverages([group("Maths", "T1", 60, 63, 80), group("Maths", "T2", 5, 50, 40)]);
    expect(maths?.passRate).toBe(76.92);
  });

  it("keeps subjects apart and sorts them by name", () => {
    const result = subjectAverages([
      group("Science", "T1", 10, 70),
      group("English", "T1", 10, 50),
      group("Science", "T2", 10, 80),
      group("Maths", "T1", 10, 60),
    ]);
    expect(result.map((s) => [s.subject, s.average])).toEqual([
      ["English", 50],
      ["Maths", 60],
      ["Science", 75],
    ]);
  });

  it("rounds to two decimals", () => {
    const [bar] = subjectAverages([group("Maths", "T1", 3, 10), group("Maths", "T2", 3, 10.01)]);
    expect(bar?.average).toBe(10.01); // 10.005 rounds up
    const [third] = subjectAverages([group("Maths", "T1", 1, 1), group("Maths", "T2", 2, 0)]);
    expect(third?.average).toBe(0.33);
  });

  it("ignores groups with no scores, and drops a subject that has none at all", () => {
    const result = subjectAverages([group("Maths", "T1", 0, 99), group("Maths", "T2", 10, 60), group("Art", "T1", 0, 40)]);
    expect(result).toEqual([{ subject: "Maths", average: 60, passRate: 50, count: 10 }]);
  });

  it("returns nothing for no data", () => {
    expect(subjectAverages([])).toEqual([]);
  });

  describe("on the real response for demo-030", () => {
    const bars = subjectAverages(real.by_subject_term);

    it("has one bar for each of the five subjects", () => {
      expect(bars.map((b) => b.subject)).toEqual(["English", "History", "ICT", "Maths", "Science"]);
    });

    it("matches an independent calculation for every subject", () => {
      for (const bar of bars) {
        const rows = real.by_subject_term.filter((g) => g.subject === bar.subject);
        const count = rows.reduce((sum, g) => sum + g.count, 0);
        const mean = rows.reduce((sum, g) => sum + g.count * g.mean, 0) / count;
        expect(bar.count).toBe(count);
        expect(bar.average).toBeCloseTo(mean, 2);
      }
    });

    it("accounts for every score and reproduces the dataset's overall mean", () => {
      const total = bars.reduce((sum, b) => sum + b.count, 0);
      const mean = bars.reduce((sum, b) => sum + b.count * b.average, 0) / total;
      expect(total).toBe(real.overall?.count); // 891 scores
      expect(mean).toBeCloseTo(real.overall?.mean ?? NaN, 1); // 62.43
    });
  });
});

describe("termTrends", () => {
  it("makes one row per term with a column for each subject", () => {
    const { terms, subjects } = termTrends([
      group("Maths", "T1", 10, 60),
      group("Maths", "T2", 10, 65.456),
      group("Science", "T1", 10, 70),
      group("Science", "T2", 10, 72),
    ]);
    expect(subjects).toEqual(["Maths", "Science"]);
    expect(terms).toEqual([
      { term: "T1", Maths: 60, Science: 70 },
      { term: "T2", Maths: 65.46, Science: 72 },
    ]);
  });

  it("puts the terms in order whatever order they arrive in", () => {
    const { terms } = termTrends([group("Maths", "T3", 5, 1), group("Maths", "T1", 5, 2), group("Maths", "T2", 5, 3)]);
    expect(terms.map((t) => t.term)).toEqual(["T1", "T2", "T3"]);
  });

  it("leaves a gap (null), never a made-up zero, where a subject has no scores in a term", () => {
    const { terms } = termTrends([
      group("Maths", "T1", 10, 60),
      group("Maths", "T2", 10, 61),
      group("Art", "T2", 10, 55),
      group("Art", "T3", 0, 99), // a group with no scores counts as missing
    ]);
    expect(terms).toEqual([
      { term: "T1", Art: null, Maths: 60 },
      { term: "T2", Art: 55, Maths: 61 },
    ]); // T3 has no scores at all, so there is no T3 row
  });

  it("returns empty chart data for no data", () => {
    expect(termTrends([])).toEqual({ terms: [], subjects: [] });
  });

  it("matches the real response: three terms, five subjects, every cell filled", () => {
    const { terms, subjects } = termTrends(real.by_subject_term);
    expect(terms.map((t) => t.term)).toEqual(["T1", "T2", "T3"]);
    expect(subjects).toHaveLength(5);
    for (const row of terms) {
      for (const subject of subjects) {
        const expected = real.by_subject_term.find((g) => g.term === row.term && g.subject === subject);
        expect(row[subject]).toBeCloseTo(expected?.mean ?? NaN, 2);
      }
    }
  });
});
