import { useQuery } from "@tanstack/react-query";
import { fetchAPI } from "@/features/api/fetchApi";
import { GraphData } from "./data/fakeGraph";

/** Shape returned by GET /api/v1.0/graph/ (see backend graph/api.py). */
type ApiGraph = {
  files: {
    id: string;
    title: string;
    mimetype: string;
    size: number;
    updated_at: string;
    creator: string;
    cluster: string | null;
    status: "indexed" | "pending" | "empty" | "failed" | "skipped" | "idle";
  }[];
  links: {
    source: string;
    target: string;
    weight: number;
    kind: "semantic" | "lexical" | "copy" | "folder";
    surprising: boolean;
    reason: string;
    evidence: string;
  }[];
  clusters: { id: string; label: string; keywords: string[] }[];
};

const NO_TOPIC = { id: "no-topic", label: "Sans sujet" };

/** Converts the API payload to the dataset shape the graph component draws. */
export const toGraphData = (api: ApiGraph): GraphData => {
  const clusters = api.clusters.map(({ id, label }) => ({ id, label }));
  if (api.files.some((file) => !file.cluster)) {
    clusters.push(NO_TOPIC);
  }
  return {
    clusters,
    files: api.files.map((file) => ({
      id: file.id,
      title: file.title,
      mimetype: file.mimetype,
      size: file.size,
      updated_at: file.updated_at,
      creator: file.creator,
      cluster: file.cluster ?? NO_TOPIC.id,
      status: file.status,
    })),
    links: mergeReciprocalLinks(api.links).map((link) => ({
      source: link.source,
      target: link.target,
      weight: link.weight,
      kind: link.surprising ? "surprise" : "semantic",
      // The passage that justifies the link says more than the generic reason;
      // the similarity is already shown next to it.
      reason: link.evidence || link.reason || undefined,
    })),
  };
};

/**
 * Links are stored per file, so two close files usually point to each other:
 * the graph draws one edge per pair, keeping the strongest of the two.
 */
const mergeReciprocalLinks = (links: ApiGraph["links"]) => {
  const byPair = new Map<string, ApiGraph["links"][number]>();
  for (const link of links) {
    const key = [link.source, link.target].sort().join("|");
    const current = byPair.get(key);
    if (!current || link.weight > current.weight) {
      byPair.set(key, link);
    }
  }
  return [...byPair.values()];
};

export const fetchGraph = async (): Promise<GraphData> => {
  const response = await fetchAPI("graph/");
  return toGraphData((await response.json()) as ApiGraph);
};

/** How often the graph is refetched while files are still being analysed. */
const PENDING_POLL_MS = 4000;

export const useGraph = () =>
  useQuery({
    queryKey: ["graph"],
    queryFn: fetchGraph,
    // Always refetch when the page opens: a file trashed or uploaded a moment
    // ago must show up right away. The cached graph is drawn meanwhile.
    staleTime: 0,
    refetchOnMount: "always",
    // While files are being analysed, poll so their links appear on their own.
    refetchInterval: ({ state }) =>
      state.data?.files.some((file) => file.status === "pending") ? PENDING_POLL_MS : false,
  });
