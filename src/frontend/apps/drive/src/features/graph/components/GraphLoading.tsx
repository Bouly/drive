import { useTranslation } from "react-i18next";

/**
 * Shown while the drive is being read.
 *
 * The page used to render nothing at all, which on nine hundred files is
 * several seconds of blank screen: indistinguishable from a broken one. The
 * dots settle into place instead, so the wait shows what is coming.
 */
export const GraphLoading = () => {
  const { t } = useTranslation();
  return (
    <div className="file-graph file-graph--dark">
      <div className="file-graph__stage">
        <div className="file-graph__loading">
          <span className="file-graph__loading-dots" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <p className="file-graph__loading-text">{t("graph.loading")}</p>
        </div>
      </div>
    </div>
  );
};
