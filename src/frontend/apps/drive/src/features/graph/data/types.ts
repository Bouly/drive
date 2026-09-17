/**
 * Shape of the dataset the graph draws.
 *
 * Every indexed file is linked to every other one: what tells a pair apart
 * is the weight of its link, from 0 (nothing in common) to 1 (same content).
 */

export const FOLDER_MIMETYPE = "application/x-directory";

/**
 * What the reader may do with a file, which is the colour of its dot: a file
 * of their own, one they may write in, one they only read, or one they reach
 * through a link and hold no right on at all.
 */
export type Ownership = "owner" | "administrator" | "editor" | "reader" | "";

export type GraphFile = {
  id: string;
  title: string;
  mimetype: string;
  /** Bytes. */
  size: number;
  /** When the file was added to the drive: the size of its dot. */
  created_at: string;
  /** When it was last saved, which is a different question. */
  updated_at: string;
  creator: string;
  /** The author, as an id: two colleagues can share a name. */
  creator_id: string;
  /** What the reader holds on it: the colour of its dot. */
  role: Ownership;
  /**
   * Fingerprint of the passages the file holds. Two files carrying the same
   * one are the same document twice, which a link's weight cannot say.
   */
  content?: string;
  /**
   * Where the file stands: "indexed" (content analysed), "pending" (being
   * analysed, drawn pulsing), "empty" (analysed, no text inside), "failed"
   * (analysis broke), "skipped" (nothing to analyse) or "idle" (never
   * analysed and not queued for it).
   */
  status?: "indexed" | "pending" | "empty" | "failed" | "skipped" | "idle";
  /** Subjects this file fell into, closest first. */
  topics?: { id: string; score: number; pinned: boolean }[];
};

/** A subject its owner wrote; files fall into it on their own. */
export type Subject = {
  id: string;
  name: string;
  description: string;
  /**
   * How well the drive answers this subject at all, in the reranker's own
   * units. A file's score is a share of this, so this is the only number that
   * says whether the subject means anything on this drive.
   */
  strength?: number;
};

export type GraphLink = {
  source: string;
  target: string;
  /** 0..1, how close the two files are. */
  weight: number;
  reason?: string;
};

/** The folder a graph is restricted to, when it is not the whole drive. */
export type GraphScope = {
  id: string;
  title: string;
  /** The folders above it the reader can open, root first, ending on itself. */
  path: string[];
};

/** A folder the graph can be pointed at, named by the trail above it. */
export type GraphFolder = {
  id: string;
  title: string;
  /** Root first, ending with the folder itself. */
  trail: string[];
};

export type GraphData = {
  files: GraphFile[];
  links: GraphLink[];
  /** The subjects of the user reading the graph; empty until they write one. */
  subjects: Subject[];
  /** The folder being drawn, or null for the whole drive. */
  scope: GraphScope | null;
  /** Every folder that could be drawn instead, for the picker. */
  folders: GraphFolder[];
};
