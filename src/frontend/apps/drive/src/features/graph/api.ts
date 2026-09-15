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
    })),
    links: api.links.map((link) => ({
      source: link.source,
      target: link.target,
      weight: link.weight,
      kind: link.surprising ? "surprise" : "semantic",
      reason: link.reason || link.evidence || undefined,
    })),
  };
};

export const fetchGraph = async (): Promise<GraphData> => {
  const response = await fetchAPI("graph/");
  return toGraphData((await response.json()) as ApiGraph);
};

export const useGraph = () =>
  useQuery({
    queryKey: ["graph"],
    queryFn: fetchGraph,
    staleTime: 60_000,
  });
