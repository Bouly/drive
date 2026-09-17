// Extracted from FileGraph.tsx: turning what the backend sends into a graph that can be drawn.
import { getMimeCategory } from "@gouvfr-lasuite/ui-components";
import { FOLDER_MIMETYPE, GraphData, GraphFile, GraphLink } from "./types";
import { ForceSimulation, SimLink, SimNode } from "../simulation";
import { findClusters, mutualCloseness } from "./clusters";
import { colorOfName, normalize } from "./naming";

/** Smallest and largest dot, in world units: the range recency is mapped to. */
const NODE_MIN_RADIUS = 3;
const NODE_MAX_RADIUS = 7;
/**
 * How much bigger a dot is drawn on a drive that has room to spare.
 *
 * The forces hold two files a fixed distance apart whatever the drive holds,
 * so a small drive lays its files out over the same kind of space as a large
 * one and the camera has to zoom out just as far: measured on a drive of
 * thirty-three, a dot landed on four pixels ‒ a void with dust in it. Marks
 * are not part of the physics here (the layout came out the same size at four
 * times the radius), so they are free to answer to how many share the stage.
 *
 * Measured: at 2.2 a drive of thirty-three draws eight-pixel dots with
 * twenty-two pixels between them; past six hundred files the gaps close and
 * the range goes back to what it was.
 */
const MARK_ROOM_PIVOT = 500;
const MARK_ROOM_MAX = 2.2;
const markRoom = (count: number) =>
  Math.min(MARK_ROOM_MAX, Math.max(1, Math.sqrt(MARK_ROOM_PIVOT / Math.max(1, count))));
/** How much further apart two files of two different packets are held. */
const CROSS_GROUP_SPREAD = 2.2;
/** How much closer two files of the same packet rest. */
const INSIDE_GROUP_TIGHTEN = 0.6;

/** Passages kept per file for the search: enough to know what it says. */
const SEARCH_PASSAGES = 8;

/**
 * Below this closeness two files count as strangers: no edge is drawn, and
 * their link only holds them apart (see buildModel). Above it the edge fades
 * in with the closeness. Without it the stage would be a solid mesh.
 *
 * One exception: whatever the threshold says, every file keeps its closest
 * neighbour. A topic held by two files only ‒ a photo of a bicycle and a PDF
 * on bicycle upkeep ‒ is looser than a topic held by ten, and a single
 * threshold would leave those two stranded on opposite sides of the stage.
 *
 * Measured on a drive of 39 files across four subjects: at 0.35 the stage
 * draws 183 of its 741 pairs, only 8 of them between two subjects ‒ and those
 * eight are real (CNIL rulings on staff files next to employment notices).
 */
export const LINK_MIN_CLOSENESS = 0.35;

/**
 * Above this weight two files are the same document twice, not two documents
 * about the same thing.
 *
 * A drive fills up with copies nobody meant to keep: a file downloaded twice,
 * a version saved under a new name, the same attachment from two colleagues.
 * The graph is the one screen that can see them, because it is the only one
 * that reads what is inside.
 */
export const DUPLICATE_WEIGHT = 0.95;

/**
 * How far below its own best subject a file may sit and still be counted in
 * another one.
 *
 * The backend keeps a file in every subject it answers at a quarter of that
 * subject's best answer. A quarter is a fair bar against one subject and a
 * poor one against twelve: a note on social protection during training came
 * out sitting in "Events" at 0.38 while it sat in "subvention" at 1.00, and
 * the summary on its own card said ‒ correctly ‒ that it is not about events.
 * Seven subjects for one file is not seven answers, it is no answer.
 *
 * A share is comparable from one subject to the next, the API normalises it
 * for that, so the question can be asked the only way it makes sense: does
 * this file belong here nearly as well as it belongs anywhere? A file somebody
 * pinned is theirs to place and is never weighed.
 */
export const SUBJECT_LOYALTY = 0.6;

/**
 * The raw answer a subject's best file must reach for the subject to hold
 * anybody at all. Mirrors GRAPH_TOPIC_RERANK_FLOOR on the backend, which stops
 * storing them; this stops drawing the ones already stored.
 *
 * Measured on a drive of case law: the subjects it is really about answered
 * between 0.09 and 0.99, and the six nothing answered between 0.002 and 0.024.
 * "Public procurement" sat at 0.007 and held a file named "ghjgh".
 */
export const SUBJECT_FLOOR = 0.05;

export type Neighbor = { node: number; link: GraphLink };

const categoryOf = (file: GraphFile) => {
  if (file.mimetype === FOLDER_MIMETYPE) {
    return "folder";
  }
  const extension = file.title.includes(".") ? file.title.split(".").pop() : null;
  const category = getMimeCategory(file.mimetype, extension) as string;
  return category === "docs" ? "doc" : category;
};

export const buildModel = (data: GraphData, placed?: Map<string, SimNode>) => {
  const index = new Map(data.files.map((file, i) => [file.id, i]));
  // Degree counts the links worth seeing, so a node's size still means
  // something now that every file is linked to every other one.
  const degree = data.files.map(() => 0);
  const neighbors: Neighbor[][] = data.files.map(() => []);

  const pairs: { source: number; target: number; link: GraphLink }[] = [];
  for (const link of data.links) {
    const source = index.get(link.source);
    const target = index.get(link.target);
    if (source === undefined || target === undefined) {
      continue;
    }
    pairs.push({ source, target, link });
  }
  const closeness = mutualCloseness(
    pairs.map((pair) => pair.link.weight),
    pairs.map((pair) => [pair.source, pair.target] as [number, number]),
    data.files.length,
  );

  // The closest pair of each file, kept whatever the threshold says.
  const best = data.files.map(() => -1);
  pairs.forEach(({ source, target }, i) => {
    for (const node of [source, target]) {
      if (best[node] < 0 || closeness[i] > closeness[best[node]]) {
        best[node] = i;
      }
    }
  });

  const links: SimLink[] = [];
  const linkMeta: GraphLink[] = [];
  const linkCloseness: number[] = [];
  const linkTies: boolean[] = [];
  // The passages a file's ties quote: what the search reads besides its name.
  const passages: string[][] = data.files.map(() => []);
  pairs.forEach(({ source, target, link }, i) => {
    const tie = closeness[i] >= LINK_MIN_CLOSENESS || best[source] === i || best[target] === i;
    // A pair kept as somebody's closest neighbour is drawn and pulled like
    // one at the threshold, otherwise the only tie of a small topic would be
    // a line nobody can see.
    const close = tie ? Math.max(closeness[i], LINK_MIN_CLOSENESS) : closeness[i];
    if (tie) {
      degree[source]++;
      degree[target]++;
    }
    neighbors[source].push({ node: target, link });
    neighbors[target].push({ node: source, link });
    if (tie && link.reason) {
      for (const node of [source, target]) {
        if (passages[node].length < SEARCH_PASSAGES && !passages[node].includes(link.reason)) {
          passages[node].push(link.reason);
        }
      }
    }
    if (tie) {
      // A close pair rests short and pulls hard; a looser one rests far and
      // barely pulls, so the closeness alone shapes the layout.
      links.push({
        source,
        target,
        length: 30 + (1 - close) * (1 - close) * 240,
        strength: 0.01 + close * close * 0.25,
      });
    } else {
      // Strangers: the link never pulls them together, it only keeps them at
      // arm's length, and the less they share the further apart they sit.
      links.push({
        source,
        target,
        length: 150 + (1 - close / LINK_MIN_CLOSENESS) * 130,
        strength: 0.02,
        spacer: true,
      });
    }
    linkMeta.push(link);
    linkCloseness.push(close);
    linkTies.push(tie);
  });

  let seed = 7;
  const rand = () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296;
  };
  const categories = data.files.map(categoryOf);

  // Copies of one another: the same fingerprint, or a pair so close that
  // nothing but sameness explains it.
  const duplicates: number[][] = data.files.map(() => []);
  const twin = (a: number, b: number) => {
    if (a !== b && !duplicates[a].includes(b)) {
      duplicates[a].push(b);
    }
  };
  const byFingerprint = new Map<string, number[]>();
  data.files.forEach((file, i) => {
    if (file.content) {
      byFingerprint.set(file.content, [...(byFingerprint.get(file.content) ?? []), i]);
    }
  });
  for (const same of byFingerprint.values()) {
    same.forEach((a) => same.forEach((b) => twin(a, b)));
  }
  pairs.forEach(({ source, target, link }) => {
    if (link.weight >= DUPLICATE_WEIGHT) {
      twin(source, target);
      twin(target, source);
    }
  });
  // What the reader may do with each file, which is the colour of its dot.
  // An empty right ‒ a file reached through a link alone ‒ is a value of its
  // own and not a missing one, so it is named rather than left blank.
  const ownerships = data.files.map((file) => file.role || "none");
  /**
   * The size of a dot says when the file was added to the drive: the newest
   * on the stage is the largest, the oldest the smallest.
   *
   * The date it arrived, not the date it was last saved: those are two
   * different questions, and the one a reader asks of a graph is which of
   * these documents are new to the drive. A file renamed this morning is not
   * a new file, and used to be drawn as one.
   *
   * The scale is the drive's own span rather than a fixed number of days, so
   * the whole range is always in use ‒ on a drive filled in one afternoon the
   * dots still separate the morning from the evening, and on one built over
   * three years the last month still stands out. A drive filled all at once
   * draws every dot the same size, which is the truth about it.
   *
   * It replaces the degree, which said how many ties a file had: that number
   * is already drawn, by the threads leaving the dot, and it left the files
   * somebody is working on indistinguishable from the rest. The degree is
   * still carried on the node, for the card and the naming.
   */
  const times = data.files.map((file) => Date.parse(file.created_at ?? file.updated_at) || 0);
  let newest = -Infinity;
  let oldest = Infinity;
  for (const time of times) {
    newest = Math.max(newest, time);
    oldest = Math.min(oldest, time);
  }
  const span = Math.max(1, newest - oldest);
  // Square root, so recency follows the area of a dot rather than its width.
  const freshness = (i: number) => Math.sqrt(Math.max(0, times[i] - oldest) / span);
  const room = markRoom(data.files.length);
  const nodes: SimNode[] = data.files.map((file, i) => {
    // A file already on screen keeps its place when the graph is refetched,
    // so a new file simply appears instead of everything moving.
    const before = placed?.get(file.id);
    return {
      id: file.id,
      x: before ? before.x : (rand() - 0.5) * 320,
      y: before ? before.y : (rand() - 0.5) * 320,
      z: rand() * 2 - 1,
      vx: 0,
      vy: 0,
      r:
        room *
        (categories[i] === "folder"
          ? NODE_MAX_RADIUS
          : NODE_MIN_RADIUS + (NODE_MAX_RADIUS - NODE_MIN_RADIUS) * freshness(i)),
      degree: degree[i],
      // Filled in below, once the packets are known.
      group: -1,
      fx: null,
      fy: null,
    };
  });

  // Title and quoted passages, folded once: the search reads this instead of
  // the titles alone, so a word that appears inside a file finds it.
  const searchText = data.files.map((file, i) => normalize(`${file.title} ${passages[i].join(" ")}`));

  // Subjects belong to whoever writes them, and the drive never invents one.
  // Groups read off the links used to stand in while a drive had none, named
  // after the words their files happened to share: that was the automatic
  // topic, it put words in the reader's mouth, and a name nobody chose was
  // read as one somebody had. A drive with no subject now shows none.
  const taken = new Set<number>();
  // Inside a folder, a subject holding none of its files would sit in the
  // legend on a count of zero: the drive has that subject, this folder does
  // not. Over the whole drive they all stay, an empty one included ‒ it was
  // just written and its files are on their way.
  // Subjects the drive answers at all. A share is measured against the
  // subject's own best answer, so a subject nothing is about crowns its least
  // bad file at 1.00: its shares say nothing and neither does its membership.
  const answered = new Set(
    data.subjects
      .filter((subject) => (subject.strength ?? Infinity) >= SUBJECT_FLOOR)
      .map((subject) => subject.id),
  );

  // The subjects each file really belongs to, best first.
  const belongs = data.files.map((file) => {
    const held = (file.topics ?? []).filter((t) => t.pinned || answered.has(t.id));
    const floor = (held[0]?.score ?? 0) * SUBJECT_LOYALTY;
    return new Set(held.filter((t) => t.pinned || t.score >= floor).map((t) => t.id));
  });

  const subjects = data.scope
    ? data.subjects.filter((subject) => belongs.some((held) => held.has(subject.id)))
    : data.subjects;
  const rank = new Map(subjects.map((subject, i) => [subject.id, i]));
  // A file can be in several subjects but is drawn in one ‒ the one it fits
  // best, which the API sends first.
  const clusters = data.files.map((file, i) => {
    // Use the same accepted memberships as the subject filters. Old rows
    // can still put a rejected subject first in the API response.
    const closest = file.topics?.find((topic) => belongs[i].has(topic.id));
    return closest ? (rank.get(closest.id) ?? -1) : -1;
  });
  const topics = subjects.map((subject, group) => ({
    label: subject.name,
    // Whether the drive answers this subject at all. A subject it does not is
    // not broken and not empty by accident: nothing here is about it, and the
    // reader is the only one who can decide whether to keep or drop it.
    answered: answered.has(subject.id),
    color: colorOfName(subject.name, taken),
    files: data.files.map((_, i) => i).filter((i) => clusters[i] === group),
    // `members` is everything the subject holds, which is what its count and
    // its filter must say: the two CVs sat in both "cv" and "curriculum
    // vitae", were drawn in the second, and "cv" looked like it had found
    // nothing.
    members: data.files.map((_, i) => i).filter((i) => belongs[i].has(subject.id)),
  }));

  // Where the files sit is read off the links themselves, whether or not
  // anybody has written a subject: files that hang together rest closer, and
  // are held further from the ones they have nothing to do with, so the stage
  // comes out in small packets instead of one crowd. Subjects say what a
  // packet is called and what color it takes ‒ never where it lands, so a
  // drive with no subject is still laid out and not piled up.
  const groups = findClusters(data.files.length, links, linkCloseness, linkTies);
  groups.forEach((group, i) => {
    nodes[i].group = group;
  });
  links.forEach((link) => {
    const group = groups[link.source];
    const same = group >= 0 && group === groups[link.target];
    if (link.spacer) {
      link.length *= same ? 1 : CROSS_GROUP_SPREAD;
    } else if (same) {
      link.length *= INSIDE_GROUP_TIGHTEN;
    }
  });

  const simulation = new ForceSimulation(nodes, links);
  // Small graphs settle before the first frame, so the initial framing
  // matches the final layout; large ones keep animating into place.
  const warmup = nodes.length <= 150 ? 320 : 40;
  for (let i = 0; i < warmup; i++) {
    simulation.tick();
  }

  // Emphasis (0 dimmed .. 1 lit) per node, eased frame by frame.
  const emphasis = nodes.map(() => 1);

  return {
    data,
    index,
    nodes,
    links,
    linkMeta,
    linkCloseness,
    linkTies,
    neighbors,
    categories,
    ownerships,
    duplicates,
    belongs,
    clusters,
    topics,
    subjects,
    searchText,
    simulation,
    emphasis,
  };
};

export type Model = ReturnType<typeof buildModel>;
