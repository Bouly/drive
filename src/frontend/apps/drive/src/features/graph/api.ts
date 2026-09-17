import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchAPI } from "@/features/api/fetchApi";
import { APIError } from "@/features/api/APIError";
import { GraphData, Ownership } from "./data/types";

/** Shape returned by GET /api/v1.0/graph/ (see backend graph/api.py). */
type ApiGraph = {
  files: {
    id: string;
    title: string;
    mimetype: string;
    size: number;
    created_at: string;
    updated_at: string;
    creator: string;
    creator_id: string;
    role: Ownership;
    content: string;
    status: "indexed" | "pending" | "empty" | "failed" | "skipped" | "idle";
    // The subjects this file is in, the one it fits best first.
    topics: { id: string; score: number; pinned: boolean }[];
  }[];
  links: {
    source: string;
    target: string;
    weight: number;
    kind: "semantic" | "lexical" | "copy" | "folder";
    evidence?: string;
  }[];
  topics: { id: string; name: string; description: string; strength?: number }[];
  scope: { id: string; title: string; path: string[] } | null;
  folders: { id: string; title: string; trail: string[] }[];
};

/** Converts the API payload to the dataset shape the graph component draws. */
export const toGraphData = (api: ApiGraph): GraphData => ({
  files: api.files.map((file) => ({
    id: file.id,
    title: file.title,
    mimetype: file.mimetype,
    size: file.size,
    created_at: file.created_at,
    updated_at: file.updated_at,
    creator: file.creator,
    creator_id: file.creator_id,
    role: file.role,
    content: file.content,
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
  scope: api.scope ? { ...api.scope, path: api.scope.path ?? [api.scope.title] } : null,
  // An older backend sends none: the page then simply offers no picker.
  folders: api.folders ?? [],
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

/** ``folderId`` draws that folder alone, at any depth; undefined draws the drive. */
export const fetchGraph = async (folderId?: string): Promise<GraphData> => {
  const response = await fetchAPI("graph/", folderId ? { params: { folder: folderId } } : undefined);
  return toGraphData((await response.json()) as ApiGraph);
};

/** How often the graph is refetched while files are still being analysed. */
const PENDING_POLL_MS = 4000;

export const useGraph = (folderId?: string, enabled = true) =>
  useQuery({
    enabled,
    // The folder is part of the key: leaving one must not read the cached
    // graph of the whole drive, and the other way round.
    queryKey: ["graph", folderId ?? null],
    queryFn: () => fetchGraph(folderId),
    // Always refetch when the page opens: a file trashed or uploaded a moment
    // ago must show up right away. The cached graph is drawn meanwhile.
    staleTime: 0,
    refetchOnMount: "always",
    // A missing folder is an answer, not a hiccup: asking again changes nothing.
    retry: (count, error) => !(error instanceof APIError && error.code === 404) && count < 3,
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
  // Every graph is invalidated, the scoped ones too: they share the subjects.
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

/**
 * What a card says in words: a summary of the file on the subject being
 * looked at, and one sentence per neighbour saying what the pair shares.
 */
export type FileBrief = {
  subject: string;
  summary: string;
  links: { id: string; sentence: string }[];
};

/**
 * Read when a card opens, never with the graph: it costs a reading of the
 * files, and a drive of nine hundred would spend it on the cards nobody
 * opens. The backend keeps every answer a week, so reopening a card is free.
 */
export const useFileBrief = (fileId: string | null, subject: string, neighbours: string[]) => {
  const asked = neighbours.join(",");
  return useQuery({
    enabled: Boolean(fileId),
    queryKey: ["graph-brief", fileId, subject, asked],
    queryFn: async () => {
      const response = await fetchAPI(`graph/files/${fileId}/brief/`, {
        params: { subject, with: asked },
      });
      return (await response.json()) as FileBrief;
    },
    // The answer depends on the file and the subject, both of which are in
    // the key: nothing is gained by asking again while the card is open.
    staleTime: Infinity,
    retry: false,
  });
};

/**
 * Send a file to the trash from the graph, the way the explorer does.
 *
 * Offered on a neighbour a file shares its whole content with: two copies of
 * the same document are the one thing a graph can point at that a folder
 * cannot, and the answer to it is to keep one.
 */
export const useDeleteFile = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => fetchAPI(`items/${id}/`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["graph"] });
      // The explorer lists the same files: it must not keep showing one that
      // has just left.
      queryClient.invalidateQueries({ queryKey: ["items"] });
    },
  });
};
