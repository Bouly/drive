import { Html, Head, Main, NextScript } from "next/document";
import { THEME_MODE_KEY } from "@/features/ui/theme/useThemeMode";

/**
 * Settles the appearance before anything is drawn.
 *
 * The page is served the same to everyone, and the reader's choice only lives
 * in their browser: without this, a drive set to dark would flash white on
 * every load, the time React reads the choice back.
 */
const settleAppearance = `
(function () {
  try {
    var mode = window.localStorage.getItem(${JSON.stringify(THEME_MODE_KEY)}) || "system";
    var dark = mode === "dark" || (mode === "system" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.appearance = dark ? "dark" : "light";
  } catch (error) {
    document.documentElement.dataset.appearance = "light";
  }
})();
`;

export default function Document() {
  return (
    <Html>
      <Head>
        <meta name="robots" content="noindex" />
        {/* Tells the browser which way to paint its own surfaces (form
            controls, scrollbars) and holds the ground until the stylesheet
            lands. */}
        <style>{`
          :root[data-appearance="dark"] { color-scheme: dark; background: #1b1b23; }
          :root[data-appearance="light"] { color-scheme: light; background: #ffffff; }
        `}</style>
        <script dangerouslySetInnerHTML={{ __html: settleAppearance }} />
      </Head>
      <body>
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}
