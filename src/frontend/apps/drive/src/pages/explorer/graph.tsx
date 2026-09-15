import { useMemo } from "react";
import { getGlobalExplorerLayout } from "@/features/layouts/components/explorer/ExplorerLayout";
import { FileGraph } from "@/features/graph/components/FileGraph";
import { useGraph } from "@/features/graph/api";
import { buildFakeGraph } from "@/features/graph/data/fakeGraph";

export default function GraphPage() {
  const { data, isLoading } = useGraph();
  const demo = useMemo(buildFakeGraph, []);

  if (isLoading) {
    return null;
  }

  // Until files are indexed, the page shows the demo dataset so the graph
  // can be explored anyway; it is labelled as such.
  const isDemo = !data || data.files.length === 0;
  return <FileGraph data={isDemo ? demo : data} demo={isDemo} />;
}

GraphPage.getLayout = getGlobalExplorerLayout;
