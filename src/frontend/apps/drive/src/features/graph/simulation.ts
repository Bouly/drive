/**
 * Small force-directed layout, written here rather than pulled from d3 so the
 * graph stays dependency-free and readable.
 *
 * Repulsion goes through a quadtree (Barnes-Hut): a far-away cluster of nodes
 * pushes as one, so a frame costs N log N instead of N². Pairwise was fine on
 * a few hundred files and hopeless on four thousand, where it is eight million
 * pairs per frame.
 *
 * Nodes carry a depth `z` in [-1, 1] that the renderer turns into size,
 * opacity and parallax: a cheap 3D feel that runs on any machine.
 */

export type SimNode = {
  id: string;
  x: number;
  y: number;
  z: number;
  vx: number;
  vy: number;
  /** Base radius in world units. */
  r: number;
  degree: number;
  /** The packet of files this one hangs with, -1 when it hangs with none. */
  group: number;
  /** Set while the node is dragged: pins it in place. */
  fx: number | null;
  fy: number | null;
};

export type SimLink = {
  source: number;
  target: number;
  /** Resting length in world units. */
  length: number;
  strength: number;
  /**
   * Set on a pair with nothing in common: the link then only keeps the two
   * apart, never pulls them together, so strangers drift away from each other
   * instead of being packed in by the crowd of faint ties.
   */
  spacer?: boolean;
};

const REPULSION = 2600;
const REPULSION_MAX_DISTANCE = 420;
/**
 * How far a cell may be seen as one point: the ratio of its width to its
 * distance. 0.9 is the usual value, precise enough that the layout is
 * indistinguishable from the pairwise one.
 */
const BARNES_HUT_THETA = 0.9;
/** Below this, walking a tree costs more than comparing every pair. */
const QUADTREE_FROM = 400;
const CENTER_PULL = 0.004;
/**
 * How hard a file is drawn to the middle of its packet.
 *
 * Holding strangers apart is not enough to make packets: a spacer only acts
 * when two files are already too close, so it sets a floor and nothing more,
 * and the crowd of them averages out into one even cloud. Measured on a drive
 * of 164 files in 46 packets, laying it out with spacers alone leaves 83
 * pairs of packets whose centres sit closer together than the packets are
 * wide ‒ which is to say no packets at all. A pull towards the middle of its
 * own packet gathers each one first, and then the spacers have something to
 * push apart: the same drive comes out with 7.
 */
const GROUP_COHESION = 0.08;
/**
 * How hard two packets push each other apart.
 *
 * The graph only ever knows a file's closest neighbours ‒ on a drive of 164
 * files that is 8% of the pairs, and for the other 92% there is no link, no
 * spacer, nothing. The layout read that silence as "no opinion" rather than
 * "strangers", so nothing stopped a file about tango from settling against
 * five town-hall records it has no relation with whatsoever: measured, it sat
 * 84 units from the nearest of them and 68 from the one file it is actually
 * tied to.
 *
 * Packets push each other away, which is the only thing that can speak for
 * the pairs nobody encoded: a file with no packet is a packet of one, so it
 * is pushed out of other people's packets too. On that drive the tango file
 * moves to 472 from the town halls while staying beside its own tie, and the
 * files with a stranger closer than their own links fall from 96 to 57.
 */
const GROUP_REPULSION = 12000;
/** Past this, two packets are far enough to leave each other alone. */
const GROUP_REPULSION_MAX_DISTANCE = 1200;
/**
 * How many packets take part. Every pair of them is measured, so the work
 * grows with the square: the biggest ones are kept, and a drive that comes
 * apart into hundreds of tiny packets stops paying for the smallest.
 */
const MAX_BODIES = 120;
const VELOCITY_DECAY = 0.4;
const ALPHA_DECAY = 0.018;
const ALPHA_MIN = 0.004;

/**
 * A square of space holding either up to one node, or four smaller squares.
 * Each cell remembers how much push it carries and where its centre of mass
 * sits, which is what lets a distant crowd count as a single node.
 */
type Cell = {
  x: number;
  y: number;
  size: number;
  /** Sum of the strengths of the nodes inside. */
  weight: number;
  /** Centre of mass, weighted. */
  cx: number;
  cy: number;
  node: SimNode | null;
  children: (Cell | null)[] | null;
};

const strengthOf = (node: SimNode) => node.r;

const makeCell = (x: number, y: number, size: number): Cell => ({
  x,
  y,
  size,
  weight: 0,
  cx: 0,
  cy: 0,
  node: null,
  children: null,
});

/** Which of the four quarters of a cell a point falls in. */
const quadrant = (cell: Cell, x: number, y: number) =>
  (x >= cell.x + cell.size / 2 ? 1 : 0) + (y >= cell.y + cell.size / 2 ? 2 : 0);

const insert = (cell: Cell, node: SimNode, depth = 0) => {
  const weight = strengthOf(node);
  cell.cx += node.x * weight;
  cell.cy += node.y * weight;
  cell.weight += weight;

  // Two nodes on the same spot would split forever: past a depth they share
  // the cell and are pushed apart by the pairwise term below.
  if (cell.children === null && cell.node === null) {
    cell.node = node;
    return;
  }
  if (cell.children === null) {
    const waiting = cell.node;
    cell.node = null;
    cell.children = [null, null, null, null];
    if (waiting && depth < 24) {
      insert(cell, waiting, depth + 1);
    }
  }
  if (depth >= 24) {
    cell.node = cell.node ?? node;
    return;
  }
  const half = cell.size / 2;
  const index = quadrant(cell, node.x, node.y);
  const children = cell.children;
  const child =
    children[index] ??
    makeCell(cell.x + (index % 2) * half, cell.y + (index >= 2 ? half : 0), half);
  children[index] = child;
  insert(child, node, depth + 1);
};

const buildTree = (nodes: SimNode[]): Cell => {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    minX = Math.min(minX, node.x);
    minY = Math.min(minY, node.y);
    maxX = Math.max(maxX, node.x);
    maxY = Math.max(maxY, node.y);
  }
  const size = Math.max(maxX - minX, maxY - minY, 1) * 1.01;
  const root = makeCell(minX, minY, size);
  for (const node of nodes) {
    insert(root, node);
  }
  return root;
};

export class ForceSimulation {
  readonly nodes: SimNode[];
  readonly links: SimLink[];
  alpha = 1;

  constructor(nodes: SimNode[], links: SimLink[]) {
    this.nodes = nodes;
    this.links = links;
  }

  /** Runs one step. Returns false once the layout has settled. */
  tick(): boolean {
    if (this.alpha < ALPHA_MIN) {
      return false;
    }
    this.alpha += (0 - this.alpha) * ALPHA_DECAY;
    const { nodes, links, alpha } = this;

    if (nodes.length >= QUADTREE_FROM) {
      const tree = buildTree(nodes);
      for (const node of nodes) {
        this.pushAwayFrom(tree, node, alpha);
      }
    } else {
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          let d2 = dx * dx + dy * dy;
          if (d2 > REPULSION_MAX_DISTANCE * REPULSION_MAX_DISTANCE) {
            continue;
          }
          if (d2 < 1) {
            d2 = 1;
          }
          const d = Math.sqrt(d2);
          // Bigger nodes push harder so labels get room.
          const f = ((REPULSION * (a.r + b.r)) / 16) * alpha / d2;
          const fx = (dx / d) * f;
          const fy = (dy / d) * f;
          a.vx -= fx;
          a.vy -= fy;
          b.vx += fx;
          b.vy += fy;
        }
      }
    }

    for (const link of links) {
      const a = nodes[link.source];
      const b = nodes[link.target];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      if (link.spacer && d >= link.length) {
        continue;
      }
      const f = ((d - link.length) / d) * link.strength * alpha;
      const fx = dx * f;
      const fy = dy * f;
      a.vx += fx;
      a.vy += fy;
      b.vx -= fx;
      b.vy -= fy;
    }

    // Where each packet sits. Read into arrays indexed by packet rather than
    // maps: this runs on every node of every frame.
    let bodyCount = 0;
    for (const node of nodes) {
      if (node.group >= bodyCount) {
        bodyCount = node.group + 1;
      }
    }
    const sumX = new Float64Array(bodyCount);
    const sumY = new Float64Array(bodyCount);
    const held = new Int32Array(bodyCount);
    for (const node of nodes) {
      if (node.group >= 0) {
        sumX[node.group] += node.x;
        sumY[node.group] += node.y;
        held[node.group] += 1;
      }
    }

    // Every pair of packets is measured, so the work grows with the square:
    // only the biggest take part, and a drive that comes apart into hundreds
    // of tiny ones stops paying for the smallest.
    let bodies: number[] = [];
    for (let g = 0; g < bodyCount; g++) {
      if (held[g] > 1) {
        bodies.push(g);
      }
    }
    if (bodies.length > MAX_BODIES) {
      bodies.sort((a, b) => held[b] - held[a]);
      bodies = bodies.slice(0, MAX_BODIES);
    }
    const pushX = new Float64Array(bodyCount);
    const pushY = new Float64Array(bodyCount);
    for (let a = 0; a < bodies.length; a++) {
      const ga = bodies[a];
      const na = held[ga];
      const ax = sumX[ga] / na;
      const ay = sumY[ga] / na;
      for (let b = a + 1; b < bodies.length; b++) {
        const gb = bodies[b];
        const nb = held[gb];
        const dx = sumX[gb] / nb - ax;
        const dy = sumY[gb] / nb - ay;
        let d2 = dx * dx + dy * dy;
        if (d2 > GROUP_REPULSION_MAX_DISTANCE * GROUP_REPULSION_MAX_DISTANCE) {
          continue;
        }
        d2 = Math.max(d2, 1);
        const d = Math.sqrt(d2);
        // The same push reaches every file of a packet, so the packet moves
        // as one instead of being torn from its middle.
        const f = (GROUP_REPULSION * alpha) / d2;
        const ux = (dx / d) * f;
        const uy = (dy / d) * f;
        pushX[ga] -= ux * nb;
        pushY[ga] -= uy * nb;
        pushX[gb] += ux * na;
        pushY[gb] += uy * na;
      }
    }

    for (const node of nodes) {
      const group = node.group;
      // A file in no packet is gathered by nothing and pushed by nothing: it
      // has no neighbours to be held with, and none to be held off.
      if (group >= 0 && held[group] > 1) {
        node.vx += (sumX[group] / held[group] - node.x) * GROUP_COHESION * alpha;
        node.vy += (sumY[group] / held[group] - node.y) * GROUP_COHESION * alpha;
        node.vx += pushX[group];
        node.vy += pushY[group];
      }
      node.vx -= node.x * CENTER_PULL * alpha;
      node.vy -= node.y * CENTER_PULL * alpha;

      node.vx *= 1 - VELOCITY_DECAY;
      node.vy *= 1 - VELOCITY_DECAY;
      if (node.fx !== null && node.fy !== null) {
        node.x = node.fx;
        node.y = node.fy;
        node.vx = 0;
        node.vy = 0;
      } else {
        node.x += node.vx;
        node.y += node.vy;
      }
    }
    return true;
  }

  /**
   * Pushes one node away from a cell: from the cell as a whole when it is far
   * enough to read as one point, from its quarters otherwise.
   */
  private pushAwayFrom(cell: Cell, node: SimNode, alpha: number) {
    if (cell.weight === 0 || (cell.node !== null && cell.node === node)) {
      return;
    }
    const dx = cell.cx / cell.weight - node.x;
    const dy = cell.cy / cell.weight - node.y;
    let d2 = dx * dx + dy * dy;
    if (d2 > REPULSION_MAX_DISTANCE * REPULSION_MAX_DISTANCE) {
      return;
    }
    if (d2 < 1) {
      d2 = 1;
    }
    if (cell.children === null || cell.size * cell.size < BARNES_HUT_THETA * BARNES_HUT_THETA * d2) {
      const d = Math.sqrt(d2);
      const f = ((REPULSION * (node.r + cell.weight)) / 16) * alpha / d2;
      node.vx -= (dx / d) * f;
      node.vy -= (dy / d) * f;
      return;
    }
    for (const child of cell.children) {
      if (child) {
        this.pushAwayFrom(child, node, alpha);
      }
    }
  }

  /** Wakes the layout up after an interaction. */
  reheat(alpha = 0.25) {
    this.alpha = Math.max(this.alpha, alpha);
  }
}
