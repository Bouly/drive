/**
 * Shape of the dataset the graph draws.
 *
 * Every indexed file is linked to every other one: what tells a pair apart
 * is the weight of its link, from 0 (nothing in common) to 1 (same content).
 */

export const FOLDER_MIMETYPE = "application/x-directory";

export type GraphFile = {
  id: string;
  title: string;
  mimetype: string;
  /** Bytes. */
  size: number;
  updated_at: string;
  creator: string;
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
};

export type GraphLink = {
  source: string;
  target: string;
  /** 0..1, how close the two files are. */
  weight: number;
  reason?: string;
};

export type GraphData = {
  files: GraphFile[];
  links: GraphLink[];
  /** The subjects of the user reading the graph; empty until they write one. */
  subjects: Subject[];
};
