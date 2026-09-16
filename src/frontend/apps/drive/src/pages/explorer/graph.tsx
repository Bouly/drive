import { useRouter } from "next/router";
import { useEffect } from "react";
import { getGlobalExplorerLayout } from "@/features/layouts/components/explorer/ExplorerLayout";
import { FileGraph } from "@/features/graph/components/FileGraph";
import { GraphEmptyState } from "@/features/graph/components/GraphEmptyState";
import { useGraph } from "@/features/graph/api";

export default function GraphPage() {
  const router = useRouter();
  // ``?folder=<id>`` draws that folder alone; without it, the whole drive.
  const folderParam = router.query.folder;
  const folderId = Array.isArray(folderParam) ? folderParam[0] : folderParam;
  // The query is empty until the router has parsed the url: asking before
  // that would fetch the whole drive, then the folder a moment later.
  const { data, isLoading, isError } = useGraph(folderId, router.isReady);

  // A folder that is gone, or was never readable, falls back on the drive
  // rather than leaving the page on an error nobody can act upon.
  useEffect(() => {
    if (isError && folderId) {
      router.replace("/explorer/graph");
    }
  }, [isError, folderId, router]);

  if (!router.isReady || isLoading || (isError && folderId)) {
    return null;
  }

  // Nothing analysed yet in what we are drawing: say so rather than a demo.
  if (!data || data.files.length === 0) {
    return <GraphEmptyState scope={data?.scope ?? null} />;
  }
  return <FileGraph data={data} />;
}

GraphPage.getLayout = getGlobalExplorerLayout;
