// How close a pair of files really is, and which files hang together.
import { SimLink } from "../simulation";
import { CLUSTER_ROUNDS } from "./theme";

/**
 * How close a pair is, in 0..1, judged from each of its two files rather than
 * from the graph as a whole.
 *
 * Two files with nothing in common already share a fair amount of cosine
 * similarity, and worse, some files are close to everyone: a long, general
 * text sits near the middle of the embedding space and turns up next to
 * anything. On this drive a photo of a bicycle came out closer to a physics
 * syllabus (0.485) than to the PDF on bicycle upkeep (0.455), and no global
 * threshold can fix that ‒ the syllabus is above average with everybody.
 *
 * So each file scores a pair against its own habits (how many standard
 * deviations above its average this pair stands), and the pair keeps the
 * lower of the two scores. A file that is everyone's neighbour has a high
 * average, so its links score low from its side and the pair is dropped:
 * being close to everything stops counting as being close to something.
 */
const MUTUAL_Z_LOW = 0.6;
const MUTUAL_Z_HIGH = 2.2;

export const mutualCloseness = (weights: number[], ends: [number, number][], count: number) => {
  // Too few pairs for a file to have habits: the raw value is all there is.
  if (weights.length < 6) {
    return weights.map((weight) => Math.min(1, Math.max(0, weight)));
  }
  const sum = new Float64Array(count);
  const squares = new Float64Array(count);
  const seen = new Float64Array(count);
  weights.forEach((weight, i) => {
    for (const node of ends[i]) {
      sum[node] += weight;
      squares[node] += weight * weight;
      seen[node]++;
    }
  });
  const mean = new Float64Array(count);
  const deviation = new Float64Array(count);
  for (let i = 0; i < count; i++) {
    mean[i] = seen[i] ? sum[i] / seen[i] : 0;
    deviation[i] = Math.sqrt(Math.max(1e-9, (seen[i] ? squares[i] / seen[i] : 0) - mean[i] * mean[i]));
  }
  return weights.map((weight, i) => {
    const [a, b] = ends[i];
    const z = Math.min((weight - mean[a]) / deviation[a], (weight - mean[b]) / deviation[b]);
    return Math.min(1, Math.max(0, (z - MUTUAL_Z_LOW) / (MUTUAL_Z_HIGH - MUTUAL_Z_LOW)));
  });
};

/**
 * Which files hang together, by label propagation over the ties: each file
 * repeatedly takes the group its closest neighbours share, weighted by how
 * close they are. One group index per file, -1 for a file in none.
 *
 * This decides where files sit and nothing else. It used to name them too,
 * which is how a drive ended up showing subjects nobody had written; the
 * names come from the subjects now, and the shape of the stage from here.
 */
export const findClusters = (count: number, links: SimLink[], weights: number[], ties: boolean[]) => {
  const adjacency: { node: number; weight: number }[][] = Array.from({ length: count }, () => []);
  links.forEach((link, i) => {
    // Groups are read off the ties: a pair the graph holds apart says nothing
    // about what its two files are about.
    if (!ties[i]) {
      return;
    }
    adjacency[link.source].push({ node: link.target, weight: weights[i] });
    adjacency[link.target].push({ node: link.source, weight: weights[i] });
  });

  const label = Array.from({ length: count }, (_, i) => i);
  for (let round = 0; round < CLUSTER_ROUNDS; round++) {
    let moved = false;
    for (let i = 0; i < count; i++) {
      const score = new Map<number, number>();
      for (const { node, weight } of adjacency[i]) {
        score.set(label[node], (score.get(label[node]) ?? 0) + weight);
      }
      let best = label[i];
      let bestScore = score.get(best) ?? 0;
      for (const [candidate, value] of score) {
        // Ties go to the lowest label so the result does not depend on order.
        if (value > bestScore || (value === bestScore && candidate < best)) {
          best = candidate;
          bestScore = value;
        }
      }
      if (best !== label[i]) {
        label[i] = best;
        moved = true;
      }
    }
    if (!moved) {
      break;
    }
  }

  const members = new Map<number, number>();
  for (const value of label) {
    members.set(value, (members.get(value) ?? 0) + 1);
  }
  // A file on its own is no group: nothing pulls it in or holds it off.
  const ranked = [...members.entries()]
    .filter(([, size]) => size > 1)
    .sort((a, b) => b[1] - a[1])
    .map(([value]) => value);
  const rank = new Map(ranked.map((value, i) => [value, i]));
  return label.map((value) => rank.get(value) ?? -1);
};
