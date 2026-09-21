import { useAppContext } from "@/pages/_app";
import { tokens } from "@/styles/cunningham-tokens";
import { themeFor } from "@/features/ui/theme/useThemeMode";

export const useCunninghamTheme = () => {
  const { theme, appearance } = useAppContext();
  // The tokens of the theme actually drawn: a dark drive reads its own
  // favicon and its own logo off them, not the light ones.
  const current = themeFor(theme, appearance);

  return tokens.themes[
    current as keyof typeof tokens.themes
  ] as (typeof tokens.themes)["dsfr-light"];
};

// Once the cunningham sass generated string is fixed, we can remove this function.
export const removeQuotes = (str: string) => {
  return str.replace(/^['"]|['"]$/g, "");
};
