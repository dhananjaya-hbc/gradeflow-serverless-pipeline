import { describe, expect, it } from "vitest";
import { formatCount, formatDateTime, formatNumber, formatPercent } from "./format";

describe("formatNumber", () => {
  it.each([
    [62.43, "62.43"],
    [80, "80"],
    [62.5, "62.5"],
    [62.456, "62.46"],
    [0, "0"],
    [-3.2, "-3.2"],
    [100, "100"],
  ])("%s -> %s", (value, expected) => expect(formatNumber(value)).toBe(expected));

  it("honours the number of decimals", () => {
    expect(formatNumber(1 / 3, 3)).toBe("0.333");
    expect(formatNumber(62.456, 1)).toBe("62.5");
    expect(formatNumber(62.456, 0)).toBe("62");
  });

  it.each([null, undefined, NaN, Infinity, -Infinity])("shows a dash for %s, never NaN", (value) => {
    expect(formatNumber(value)).toBe("-");
  });
});

describe("formatPercent", () => {
  it("adds the percent sign and trims zeros", () => {
    expect(formatPercent(78.34)).toBe("78.34%");
    expect(formatPercent(80)).toBe("80%");
    expect(formatPercent(0)).toBe("0%");
    expect(formatPercent(89.2307, 1)).toBe("89.2%");
  });

  it("shows a dash for a missing value", () => {
    expect(formatPercent(null)).toBe("-");
    expect(formatPercent(undefined)).toBe("-");
    expect(formatPercent(NaN)).toBe("-");
  });
});

describe("formatCount", () => {
  it("separates thousands", () => {
    expect(formatCount(524991)).toBe("524,991");
    expect(formatCount(904)).toBe("904");
    expect(formatCount(0)).toBe("0");
  });

  it("shows a dash for a missing value", () => {
    expect(formatCount(null)).toBe("-");
    expect(formatCount(undefined)).toBe("-");
  });
});

describe("formatDateTime", () => {
  const iso = "2026-10-15T10:09:30+00:00";

  it("shows the time in the viewer's time zone", () => {
    expect(formatDateTime(iso, { timeZone: "UTC" })).toBe("15 Oct 2026, 10:09");
    expect(formatDateTime(iso, { timeZone: "Asia/Kolkata" })).toBe("15 Oct 2026, 15:39");
  });

  it("moves to the next day when the time zone is ahead", () => {
    expect(formatDateTime("2026-10-15T20:00:00+00:00", { timeZone: "Asia/Kolkata" })).toBe("16 Oct 2026, 01:30");
  });

  it("understands the microsecond timestamps the pipeline writes", () => {
    expect(formatDateTime("2026-10-15T10:13:25.123456+00:00", { timeZone: "UTC" })).toBe("15 Oct 2026, 10:13");
  });

  it.each([null, undefined, "", "not a date", "2026-13-45"])("shows a dash for %s", (value) => {
    expect(formatDateTime(value)).toBe("-");
  });
});
