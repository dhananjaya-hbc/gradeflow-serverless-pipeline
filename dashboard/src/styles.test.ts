import { describe, expect, it } from "vitest";
import { CHART_COLORS } from "./lib/palette";
import css from "./styles.css?raw";

type Tokens = Record<string, string>;

function tokens(block: string): Tokens {
  return Object.fromEntries([...block.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1]!, m[2]!]));
}

// The first :root block is the light theme; the one inside the dark media query overrides it.
const light = tokens(css.slice(0, css.indexOf("@media")));
const darkBlock = css.slice(css.indexOf("prefers-color-scheme: dark"), css.indexOf("*,\n*::before"));
const dark: Tokens = { ...light, ...tokens(darkBlock) };

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

// Foreground on background pairs the stylesheet really uses (WCAG AA for text: 4.5).
const TEXT_PAIRS: [string, string][] = [
  ["text", "bg"],
  ["text", "surface"],
  ["text", "row-hover"],
  ["muted", "bg"],
  ["muted", "surface"],
  ["muted", "row-hover"],
  ["muted", "info-bg"],
  ["accent", "bg"],
  ["accent", "surface"],
  ["accent-text", "accent"],
  ["success-fg", "success-bg"],
  ["danger-fg", "danger-bg"],
  ["warning-fg", "warning-bg"],
  ["info-fg", "info-bg"],
  ["neutral-fg", "neutral-bg"],
  ["text", "info-bg"],
];

describe.each([
  ["light", light],
  ["dark", dark],
] as const)("%s theme", (_name, theme) => {
  it.each(TEXT_PAIRS)("%s on %s has at least 4.5:1 contrast", (fg, bg) => {
    expect(theme[fg], `missing token --${fg}`).toBeDefined();
    expect(theme[bg], `missing token --${bg}`).toBeDefined();
    expect(contrast(theme[fg]!, theme[bg]!)).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps every chart colour at least 3:1 against the surface", () => {
    for (const color of CHART_COLORS) {
      expect(contrast(color, theme["surface"]!), color).toBeGreaterThanOrEqual(3);
    }
  });
});

describe("stylesheet", () => {
  it("defines a dark theme that actually differs from the light one", () => {
    expect(dark["bg"]).not.toBe(light["bg"]);
    expect(dark["text"]).not.toBe(light["text"]);
  });
  it("styles every status tone the labels can produce", () => {
    for (const tone of ["success", "danger", "warning", "info", "neutral"]) {
      expect(css).toContain(`.badge-${tone}`);
    }
  });
});
