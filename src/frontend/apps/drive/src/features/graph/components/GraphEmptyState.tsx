import { useTranslation } from "react-i18next";
import Link from "next/link";
import gridEmpty from "@/assets/grid_empty.png";
import { GraphScope } from "../data/types";

/**
 * Shown instead of the graph when there is nothing to draw: the same
 * illustration as an empty explorer folder, and nothing else. The graph itself
 * carries no page title either, so this one does not announce one.
 *
 * A folder holding no analysed file says so, and offers the whole drive.
 */
export const GraphEmptyState = ({ scope }: { scope?: GraphScope | null }) => {
  const { t } = useTranslation();
  return (
    <div className="file-graph file-graph--light">
      <div className="file-graph__stage">
        <div className="file-graph__empty">
          <img src={gridEmpty.src} alt="" className="file-graph__empty-image" />
          <p className="file-graph__empty-caption">
            {scope ? t("graph.scope.empty") : t("graph.empty.caption")}
          </p>
          {scope ? (
            <Link href="/explorer/graph" className="file-graph__empty-cta">
              {t("graph.scope.leave")}
            </Link>
          ) : (
            <p className="file-graph__empty-cta">{t("graph.empty.cta")}</p>
          )}
        </div>
      </div>
    </div>
  );
};
