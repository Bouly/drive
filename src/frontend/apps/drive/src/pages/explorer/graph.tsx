import { getGlobalExplorerLayout } from "@/features/layouts/components/explorer/ExplorerLayout";
import { FileGraph } from "@/features/graph/components/FileGraph";

export default function GraphPage() {
  return <FileGraph />;
}

GraphPage.getLayout = getGlobalExplorerLayout;
