/**
 * Whether the application is drawn light or dark, and who decides.
 *
 * The choice is the reader's, kept in their browser, and "système" follows
 * the operating system so the drive turns dark with everything else at night.
 * What it selects is a Cunningham theme ‒ the design system of La Suite ships
 * a dark set of tokens next to the light one ‒ so the whole interface follows,
 * not one page of it.
 */

import { useCallback, useEffect, useState } from "react";

export type ThemeMode = "system" | "light" | "dark";
/** What a mode resolves to once the operating system has had its say. */
export type Appearance = "light" | "dark";

export const THEME_MODE_KEY = "drive-theme-mode";
export const THEME_MODES: ThemeMode[] = ["system", "light", "dark"];

const DARK_QUERY = "(prefers-color-scheme: dark)";

/** The mode kept in this browser, "system" when nothing was ever chosen. */
export const readThemeMode = (): ThemeMode => {
  try {
    const stored = window.localStorage.getItem(THEME_MODE_KEY);
    return THEME_MODES.includes(stored as ThemeMode) ? (stored as ThemeMode) : "system";
  } catch {
    // Private browsing, or storage turned off: the system decides.
    return "system";
  }
};

export const systemAppearance = (): Appearance =>
  typeof window !== "undefined" && window.matchMedia(DARK_QUERY).matches ? "dark" : "light";

export const appearanceOf = (mode: ThemeMode): Appearance =>
  mode === "system" ? systemAppearance() : mode;

/**
 * The Cunningham theme to hand to the provider: the one the deployment
 * configured, redrawn in the chosen appearance.
 *
 * A theme name carries both the family and the appearance ‒ "dsfr-light",
 * "anct-dark" ‒ and the plain "default" theme is the light one of its family.
 */
export const themeFor = (configured: string, appearance: Appearance): string => {
  const family = configured.replace(/-(light|dark)$/, "");
  if (family === "default" || family === "dark" || family === "") {
    return appearance === "dark" ? "dark" : "default";
  }
  return `${family}-${appearance}`;
};

/** The chosen mode, what it resolves to, and a way to change it. */
export const useThemeMode = () => {
  // The server renders the light one; the script in _document has already put
  // the right class on the page, and the first effect below catches up.
  const [mode, setStoredMode] = useState<ThemeMode>("system");
  const [appearance, setAppearance] = useState<Appearance>("light");

  useEffect(() => {
    const chosen = readThemeMode();
    setStoredMode(chosen);
    setAppearance(appearanceOf(chosen));
  }, []);

  useEffect(() => {
    if (mode !== "system") {
      return;
    }
    const media = window.matchMedia(DARK_QUERY);
    const follow = () => setAppearance(systemAppearance());
    media.addEventListener("change", follow);
    return () => media.removeEventListener("change", follow);
  }, [mode]);

  const setMode = useCallback((next: ThemeMode) => {
    setStoredMode(next);
    setAppearance(appearanceOf(next));
    try {
      window.localStorage.setItem(THEME_MODE_KEY, next);
    } catch {
      // Nothing to keep it in: the choice lasts as long as the page does.
    }
  }, []);

  return { mode, setMode, appearance };
};
