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

    // Each packet gathers around its own middle before the stage spreads them.
    const sumX = new Map<number, number>();
    const sumY = new Map<number, number>();
    const held = new Map<number, number>();
    for (const node of nodes) {
      if (node.group < 0) {
        continue;
      }
      sumX.set(node.group, (sumX.get(node.group) ?? 0) + node.x);
      sumY.set(node.group, (sumY.get(node.group) ?? 0) + node.y);
      held.set(node.group, (held.get(node.group) ?? 0) + 1);
    }

    for (const node of nodes) {
      const count = held.get(node.group) ?? 0;
      // A file on its own is no packet: nothing to gather around.
      if (count > 1) {
        node.vx += ((sumX.get(node.group) as number) / count - node.x) * GROUP_COHESION * alpha;
        node.vy += ((sumY.get(node.group) as number) / count - node.y) * GROUP_COHESION * alpha;
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
