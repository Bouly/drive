/**
 * What the layout looks like, in numbers, without opening a browser.
 *
 * The stage came out as a void with dust in it on a small drive: the files
 * spread over two thousand world units while a dot is five of them, so the
 * camera zooms out to fit and every mark lands on two or three pixels.
 *
 * This measures the one ratio that decides it ‒ the typical gap between two
 * neighbouring files, divided by the size of a file ‒ and what that gives on
 * a real screen. Run it against a dump of the graph API:
 *
 *   node --loader ts-node/esm geombench.ts <graph.json>
 */
import { readFileSync } from "node:fs";
import { buildModel } from "./src/features/graph/data/model";
import type { GraphData } from "./src/features/graph/data/types";

// The stage of a laptop, minus the left panel and the app header.
const VIEW_W = 1208;
const VIEW_H = 678;
const TOP_INSET = 56;
const MIN_SCALE = 0.35;
const MAX_SCALE = 14;

const median = (values: number[]) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] ?? 0;
};

const report = (label: string, data: GraphData, grow = 1) => {
  const model = buildModel(data);
  // Bigger marks also push harder, so the layout answers back: the only way
  // to know what a radius gives is to settle it and look.
  if (grow !== 1) {
    for (const node of model.nodes) {
      node.r *= grow;
    }
    model.simulation.reheat(1);
  }
  // Settle it the way the page does before the camera looks.
  for (let i = 0; i < 400; i++) {
    model.simulation.tick();
  }
  const nodes = model.nodes;

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    minX = Math.min(minX, node.x - node.r);
    maxX = Math.max(maxX, node.x + node.r);
    minY = Math.min(minY, node.y - node.r);
    maxY = Math.max(maxY, node.y + node.r);
  }
  const spanX = maxX - minX;
  const spanY = maxY - minY;

  // The gap a reader actually sees: how far the nearest other file sits.
  const gaps = nodes.map((a) => {
    let best = Infinity;
    for (const b of nodes) {
      if (a !== b) {
        best = Math.min(best, Math.hypot(a.x - b.x, a.y - b.y));
      }
    }
    return best;
  });
  const gap = median(gaps);
  const radius = median(nodes.map((n) => n.r));

  // The same box, with the strays left out: where the files actually are.
  const at = (values: number[], p: number) => {
    const sorted = [...values].sort((a, b) => a - b);
    return sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
  };
  const xs = nodes.map((n) => n.x);
  const ys = nodes.map((n) => n.y);
  for (const keep of [1, 0.98, 0.94, 0.9]) {
    const edge = (1 - keep) / 2;
    const bx = at(xs, 1 - edge) - at(xs, edge);
    const by = at(ys, 1 - edge) - at(ys, edge);
    const s = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.min(VIEW_W / bx, (VIEW_H - TOP_INSET) / by) * 0.8));
    const ms = s <= 1 ? s : s ** 0.55;
    console.log(`    garde ${(keep * 100).toFixed(0)}% : boîte ${Math.round(bx)}x${Math.round(by)}`
      + ` -> échelle ${s.toFixed(2)}, point ${(radius * 2 * ms).toFixed(1)} px`);
  }

  const usable = VIEW_H - TOP_INSET;
  const scale = Math.min(
    MAX_SCALE,
    Math.max(MIN_SCALE, Math.min(VIEW_W / spanX, usable / spanY) * 0.8),
  );
  // Marks shrink with the camera below scale 1, as the page draws them.
  const markScale = scale <= 1 ? scale : scale ** 0.55;

  console.log(`\n${label}  (${nodes.length} fichiers)`);
  console.log(`  étendue du monde   ${Math.round(spanX)} x ${Math.round(spanY)}`);
  console.log(`  écart type         ${gap.toFixed(0)} unités`);
  console.log(`  rayon d'un point   ${radius.toFixed(1)} unités`);
  console.log(`  écart / rayon      ${(gap / radius).toFixed(1)}  (visé : 6 à 14)`);
  console.log(`  échelle caméra     ${scale.toFixed(2)}`);
  console.log(`  à l'écran          point ${(radius * 2 * markScale).toFixed(1)} px,` +
    ` écart ${(gap * scale).toFixed(0)} px  (visé : point >= 7 px)`);
};

const dump = JSON.parse(readFileSync(process.argv[2], "utf8"));
// The API calls them topics; the page calls them subjects.
const raw = { ...dump, subjects: dump.topics ?? [], folders: [], scope: null };
const GROW = Number(process.argv[3] ?? 1);
report(`drive réel`, raw as GraphData, GROW);

// The same drive, multiplied, to see whether the shape holds as it grows.
for (const times of [4, 12, 27]) {
  const files = [];
  const links = [];
  for (let copy = 0; copy < times; copy++) {
    for (const file of raw.files) {
      files.push({ ...file, id: `${copy}-${file.id}` });
    }
    for (const link of raw.links) {
      links.push({ ...link, source: `${copy}-${link.source}`, target: `${copy}-${link.target}` });
    }
  }
  report(`drive x${times}`, { ...raw, files, links } as GraphData, GROW);
}

