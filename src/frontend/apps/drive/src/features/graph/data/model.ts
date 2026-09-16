// Extracted from FileGraph.tsx: turning what the backend sends into a graph that can be drawn.
import { getMimeCategory } from "@gouvfr-lasuite/ui-components";
import { FOLDER_MIMETYPE, GraphData, GraphFile, GraphLink } from "./types";
import { ForceSimulation, SimLink, SimNode } from "../simulation";
import { mutualCloseness } from "./clusters";
import { colorOfName, normalize } from "./naming";

/** Smallest and largest dot, in world units: the range a degree is mapped to. */
const NODE_MIN_RADIUS = 3;
const NODE_MAX_RADIUS = 7;
/** How much further apart two files of two different groups are held. */
const CROSS_GROUP_SPREAD = 1.25;
/** How much closer two files of the same group rest. */
const INSIDE_GROUP_TIGHTEN = 0.85;

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
  // A dot reads its degree against the busiest file of this drive, not
  // against a fixed count: on a graph where everyone has twenty ties, a
  // fixed scale saturates and every file ends up the same big blob.
  const busiest = Math.max(1, ...degree);
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
        categories[i] === "folder"
          ? NODE_MAX_RADIUS
          : NODE_MIN_RADIUS + (NODE_MAX_RADIUS - NODE_MIN_RADIUS) * Math.sqrt(degree[i] / busiest),
      degree: degree[i],
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
  const rank = new Map(data.subjects.map((subject, i) => [subject.id, i]));
  // A file can be in several subjects but is drawn in one ‒ the one it fits
  // best, which the API sends first.
  const clusters = data.files.map((file) => {
    const closest = file.topics?.[0];
    return closest ? (rank.get(closest.id) ?? -1) : -1;
  });
  const topics = data.subjects.map((subject, group) => ({
    label: subject.name,
    color: colorOfName(subject.name, taken),
    files: data.files.map((_, i) => i).filter((i) => clusters[i] === group),
    // `members` is everything the subject holds, which is what its count and
    // its filter must say: the two CVs sat in both "cv" and "curriculum
    // vitae", were drawn in the second, and "cv" looked like it had found
    // nothing.
    members: data.files
      .map((_, i) => i)
      .filter((i) => data.files[i].topics?.some((t) => t.id === subject.id)),
  }));

  // Now that the groups are known, the layout can say so: a pair inside a
  // group rests closer, a pair across two groups is held further apart, and
  // the subjects come apart on their own.
  links.forEach((link) => {
    const group = clusters[link.source];
    const same = group >= 0 && group === clusters[link.target];
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
    clusters,
    topics,
    searchText,
    simulation,
    emphasis,
  };
};

export type Model = ReturnType<typeof buildModel>;
