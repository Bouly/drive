// Extracted from FileGraph.tsx: the colors of the stage, and what the reader chose to keep.

/**
 * One color per file family, taken from the ui-kit file icons (mime-*.svg) so
 * the graph matches the explorer; keys are ui-kit MimeCategory values plus
 * "folder". Families without an icon color use DSFR palette tokens.
 */
const CATEGORY_COLORS: Record<string, string> = {
  docs: "#3677CC",
  doc: "#3677CC",
  calc: "#5A8228",
  powerpoint: "#AE6257",
  pdf: "#6D778C",
  image: "#6969DF", // brand-500
  video: "#3A7EA0",
  audio: "#E57036", // warning-400
  archive: "#EB9970", // warning-300
  folder: "#75758A", // gray-500
  other: "#A9A9BF", // gray-300
};
export const CATEGORY_ORDER = ["folder", "doc", "calc", "powerpoint", "pdf", "image", "video", "archive", "other"];

/**
 * One color per subject. Sixteen of them, so a drive can hold sixteen
 * subjects before two share a hue.
 *
 * Any two subjects can end up side by side on the stage, so the hues are held
 * to the all-pairs floors of the data-viz palette: telling two apart must not
 * depend on color vision. Each theme has its own steps, picked for its
 * background rather than lightened from the other one.
 *
 * They were searched, not picked: a greedy walk over the OKLCH wheel (hue by
 * lightness, each at the most chroma sRGB holds there) keeping the set whose
 * worst pair is furthest apart. Hand-picked eights do not survive that test ‒
 * the reference eight of the data-viz palette drops to a distance of 3.2 for
 * a color-blind reader and 7.1 for everyone else.
 *
 * The first eight are that search; the next eight continue it, seeded with
 * them so none of the eight moved. Measured as CIEDE2000 over every pair, for
 * a reader with normal vision, with deuteranopia and with protanopia, the
 * light stage holds 12.14 / 3.78 / 8.77 at eight colors and 9.44 / 3.78 /
 * 8.77 at sixteen, the dark one 12.75 / 6.00 / 7.10 and 11.00 / 6.00 / 7.10:
 * what the sixteenth costs is room for a reader who has all three cones, and
 * the floor that binds ‒ the one a color-blind reader reads ‒ never moves. It
 * was already set by two of the original eight. Going on to twenty is what
 * finally costs them: protanopia drops to 7.52 on the light stage.
 */
export const CLUSTER_COLORS: Record<"dark" | "light", string[]> = {
  dark: [
    "#B0005C", "#65A800", "#332CFF", "#009ED9", "#FF199D", "#955900", "#8D00C1", "#8A6FFF",
    "#07715E", "#D10C31", "#0FA462", "#148908", "#E311CC", "#0D9491", "#B20A8F", "#FD453C",
  ],
  light: [
    "#A20054", "#6EB600", "#2F00FC", "#00ACEB", "#FF53A8", "#955900", "#8100B1", "#8A6FFF",
    "#066754", "#0C9430", "#DF10C2", "#CE0C30", "#0C8B93", "#FD4543", "#C00B89", "#10AF5B",
  ],
};

/** Mixes a hex color with white; the dark stage needs brighter families. */
export const lighten = (hex: string, amount: number) => {
  const value = parseInt(hex.slice(1), 16);
  const channel = (shift: number) => Math.round(((value >> shift) & 255) + (255 - ((value >> shift) & 255)) * amount);
  return `#${[16, 8, 0].map((s) => channel(s).toString(16).padStart(2, "0")).join("")}`;
};

export type Theme = {
  bg: string;
  dot: string;
  link: string;
  /** Color of a group of files that hang together, by palette slot. */
  clusterColor: (slot: number) => string;
  label: string;
  labelHalo: string;
  ring: string;
  glow: boolean;
  categoryColor: (category: string) => string;
};
// Values are DSFR palette tokens (cunningham-tokens.css): gray-*, brand-*, warning-*.
export const THEMES: Record<"dark" | "light", Theme> = {
  dark: {
    bg: "#1B1B23", // gray-900
    dot: "rgba(117, 117, 138, 0.28)", // gray-500
    link: "169, 169, 191", // gray-300
    label: "#F0F0F3", // gray-050
    labelHalo: "rgba(27, 27, 35, 0.85)",
    ring: "rgba(27, 27, 35, 0.9)",
    glow: true,
    clusterColor: (slot) => CLUSTER_COLORS.dark[slot],
    categoryColor: (category) => lighten(CATEGORY_COLORS[category] ?? CATEGORY_COLORS.other, 0.3),
  },
  light: {
    bg: "#F0F0F3", // gray-050
    dot: "rgba(117, 117, 138, 0.25)",
    link: "105, 105, 125", // gray-550
    label: "#25252F", // gray-850
    labelHalo: "rgba(240, 240, 243, 0.92)",
    ring: "#FFFFFF",
    glow: false,
    clusterColor: (slot) => CLUSTER_COLORS.light[slot],
    categoryColor: (category) => CATEGORY_COLORS[category] ?? CATEGORY_COLORS.other,
  },
};
export const THEME_STORAGE_KEY = "drive-graph-theme";
export const PANEL_STORAGE_KEY = "drive-graph-subjects";

export const hexToRgb = (hex: string) => {
  const value = parseInt(hex.slice(1), 16);
  return `${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}`;
};

export const readStoredTheme = (): "dark" | "light" => {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
};

/** The subjects panel opens by default; whoever closed it gets it closed back. */
export const readStoredPanel = (): boolean => {
  try {
    return window.localStorage.getItem(PANEL_STORAGE_KEY) !== "closed";
  } catch {
    return true;
  }
};
