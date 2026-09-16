import { useTranslation } from "react-i18next";
import gridEmpty from "@/assets/grid_empty.png";

/**
 * Shown instead of the graph when the user has no analysed file yet: the same
 * illustration as an empty explorer folder, and nothing else. The graph itself
 * carries no page title either, so this one does not announce one.
 */
export const GraphEmptyState = () => {
  const { t } = useTranslation();
  return (
    <div className="file-graph file-graph--light">
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
