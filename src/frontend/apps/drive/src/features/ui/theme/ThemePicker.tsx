import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  Button,
  DropdownMenu,
  Icon,
  IconSize,
  useDropdownMenu,
} from "@gouvfr-lasuite/ui-components";
import { useAppContext } from "@/pages/_app";
import { THEME_MODES, type ThemeMode } from "./useThemeMode";

/** The icon each mode is recognised by, in the menu and on the button. */
const ICONS: Record<ThemeMode, string> = {
  system: "contrast",
  light: "light_mode",
  dark: "dark_mode",
};

/**
 * The reader's choice of appearance, next to the language in the user menu:
 * the two things about the interface they may want to change, in one place.
 */
export const ThemePicker = () => {
  const { t } = useTranslation();
  const { themeMode, setThemeMode, appearance } = useAppContext();
  const { isOpen, setIsOpen } = useDropdownMenu();

  const options = useMemo(
    () =>
      THEME_MODES.map((mode) => ({
        icon: <Icon name={ICONS[mode]} size={IconSize.SMALL} />,
        label: t(`theme.modes.${mode}`),
        value: mode,
        isChecked: mode === themeMode,
        callback: () => setThemeMode(mode),
      })),
    [t, themeMode, setThemeMode],
  );

  return (
    <DropdownMenu
      options={options}
      isOpen={isOpen}
      onOpenChange={setIsOpen}
      selectedValues={[themeMode]}
    >
      <Button
        onClick={() => setIsOpen(!isOpen)}
        className="drive__theme-picker"
        size="small"
        color="neutral"
        variant="tertiary"
        aria-label={t("theme.label", { mode: t(`theme.modes.${themeMode}`) })}
        icon={<Icon name={isOpen ? "arrow_drop_up" : "arrow_drop_down"} />}
        iconPosition="right"
      >
        {/* The button shows what is on screen, which is the useful thing when
            the mode is "système" and the answer depends on the hour. */}
        <Icon name={ICONS[themeMode === "system" ? appearance : themeMode]} size={IconSize.SMALL} />
      </Button>
    </DropdownMenu>
  );
};
