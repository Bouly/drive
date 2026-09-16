// Extracted from FileGraph.tsx: how close a pair of files really is.

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
