import {
  DropdownMenu,
  Icon,
  IconSize,
  useDropdownMenu,
  UserMenu,
  Button,
} from "@gouvfr-lasuite/ui-components";
import { useAuth } from "@/features/auth/Auth";
import { logout } from "@/features/auth/Auth";
import { LanguagePickerUserMenu } from "@/features/layouts/components/header/Header";
import { LANGUAGES } from "@/features/i18n/conf";
import { AnonymousCTA } from "../anonymous-cta/AnonymousCTA";
import { useTranslation } from "react-i18next";
import { useClipboard } from "@/hooks/useCopyToClipboard";
import { ThemePicker } from "@/features/ui/theme/ThemePicker";
import { THEME_MODES } from "@/features/ui/theme/useThemeMode";
import { useAppContext } from "@/pages/_app";

export const UserProfile = () => {
  const { user } = useAuth();
  return (
    <div className="user-profile">
      {user ? (
        <UserMenu
          user={user}
          logout={logout}
          termOfServiceUrl="https://docs.numerique.gouv.fr/docs/8e298e03-c95f-44c7-be4a-ffb618af1854/"
          actions={
            <>
              <ThemePicker />
              <LanguagePickerUserMenu />
            </>
          }
        />
      ) : (
        <>
          <AnonymousDropdownMenu />
          <AnonymousCTA />
        </>
      )}
    </div>
  );
};

const AnonymousDropdownMenu = () => {
  const { isOpen, setIsOpen } = useDropdownMenu();
  const { t, i18n } = useTranslation();
  const copyToClipboard = useClipboard();
  const { themeMode, setThemeMode } = useAppContext();

  return (
    <DropdownMenu
      isOpen={isOpen}
      onOpenChange={setIsOpen}
      options={[
        {
          icon: <Icon name="link" size={IconSize.SMALL} />,
          label: t("anonymous_dropdown_menu.copy_link"),
          callback: () => {
            copyToClipboard(window.location.href);
          },
        },
        {
          icon: <Icon name="language" size={IconSize.SMALL} />,
          label: t("anonymous_dropdown_menu.languages"),
          children: LANGUAGES.map((language) => ({
            label: language.label,
            callback: () => {
              i18n.changeLanguage(language.value);
            },
          })),
        },
        {
          icon: <Icon name="contrast" size={IconSize.SMALL} />,
          label: t("theme.title"),
          children: THEME_MODES.map((mode) => ({
            label: t(`theme.modes.${mode}`),
            isChecked: mode === themeMode,
            callback: () => setThemeMode(mode),
          })),
        },
      ]}
    >
      <Button
        icon={<Icon name="more_horiz" />}
        variant="tertiary"
        onClick={() => setIsOpen(!isOpen)}
        data-testid="anonymous-dropdown-menu"
      />
    </DropdownMenu>
  );
};
