import { useTranslation } from "react-i18next";
import gridEmpty from "@/assets/grid_empty.png";

/**
 * Shown instead of the graph when the user has no analysed file yet: same
 * header as the graph, same illustration as an empty explorer folder.
 */
export const GraphEmptyState = () => {
  const { t } = useTranslation();
  return (
    <div className="file-graph file-graph--light">
      <header className="file-graph__header">
        <div className="file-graph__heading">
          <h1 className="file-graph__title">{t("graph.title")}</h1>
          <p className="file-graph__hint">{t("graph.hint")}</p>
        </div>
      </header>
      <div className="file-graph__stage">
        <div className="file-graph__empty">
          <img src={gridEmpty.src} alt="" className="file-graph__empty-image" />
          <p className="file-graph__empty-caption">{t("graph.empty.caption")}</p>
          <p className="file-graph__empty-cta">{t("graph.empty.cta")}</p>
        </div>
      </div>
    </div>
  );
};
