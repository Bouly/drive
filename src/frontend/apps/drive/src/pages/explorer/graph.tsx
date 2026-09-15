import { getGlobalExplorerLayout } from "@/features/layouts/components/explorer/ExplorerLayout";
import { FileGraph } from "@/features/graph/components/FileGraph";
import { GraphEmptyState } from "@/features/graph/components/GraphEmptyState";
import { useGraph } from "@/features/graph/api";

export default function GraphPage() {
  const { data, isLoading } = useGraph();

  if (isLoading) {
    return null;
  }

  // Nothing analysed yet for this user: say so rather than draw a demo.
  if (!data || data.files.length === 0) {
    return <GraphEmptyState />;
  }
  return <FileGraph data={data} />;
}

GraphPage.getLayout = getGlobalExplorerLayout;
