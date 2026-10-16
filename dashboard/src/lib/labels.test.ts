import { describe, expect, it } from "vitest";
import type { AnomalyMethod, AnomaliesResponse, DatasetsPage } from "../api/types";
import anomalies030 from "../test/fixtures/anomalies.demo-030.json";
import datasetsPage from "../test/fixtures/datasets.page1.json";
import { isFinished, methodLabel, METHOD_OPTIONS, needsRefresh, patternInfo, statusInfo } from "./labels";

const page = datasetsPage as unknown as DatasetsPage;
const anomalies = anomalies030 as unknown as AnomaliesResponse;

describe("statusInfo", () => {
  it.each([
    ["COMPLETED", "Completed", "success"],
    ["FAILED", "Failed", "danger"],
    ["PROCESSING", "Processing", "warning"],
    ["RECEIVED", "Received", "info"],
    ["DUPLICATE", "Duplicate", "neutral"],
  ])("%s is shown as %s (%s)", (status, label, tone) => {
    expect(statusInfo(status)).toMatchObject({ label, tone });
    expect(statusInfo(status).description).not.toBe("");
  });

  it("shows a status the dashboard has never heard of as it is, without crashing", () => {
    expect(statusInfo("ARCHIVED")).toEqual({ label: "ARCHIVED", tone: "neutral", description: "" });
  });

  it("knows every status the real datasets have (a known status has a description)", () => {
    for (const dataset of page.datasets) {
      expect(statusInfo(dataset.status).description, dataset.status).not.toBe("");
    }
  });
});

describe("isFinished and needsRefresh decide when polling stops", () => {
  it.each([
    ["COMPLETED", true],
    ["FAILED", true],
    ["DUPLICATE", true],
    ["PROCESSING", false],
    ["RECEIVED", false],
  ])("%s finished: %s", (status, finished) => expect(isFinished(status)).toBe(finished));

  it("treats an unknown status as finished, so a new status can never make the page poll forever", () => {
    expect(isFinished("SOMETHING_NEW")).toBe(true);
  });

  it("keeps refreshing while any dataset is unfinished", () => {
    expect(needsRefresh([{ status: "COMPLETED" }, { status: "PROCESSING" }])).toBe(true);
    expect(needsRefresh([{ status: "RECEIVED" }])).toBe(true);
  });

  it("stops when everything is finished, or there is nothing", () => {
    expect(needsRefresh([{ status: "COMPLETED" }, { status: "FAILED" }, { status: "DUPLICATE" }])).toBe(false);
    expect(needsRefresh([])).toBe(false);
  });

  it("does not need to refresh the real first page (all finished)", () => {
    expect(needsRefresh(page.datasets)).toBe(false);
  });
});

describe("patternInfo", () => {
  it.each([
    ["spike", "Spike"],
    ["sustained", "Sustained"],
    ["latest", "Latest term"],
  ] as const)("%s -> %s, with an explanation", (pattern, label) => {
    expect(patternInfo(pattern).label).toBe(label);
    expect(patternInfo(pattern).hint.length).toBeGreaterThan(10);
  });

  it("shows a dash for no pattern", () => {
    expect(patternInfo(null)).toEqual({ label: "-", hint: "" });
    expect(patternInfo(undefined)).toEqual({ label: "-", hint: "" });
  });
});

describe("methodLabel", () => {
  it.each([
    ["term_jump", "Term jump"],
    ["duplicate_id", "Duplicate ID"],
    ["zscore", "Score outlier"],
    ["isolation_forest", "Unusual student"],
  ])("%s -> %s", (method, label) => expect(methodLabel(method)).toBe(label));

  it("shows an unknown method as it is", () => {
    expect(methodLabel("telepathy")).toBe("telepathy");
  });

  it("lists every method the API knows, once, in a fixed order", () => {
    const known: AnomalyMethod[] = ["zscore", "term_jump", "duplicate_id", "isolation_forest"];
    expect([...METHOD_OPTIONS].sort()).toEqual([...known].sort());
    expect(METHOD_OPTIONS).toHaveLength(new Set(METHOD_OPTIONS).size);
  });

  it("has a readable label for every method in the real anomalies", () => {
    for (const item of anomalies.items) expect(methodLabel(item.method)).not.toContain("_");
  });
});
