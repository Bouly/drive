import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchAPI } from "@/features/api/fetchApi";
import { GraphData } from "./data/types";

/** Shape returned by GET /api/v1.0/graph/ (see backend graph/api.py). */
type ApiGraph = {
  files: {
    id: string;
    title: string;
    mimetype: string;
    size: number;
    updated_at: string;
    creator: string;
    status: "indexed" | "pending" | "empty" | "failed" | "skipped" | "idle";
    // The subjects this file is in, the one it fits best first.
    topics: { id: string; score: number; pinned: boolean; strength: number }[];
  }[];
  links: {
    source: string;
    target: string;
    weight: number;
    kind: "semantic" | "lexical" | "copy" | "folder";
    evidence?: string;
  }[];
  topics: { id: string; name: string; description: string }[];
};

/** Converts the API payload to the dataset shape the graph component draws. */
export const toGraphData = (api: ApiGraph): GraphData => ({
  files: api.files.map((file) => ({
    id: file.id,
    title: file.title,
    mimetype: file.mimetype,
    size: file.size,
    updated_at: file.updated_at,
    creator: file.creator,
    status: file.status,
    topics: file.topics,
  })),
  links: mergeReciprocalLinks(api.links).map((link) => ({
    source: link.source,
    target: link.target,
    weight: link.weight,
    // The passage that justifies the link says more than a generic sentence;
    // the similarity is already shown next to it.
    reason: link.evidence || undefined,
  })),
  subjects: api.topics,
});

/**
 * Links are stored per file, so every pair points both ways: the graph draws
 * one edge per pair, keeping the strongest of the two.
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

/**
 * Writing a subject: each call answers once the files have been sorted into
 * it, so the graph only has to be read again.
 */
export const useSubjects = () => {
  const queryClient = useQueryClient();
  const refresh = { onSuccess: () => queryClient.invalidateQueries({ queryKey: ["graph"] }) };

  return {
    create: useMutation({
      mutationFn: (subject: { name: string; description?: string }) =>
        fetchAPI("graph/topics/", { method: "POST", body: JSON.stringify(subject) }),
      ...refresh,
    }),
    rename: useMutation({
      mutationFn: ({ id, ...subject }: { id: string; name: string; description: string }) =>
        fetchAPI(`graph/topics/${id}/`, { method: "PATCH", body: JSON.stringify(subject) }),
      ...refresh,
    }),
    remove: useMutation({
      mutationFn: (id: string) => fetchAPI(`graph/topics/${id}/`, { method: "DELETE" }),
      ...refresh,
    }),
    pin: useMutation({
      mutationFn: ({ topic, item }: { topic: string; item: string }) =>
        fetchAPI(`graph/topics/${topic}/files/`, { method: "POST", body: JSON.stringify({ item }) }),
      ...refresh,
    }),
    unpin: useMutation({
      mutationFn: ({ topic, item }: { topic: string; item: string }) =>
        fetchAPI(`graph/topics/${topic}/files/${item}/`, { method: "DELETE" }),
      ...refresh,
    }),
  };
};
