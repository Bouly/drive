import { ForceSimulation, SimNode, SimLink } from "./src/features/graph/simulation";

const build = (n: number, perNode = 24) => {
  const nodes: SimNode[] = [];
  for (let i = 0; i < n; i++) {
    nodes.push({ id: `${i}`, x: Math.random() * 1200 - 600, y: Math.random() * 1200 - 600, z: 0, vx: 0, vy: 0, r: 6, degree: perNode, group: i % 12, fx: null, fy: null });
  }
  const links: SimLink[] = [];
  for (let i = 0; i < n; i++) {
    for (let k = 1; k <= perNode / 2; k++) {
      links.push({ source: i, target: (i + k * 7) % n, length: 60, strength: 0.06 });
    }
  }
  return new ForceSimulation(nodes, links);
};

for (const n of [500, 1000, 2000, 4000]) {
  const sim = build(n);
  const start = performance.now();
  for (let i = 0; i < 30; i++) { sim.alpha = 0.5; sim.tick(); }
  const per = (performance.now() - start) / 30;
  console.log(`${n} points : ${per.toFixed(1)} ms par image  (${(1000 / per).toFixed(0)} images/s)`);
}
