/**
 * Small force-directed layout, written here rather than pulled from d3 so the
 * graph stays dependency-free and readable. Sized for a few hundred nodes:
 * repulsion is computed pairwise, which is plenty fast at that scale.
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
const CENTER_PULL = 0.004;
const VELOCITY_DECAY = 0.4;
const ALPHA_DECAY = 0.018;
const ALPHA_MIN = 0.004;

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

    for (const node of nodes) {
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

  /** Wakes the layout up after an interaction. */
  reheat(alpha = 0.25) {
    this.alpha = Math.max(this.alpha, alpha);
  }
}
