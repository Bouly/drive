import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import type { NextPage } from "next";
import type { AppProps } from "next/app";
import {
  ContextMenuProvider,
  CunninghamProvider,
} from "@gouvfr-lasuite/ui-components";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import {
  MutationCache,
  Query,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

import "../styles/globals.scss";
import "../features/i18n/initI18n";
import {
  addToast,
  ToasterItem,
} from "@/features/ui/components/toaster/Toaster";
import { APIError, errorToString } from "@/features/api/APIError";
import Head from "next/head";
import { useTranslation } from "react-i18next";
import { AnalyticsProvider } from "@/features/analytics/AnalyticsProvider";
import { capitalizeRegion } from "@/features/i18n/utils";
import { ConfigProvider } from "@/features/config/ConfigProvider";
import {
  removeQuotes,
  useCunninghamTheme,
} from "@/features/ui/cunningham/useCunninghamTheme";
import type { Appearance, ThemeMode } from "@/features/ui/theme/useThemeMode";
import { ResponsiveDivs } from "@/features/ui/components/responsive/ResponsiveDivs";
import { themeFor, useThemeMode } from "@/features/ui/theme/useThemeMode";
import { FeedbackFooterMobile } from "@/features/feedback/Feedback";
import { useRouter } from "next/router";

export type NextPageWithLayout<P = object, IP = P> = NextPage<P, IP> & {
  getLayout?: (page: ReactElement) => ReactNode;
};

type AppPropsWithLayout = AppProps & {
  Component: NextPageWithLayout;
};
const onError = (error: Error, query: unknown) => {
  if ((query as Query).meta?.noGlobalError) {
    return;
  }

  // Don't show toast for 401/403 errors because the app handles them by
  // redirecting to the 401/403 page. So we don't want to show a toast before
  // the redirect, it would feels buggy.
  if (error instanceof APIError) {
    if (error.code === 401) {
      return;
    }
    if (error.code === 403 && !(query as Query).meta?.showErrorOn403) {
      return;
    }
  }

  addToast(
    <ToasterItem type="error">
      <span>{errorToString(error)}</span>
    </ToasterItem>,
  );
};

const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    onError: (error, variables, context, mutation) => {
      onError(error, mutation);
    },
  }),
  queryCache: new QueryCache({
    onError: (error, query) => onError(error, query),
  }),
  defaultOptions: {
    queries: {
      retry: false,
    },
  },
});

export interface AppContextType {
  /** The theme the deployment configured, light or dark left aside. */
  theme: string;
  setTheme: (theme: string) => void;
  /** What the reader chose to see, and what it resolves to. */
  themeMode: ThemeMode;
  setThemeMode: (mode: ThemeMode) => void;
  appearance: Appearance;
}

const AppContext = createContext<AppContextType | undefined>(undefined);

export const useAppContext = () => {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error("useAppContext must be used within an AppContextProvider");
  }
  return context;
};

export default function MyApp({
  Component,
  pageProps,
  router,
}: AppPropsWithLayout) {
  const [theme, setTheme] = useState<string>("anct-light");
  const { mode: themeMode, setMode: setThemeMode, appearance } = useThemeMode();

  return (
    <AppContext.Provider
      value={{ theme, setTheme, themeMode, setThemeMode, appearance }}
    >
      <MyAppInner Component={Component} pageProps={pageProps} router={router} />
    </AppContext.Provider>
  );
}

const MyAppInner = ({ Component, pageProps }: AppPropsWithLayout) => {
  // Use the layout defined at the page level, if available
  const getLayout = Component.getLayout ?? ((page) => page);
  const { t, i18n } = useTranslation();
  const { theme, appearance } = useAppContext();
  const router = useRouter();
  const themeTokens = useCunninghamTheme();
  // The theme the deployment configured, drawn the way the reader asked.
  const currentTheme = themeFor(theme, appearance);

  useEffect(() => {
    document.documentElement.dataset.appearance = appearance;
  }, [appearance]);

  const isSdk = useMemo(
    () => router.pathname.startsWith("/sdk"),
    [router.pathname],
  );

  return (
    <>
      <Head>
        <title>{t("app_title")}</title>
        <link
          rel="icon"
          href={removeQuotes(themeTokens.components.favicon.src)}
          type="image/png"
        />
      </Head>
      <QueryClientProvider client={queryClient}>
        <CunninghamProvider
          currentLocale={capitalizeRegion(i18n.language)}
          theme={currentTheme}
        >
          <ConfigProvider>
            <AnalyticsProvider>
              <ContextMenuProvider>
                {getLayout(<Component {...pageProps} />)}
              </ContextMenuProvider>
              <ResponsiveDivs />
              {!isSdk && <FeedbackFooterMobile />}
            </AnalyticsProvider>
          </ConfigProvider>
        </CunninghamProvider>
        {process.env.NODE_ENV === "development" && (
          <ReactQueryDevtools initialIsOpen={false} />
        )}
      </QueryClientProvider>
    </>
  );
};
