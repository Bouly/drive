import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge, Button, getMimeCategory, Icon, Switch, Tooltip } from "@gouvfr-lasuite/ui-components";
import { Maximize, ZoomMinus, ZoomPlus } from "@gouvfr-lasuite/ui-components/icons";
import prettyBytes from "pretty-bytes";
import { buildFakeGraph, FOLDER_MIMETYPE, GraphFile, GraphLink } from "../data/fakeGraph";
import { ForceSimulation, SimLink, SimNode } from "../simulation";

/**
 * One color per file family, taken from the ui-kit file icons (mime-*.svg) so
 * the graph matches the explorer; keys are ui-kit MimeCategory values plus
 * "folder". Families without an icon color use DSFR palette tokens.
 */
const CATEGORY_COLORS: Record<string, string> = {
  docs: "#3677CC",
  doc: "#3677CC",
  calc: "#5A8228",
  powerpoint: "#AE6257",
  pdf: "#6D778C",
  image: "#6969DF", // brand-500
  video: "#3A7EA0",
  audio: "#E57036", // warning-400
  archive: "#EB9970", // warning-300
  folder: "#75758A", // gray-500
  other: "#A9A9BF", // gray-300
};
const CATEGORY_ORDER = ["folder", "doc", "calc", "powerpoint", "pdf", "image", "video", "archive", "other"];

/** Mixes a hex color with white; the dark stage needs brighter families. */
const lighten = (hex: string, amount: number) => {
  const value = parseInt(hex.slice(1), 16);
  const channel = (shift: number) => Math.round(((value >> shift) & 255) + (255 - ((value >> shift) & 255)) * amount);
  return `#${[16, 8, 0].map((s) => channel(s).toString(16).padStart(2, "0")).join("")}`;
};

type Theme = {
  bg: string;
  dot: string;
  link: string;
  label: string;
  labelHalo: string;
  clusterLabel: string;
  clusterHull: string;
  ring: string;
  /** Color of the dashed "unexpected connection" links. */
  surprise: string;
  glow: boolean;
  categoryColor: (category: string) => string;
};
// Values are DSFR palette tokens (cunningham-tokens.css): gray-*, brand-*, warning-*.
const THEMES: Record<"dark" | "light", Theme> = {
  dark: {
    bg: "#1B1B23", // gray-900
    dot: "rgba(117, 117, 138, 0.28)", // gray-500
    link: "169, 169, 191", // gray-300
    label: "#F0F0F3", // gray-050
    labelHalo: "rgba(27, 27, 35, 0.85)",
    clusterLabel: "rgba(169, 169, 191, 0.55)",
    clusterHull: "255, 255, 255",
    ring: "rgba(27, 27, 35, 0.9)",
    surprise: "#EB9970", // warning-300
    glow: true,
    categoryColor: (category) => lighten(CATEGORY_COLORS[category] ?? CATEGORY_COLORS.other, 0.3),
  },
  light: {
    bg: "#F0F0F3", // gray-050
    dot: "rgba(117, 117, 138, 0.25)",
    link: "105, 105, 125", // gray-550
    label: "#25252F", // gray-850
    labelHalo: "rgba(240, 240, 243, 0.92)",
    clusterLabel: "rgba(105, 105, 125, 0.7)",
    clusterHull: "94, 92, 208", // brand-550
    ring: "#FFFFFF",
    surprise: "#CB5000", // warning-500
    glow: false,
    categoryColor: (category) => CATEGORY_COLORS[category] ?? CATEGORY_COLORS.other,
  },
};
const THEME_STORAGE_KEY = "drive-graph-theme";

/** How much depth shifts a node when panning: the fake-3D parallax. */
const PARALLAX = 0.1;
const MIN_SCALE = 0.35;
const MAX_SCALE = 3.5;
const INTRO_DURATION = 700;
const INTRO_STAGGER = 14;
/** Per-frame convergence of emphasis and camera animations (0..1). */
const EASE = 0.22;
const MAX_SEARCH_RESULTS = 6;
/** Room kept above the graph so the top topic name stays visible. */
const TOP_INSET = 36;

type Neighbor = { node: number; link: GraphLink };

type View = { scale: number; ox: number; oy: number };

type ScreenNode = SimNode & { sx: number; sy: number; sr: number; depth: number; intro: number };

type Filters = {
  selected: number | null;
  category: string | null;
  cluster: number | null;
  activeLink: number | null;
};

const normalize = (text: string) =>
  text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "");

const easeOutCubic = (t: number) => 1 - (1 - t) ** 3;

const hexToRgb = (hex: string) => {
  const value = parseInt(hex.slice(1), 16);
  return `${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}`;
};

const categoryOf = (file: GraphFile) => {
  if (file.mimetype === FOLDER_MIMETYPE) {
    return "folder";
  }
  const extension = file.title.includes(".") ? file.title.split(".").pop() : null;
  const category = getMimeCategory(file.mimetype, extension) as string;
  return category === "docs" ? "doc" : category;
};

const readStoredTheme = (): "dark" | "light" => {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
};

const buildModel = () => {
  const data = buildFakeGraph();
  const index = new Map(data.files.map((file, i) => [file.id, i]));
  const clusterIndex = new Map(data.clusters.map((cluster, i) => [cluster.id, i]));
  const degree = data.files.map(() => 0);
  const neighbors: Neighbor[][] = data.files.map(() => []);

  const links: SimLink[] = [];
  const linkMeta: GraphLink[] = [];
  for (const link of data.links) {
    const source = index.get(link.source);
    const target = index.get(link.target);
    if (source === undefined || target === undefined) {
      continue;
    }
    degree[source]++;
    degree[target]++;
    neighbors[source].push({ node: target, link });
    neighbors[target].push({ node: source, link });
    links.push({
      source,
      target,
      length: link.kind === "surprise" ? 210 : 52 + (1 - link.weight) * 60,
      strength: link.kind === "surprise" ? 0.012 : 0.05 + link.weight * 0.06,
    });
    linkMeta.push(link);
  }
  const surprises = linkMeta.map((meta, i) => (meta.kind === "surprise" ? i : -1)).filter((i) => i >= 0);

  // Clusters sit on a ring; nodes start near their cluster with a bit of noise.
  const ring = 320;
  const centers = data.clusters.map((_, i) => {
    const angle = (i / data.clusters.length) * Math.PI * 2 - Math.PI / 2;
    return { x: Math.cos(angle) * ring, y: Math.sin(angle) * ring };
  });
  let seed = 7;
  const rand = () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296;
  };
  const categories = data.files.map(categoryOf);
  const clusterOf = data.files.map((file) => clusterIndex.get(file.cluster) ?? 0);
  const nodes: SimNode[] = data.files.map((file, i) => {
    const cluster = clusterOf[i];
    return {
      id: file.id,
      x: centers[cluster].x + (rand() - 0.5) * 120,
      y: centers[cluster].y + (rand() - 0.5) * 120,
      z: rand() * 2 - 1,
      vx: 0,
      vy: 0,
      r: categories[i] === "folder" ? 10 : 5.5 + Math.min(6.5, degree[i] * 1.1),
      cluster,
      degree: degree[i],
      fx: null,
      fy: null,
    };
  });

  const simulation = new ForceSimulation(nodes, links, centers);
  for (let i = 0; i < 40; i++) {
    simulation.tick();
  }

  // Emphasis (0 dimmed .. 1 lit) per node, eased frame by frame.
  const emphasis = nodes.map(() => 1);

  return { data, index, nodes, links, linkMeta, surprises, neighbors, categories, clusterOf, simulation, emphasis };
};

type Model = ReturnType<typeof buildModel>;

export const FileGraph = () => {
  const { t, i18n } = useTranslation();
  const model = useMemo<Model>(buildModel, []);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef<View>({ scale: 1, ox: 0, oy: 0 });
  const viewTargetRef = useRef<View | null>(null);
  const sizeRef = useRef({ width: 0, height: 0 });
  const hoverRef = useRef<number | null>(null);
  const previewCategoryRef = useRef<string | null>(null);
  const frameRef = useRef<number | null>(null);
  const patternRef = useRef<CanvasPattern | null>(null);
  const screenRef = useRef<ScreenNode[]>([]);
  /** Screen rectangles of the topic names drawn on the stage: they are clickable. */
  const clusterLabelsRef = useRef<{ x: number; y: number; w: number; h: number }[]>([]);
  const hoverClusterRef = useRef<number | null>(null);
  const introStartRef = useRef<number | null>(null);
  // Mirrors of the React state read by the render loop.
  const uiRef = useRef<Filters & { theme: "dark" | "light" }>({
    selected: null,
    category: null,
    cluster: null,
    activeLink: null,
    theme: "dark",
  });

  const [filters, setFilters] = useState<Filters>({ selected: null, category: null, cluster: null, activeLink: null });
  const { selected, category, cluster, activeLink } = filters;
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [showSurprises, setShowSurprises] = useState(false);
  const [hovered, setHovered] = useState<number | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">("dark");

  useEffect(() => {
    setTheme(readStoredTheme());
  }, []);

  const matches = useMemo(() => {
    const needle = normalize(query.trim());
    if (!needle) {
      return null;
    }
    const set = new Set<number>();
    model.data.files.forEach((file, i) => {
      if (normalize(file.title).includes(needle)) {
        set.add(i);
      }
    });
    return set;
  }, [model, query]);
  const matchesRef = useRef(matches);
  const searchResults = useMemo(
    () => (matches ? Array.from(matches).sort((a, b) => model.nodes[b].degree - model.nodes[a].degree).slice(0, MAX_SEARCH_RESULTS) : []),
    [matches, model],
  );

  const categoriesInUse = useMemo(() => {
    const counts = new Map<string, number>();
    model.categories.forEach((c) => counts.set(c, (counts.get(c) ?? 0) + 1));
    return CATEGORY_ORDER.filter((c) => counts.has(c)).map((c) => ({ id: c, count: counts.get(c) ?? 0 }));
  }, [model]);

  const clusterSizes = useMemo(() => {
    const counts = model.data.clusters.map(() => 0);
    model.clusterOf.forEach((c) => counts[c]++);
    return counts;
  }, [model]);

  const nodesOfCategory = useCallback(
    (id: string) => model.categories.map((c, i) => (c === id ? i : -1)).filter((i) => i >= 0),
    [model],
  );
  const nodesOfCluster = useCallback(
    (ci: number) => model.clusterOf.map((c, i) => (c === ci ? i : -1)).filter((i) => i >= 0),
    [model],
  );

  /** Nodes emphasised by the current interaction, or null when nothing is. */
  const litNodes = useCallback((): Set<number> | null => {
    const ui = uiRef.current;
    const focus = hoverRef.current ?? ui.selected;
    if (focus !== null) {
      const set = new Set<number>([focus]);
      model.neighbors[focus].forEach((n) => set.add(n.node));
      return set;
    }
    if (ui.activeLink !== null) {
      const link = model.links[ui.activeLink];
      return new Set([link.source, link.target]);
    }
    if (matchesRef.current) {
      return matchesRef.current;
    }
    const category = previewCategoryRef.current ?? ui.category;
    if (category) {
      return new Set(nodesOfCategory(category));
    }
    if (ui.cluster !== null) {
      return new Set(nodesOfCluster(ui.cluster));
    }
    return null;
  }, [model, nodesOfCategory, nodesOfCluster]);

  /** Draws one frame. Returns true while an animation still needs frames. */
  const draw = useCallback((): boolean => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) {
      return false;
    }
    let animating = false;
    const now = performance.now();
    const theme = THEMES[uiRef.current.theme];
    const { width, height } = sizeRef.current;

    // Camera easing towards its target.
    const target = viewTargetRef.current;
    if (target) {
      const view = viewRef.current;
      view.scale += (target.scale - view.scale) * EASE;
      view.ox += (target.ox - view.ox) * EASE;
      view.oy += (target.oy - view.oy) * EASE;
      if (Math.abs(target.scale - view.scale) < 0.001 && Math.abs(target.ox - view.ox) < 0.3 && Math.abs(target.oy - view.oy) < 0.3) {
        viewRef.current = { ...target };
        viewTargetRef.current = null;
      } else {
        animating = true;
      }
    }
    const { scale, ox, oy } = viewRef.current;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    ctx.fillStyle = theme.bg;
    ctx.fillRect(0, 0, width, height);
    if (patternRef.current) {
      ctx.fillStyle = patternRef.current;
      ctx.fillRect(0, 0, width, height);
    }

    const focus = hoverRef.current ?? uiRef.current.selected;
    const activeLinkIndex = uiRef.current.activeLink;
    const lit = litNodes();
    const nodes = model.nodes;
    const introStart = introStartRef.current ?? now;
    const screen: ScreenNode[] = nodes.map((node, i) => {
      const depth = (node.z + 1) / 2;
      const parallax = 1 + PARALLAX * node.z;
      const intro = easeOutCubic(Math.min(1, Math.max(0, (now - introStart - i * INTRO_STAGGER) / INTRO_DURATION)));
      if (intro < 1) {
        animating = true;
      }
      // Emphasis eases towards 1 (lit or nothing lit) or 0 (dimmed).
      const wanted = lit === null || lit.has(i) ? 1 : 0;
      const current = model.emphasis[i];
      if (Math.abs(wanted - current) > 0.01) {
        model.emphasis[i] = current + (wanted - current) * EASE;
        animating = true;
      } else {
        model.emphasis[i] = wanted;
      }
      const emphasis = model.emphasis[i];
      return {
        ...node,
        depth,
        intro,
        sx: width / 2 + (node.x * scale + ox) * parallax,
        sy: height / 2 + (node.y * scale + oy) * parallax,
        sr: node.r * scale * (0.72 + 0.5 * depth) * (0.4 + 0.6 * intro) * (0.85 + 0.15 * emphasis),
      };
    });
    screenRef.current = screen;
    const dimOf = (i: number) => 0.12 + 0.88 * model.emphasis[i];

    // Soft hull and name behind each cluster.
    const clusters = model.data.clusters.map(() => ({ x: 0, y: 0, n: 0, spread: 0, intro: 0, emphasis: 0 }));
    screen.forEach((node, i) => {
      const c = clusters[node.cluster];
      c.x += node.sx;
      c.y += node.sy;
      c.n++;
      c.intro = Math.max(c.intro, node.intro);
      c.emphasis = Math.max(c.emphasis, model.emphasis[i]);
    });
    clusters.forEach((c) => {
      if (c.n) {
        c.x /= c.n;
        c.y /= c.n;
      }
    });
    screen.forEach((node) => {
      const c = clusters[node.cluster];
      c.spread = Math.max(c.spread, Math.hypot(node.sx - c.x, node.sy - c.y));
    });
    clusters.forEach((c, i) => {
      if (!c.n) {
        return;
      }
      const radius = c.spread + 40 * scale;
      const strength = c.intro * (0.3 + 0.7 * c.emphasis);
      const hull = ctx.createRadialGradient(c.x, c.y, 0, c.x, c.y, radius);
      hull.addColorStop(0, `rgba(${theme.clusterHull}, ${0.07 * strength})`);
      hull.addColorStop(1, `rgba(${theme.clusterHull}, 0)`);
      ctx.fillStyle = hull;
      ctx.beginPath();
      ctx.arc(c.x, c.y, radius, 0, Math.PI * 2);
      ctx.fill();

      const size = Math.round(12 * Math.min(1.3, Math.max(0.85, Math.sqrt(scale))));
      const hoveredLabel = i === hoverClusterRef.current;
      const active = i === uiRef.current.cluster;
      ctx.font = `600 ${size}px Marianne, system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      ctx.fillStyle = hoveredLabel || active ? theme.label : theme.clusterLabel;
      ctx.globalAlpha = hoveredLabel || active ? Math.max(strength, 0.85) : strength;
      const text = model.data.clusters[i].label.toUpperCase();
      const labelY = c.y - radius + size * 1.6;
      ctx.fillText(text, c.x, labelY);
      const w = ctx.measureText(text).width + 16;
      clusterLabelsRef.current[i] = { x: c.x - w / 2, y: labelY - size - 6, w, h: size + 12 };
      ctx.globalAlpha = 1;
    });

    ctx.lineCap = "round";
    model.links.forEach((link, li) => {
      const a = screen[link.source];
      const b = screen[link.target];
      const meta = model.linkMeta[li];
      const surprise = meta.kind === "surprise";
      const touchesFocus = focus !== null && (link.source === focus || link.target === focus);
      const isActive = li === activeLinkIndex;
      const depth = (a.depth + b.depth) / 2;
      const intro = Math.min(a.intro, b.intro);
      const dim = Math.min(dimOf(link.source), dimOf(link.target));
      let alpha = (0.16 + 0.34 * depth) * dim * intro;
      let lineWidth = Math.min(2.6, (0.7 + meta.weight * 1.4) * Math.sqrt(scale));
      if (touchesFocus || isActive) {
        alpha = 0.95;
        lineWidth *= isActive ? 2.1 : 1.7;
      }
      if (surprise) {
        ctx.strokeStyle = theme.surprise;
        ctx.globalAlpha = Math.min(1, alpha * 1.6);
        ctx.lineWidth = lineWidth + 0.5;
        ctx.setLineDash([7, 5]);
      } else if (touchesFocus) {
        ctx.strokeStyle = theme.categoryColor(model.categories[focus]);
        ctx.globalAlpha = alpha;
        ctx.lineWidth = lineWidth;
        ctx.setLineDash([]);
      } else {
        ctx.strokeStyle = `rgb(${theme.link})`;
        ctx.globalAlpha = alpha;
        ctx.lineWidth = lineWidth;
        ctx.setLineDash([]);
      }
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
    });
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    const order = screen.map((_, i) => i).sort((i, j) => screen[i].z - screen[j].z);
    const showAllLabels = scale > 1.05;
    // Labels already placed this frame, so overlapping ones are skipped
    // (the focused node is drawn last and always wins).
    const placed: { x: number; y: number; w: number; h: number }[] = [];
    const overlaps = (x: number, y: number, w: number, h: number) =>
      placed.some((p) => Math.abs(p.x - x) < (p.w + w) / 2 && Math.abs(p.y - y) < (p.h + h) / 2);
    ctx.textBaseline = "middle";
    ctx.textAlign = "center";
    if (focus !== null) {
      order.splice(order.indexOf(focus), 1);
      order.push(focus);
    }
    for (const i of order) {
      const node = screen[i];
      if (node.intro <= 0) {
        continue;
      }
      const color = theme.categoryColor(model.categories[i]);
      const isFocus = i === focus;
      const emphasis = model.emphasis[i];
      const alpha = (0.55 + 0.45 * node.depth) * dimOf(i) * node.intro;

      if (theme.glow) {
        const glowRadius = node.sr * (isFocus ? 4.5 : 2.6);
        const glow = ctx.createRadialGradient(node.sx, node.sy, node.sr * 0.6, node.sx, node.sy, glowRadius);
        const rgb = hexToRgb(color);
        glow.addColorStop(0, `rgba(${rgb}, ${(isFocus ? 0.55 : 0.28) * emphasis * node.intro})`);
        glow.addColorStop(1, `rgba(${rgb}, 0)`);
        ctx.globalAlpha = 1;
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(node.sx, node.sy, glowRadius, 0, Math.PI * 2);
        ctx.fill();
      } else if (isFocus) {
        ctx.shadowColor = color;
        ctx.shadowBlur = 18;
      }

      ctx.globalAlpha = alpha;
      ctx.fillStyle = color;
      ctx.beginPath();
      if (model.categories[i] === "folder") {
        const s = node.sr * 1.7;
        ctx.roundRect(node.sx - s / 2, node.sy - s / 2, s, s, s * 0.28);
      } else {
        ctx.arc(node.sx, node.sy, node.sr, 0, Math.PI * 2);
      }
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.lineWidth = isFocus ? 2.5 : 1.5;
      ctx.strokeStyle = isFocus && theme.glow ? "#ffffff" : theme.ring;
      ctx.stroke();

      const showLabel =
        emphasis > 0.5 &&
        (isFocus || showAllLabels || model.categories[i] === "folder" || (lit !== null && lit.has(i)));
      if (showLabel) {
        const file = model.data.files[i];
        const size = Math.round(11 * Math.min(1.35, Math.max(0.95, Math.sqrt(scale))));
        ctx.font = `${isFocus ? 600 : 500} ${size}px Marianne, system-ui, sans-serif`;
        const label = file.title.length > 30 ? `${file.title.slice(0, 29)}…` : file.title;
        const w = ctx.measureText(label).width + 6;
        // Zoomed in, labels sit to the right of their node so they stop
        // crossing the neighbours below; zoomed out they hang underneath.
        const sideways = showAllLabels;
        const x = sideways ? node.sx + node.sr + 6 + w / 2 : node.sx;
        const y = sideways ? node.sy : node.sy + node.sr + size * 0.9;
        if (!isFocus && overlaps(x, y, w, size + 4)) {
          continue;
        }
        placed.push({ x, y, w, h: size + 4 });
        ctx.globalAlpha = node.intro * emphasis;
        ctx.lineWidth = 3.5;
        ctx.strokeStyle = theme.labelHalo;
        ctx.lineJoin = "round";
        ctx.strokeText(label, x, y);
        ctx.fillStyle = theme.label;
        ctx.fillText(label, x, y);
      }
    }
    ctx.globalAlpha = 1;
    return animating;
  }, [litNodes, model]);

  const frame = useCallback(() => {
    frameRef.current = null;
    const running = model.simulation.tick();
    const animating = draw();
    if (running || animating) {
      frameRef.current = requestAnimationFrame(frame);
    }
  }, [draw, model]);

  const requestRender = useCallback(() => {
    if (frameRef.current === null) {
      frameRef.current = requestAnimationFrame(frame);
    }
  }, [frame]);

  const animateTo = useCallback(
    (view: View, immediate = false) => {
      if (immediate) {
        viewRef.current = view;
        viewTargetRef.current = null;
      } else {
        viewTargetRef.current = view;
      }
      requestRender();
    },
    [requestRender],
  );

  /** Moves the camera so the given nodes fill the stage (all nodes by default). */
  const fitToNodes = useCallback(
    (indices?: number[], immediate = false) => {
      const { width, height } = sizeRef.current;
      if (!width || !height) {
        return;
      }
      const list = indices && indices.length ? indices : model.nodes.map((_, i) => i);
      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      for (const i of list) {
        const node = model.nodes[i];
        minX = Math.min(minX, node.x - node.r);
        minY = Math.min(minY, node.y - node.r);
        maxX = Math.max(maxX, node.x + node.r);
        maxY = Math.max(maxY, node.y + node.r);
      }
      // A pair needs room for the labels of its two nodes; a group less so.
      const padding = !indices ? 0 : indices.length <= 2 ? 240 : 110;
      const spanX = maxX - minX + padding;
      const spanY = maxY - minY + padding;
      const cap = indices ? 2.2 : MAX_SCALE;
      const usable = height - TOP_INSET;
      const scale = Math.min(cap, Math.max(MIN_SCALE, Math.min(width / spanX, usable / spanY) * 0.8));
      animateTo({ scale, ox: (-(minX + maxX) / 2) * scale, oy: (-(minY + maxY) / 2) * scale + TOP_INSET / 2 }, immediate);
    },
    [animateTo, model],
  );

  const centerOn = useCallback(
    (i: number) => {
      const node = model.nodes[i];
      const scale = Math.max(viewRef.current.scale, 1.2);
      animateTo({ scale, ox: -node.x * scale, oy: -node.y * scale });
    },
    [animateTo, model],
  );

  const zoomBy = useCallback(
    (factor: number, at?: { x: number; y: number }, immediate = false) => {
      const { width, height } = sizeRef.current;
      const base = viewTargetRef.current ?? viewRef.current;
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, base.scale * factor));
      const px = (at?.x ?? width / 2) - width / 2;
      const py = (at?.y ?? height / 2) - height / 2;
      // Keep the world point under the cursor in place.
      const wx = (px - base.ox) / base.scale;
      const wy = (py - base.oy) / base.scale;
      animateTo({ scale: next, ox: px - wx * next, oy: py - wy * next }, immediate);
    },
    [animateTo],
  );

  const hitTest = useCallback((x: number, y: number): number | null => {
    const screen = screenRef.current;
    for (let i = screen.length - 1; i >= 0; i--) {
      const node = screen[i];
      const dx = node.sx - x;
      const dy = node.sy - y;
      const radius = node.sr + 4;
      if (dx * dx + dy * dy <= radius * radius) {
        return i;
      }
    }
    return null;
  }, []);

  const clusterHitTest = useCallback((x: number, y: number): number | null => {
    const index = clusterLabelsRef.current.findIndex((r) => r && x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h);
    return index >= 0 ? index : null;
  }, []);

  // Canvas sizing, intro and wheel zoom.
  useEffect(() => {
    const wrapper = wrapperRef.current;
    const canvas = canvasRef.current;
    if (!wrapper || !canvas) {
      return;
    }
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    introStartRef.current = reduceMotion ? performance.now() - 60_000 : performance.now();

    let fitted = false;
    const observer = new ResizeObserver(() => {
      const dpr = window.devicePixelRatio || 1;
      const { width, height } = wrapper.getBoundingClientRect();
      sizeRef.current = { width, height };
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      if (!fitted && width && height) {
        fitted = true;
        fitToNodes(undefined, true);
      }
      requestRender();
    });
    observer.observe(wrapper);

    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      zoomBy(Math.exp(-event.deltaY * 0.0016), { x: event.clientX - rect.left, y: event.clientY - rect.top }, true);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      observer.disconnect();
      canvas.removeEventListener("wheel", onWheel);
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
    };
  }, [fitToNodes, requestRender, zoomBy]);

  // Background dots follow the theme.
  useEffect(() => {
    const canvas = canvasRef.current;
    const dots = document.createElement("canvas");
    dots.width = 26;
    dots.height = 26;
    const dctx = dots.getContext("2d");
    if (dctx && canvas) {
      dctx.fillStyle = THEMES[theme].dot;
      dctx.beginPath();
      dctx.arc(13, 13, 1, 0, Math.PI * 2);
      dctx.fill();
      patternRef.current = canvas.getContext("2d")?.createPattern(dots, "repeat") ?? null;
    }
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Storage may be unavailable (private mode): the theme just won't persist.
    }
  }, [theme]);

  // Keep the render loop in sync with the React state.
  useEffect(() => {
    uiRef.current = { ...filters, theme };
    matchesRef.current = matches;
    requestRender();
  }, [filters, theme, matches, requestRender]);

  // --- Filters -------------------------------------------------------------

  const clearFilters = useCallback(() => {
    setFilters({ selected: null, category: null, cluster: null, activeLink: null });
    setShowSurprises(false);
    fitToNodes();
  }, [fitToNodes]);

  const selectNode = useCallback((i: number | null) => {
    setFilters((f) => ({ ...f, selected: i, activeLink: null }));
    if (i !== null) {
      setShowSurprises(false);
    }
  }, []);

  const toggleCategory = (id: string) => {
    if (category === id) {
      clearFilters();
      return;
    }
    setFilters({ selected: null, category: id, cluster: null, activeLink: null });
    setShowSurprises(false);
    fitToNodes(nodesOfCategory(id));
  };

  const toggleCluster = (ci: number) => {
    if (cluster === ci) {
      clearFilters();
      return;
    }
    setFilters({ selected: null, category: null, cluster: ci, activeLink: null });
    setShowSurprises(false);
    fitToNodes(nodesOfCluster(ci));
  };

  const showLink = (li: number) => {
    const link = model.links[li];
    setFilters({ selected: null, category: null, cluster: null, activeLink: li });
    fitToNodes([link.source, link.target]);
  };

  const previewCategory = (id: string | null) => {
    previewCategoryRef.current = id;
    requestRender();
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setQuery("");
        setSearchOpen(false);
        clearFilters();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [clearFilters]);

  // --- Pointer interactions: drag a node, pan the view, hover, click to select.

  const gestureRef = useRef<{
    mode: "node" | "pan";
    node: number | null;
    cluster: number | null;
    startX: number;
    startY: number;
    lastX: number;
    lastY: number;
    moved: boolean;
  } | null>(null);

  const localPoint = (event: React.PointerEvent | React.MouseEvent) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const setHover = (hit: number | null, clusterHit: number | null, canvas: HTMLCanvasElement) => {
    if (hit !== hoverRef.current || clusterHit !== hoverClusterRef.current) {
      hoverRef.current = hit;
      hoverClusterRef.current = hit === null ? clusterHit : null;
      setHovered(hit);
      canvas.style.cursor = hit === null && clusterHit === null ? "grab" : "pointer";
      requestRender();
    }
  };

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const { x, y } = localPoint(event);
    const hit = hitTest(x, y);
    event.currentTarget.setPointerCapture(event.pointerId);
    viewTargetRef.current = null;
    gestureRef.current = {
      mode: hit === null ? "pan" : "node",
      node: hit,
      cluster: hit === null ? clusterHitTest(x, y) : null,
      startX: x,
      startY: y,
      lastX: x,
      lastY: y,
      moved: false,
    };
    if (hit !== null) {
      const node = model.nodes[hit];
      node.fx = node.x;
      node.fy = node.y;
    }
  };

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const { x, y } = localPoint(event);
    const gesture = gestureRef.current;
    if (!gesture) {
      const hit = hitTest(x, y);
      setHover(hit, hit === null ? clusterHitTest(x, y) : null, event.currentTarget);
      return;
    }
    const dx = x - gesture.lastX;
    const dy = y - gesture.lastY;
    gesture.lastX = x;
    gesture.lastY = y;
    if (Math.abs(x - gesture.startX) + Math.abs(y - gesture.startY) > 4) {
      gesture.moved = true;
    }
    if (gesture.mode === "node" && gesture.node !== null) {
      const node = model.nodes[gesture.node];
      const { scale } = viewRef.current;
      const parallax = 1 + PARALLAX * node.z;
      node.fx = (node.fx ?? node.x) + dx / (scale * parallax);
      node.fy = (node.fy ?? node.y) + dy / (scale * parallax);
      model.simulation.reheat(0.2);
    } else {
      viewRef.current.ox += dx;
      viewRef.current.oy += dy;
      event.currentTarget.style.cursor = "grabbing";
    }
    requestRender();
  };

  const onPointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const gesture = gestureRef.current;
    gestureRef.current = null;
    event.currentTarget.style.cursor = "grab";
    if (!gesture) {
      return;
    }
    if (gesture.mode === "node" && gesture.node !== null) {
      const node = model.nodes[gesture.node];
      node.fx = null;
      node.fy = null;
      model.simulation.reheat(0.1);
      if (!gesture.moved) {
        selectNode(gesture.node);
      }
    } else if (!gesture.moved) {
      if (gesture.cluster !== null) {
        toggleCluster(gesture.cluster);
      } else {
        selectNode(null);
      }
    }
    requestRender();
  };

  const onPointerLeave = (event: React.PointerEvent<HTMLCanvasElement>) => {
    setHover(null, null, event.currentTarget);
  };

  const onDoubleClick = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const { x, y } = localPoint(event);
    if (hitTest(x, y) === null) {
      zoomBy(1.6, { x, y });
    }
  };

  const selectAndCenter = (i: number) => {
    selectNode(i);
    setSearchOpen(false);
    centerOn(i);
  };

  const onSearchKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" && searchResults.length) {
      selectAndCenter(searchResults[0]);
    }
  };

  // --- Derived data for the panels ----------------------------------------

  const selectedFile = selected !== null ? model.data.files[selected] : null;
  const selectedNeighbors = selected !== null ? [...model.neighbors[selected]].sort((a, b) => b.link.weight - a.link.weight) : [];
  const clusterLabel = (i: number) => model.data.clusters[model.clusterOf[i]]?.label ?? "";
  const formatDate = (iso: string) => new Date(iso).toLocaleDateString(i18n.language, { day: "numeric", month: "short", year: "numeric" });
  const hoveredScreen = hovered !== null ? screenRef.current[hovered] : null;
  const activeLinkMeta = activeLink !== null ? model.linkMeta[activeLink] : null;
  const dotColor = THEMES[theme].categoryColor;

  let filterChip: string | null = null;
  if (category) {
    filterChip = `${t(`graph.categories.${category}`)} · ${nodesOfCategory(category).length}`;
  } else if (cluster !== null) {
    filterChip = `${model.data.clusters[cluster].label} · ${clusterSizes[cluster]}`;
  } else if (activeLinkMeta) {
    filterChip = t("graph.surprise_link");
  }

  const renderSurprises = () => (
    <aside className="file-graph__card file-graph__card--list">
      <button type="button" className="file-graph__close" onClick={() => setShowSurprises(false)} aria-label={t("graph.close")}>
        ×
      </button>
      <h2 className="file-graph__card-title">
        <span className="file-graph__dash file-graph__dash--inline" />
        {t("graph.surprises_title")}
      </h2>
      <p className="file-graph__card-intro">{t("graph.surprises_intro")}</p>
      <ul className="file-graph__links">
        {model.surprises.map((li) => {
          const link = model.links[li];
          const meta = model.linkMeta[li];
          return (
            <li key={li}>
              <button
                type="button"
                className={`file-graph__pair${activeLink === li ? " file-graph__pair--active" : ""}`}
                onClick={() => showLink(li)}
              >
                <span className="file-graph__pair-files">
                  <span className="file-graph__dot" style={{ background: CATEGORY_COLORS[model.categories[link.source]] }} />
                  <span className="file-graph__link-title">{model.data.files[link.source].title}</span>
                  <span className="file-graph__weight file-graph__weight--surprise">{Math.round(meta.weight * 100)}%</span>
                </span>
                <span className="file-graph__pair-files">
                  <span className="file-graph__pair-arrow">↔</span>
                  <span className="file-graph__dot" style={{ background: CATEGORY_COLORS[model.categories[link.target]] }} />
                  <span className="file-graph__link-title">{model.data.files[link.target].title}</span>
                </span>
                <span className="file-graph__reason">{meta.reason}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </aside>
  );

  const renderCard = (i: number, file: GraphFile) => (
    <aside className="file-graph__card">
      <button type="button" className="file-graph__close" onClick={() => selectNode(null)} aria-label={t("graph.close")}>
        ×
      </button>
      <span className="file-graph__dot file-graph__dot--large" style={{ background: dotColor(model.categories[i]), color: dotColor(model.categories[i]) }} />
      <h2 className="file-graph__card-title">{file.title}</h2>
      <dl className="file-graph__meta">
        <dt>{t("graph.category")}</dt>
        <dd>
          <button type="button" className="file-graph__inline-link" onClick={() => toggleCategory(model.categories[i])}>
            {t(`graph.categories.${model.categories[i]}`)}
          </button>
        </dd>
        <dt>{t("graph.cluster")}</dt>
        <dd>
          <button type="button" className="file-graph__inline-link" onClick={() => toggleCluster(model.clusterOf[i])}>
            {clusterLabel(i)}
          </button>
        </dd>
        {file.size > 0 && (
          <>
            <dt>{t("graph.size")}</dt>
            <dd>{prettyBytes(file.size, { locale: i18n.language })}</dd>
          </>
        )}
        <dt>{t("graph.last_update")}</dt>
        <dd>{formatDate(file.updated_at)}</dd>
        <dt>{t("graph.created_by")}</dt>
        <dd>{file.creator}</dd>
      </dl>
      <div className="file-graph__card-actions">
        <button type="button" className="file-graph__button file-graph__button--text" onClick={() => centerOn(i)}>
          {t("graph.center")}
        </button>
      </div>
      <h3 className="file-graph__card-subtitle">{t("graph.connections", { count: selectedNeighbors.length })}</h3>
      <ul className="file-graph__links">
        {selectedNeighbors.map(({ node, link }) => (
          <li key={node}>
            <button type="button" className="file-graph__link" onClick={() => selectAndCenter(node)}>
              <span className="file-graph__dot" style={{ background: dotColor(model.categories[node]) }} />
              <span className="file-graph__link-title">{model.data.files[node].title}</span>
              <span className={`file-graph__weight${link.kind === "surprise" ? " file-graph__weight--surprise" : ""}`}>
                {Math.round(link.weight * 100)}%
              </span>
            </button>
            {link.reason && <p className="file-graph__reason">{link.reason}</p>}
          </li>
        ))}
      </ul>
    </aside>
  );

  return (
    <div className={`file-graph file-graph--${theme}`}>
      <header className="file-graph__header">
        <div className="file-graph__heading">
          <h1 className="file-graph__title">
            {t("graph.title")}
            <Badge type="accent" uppercased>
              {t("graph.demo_badge")}
            </Badge>
          </h1>
          <p className="file-graph__hint">
            <span className="file-graph__stats">
              {t("graph.stats", { files: model.data.files.length, topics: model.data.clusters.length, surprises: model.surprises.length })}
            </span>
            {t("graph.hint")}
          </p>
        </div>
        <div className="file-graph__toolbar">
          {filterChip && (
            <Tooltip content={t("graph.filter_clear")}>
              <Button size="small" variant="secondary" icon={<Icon name="close" />} iconPosition="right" onClick={clearFilters}>
                {filterChip}
              </Button>
            </Tooltip>
          )}
          <div className="file-graph__search-wrap">
            <span className="file-graph__search-icon" aria-hidden="true">
              <Icon name="search" size={18} />
            </span>
            <input
              className="file-graph__search"
              type="search"
              value={query}
              placeholder={t("graph.search_placeholder")}
              onChange={(event) => {
                setQuery(event.target.value);
                setSearchOpen(true);
              }}
              onFocus={() => setSearchOpen(true)}
              onBlur={() => window.setTimeout(() => setSearchOpen(false), 150)}
              onKeyDown={onSearchKeyDown}
              aria-label={t("graph.search_placeholder")}
            />
            {searchOpen && matches && (
              <ul className="file-graph__results">
                {searchResults.length === 0 && <li className="file-graph__results-empty">{t("graph.no_result")}</li>}
                {searchResults.map((i) => (
                  <li key={i}>
                    <button type="button" className="file-graph__link" onMouseDown={(event) => event.preventDefault()} onClick={() => selectAndCenter(i)}>
                      <span className="file-graph__dot" style={{ background: dotColor(model.categories[i]) }} />
                      <span className="file-graph__link-title">{model.data.files[i].title}</span>
                      <span className="file-graph__results-topic">{clusterLabel(i)}</span>
                    </button>
                  </li>
                ))}
                {matches.size > searchResults.length && (
                  <li className="file-graph__results-empty">{t("graph.results_more", { count: matches.size - searchResults.length })}</li>
                )}
              </ul>
            )}
          </div>
          <Tooltip content={t("graph.zoom_in")}>
            <Button size="small" variant="bordered" color="neutral" icon={<ZoomPlus />} aria-label={t("graph.zoom_in")} onClick={() => zoomBy(1.3)} />
          </Tooltip>
          <Tooltip content={t("graph.zoom_out")}>
            <Button size="small" variant="bordered" color="neutral" icon={<ZoomMinus />} aria-label={t("graph.zoom_out")} onClick={() => zoomBy(1 / 1.3)} />
          </Tooltip>
          <Button size="small" variant="bordered" color="neutral" icon={<Maximize />} onClick={() => fitToNodes()}>
            {t("graph.recenter")}
          </Button>
          <Switch
            label={t("graph.theme_dark")}
            checked={theme === "dark"}
            onChange={(event) => setTheme(event.target.checked ? "dark" : "light")}
          />
        </div>
      </header>

      <div className="file-graph__stage" ref={wrapperRef}>
        <canvas
          ref={canvasRef}
          className="file-graph__canvas"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onPointerLeave={onPointerLeave}
          onDoubleClick={onDoubleClick}
        />

        {hovered !== null && hoveredScreen && !gestureRef.current && (
          <div className="file-graph__tooltip" style={{ left: hoveredScreen.sx, top: hoveredScreen.sy - hoveredScreen.sr - 10 }}>
            <strong>{model.data.files[hovered].title}</strong>
            <span>
              {t(`graph.categories.${model.categories[hovered]}`)} · {clusterLabel(hovered)} ·{" "}
              {t("graph.connections", { count: model.nodes[hovered].degree })}
            </span>
          </div>
        )}

        <aside className="file-graph__legend" aria-label={t("graph.legend")}>
          <h3 className="file-graph__section-title">{t("graph.types")}</h3>
          {categoriesInUse.map(({ id, count }) => (
            <button
              key={id}
              type="button"
              className={`file-graph__legend-item${category === id ? " file-graph__legend-item--active" : ""}`}
              style={category === id ? { background: `${dotColor(id)}33` } : undefined}
              onClick={() => toggleCategory(id)}
              onMouseEnter={() => previewCategory(id)}
              onMouseLeave={() => previewCategory(null)}
            >
              <span className="file-graph__dot" style={{ background: dotColor(id) }} />
              {t(`graph.categories.${id}`)}
              <span className="file-graph__legend-count">{count}</span>
            </button>
          ))}
          <button
            type="button"
            className={`file-graph__legend-item file-graph__legend-item--surprise${showSurprises || activeLink !== null ? " file-graph__legend-item--active" : ""}`}
            onClick={() => {
              setShowSurprises(!showSurprises);
              selectNode(null);
            }}
          >
            <span className="file-graph__dash" />
            {t("graph.surprise_link")}
            <span className="file-graph__legend-count">{model.surprises.length}</span>
          </button>
        </aside>

        {selectedFile && selected !== null ? renderCard(selected, selectedFile) : showSurprises && renderSurprises()}

        {activeLinkMeta && !showSurprises && selected === null && (
          <div className="file-graph__callout">
            <span className="file-graph__dash file-graph__dash--inline" />
            <span>{activeLinkMeta.reason}</span>
            <button type="button" className="file-graph__close file-graph__close--inline" onClick={clearFilters} aria-label={t("graph.close")}>
              ×
            </button>
          </div>
        )}
      </div>
    </div>
  );
};
