import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { getMimeCategory } from "@gouvfr-lasuite/ui-components";
import prettyBytes from "pretty-bytes";
import { buildFakeGraph, FOLDER_MIMETYPE, GraphFile, GraphLink } from "../data/fakeGraph";
import { ForceSimulation, SimLink, SimNode } from "../simulation";

/** One color per file family; keys are ui-kit MimeCategory values plus "folder". */
const CATEGORY_COLORS: Record<string, string> = {
  docs: "#2f6fed",
  doc: "#2f6fed",
  calc: "#1f9d55",
  powerpoint: "#f0842c",
  pdf: "#e0342c",
  image: "#8a4fd6",
  video: "#16a2b8",
  audio: "#b8860b",
  archive: "#a67c52",
  folder: "#5c6b8a",
  other: "#8b95a5",
};
const CATEGORY_ORDER = ["folder", "doc", "calc", "powerpoint", "pdf", "image", "video", "archive", "other"];
const SURPRISE_COLOR = "#d64ea6";
const LINK_COLOR = "70, 82, 104";
const LABEL_COLOR = "#1e293b";
/** How much depth shifts a node when panning: the fake-3D parallax. */
const PARALLAX = 0.08;
const MIN_SCALE = 0.35;
const MAX_SCALE = 3.5;

type Neighbor = { node: number; link: GraphLink };

type View = { scale: number; ox: number; oy: number };

type ScreenNode = SimNode & { sx: number; sy: number; sr: number; depth: number };

const normalize = (text: string) =>
  text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");

const categoryOf = (file: GraphFile) => {
  if (file.mimetype === FOLDER_MIMETYPE) {
    return "folder";
  }
  const extension = file.title.includes(".") ? file.title.split(".").pop() : null;
  const category = getMimeCategory(file.mimetype, extension) as string;
  return category === "docs" ? "doc" : category;
};

const buildModel = () => {
  const data = buildFakeGraph();
  const index = new Map(data.files.map((file, i) => [file.id, i]));
  const clusterIndex = new Map(data.clusters.map((cluster, i) => [cluster.id, i]));
  const degree = data.files.map(() => 0);
  const neighbors: Neighbor[][] = data.files.map(() => []);

  const links: SimLink[] = [];
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
  }

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
  const nodes: SimNode[] = data.files.map((file, i) => {
    const cluster = clusterIndex.get(file.cluster) ?? 0;
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

  return { data, index, nodes, links, neighbors, categories, simulation };
};

type Model = ReturnType<typeof buildModel>;

export const FileGraph = () => {
  const { t, i18n } = useTranslation();
  const model = useMemo<Model>(buildModel, []);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef<View>({ scale: 1, ox: 0, oy: 0 });
  const sizeRef = useRef({ width: 0, height: 0 });
  const hoverRef = useRef<number | null>(null);
  const frameRef = useRef<number | null>(null);
  const patternRef = useRef<CanvasPattern | null>(null);
  const screenRef = useRef<ScreenNode[]>([]);
  // Mirrors of the React state read by the render loop.
  const uiRef = useRef({ selected: null as number | null, query: "", category: null as string | null });

  const [selected, setSelected] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string | null>(null);

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

  const categoriesInUse = useMemo(() => {
    const counts = new Map<string, number>();
    model.categories.forEach((c) => counts.set(c, (counts.get(c) ?? 0) + 1));
    return CATEGORY_ORDER.filter((c) => counts.has(c)).map((c) => ({ id: c, count: counts.get(c) ?? 0 }));
  }, [model]);

  /** Nodes emphasised by the current interaction, or null when nothing is. */
  const litNodes = useCallback((): Set<number> | null => {
    const { selected: sel, category: cat } = uiRef.current;
    const hover = hoverRef.current;
    const focus = hover ?? sel;
    if (focus !== null) {
      const set = new Set<number>([focus]);
      model.neighbors[focus].forEach((n) => set.add(n.node));
      return set;
    }
    if (matchesRef.current) {
      return matchesRef.current;
    }
    if (cat) {
      return new Set(model.categories.map((c, i) => (c === cat ? i : -1)).filter((i) => i >= 0));
    }
    return null;
  }, [model]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) {
      return;
    }
    const { width, height } = sizeRef.current;
    const { scale, ox, oy } = viewRef.current;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    ctx.fillStyle = "#f7f8fb";
    ctx.fillRect(0, 0, width, height);
    if (patternRef.current) {
      ctx.fillStyle = patternRef.current;
      ctx.fillRect(0, 0, width, height);
    }

    const focus = hoverRef.current ?? uiRef.current.selected;
    const lit = litNodes();
    const nodes = model.nodes;
    const screen: ScreenNode[] = nodes.map((node) => {
      const depth = (node.z + 1) / 2;
      const parallax = 1 + PARALLAX * node.z;
      return {
        ...node,
        depth,
        sx: width / 2 + (node.x * scale + ox) * parallax,
        sy: height / 2 + (node.y * scale + oy) * parallax,
        sr: node.r * scale * (0.72 + 0.5 * depth),
      };
    });
    screenRef.current = screen;

    ctx.lineCap = "round";
    for (const link of model.links) {
      const a = screen[link.source];
      const b = screen[link.target];
      const meta = model.neighbors[link.source].find((n) => n.node === link.target)?.link;
      const surprise = meta?.kind === "surprise";
      const weight = meta?.weight ?? 0.5;
      const isLit = lit ? lit.has(link.source) && lit.has(link.target) : true;
      const touchesFocus = focus !== null && (link.source === focus || link.target === focus);
      const depth = (a.depth + b.depth) / 2;
      let alpha = (0.18 + 0.32 * depth) * (lit && !isLit ? 0.18 : 1);
      let lineWidth = Math.min(2.6, (0.7 + weight * 1.4) * Math.sqrt(scale));
      if (touchesFocus) {
        alpha = 0.95;
        lineWidth *= 1.7;
      }
      ctx.strokeStyle = surprise ? SURPRISE_COLOR : `rgb(${LINK_COLOR})`;
      ctx.globalAlpha = surprise ? Math.min(1, alpha * 1.6) : alpha;
      ctx.lineWidth = surprise ? lineWidth + 0.5 : lineWidth;
      ctx.setLineDash(surprise ? [7, 5] : []);
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    const order = screen.map((_, i) => i).sort((i, j) => screen[i].z - screen[j].z);
    const showAllLabels = scale > 1.25;
    ctx.textBaseline = "middle";
    ctx.textAlign = "center";
    for (const i of order) {
      const node = screen[i];
      const color = CATEGORY_COLORS[model.categories[i]] ?? CATEGORY_COLORS.other;
      const dimmed = lit ? !lit.has(i) : false;
      const isFocus = i === focus;
      ctx.globalAlpha = (0.55 + 0.45 * node.depth) * (dimmed ? 0.16 : 1);

      if (isFocus) {
        ctx.shadowColor = color;
        ctx.shadowBlur = 18;
      }
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
      ctx.strokeStyle = "#ffffff";
      ctx.stroke();

      const showLabel =
        !dimmed &&
        (isFocus || showAllLabels || model.categories[i] === "folder" || (lit !== null && lit.has(i)));
      if (showLabel) {
        const file = model.data.files[i];
        const size = Math.round(11 * Math.min(1.35, Math.max(0.95, Math.sqrt(scale))));
        ctx.font = `${isFocus ? 600 : 500} ${size}px Marianne, system-ui, sans-serif`;
        const label = file.title.length > 30 ? `${file.title.slice(0, 29)}…` : file.title;
        const y = node.sy + node.sr + size * 0.9;
        ctx.lineWidth = 3.5;
        ctx.strokeStyle = "rgba(247, 248, 251, 0.92)";
        ctx.lineJoin = "round";
        ctx.strokeText(label, node.sx, y);
        ctx.fillStyle = LABEL_COLOR;
        ctx.fillText(label, node.sx, y);
      }
    }
    ctx.globalAlpha = 1;
  }, [litNodes, model]);

  const frame = useCallback(() => {
    frameRef.current = null;
    const running = model.simulation.tick();
    draw();
    if (running) {
      frameRef.current = requestAnimationFrame(frame);
    }
  }, [draw, model]);

  const requestRender = useCallback(() => {
    if (frameRef.current === null) {
      frameRef.current = requestAnimationFrame(frame);
    }
  }, [frame]);

  const fitView = useCallback(() => {
    const { width, height } = sizeRef.current;
    if (!width || !height) {
      return;
    }
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const node of model.nodes) {
      minX = Math.min(minX, node.x - node.r);
      minY = Math.min(minY, node.y - node.r);
      maxX = Math.max(maxX, node.x + node.r);
      maxY = Math.max(maxY, node.y + node.r);
    }
    const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, Math.min(width / (maxX - minX), height / (maxY - minY)) * 0.9));
    viewRef.current = {
      scale,
      ox: (-(minX + maxX) / 2) * scale,
      oy: (-(minY + maxY) / 2) * scale,
    };
    requestRender();
  }, [model, requestRender]);

  const centerOn = useCallback(
    (i: number) => {
      const node = model.nodes[i];
      const { scale } = viewRef.current;
      viewRef.current = { scale, ox: -node.x * scale, oy: -node.y * scale };
      requestRender();
    },
    [model, requestRender],
  );

  const zoomBy = useCallback(
    (factor: number, at?: { x: number; y: number }) => {
      const { width, height } = sizeRef.current;
      const { scale, ox, oy } = viewRef.current;
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));
      const px = (at?.x ?? width / 2) - width / 2;
      const py = (at?.y ?? height / 2) - height / 2;
      // Keep the world point under the cursor in place.
      const wx = (px - ox) / scale;
      const wy = (py - oy) / scale;
      viewRef.current = { scale: next, ox: px - wx * next, oy: py - wy * next };
      requestRender();
    },
    [requestRender],
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

  // Canvas sizing, background pattern and wheel zoom.
  useEffect(() => {
    const wrapper = wrapperRef.current;
    const canvas = canvasRef.current;
    if (!wrapper || !canvas) {
      return;
    }
    const dots = document.createElement("canvas");
    dots.width = 26;
    dots.height = 26;
    const dctx = dots.getContext("2d");
    if (dctx) {
      dctx.fillStyle = "rgba(30, 41, 59, 0.10)";
      dctx.beginPath();
      dctx.arc(13, 13, 1, 0, Math.PI * 2);
      dctx.fill();
      patternRef.current = canvas.getContext("2d")?.createPattern(dots, "repeat") ?? null;
    }

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
        fitView();
      }
      requestRender();
    });
    observer.observe(wrapper);

    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      zoomBy(Math.exp(-event.deltaY * 0.0016), { x: event.clientX - rect.left, y: event.clientY - rect.top });
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
  }, [fitView, requestRender, zoomBy]);

  // Keep the render loop in sync with the React state.
  useEffect(() => {
    uiRef.current = { selected, query, category };
    matchesRef.current = matches;
    requestRender();
  }, [selected, query, category, matches, requestRender]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelected(null);
        setQuery("");
        setCategory(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Pointer interactions: drag a node, pan the view, hover, click to select.
  const gestureRef = useRef<{
    mode: "node" | "pan";
    node: number | null;
    startX: number;
    startY: number;
    lastX: number;
    lastY: number;
    moved: boolean;
  } | null>(null);

  const localPoint = (event: React.PointerEvent) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const { x, y } = localPoint(event);
    const hit = hitTest(x, y);
    event.currentTarget.setPointerCapture(event.pointerId);
    gestureRef.current = { mode: hit === null ? "pan" : "node", node: hit, startX: x, startY: y, lastX: x, lastY: y, moved: false };
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
      if (hit !== hoverRef.current) {
        hoverRef.current = hit;
        event.currentTarget.style.cursor = hit === null ? "grab" : "pointer";
        requestRender();
      }
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
        setSelected(gesture.node);
      }
    } else if (!gesture.moved) {
      setSelected(null);
    }
    requestRender();
  };

  const onPointerLeave = () => {
    if (hoverRef.current !== null) {
      hoverRef.current = null;
      requestRender();
    }
  };

  const selectAndCenter = (i: number) => {
    setSelected(i);
    centerOn(i);
  };

  const onSearchKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" && matches && matches.size) {
      const first = Array.from(matches).sort((a, b) => model.nodes[b].degree - model.nodes[a].degree)[0];
      selectAndCenter(first);
    }
  };

  const selectedFile = selected !== null ? model.data.files[selected] : null;
  const selectedNeighbors = selected !== null ? [...model.neighbors[selected]].sort((a, b) => b.link.weight - a.link.weight) : [];
  const clusterLabel = (file: GraphFile) => model.data.clusters.find((c) => c.id === file.cluster)?.label ?? "";
  const formatDate = (iso: string) => new Date(iso).toLocaleDateString(i18n.language, { day: "numeric", month: "short", year: "numeric" });

  return (
    <div className="file-graph">
      <header className="file-graph__header">
        <div className="file-graph__heading">
          <h1 className="file-graph__title">
            {t("graph.title")}
            <span className="file-graph__badge">{t("graph.demo_badge")}</span>
          </h1>
          <p className="file-graph__hint">{t("graph.hint")}</p>
        </div>
        <div className="file-graph__toolbar">
          <input
            className="file-graph__search"
            type="search"
            value={query}
            placeholder={t("graph.search_placeholder")}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onSearchKeyDown}
            aria-label={t("graph.search_placeholder")}
          />
          {matches && <span className="file-graph__count">{t("graph.results", { count: matches.size })}</span>}
          <button type="button" className="file-graph__button" onClick={() => zoomBy(1.3)} aria-label={t("graph.zoom_in")}>
            +
          </button>
          <button type="button" className="file-graph__button" onClick={() => zoomBy(1 / 1.3)} aria-label={t("graph.zoom_out")}>
            −
          </button>
          <button type="button" className="file-graph__button file-graph__button--text" onClick={fitView}>
            {t("graph.recenter")}
          </button>
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
        />

        <aside className="file-graph__legend" aria-label={t("graph.legend")}>
          {categoriesInUse.map(({ id, count }) => (
            <button
              key={id}
              type="button"
              className={`file-graph__legend-item${category === id ? " file-graph__legend-item--active" : ""}`}
              onClick={() => setCategory(category === id ? null : id)}
            >
              <span className="file-graph__dot" style={{ background: CATEGORY_COLORS[id] }} />
              {t(`graph.categories.${id}`)}
              <span className="file-graph__legend-count">{count}</span>
            </button>
          ))}
          <span className="file-graph__legend-item file-graph__legend-item--static">
            <span className="file-graph__dash" />
            {t("graph.surprise_link")}
          </span>
        </aside>

        {selectedFile && (
          <aside className="file-graph__card">
            <button type="button" className="file-graph__close" onClick={() => setSelected(null)} aria-label={t("graph.close")}>
              ×
            </button>
            <span className="file-graph__dot file-graph__dot--large" style={{ background: CATEGORY_COLORS[model.categories[selected!]] }} />
            <h2 className="file-graph__card-title">{selectedFile.title}</h2>
            <dl className="file-graph__meta">
              <dt>{t("graph.category")}</dt>
              <dd>{t(`graph.categories.${model.categories[selected!]}`)}</dd>
              <dt>{t("graph.cluster")}</dt>
              <dd>{clusterLabel(selectedFile)}</dd>
              {selectedFile.size > 0 && (
                <>
                  <dt>{t("graph.size")}</dt>
                  <dd>{prettyBytes(selectedFile.size, { locale: i18n.language })}</dd>
                </>
              )}
              <dt>{t("graph.last_update")}</dt>
              <dd>{formatDate(selectedFile.updated_at)}</dd>
              <dt>{t("graph.created_by")}</dt>
              <dd>{selectedFile.creator}</dd>
            </dl>
            <h3 className="file-graph__card-subtitle">{t("graph.connections", { count: selectedNeighbors.length })}</h3>
            <ul className="file-graph__links">
              {selectedNeighbors.map(({ node, link }) => (
                <li key={node}>
                  <button type="button" className="file-graph__link" onClick={() => selectAndCenter(node)}>
                    <span className="file-graph__dot" style={{ background: CATEGORY_COLORS[model.categories[node]] }} />
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
        )}
      </div>
    </div>
  );
};
