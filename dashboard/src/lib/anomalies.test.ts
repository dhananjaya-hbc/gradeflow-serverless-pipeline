import { describe, expect, it } from "vitest";
import type { AnomaliesResponse, Anomaly } from "../api/types";
import anomalies030 from "../test/fixtures/anomalies.demo-030.json";
import { filterAnomalies } from "./anomalies";

const real = (anomalies030 as unknown as AnomaliesResponse).items;

function anomaly(rank: number, extra: Partial<Anomaly> = {}): Anomaly {
  return {
    rank,
    method: "term_jump",
    student_id: `S0000${rank}`,
    student_name: `Student ${rank}`,
    subject: null,
    term: null,
    score: null,
    pattern: null,
    severity: 1,
    details: "x",
    ...extra,
  };
}

describe("filterAnomalies", () => {
  it("returns everything, in the same order, when no filter is set", () => {
    expect(filterAnomalies(real, {})).toEqual(real);
    expect(filterAnomalies(real, { method: "", search: "   " })).toEqual(real);
    expect(filterAnomalies(real, { method: null })).toEqual(real);
  });

  it("filters by method", () => {
    const counts = Object.fromEntries(
      (["term_jump", "duplicate_id", "isolation_forest", "zscore"] as const).map((m) => [m, filterAnomalies(real, { method: m }).length]),
    );
    expect(counts).toEqual({ term_jump: 5, duplicate_id: 1, isolation_forest: 3, zscore: 0 });
  });

  it("finds a student by id, ignoring case and surrounding spaces", () => {
    const matches = filterAnomalies(real, { search: "  s00040 " });
    expect(matches).toHaveLength(3); // two term jumps and one Isolation Forest finding
    expect(matches.every((m) => m.student_id === "S00040")).toBe(true);
  });

  it("matches part of an id", () => {
    expect(new Set(filterAnomalies(real, { search: "S0004" }).map((m) => m.student_id))).toEqual(new Set(["S00040", "S00045"]));
  });

  it("finds a student by part of their name, in any case", () => {
    const named = real.find((item) => item.student_name);
    const fragment = (named?.student_name ?? "").slice(1, 6).toUpperCase();
    const matches = filterAnomalies(real, { search: fragment });
    expect(matches.length).toBeGreaterThan(0);
    expect(matches.every((m) => (m.student_name ?? "").toUpperCase().includes(fragment))).toBe(true);
  });

  it("combines the method and the search", () => {
    const matches = filterAnomalies(real, { method: "term_jump", search: "S00040" });
    expect(matches.map((m) => m.rank)).toEqual([2, 4]);
  });

  it("does not crash on findings without a student name", () => {
    const items = [anomaly(1, { student_name: null }), anomaly(2, { student_name: "Amal Perera" })];
    expect(filterAnomalies(items, { search: "amal" }).map((m) => m.rank)).toEqual([2]);
    expect(filterAnomalies(items, { search: "null" })).toEqual([]);
  });

  it("returns nothing when nothing matches", () => {
    expect(filterAnomalies(real, { search: "nobody-with-this-name" })).toEqual([]);
    expect(filterAnomalies(real, { method: "zscore" })).toEqual([]);
  });

  it("does not change the list it was given", () => {
    const copy = structuredClone(real);
    filterAnomalies(real, { method: "term_jump", search: "S0" });
    expect(real).toEqual(copy);
  });
});
