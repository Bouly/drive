import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Link from "next/link";
import { useRouter } from "next/router";
import {
  Badge,
  Button,
  Icon,
  Select,
  Switch,
  Tooltip,
  ZoomControls,
  headerHeight,
} from "@gouvfr-lasuite/ui-components";
import { ChevronDown, ChevronRight, Edit, Filter, Plus, Settings, Trash, XMark } from "@gouvfr-lasuite/ui-components/icons";
import prettyBytes from "pretty-bytes";
import { GraphData, GraphFile, Subject } from "../data/types";
import { ForceSimulation, SimNode } from "../simulation";
import {
  CATEGORY_ORDER,
  OWNERSHIP_ORDER,
  PANEL_STORAGE_KEY,
  THEMES,
  THEME_STORAGE_KEY,
  hexToRgb,
  readStoredPanel,
  readStoredTheme,
} from "../data/theme";
import { normalize } from "../data/naming";
import { LINK_MIN_CLOSENESS, Model, buildModel } from "../data/model";
import { useDeleteFile, useFileBrief, useSubjects } from "../api";
import { DuplicateModal } from "./DuplicateModal";
import { SubjectModal } from "./SubjectModal";

/**
 * Filters stack: a file must satisfy every family of facets at once, and any
 * one facet inside a family. Picking two subjects widens the selection,
 * adding a file type narrows it.
 */
const TOPIC_FILTER_PREFIX = "topic:";
const CATEGORY_FILTER_PREFIX = "cat:";
const AUTHOR_FILTER_PREFIX = "author:";
const DATE_FILTER_PREFIX = "date:";
const RIGHT_FILTER_PREFIX = "right:";

/**
 * The ages a file can be filtered on, counted from the day it was added.
 *
 * Disjoint on purpose: facets of one family widen the selection, so two
 * ranges that overlap would let "the last month" and "the last year" answer
 * the same files twice and read as though one contained the other.
 */
const DATE_BUCKETS = [
  { id: "month", from: 0, to: 31 },
  { id: "quarter", from: 31, to: 92 },
  { id: "year", from: 92, to: 366 },
  { id: "older", from: 366, to: Infinity },
];
const DAY = 24 * 60 * 60 * 1000;

/**
 * Above this, a pair is close enough to be read as the same document even
 * without the fingerprints agreeing: a one-line file has one passage, and
 * its copy answers 1 exactly.
 *
 * The fingerprint is what usually says it ‒ see the card. A link's weight is
 * the whole of one file against the nearest passage of the other, so two
 * copies of a twelve-passage document sit at 0.96 and never reach 1.
 */
const DUPLICATE_WEIGHT = 0.995;
/** Neighbours a card explains in words ‒ what the backend answers for. */
const EXPLAINED_NEIGHBORS = 6;
/** How near the pointer must come to a thread to pick it up, in pixels. */
const LINK_HIT_SLACK = 7;
/** Similarities written on the stage at once, around the file being read. */
const MAX_WEIGHT_LABELS = 8;
/**
 * Steps run on a subject's own layout before the camera looks at it, so the
 * stage shows the shape of the subject rather than where its files were left.
 */
const STAGE_WARMUP = 320;
/** Files kept around the one being explored on its own. */
const NEIGHBOURHOOD = 8;

/** The strength slider runs the drawing threshold from every tie to the few strongest. */
/**
 * How many threads per file the stage draws before anyone touches the slider.
 *
 * Every file is linked to every other one, so the drive decides nothing here:
 * on nine hundred files the backend sends ten ties each, and drawing them all
 * gives a ball of wool where a map should be. Two and a half is what keeps a
 * packet readable while still showing the bridges between packets.
 */
const DEFAULT_THREADS_PER_FILE = 2.5;

/** How much depth shifts a node when panning: the fake-3D parallax. */
const PARALLAX = 0.1;
const MIN_SCALE = 0.35;
const MAX_SCALE = 14;
/**
 * Dots grow slower than the stage does, so zooming in pulls the files apart
 * instead of inflating them: at full zoom the positions are 14 times further
 * apart while a dot is only 4 times wider, which is what makes a crowded
 * corner readable. Below 1 the dot follows the zoom exactly.
 */
const MARK_ZOOM_EXPONENT = 0.55;
const markScale = (scale: number) => (scale <= 1 ? scale : scale ** MARK_ZOOM_EXPONENT);
const INTRO_DURATION = 700;
/**
 * How the files arrive: one after another, but the whole sweep is held to
 * INTRO_SWEEP however many there are.
 *
 * A fixed delay per file reads well on forty and is unusable on nine hundred:
 * at 14ms each the last dot lands twelve seconds after the first, and for the
 * first several seconds the stage looks empty ‒ a broken page, right at the
 * moment the reader is deciding what this screen is.
 */
const INTRO_STAGGER = 14;
const INTRO_SWEEP = 900;
/** Per-frame convergence of emphasis and camera animations (0..1). */
const EASE = 0.22;
const MAX_SEARCH_RESULTS = 6;
/**
 * Room kept above the graph so the top labels clear the search pill, which
 * now floats over the stage rather than sitting in a band above it.
 */
const TOP_INSET = 56;

/** Connections listed on a file card: the closest ones only. */
const MAX_LISTED_NEIGHBORS = 12;

type View = { scale: number; ox: number; oy: number };

type ScreenNode = SimNode & { sx: number; sy: number; sr: number; depth: number; intro: number };

type Filters = {
  selected: number | null;
  /** Stacked facets, "topic:2" or "cat:pdf". */
  facets: string[];
  activeLink: number | null;
  /** Show the selected file and its closest ones only, hiding the rest. */
  isolated: boolean;
};

const easeOutCubic = (t: number) => 1 - (1 - t) ** 3;

type FileGraphProps = {
  data: GraphData;
  /** True when the dataset is the built-in sample, not the user's files. */
  demo?: boolean;
};
export const FileGraph = ({ data, demo = false }: FileGraphProps) => {
  // The folder this graph is restricted to, when it is not the whole drive.
  const scope = data.scope ?? null;
  const subjects = useSubjects();
  const router = useRouter();
  /** What the subject modal is on: a subject to edit, "new" to name one. */
  const [editingSubject, setEditingSubject] = useState<Subject | "new" | null>(null);
  const { t, i18n } = useTranslation();
  /** Where each file sits, so a refetch does not shuffle the whole graph. */
  const placedRef = useRef(new Map<string, SimNode>());
  const model = useMemo<Model>(() => {
    const built = buildModel(data, placedRef.current);
    placedRef.current = new Map(built.nodes.map((node) => [node.id, node]));
    return built;
  }, [data]);

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
  const introStartRef = useRef<number | null>(null);
  // Mirrors of the React state read by the render loop.
  const uiRef = useRef<Filters & { theme: "dark" | "light"; strength: number }>({
    selected: null,
    facets: [],
    activeLink: null,
    isolated: false,
    theme: "dark",
    strength: 0,
  });

  const [filters, setFilters] = useState<Filters>({
    selected: null,
    facets: [],
    activeLink: null,
    isolated: false,
  });
  const { selected, facets, activeLink, isolated } = filters;
  /** 0 draws every tie, 1 keeps only the closest pairs. */
  /** 0 draws every tie, 1 keeps only the closest pair of the whole drive. */
  const [strength, setStrength] = useState(0);
  /** True once the reader has moved the slider: the default stops applying. */
  const strengthTouched = useRef(false);
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [hovered, setHovered] = useState<number | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  /** The subjects panel, folded down to its dots when closed. */
  const [panelOpen, setPanelOpen] = useState(true);
  /** Settings that are read once and left alone: folder, threads, background. */
  const [displayOpen, setDisplayOpen] = useState(false);
  const displayRef = useRef<HTMLDivElement>(null);
  /** The facets: what the stage is narrowed to, and what the colours mean. */
  const [filtersOpen, setFiltersOpen] = useState(false);
  const filtersRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setTheme(readStoredTheme());
    setPanelOpen(readStoredPanel());
  }, []);

  /**
   * Files matching the search, with a score.
   *
   * Every word must appear somewhere, in the name or in what the file says;
   * a word found in the name counts for more than one found in a passage, and
   * a name starting with it more still. So "télétravail" finds the ministry
   * agreement that never says it in its title, but a file actually named
   * after it comes first.
   */
  const matches = useMemo(() => {
    const words = normalize(query.trim()).split(/\s+/).filter(Boolean);
    if (!words.length) {
      return null;
    }
    const scored = new Map<number, number>();
    model.data.files.forEach((file, i) => {
      const title = normalize(file.title);
      const text = model.searchText[i];
      let score = 0;
      for (const word of words) {
        const inTitle = title.includes(word);
        if (!inTitle && !text.includes(word)) {
          return;
        }
        score += inTitle ? 3 : 1;
        if (title.startsWith(word)) {
          score += 2;
        }
      }
      scored.set(i, score);
    });
    return scored;
  }, [model, query]);
  const matchesRef = useRef<Set<number> | null>(matches && new Set(matches.keys()));
  const searchResults = useMemo(
    () =>
      matches
        ? [...matches.keys()]
            .sort((a, b) => (matches.get(b) ?? 0) - (matches.get(a) ?? 0) || model.nodes[b].degree - model.nodes[a].degree)
            .slice(0, MAX_SEARCH_RESULTS)
        : [],
    [matches, model],
  );
  /** True when a result owes its match to what the file says, not to its name. */
  const matchedInContent = useCallback(
    (i: number) => {
      const title = normalize(model.data.files[i].title);
      const words = normalize(query.trim()).split(/\s+/).filter(Boolean);
      return words.length > 0 && !words.some((word) => title.includes(word));
    },
    [model, query],
  );

  const categoriesInUse = useMemo(() => {
    const counts = new Map<string, number>();
    model.categories.forEach((c) => counts.set(c, (counts.get(c) ?? 0) + 1));
    return CATEGORY_ORDER.filter((c) => counts.has(c)).map((c) => ({ id: c, count: counts.get(c) ?? 0 }));
  }, [model]);

  /**
   * Which age bracket each file falls in, counted from the day it arrived.
   *
   * Read once per graph rather than per frame or per click: the brackets are
   * what the date filter cuts on, and the stage asks for them on every
   * keystroke of the filter panel.
   */
  const buckets = useMemo(() => {
    const now = Date.now();
    return model.data.files.map((file) => {
      const added = Date.parse(file.created_at ?? file.updated_at) || now;
      const days = Math.max(0, (now - added) / DAY);
      return (DATE_BUCKETS.find((range) => days >= range.from && days < range.to) ?? DATE_BUCKETS[DATE_BUCKETS.length - 1]).id;
    });
  }, [model]);

  /** The age brackets this drive actually holds, newest first. */
  const datesInUse = useMemo(() => {
    const counts = new Map<string, number>();
    buckets.forEach((id) => counts.set(id, (counts.get(id) ?? 0) + 1));
    return DATE_BUCKETS.filter((range) => counts.has(range.id)).map((range) => ({
      id: range.id,
      count: counts.get(range.id) ?? 0,
    }));
  }, [buckets]);

  /** Who wrote what, most prolific first: the author filter reads off this. */
  const authorsInUse = useMemo(() => {
    const counts = new Map<string, { id: string; name: string; count: number }>();
    model.data.files.forEach((file) => {
      const id = file.creator_id || file.creator || "";
      const held = counts.get(id) ?? { id, name: file.creator || t("graph.author_unknown"), count: 0 };
      held.count += 1;
      counts.set(id, held);
    });
    return [...counts.values()].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  }, [model, t]);

  /** What the reader may do with the files of this drive: the dots' colours. */
  const rightsInUse = useMemo(() => {
    const counts = new Map<string, number>();
    model.ownerships.forEach((role) => counts.set(role, (counts.get(role) ?? 0) + 1));
    return OWNERSHIP_ORDER.filter((role) => counts.has(role)).map((role) => ({
      id: role,
      count: counts.get(role) ?? 0,
    }));
  }, [model]);

  /** Files whose content is still being analysed: they pulse and are polled. */
  const pendingCount = useMemo(
    () => model.data.files.filter((file) => file.status === "pending").length,
    [model],
  );

  /**
   * The files left by a set of facets: any subject of the list, and any file
   * type of the list, and both at once when both are given.
   */
  const nodesOfFacets = useCallback(
    (list: string[]) => {
      if (!list.length) {
        return [];
      }
      const of = (prefix: string) =>
        list.filter((f) => f.startsWith(prefix)).map((f) => f.slice(prefix.length));
      const topics = of(TOPIC_FILTER_PREFIX).map(Number);
      const kinds = of(CATEGORY_FILTER_PREFIX);
      const authors = of(AUTHOR_FILTER_PREFIX);
      const ages = of(DATE_FILTER_PREFIX);
      const rights = of(RIGHT_FILTER_PREFIX);
      // Subjects select what they hold, not only what is drawn in their
      // colour: a file in two subjects answers to both.
      const held = new Set<number>();
      topics.forEach((group) => {
        model.topics[group]?.members.forEach((i) => held.add(i));
      });
      const kept: number[] = [];
      model.data.files.forEach((file, i) => {
        if (topics.length && !held.has(i)) {
          return;
        }
        if (kinds.length && !kinds.includes(model.categories[i])) {
          return;
        }
        if (authors.length && !authors.includes(file.creator_id || file.creator || "")) {
          return;
        }
        if (ages.length && !ages.includes(buckets[i])) {
          return;
        }
        if (rights.length && !rights.includes(model.ownerships[i])) {
          return;
        }
        kept.push(i);
      });
      return kept;
    },
    [buckets, model],
  );

  /**
   * The closeness of every drawn tie, sorted.
   *
   * The slider reads its threshold off this table rather than off the raw
   * 0.35..0.95 range, because that range is not where the links are: on this
   * drive half of them sit above 0.74, so four fifths of the slider's travel
   * used to remove nothing at all and the last fifth removed everything. By
   * rank, every millimetre of the slider takes the same number of threads off
   * the stage, whatever the drive.
   */
  const closeness = useMemo(() => {
    const kept: number[] = [];
    model.linkCloseness.forEach((value, i) => {
      if (model.linkTies[i]) {
        kept.push(value);
      }
    });
    return Float64Array.from(kept).sort();
  }, [model]);
  const closenessRef = useRef(closeness);
  closenessRef.current = closeness;

  /**
   * Files no subject holds. On a drive of nine hundred with nine subjects
   * over forty-nine files, saying so is the difference between a panel that
   * lists what exists and one that says what is left to do.
   */
  const unsorted = useMemo(
    () => model.data.files.filter((file) => !file.topics?.length).length,
    [model],
  );

  /** The closeness below which the slider stops drawing, at that position. */
  const closenessAt = useCallback((strengthValue: number) => {
    const table = closenessRef.current;
    if (!table.length) {
      return LINK_MIN_CLOSENESS;
    }
    const at = Math.min(table.length - 1, Math.floor(strengthValue * table.length));
    return table[at];
  }, []);

  /** How many threads the stage draws at a given slider position. */
  const threadsAt = useCallback(
    (strengthValue: number) => Math.round(closenessRef.current.length * (1 - strengthValue)),
    [],
  );

  // A drive arrives with ten ties per file: the slider starts where the stage
  // reads as a map rather than a ball of wool, unless the reader moved it.
  useEffect(() => {
    if (strengthTouched.current || !closeness.length) {
      return;
    }
    const wanted = model.nodes.length * DEFAULT_THREADS_PER_FILE;
    setStrength(closeness.length > wanted ? 1 - wanted / closeness.length : 0);
  }, [closeness, model]);

  /**
   * Colour of a node: what the reader holds on that file.
   *
   * The dot used to carry the file family. Nobody reads a drive by format ‒
   * "the PDFs" is not a question ‒ while whose a file is decides what may be
   * done with it, which on a shared drive is the first thing to know about a
   * document somebody points you at. The family is still drawn, as the square
   * of a folder, and is still a filter.
   */
  const nodeColor = useCallback(
    (i: number, themeName: "dark" | "light") =>
      THEMES[themeName].ownershipColor(model.ownerships[i]),
    [model],
  );

  /** The color of the group a file belongs to, null when it is in none. */
  const groupColor = useCallback(
    (i: number, themeName: "dark" | "light") => {
      const slot = model.clusters[i] >= 0 ? model.topics[model.clusters[i]].color : null;
      return slot === null ? null : THEMES[themeName].clusterColor(slot);
    },
    [model],
  );

  /** Nodes emphasised by the current interaction, or null when nothing is. */
  /**
   * The files a chosen subject puts on stage, or null when the whole drive is.
   *
   * Picking a subject is not a highlight: the stage is laid out again over
   * those files alone, so the shape you read is the shape of the subject and
   * not a corner of a bigger web. A file type keeps dimming rather than
   * removing ‒ narrowing to "PDF" is a question about the whole drive.
   */
  const stagedNodes = useMemo(() => {
    const chosen = facets.filter((f) => f.startsWith(TOPIC_FILTER_PREFIX));
    return chosen.length ? nodesOfFacets(chosen) : null;
  }, [facets, nodesOfFacets]);
  const stagedRef = useRef<Set<number> | null>(null);

  /**
   * How much each file belongs to the subject being looked at, or null when
   * none is.
   *
   * The threads of a subject are then drawn by it: a tie between two files
   * squarely inside it shows, one grazing it barely does. Reading a subject
   * is reading what holds it together, and until now every thread on stage
   * was drawn as though it were equally about it.
   */
  const subjectScores = useMemo(() => {
    const chosen = facets.filter((f) => f.startsWith(TOPIC_FILTER_PREFIX));
    if (chosen.length !== 1) {
      return null;
    }
    const subject = model.subjects[Number(chosen[0].slice(TOPIC_FILTER_PREFIX.length))];
    if (!subject) {
      return null;
    }
    return Float64Array.from(
      model.data.files.map((file) => {
        const membership = file.topics?.find((topic) => topic.id === subject.id);
        if (!membership) {
          return 0;
        }
        return membership.pinned ? 1 : membership.score;
      }),
    );
  }, [facets, model]);
  const subjectScoresRef = useRef(subjectScores);
  subjectScoresRef.current = subjectScores;
  /** Where each drawn thread runs, for the pointer and for the figures. */
  const drawnLinksRef = useRef<{ link: number; ax: number; ay: number; bx: number; by: number; weight: number; near: boolean }[]>([]);

  const litNodes = useCallback((): Set<number> | null => {
    const ui = uiRef.current;
    const focus = hoverRef.current ?? ui.selected;
    if (ui.isolated && ui.selected !== null) {
      // Exploring one file on its own: it and its closest, nobody else.
      const set = new Set<number>([ui.selected]);
      [...model.neighbors[ui.selected]]
        .sort((a, b) => b.link.weight - a.link.weight)
        .slice(0, NEIGHBOURHOOD)
        .forEach((n) => set.add(n.node));
      return set;
    }
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
    const preview = previewCategoryRef.current;
    const list = preview ? [...ui.facets, preview] : ui.facets;
    if (list.length) {
      return new Set(nodesOfFacets(list));
    }
    return null;
  }, [model, nodesOfFacets]);

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
    const strengthFloor = closenessAt(uiRef.current.strength);
    const lit = litNodes();
    const nodes = model.nodes;
    const introStart = introStartRef.current ?? now;
    const stagger = Math.min(INTRO_STAGGER, INTRO_SWEEP / Math.max(1, nodes.length));
    const screen: ScreenNode[] = nodes.map((node, i) => {
      const depth = (node.z + 1) / 2;
      const parallax = 1 + PARALLAX * node.z;
      const intro = easeOutCubic(Math.min(1, Math.max(0, (now - introStart - i * stagger) / INTRO_DURATION)));
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
        sr:
          node.r * markScale(scale) * (0.72 + 0.5 * depth) * (0.4 + 0.6 * intro) * (0.85 + 0.15 * emphasis),
      };
    });
    screenRef.current = screen;
    // Isolating a file takes the others off the stage; every other filter
    // only pushes them back, so the shape of the whole graph is still there.
    const onlyOne = uiRef.current.isolated && uiRef.current.selected !== null;
    const staged = stagedRef.current;
    const floor = onlyOne || staged ? 0 : 0.12;
    const dimOf = (i: number) => floor + (1 - floor) * model.emphasis[i];
    // A subject on stage takes the rest of the drive off it, rather than
    // fading it towards nothing: at nine hundred files the faint remainder
    // is still a haze over the answer, and drawing it costs a frame it does
    // not earn.
    const onStage = (i: number) => !staged || staged.has(i);

    // A cloud of color behind each group, so the topics of the drive read
    // before its files do. It follows the nodes, so it breathes with the
    // layout instead of being a shape drawn on top of it.
    const clouds = new Map<number, { x: number; y: number; lit: number; count: number }>();
    screen.forEach((node, i) => {
      const cluster = model.clusters[i];
      if (cluster < 0 || node.intro <= 0 || !onStage(i)) {
        return;
      }
      const cloud = clouds.get(cluster) ?? { x: 0, y: 0, lit: 0, count: 0 };
      cloud.x += node.sx;
      cloud.y += node.sy;
      cloud.lit += model.emphasis[i] * node.intro;
      cloud.count++;
      clouds.set(cluster, cloud);
    });
    const centres = new Map<number, { x: number; y: number; lit: number }>();
    for (const [cluster, cloud] of clouds) {
      if (cloud.count < 2) {
        continue;
      }
      const cx = cloud.x / cloud.count;
      const cy = cloud.y / cloud.count;
      centres.set(cluster, { x: cx, y: cy, lit: cloud.lit / cloud.count });
      let radius = 0;
      screen.forEach((node, i) => {
        if (model.clusters[i] === cluster) {
          radius = Math.max(radius, Math.hypot(node.sx - cx, node.sy - cy) + node.sr * 6);
        }
      });
      const rgb = hexToRgb(theme.clusterColor(model.topics[cluster].color));
      const peak = (theme.glow ? 0.3 : 0.2) * (cloud.lit / cloud.count);
      const cloudGradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
      cloudGradient.addColorStop(0, `rgba(${rgb}, ${peak})`);
      cloudGradient.addColorStop(0.55, `rgba(${rgb}, ${peak * 0.45})`);
      cloudGradient.addColorStop(1, `rgba(${rgb}, 0)`);
      ctx.fillStyle = cloudGradient;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.lineCap = "round";
    const scores = subjectScoresRef.current;
    const drawn: typeof drawnLinksRef.current = [];
    // Every pair is linked: the closeness alone says how much a line shows.
    // Below LINK_MIN_CLOSENESS nothing is drawn, above it the line fades in,
    // except around the focused file where even faint ties are worth seeing.
    model.links.forEach((link, li) => {
      const close = model.linkCloseness[li];
      const touchesFocus = focus !== null && (link.source === focus || link.target === focus);
      const isActive = li === activeLinkIndex;
      // Pointing at a file empties the stage of everything else: its own
      // links are the only ones left, however faint, which is the one moment
      // a weak tie is worth seeing.
      if (focus !== null && !touchesFocus && !isActive) {
        return;
      }
      if (!model.linkTies[li] && !touchesFocus && !isActive) {
        return;
      }
      // The strength slider trims the web from every tie to the closest few.
      if (close < strengthFloor && !touchesFocus && !isActive) {
        return;
      }
      if (!onStage(link.source) || !onStage(link.target)) {
        return;
      }
      const a = screen[link.source];
      const b = screen[link.target];
      const depth = (a.depth + b.depth) / 2;
      const intro = Math.min(a.intro, b.intro);
      const dim = Math.min(dimOf(link.source), dimOf(link.target));
      // Squared so a close pair stands out among the many faint ones. Only
      // the ink says it: every line is drawn at the same width, so a dense
      // corner reads as a web and not as a pile of ribbons.
      const strength = close * close;
      let alpha = (0.18 + 0.7 * strength) * (0.55 + 0.45 * depth) * dim * intro;
      let lineWidth = Math.min(3.2, 1.6 * Math.sqrt(scale));
      // A subject is being read: a thread shows as much as the subject holds
      // its two ends. Never down to nothing ‒ the tie is still there, and a
      // line that disappears reads as a file with no neighbours.
      if (scores) {
        alpha *= 0.2 + 0.8 * scores[link.source] * scores[link.target];
      }
      if (touchesFocus || isActive) {
        alpha = Math.max(alpha, 0.3 + 0.7 * close);
        lineWidth *= isActive ? 2.1 : 1.6;
      }
      // Two files of the same group are tied in its color; everything else
      // stays neutral, so the groups read at a glance.
      const cluster =
        model.clusters[link.source] === model.clusters[link.target] ? model.clusters[link.source] : -1;
      const clusterSlot = cluster >= 0 ? model.topics[cluster].color : null;
      ctx.strokeStyle = touchesFocus
        ? nodeColor(focus, uiRef.current.theme)
        : clusterSlot !== null
          ? theme.clusterColor(clusterSlot)
          : `rgb(${theme.link})`;
      ctx.globalAlpha = alpha;
      ctx.lineWidth = lineWidth;
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
      drawn.push({
        link: li,
        ax: a.sx,
        ay: a.sy,
        bx: b.sx,
        by: b.sy,
        weight: model.linkMeta[li].weight,
        near: touchesFocus || isActive,
      });
    });
    drawnLinksRef.current = drawn;
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;

    const order = screen.map((_, i) => i).sort((i, j) => screen[i].z - screen[j].z);
    const showAllLabels = scale > 1.05;
    // Labels already placed this frame, so overlapping ones are skipped
    // (the focused node is drawn last and always wins).
    const placed: { x: number; y: number; w: number; h: number }[] = [];
    /** Files worth naming this frame, drawn in a pass of their own below. */
    const labelled: number[] = [];
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
      if (node.intro <= 0 || !onStage(i)) {
        continue;
      }
      const color = nodeColor(i, uiRef.current.theme);
      const isFocus = i === focus;
      const emphasis = model.emphasis[i];
      const alpha = (0.55 + 0.45 * node.depth) * dimOf(i) * node.intro;

      // Being analysed: a halo breathes around the dot until its links arrive.
      if (model.data.files[i].status === "pending") {
        animating = true;
        const pulse = 0.5 + 0.5 * Math.sin(now / 420);
        ctx.globalAlpha = (0.12 + 0.3 * pulse) * dimOf(i) * node.intro;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(node.sx, node.sy, node.sr * (1.7 + 0.9 * pulse), 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      if (theme.glow) {
        const glowRadius = node.sr * (isFocus ? 4.5 : 2.6);
        const glow = ctx.createRadialGradient(node.sx, node.sy, node.sr * 0.6, node.sx, node.sy, glowRadius);
        // The halo carries the group, the dot keeps its file family: a file
        // then says what it is about and what it is, at the same time.
        const rgb = hexToRgb(groupColor(i, uiRef.current.theme) ?? color);
        glow.addColorStop(0, `rgba(${rgb}, ${(isFocus ? 0.7 : 0.42) * emphasis * node.intro})`);
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
      const group = groupColor(i, uiRef.current.theme);
      ctx.lineWidth = isFocus ? 2.5 : !theme.glow && group ? 2 : 1.5;
      // Without a halo to carry it, the ring shows the group on the light
      // stage; the focused node keeps its own outline.
      ctx.strokeStyle = isFocus
        ? theme.glow
          ? "#ffffff"
          : theme.ring
        : !theme.glow && group
          ? group
          : theme.ring;
      ctx.stroke();

      if (emphasis > 0.5) {
        labelled.push(i);
      }
    }

    /**
     * How close a pair is, written on the thread that joins them.
     *
     * The card lists that figure next to each neighbour, and the stage could
     * only ever say it with ink: two threads of 0.82 and 0.64 are drawn a
     * shade apart and read as "both quite close". Only around the file being
     * read, and only its strongest few, or the stage becomes a sheet of
     * numbers laid over a drawing.
     */
    const figures = drawn
      .filter((one) => one.near)
      .sort((a, b) => b.weight - a.weight)
      .slice(0, MAX_WEIGHT_LABELS);
    if (figures.length) {
      const figureSize = Math.round(10 * Math.min(1.5, Math.max(1, Math.sqrt(scale))));
      ctx.font = `600 ${figureSize}px Marianne, system-ui, sans-serif`;
      for (const one of figures) {
        const x = (one.ax + one.bx) / 2;
        const y = (one.ay + one.by) / 2;
        const label = `${Math.round(one.weight * 100)} %`;
        const width = ctx.measureText(label).width + 12;
        const height = figureSize + 8;
        if (overlaps(x, y, width, height)) {
          continue;
        }
        placed.push({ x, y, w: width, h: height });
        // On a pill of the background, so a figure sitting on its own thread
        // stays readable instead of being crossed out by it.
        ctx.globalAlpha = 0.94;
        ctx.fillStyle = theme.labelHalo;
        ctx.beginPath();
        ctx.roundRect(x - width / 2, y - height / 2, width, height, height / 2);
        ctx.fill();
        ctx.fillStyle = theme.label;
        ctx.fillText(label, x, y);
      }
      ctx.globalAlpha = 1;
    }

    // Labels are a second pass, richest file first: a name is worth more on
    // the file everything hangs from than on the one nobody points at, and
    // whoever comes first keeps the room. Drawing them inside the loop above
    // handed the room to whatever the depth order put first.
    const size = Math.round(11 * Math.min(1.8, Math.max(0.95, Math.sqrt(scale))));

    // Seen whole, the stage names its subjects and not its files: forty file
    // names at once is a wall of text nobody reads, while the group is
    // exactly what one looks for from afar. The names sit on the groups, and
    // the file names come back as soon as the stage is zoomed into.
    if (!showAllLabels) {
      const topicSize = Math.round(size * 1.25);
      ctx.font = `600 ${topicSize}px Marianne, system-ui, sans-serif`;
      // Biggest group first, so when two of them sit on top of each other it
      // is the small one that gives up its name.
      for (const [cluster, centre] of [...centres].sort((a, b) => a[0] - b[0])) {
        const topic = model.topics[cluster];
        const slot = topic.color;
        const w = ctx.measureText(topic.label).width;
        const dot = topicSize * 0.42;
        const total = w + dot + 7;
        const x = centre.x - total / 2 + dot + 7;
        const y = centre.y;
        if (overlaps(centre.x, y, total, topicSize + 8)) {
          continue;
        }
        placed.push({ x: centre.x, y, w: total, h: topicSize + 8 });
        ctx.globalAlpha = 0.35 + 0.65 * centre.lit;
        if (slot !== null) {
          // The color rides a mark next to the name, never the text itself:
          // a saturated word is harder to read than a black one.
          ctx.fillStyle = theme.clusterColor(slot);
          ctx.beginPath();
          ctx.arc(x - 7 - dot / 2, y, dot / 2, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.lineWidth = 4;
        ctx.strokeStyle = theme.labelHalo;
        ctx.lineJoin = "round";
        ctx.textAlign = "left";
        ctx.strokeText(topic.label, x, y);
        ctx.fillStyle = theme.label;
        ctx.fillText(topic.label, x, y);
        ctx.textAlign = "center";
      }
      ctx.globalAlpha = 1;
    }
    const priority = (i: number) =>
      (i === focus ? 3 : 0) +
      (lit !== null && lit.has(i) ? 2 : 0) +
      (model.categories[i] === "folder" ? 1 : 0);
    labelled.sort(
      (a, b) => priority(b) - priority(a) || model.nodes[b].degree - model.nodes[a].degree,
    );
    for (const i of showAllLabels ? labelled : labelled.filter((n) => n === focus)) {
      const node = screen[i];
      const isFocus = i === focus;
      const file = model.data.files[i];
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
      ctx.globalAlpha = node.intro * model.emphasis[i];
      ctx.lineWidth = 3.5;
      ctx.strokeStyle = theme.labelHalo;
      ctx.lineJoin = "round";
      ctx.strokeText(label, x, y);
      ctx.fillStyle = theme.label;
      ctx.fillText(label, x, y);
    }
    ctx.globalAlpha = 1;
    return animating;
  }, [groupColor, litNodes, model, nodeColor]);

  /** The simulation being stepped: the whole drive, or the subject on stage. */
  const simRef = useRef(model.simulation);

  const frame = useCallback(() => {
    frameRef.current = null;
    const running = simRef.current.tick();
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
      const cap = indices ? 5 : MAX_SCALE;
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
      const radius = node.sr + 6;
      if (dx * dx + dy * dy <= radius * radius) {
        return i;
      }
    }
    return null;
  }, []);

  /**
   * The thread under the pointer, or null.
   *
   * Only the threads drawn on the last frame are measured, which is what
   * makes this cheap: a drive of nine hundred files draws a few thousand of
   * its two hundred thousand pairs, and the slider takes that down further.
   */
  const hitTestLink = useCallback((x: number, y: number): number | null => {
    let best: number | null = null;
    let nearest = LINK_HIT_SLACK;
    for (const one of drawnLinksRef.current) {
      const dx = one.bx - one.ax;
      const dy = one.by - one.ay;
      const span = dx * dx + dy * dy;
      // Where on the segment the pointer falls, kept between its two ends.
      const at = span ? Math.max(0, Math.min(1, ((x - one.ax) * dx + (y - one.ay) * dy) / span)) : 0;
      const distance = Math.hypot(x - (one.ax + at * dx), y - (one.ay + at * dy));
      if (distance < nearest) {
        nearest = distance;
        best = one.link;
      }
    }
    return best;
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
      zoomBy(Math.exp(-event.deltaY * 0.0024), { x: event.clientX - rect.left, y: event.clientY - rect.top }, true);
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

  useEffect(() => {
    try {
      window.localStorage.setItem(PANEL_STORAGE_KEY, panelOpen ? "open" : "closed");
    } catch {
      // Same here: the panel just reopens on the next visit.
    }
  }, [panelOpen]);

  // Files came or went: frame them, so a new one is never drawn off screen.
  const shownRef = useRef(model.nodes.length);
  useEffect(() => {
    if (model.nodes.length !== shownRef.current) {
      shownRef.current = model.nodes.length;
      fitToNodes();
    }
    requestRender();
  }, [model, fitToNodes, requestRender]);

  // Keep the render loop in sync with the React state.
  useEffect(() => {
    uiRef.current = { ...filters, theme, strength };
    matchesRef.current = matches && new Set(matches.keys());
    requestRender();
  }, [filters, theme, strength, matches, requestRender]);

  /** What was on stage last time, so a refetch does not re-frame the camera. */
  const stagedBeforeRef = useRef<string | null>(null);

  // A subject came or went: lay the stage out again over what it holds, and
  // frame it. The nodes are the same objects, so the files that stay keep
  // their places and the others simply leave.
  useEffect(() => {
    stagedRef.current = stagedNodes && new Set(stagedNodes);
    if (!stagedNodes) {
      simRef.current = model.simulation;
    } else {
      const rank = new Map(stagedNodes.map((node, at) => [node, at]));
      const staged = new ForceSimulation(
        stagedNodes.map((node) => model.nodes[node]),
        model.links
          .filter((link) => rank.has(link.source) && rank.has(link.target))
          .map((link) => ({
            ...link,
            source: rank.get(link.source) as number,
            target: rank.get(link.target) as number,
          })),
      );
      // Settle it before the camera looks, the way the whole graph is settled
      // when it is built: the files of a subject start scattered across the
      // drive, so framing them first would frame the drive and show nothing.
      for (let step = 0; step < STAGE_WARMUP; step++) {
        staged.tick();
      }
      simRef.current = staged;
    }
    // Only move the camera when the staging itself changed: a refetch keeps
    // whatever the reader was looking at, and the effect above frames new files.
    const key = stagedNodes ? stagedNodes.join(",") : null;
    if (key !== stagedBeforeRef.current) {
      stagedBeforeRef.current = key;
      if (stagedNodes) {
        fitToNodes(stagedNodes);
      } else {
        // The whole drive settled long ago: warm it so the files come back.
        model.simulation.reheat(0.3);
        fitToNodes();
      }
    }
    requestRender();
  }, [stagedNodes, model, fitToNodes, requestRender]);

  // --- Filters -------------------------------------------------------------

  const clearFilters = useCallback(() => {
    setFilters({ selected: null, facets: [], activeLink: null, isolated: false });
    fitToNodes();
  }, [fitToNodes]);

  const selectNode = useCallback((i: number | null) => {
    setFilters((f) => ({ ...f, selected: i, activeLink: null, isolated: f.isolated && i !== null }));
  }, []);

  /** Adds a facet to the stack, or takes it back out. */
  const toggleFacet = (id: string) => {
    const next = facets.includes(id) ? facets.filter((f) => f !== id) : [...facets, id];
    setFilters({ selected: null, facets: next, activeLink: null, isolated: false });
    if (next.length) {
      fitToNodes(nodesOfFacets(next));
    } else {
      fitToNodes();
    }
  };

  /** Explore the selected file on its own: the rest of the stage steps out. */
  const toggleIsolate = () => {
    setFilters((f) => ({ ...f, isolated: !f.isolated }));
    if (!isolated && selected !== null) {
      fitToNodes([
        selected,
        ...[...model.neighbors[selected]]
          .sort((a, b) => b.link.weight - a.link.weight)
          .slice(0, NEIGHBOURHOOD)
          .map((n) => n.node),
      ]);
    }
  };

  const previewCategory = useCallback(
    (id: string | null) => {
      previewCategoryRef.current = id;
      requestRender();
    },
    [requestRender],
  );

  /**
   * A facet lights its files while the pointer rests on its row. When the
   * panel closes under the pointer ‒ on Escape, or on a click that both
   * chooses a facet and shuts the panel ‒ that row never sees the pointer
   * leave, and the stage stayed dimmed around files nobody was pointing at.
   */
  useEffect(() => {
    if (!filtersOpen && !displayOpen) {
      previewCategory(null);
    }
  }, [filtersOpen, displayOpen, previewCategory]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setQuery("");
        setSearchOpen(false);
        setDisplayOpen(false);
        setFiltersOpen(false);
        clearFilters();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [clearFilters]);

  // The popovers close on a click anywhere else, as the ui-kit ones do.
  useEffect(() => {
    if (!displayOpen && !filtersOpen) {
      return;
    }
    const onDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!displayRef.current?.contains(target)) {
        setDisplayOpen(false);
      }
      if (!filtersRef.current?.contains(target)) {
        setFiltersOpen(false);
      }
    };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [displayOpen, filtersOpen]);

  // --- Pointer interactions: drag a node, pan the view, hover, click to select.

  const gestureRef = useRef<{
    mode: "node" | "pan";
    node: number | null;
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

  const setHover = (hit: number | null, canvas: HTMLCanvasElement, at?: { x: number; y: number }) => {
    // A thread is only picked up when no file is under the pointer and none
    // is open: a card already lists every tie of its file, with its figure,
    // and lighting a thread underneath it would fight with the card.
    const link =
      hit === null && at && uiRef.current.selected === null ? hitTestLink(at.x, at.y) : null;
    if (hit !== hoverRef.current) {
      hoverRef.current = hit;
      setHovered(hit);
      requestRender();
    }
    if (link !== uiRef.current.activeLink) {
      setFilters((f) => ({ ...f, activeLink: link }));
    }
    canvas.style.cursor = hit === null && link === null ? "grab" : "pointer";
  };

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const { x, y } = localPoint(event);
    const hit = hitTest(x, y);
    event.currentTarget.setPointerCapture(event.pointerId);
    viewTargetRef.current = null;
    gestureRef.current = {
      mode: hit === null ? "pan" : "node",
      node: hit,
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
      setHover(hitTest(x, y), event.currentTarget, { x, y });
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
      simRef.current.reheat(0.2);
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
      simRef.current.reheat(0.1);
      if (!gesture.moved) {
        selectNode(gesture.node);
      }
    } else if (!gesture.moved) {
      selectNode(null);
    }
    requestRender();
  };

  const onPointerLeave = (event: React.PointerEvent<HTMLCanvasElement>) => {
    setHover(null, event.currentTarget);
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
  // Every file is linked to every other one: the card lists the closest ones.
  const selectedNeighbors =
    selected !== null
      ? [...model.neighbors[selected]].sort((a, b) => b.link.weight - a.link.weight).slice(0, MAX_LISTED_NEIGHBORS)
      : [];
  /**
   * The subject the card answers on.
   *
   * What the reader is looking at, in order: the subject they put on stage,
   * what they typed in the search field, and failing both the subject this
   * file belongs to most. A card opened out of the blue then still says what
   * the file is about rather than nothing.
   */
  const readingSubject = useMemo(() => {
    const chosen = facets.filter((f) => f.startsWith(TOPIC_FILTER_PREFIX));
    if (chosen.length === 1) {
      return model.topics[Number(chosen[0].slice(TOPIC_FILTER_PREFIX.length))]?.label ?? "";
    }
    const typed = query.trim();
    if (typed) {
      return typed;
    }
    const own = selectedFile?.topics?.[0];
    return own ? (model.subjects.find((subject) => subject.id === own.id)?.name ?? "") : "";
  }, [facets, model, query, selectedFile]);

  // The neighbours the card explains in words: the closest ones, as many as
  // the backend answers for. The rest keep the passage the storage holds.
  const explained = selectedNeighbors
    .slice(0, EXPLAINED_NEIGHBORS)
    .map(({ node }) => model.data.files[node].id);
  const brief = useFileBrief(selectedFile?.id ?? null, readingSubject, explained);
  const sentences = useMemo(
    () => new Map((brief.data?.links ?? []).map((link) => [link.id, link.sentence])),
    [brief.data],
  );
  const removeFile = useDeleteFile();
  /** The neighbour holding the same content as the open file, once confirmed. */
  const [duplicate, setDuplicate] = useState<{ id: string; title: string } | null>(null);

  const formatDate = (iso: string) => new Date(iso).toLocaleDateString(i18n.language, { day: "numeric", month: "short", year: "numeric" });
  const hoveredScreen = hovered !== null ? screenRef.current[hovered] : null;
  const activeLinkMeta = activeLink !== null ? model.linkMeta[activeLink] : null;
  const colorOf = (i: number) => nodeColor(i, theme);

  const filterName = (id: string) => {
    const value = id.slice(id.indexOf(":") + 1);
    if (id.startsWith(TOPIC_FILTER_PREFIX)) {
      return model.topics[Number(value)]?.label ?? "";
    }
    if (id.startsWith(AUTHOR_FILTER_PREFIX)) {
      return authorsInUse.find((author) => author.id === value)?.name ?? value;
    }
    if (id.startsWith(DATE_FILTER_PREFIX)) {
      return t(`graph.dates.${value}`);
    }
    if (id.startsWith(RIGHT_FILTER_PREFIX)) {
      return t(`graph.rights_of.${value}`);
    }
    return t(`graph.categories.${value}`);
  };
  const filteredCount = facets.length ? nodesOfFacets(facets).length : 0;

  /**
   * One line of a filter list: the facet, how many files answer to it, and
   * for the rights, the colour those files are drawn in.
   */
  const renderFacet = (id: string, label: string, count: number, dot?: string) => {
    const on = facets.includes(id);
    return (
      <button
        key={id}
        type="button"
        className={`file-graph__legend-item${on ? " file-graph__legend-item--active" : ""}`}
        style={on && dot ? { background: `${dot}33` } : undefined}
        aria-pressed={on}
        onClick={() => toggleFacet(id)}
        onMouseEnter={() => previewCategory(id)}
        onMouseLeave={() => previewCategory(null)}
      >
        {dot && <span className="file-graph__dot" style={{ background: dot }} />}
        <span className="file-graph__facet-label">{label}</span>
        <span className="file-graph__legend-count">{count}</span>
      </button>
    );
  };

  const renderCard = (i: number, file: GraphFile) => (
    <aside className="file-graph__card">
      <div className="file-graph__card-head">
        <span className="file-graph__dot file-graph__dot--large" style={{ background: colorOf(i), color: colorOf(i) }} />
        <h2 className="file-graph__card-title">{file.title}</h2>
        <button type="button" className="file-graph__close" onClick={() => selectNode(null)} aria-label={t("graph.close")}>
          <XMark />
        </button>
      </div>
      {/*
        What the file is, when it arrived, who wrote it and what may be done
        with it: the four things the stage draws it by, written out, so the
        size of a dot and its colour can be read back in words.
      */}
      <dl className="file-graph__meta">
        <div>
          <dt>{t("graph.category")}</dt>
          <dd>
            {t(`graph.categories.${model.categories[i]}`)}
            {file.size > 0 && <> · {prettyBytes(file.size, { locale: i18n.language })}</>}
          </dd>
        </div>
        <div>
          <dt>{t("graph.added_on")}</dt>
          <dd>{formatDate(file.created_at ?? file.updated_at)}</dd>
        </div>
        <div>
          <dt>{t("graph.created_by")}</dt>
          <dd>{file.creator || t("graph.author_unknown")}</dd>
        </div>
        <div>
          <dt>{t("graph.rights")}</dt>
          <dd>
            <span className="file-graph__dot" style={{ background: colorOf(i) }} />
            {t(`graph.rights_of.${model.ownerships[i]}`)}
          </dd>
        </div>
      </dl>
      <div className="file-graph__card-actions">
        <Tooltip content={t("graph.isolate_hint", { count: NEIGHBOURHOOD })}>
          <Button
            size="small"
            variant={isolated ? "primary" : "bordered"}
            color="neutral"
            onClick={toggleIsolate}
          >
            {t(isolated ? "graph.isolate_off" : "graph.isolate")}
          </Button>
        </Tooltip>
        <Button size="small" variant="bordered" color="neutral" onClick={() => centerOn(i)}>
          {t("graph.center")}
        </Button>
        {!demo && (
          <Button
            size="small"
            variant="bordered"
            color="neutral"
            href={`/explorer/items/files/${file.id}`}
          >
            {t("graph.open_file")}
          </Button>
        )}
      </div>
      {model.subjects.length > 0 && (
        <details className="file-graph__card-fold">
          <summary>{t("graph.subject_put")}</summary>
          <div className="file-graph__subjects">
          {model.subjects.map((subject) => {
            const membership = file.topics?.find((t) => t.id === subject.id);
            return (
              <button
                key={subject.id}
                type="button"
                className={`file-graph__subject${membership ? " file-graph__subject--in" : ""}${
                  membership?.pinned ? " file-graph__subject--pinned" : ""
                }`}
                title={
                  membership?.pinned
                    ? t("graph.subject_pinned")
                    : membership
                      ? t("graph.subject_matched", { score: Math.round(membership.score * 100) })
                      : t("graph.subject_put")
                }
                onClick={() =>
                  membership?.pinned
                    ? subjects.unpin.mutate({ topic: subject.id, item: file.id })
                    : subjects.pin.mutate({ topic: subject.id, item: file.id })
                }
              >
                {subject.name}
              </button>
            );
          })}
          </div>
        </details>
      )}
      {file.status === "pending" && (
        <p className="file-graph__pending">
          <span className="file-graph__pulse" />
          {t("graph.analysing_file")}
        </p>
      )}
      {(file.status === "skipped" ||
        file.status === "empty" ||
        file.status === "failed" ||
        file.status === "idle") && (
        <p className="file-graph__pending">{t(`graph.status_${file.status}`)}</p>
      )}
      {/*
        What the file says, on whatever is being looked at. Asked for when the
        card opens: it costs a reading of the file, and most cards are never
        opened.
      */}
      <h3 className="file-graph__card-subtitle">
        {t("graph.summary")}
        {readingSubject && (
          <span className="file-graph__card-subject" title={readingSubject}>
            {readingSubject}
          </span>
        )}
      </h3>
      {brief.isPending ? (
        <p className="file-graph__loading-lines" aria-label={t("graph.summary_loading")}>
          <span />
          <span />
          <span />
        </p>
      ) : brief.data?.summary ? (
        <p className="file-graph__summary">{brief.data.summary}</p>
      ) : (
        <p className="file-graph__reason">{t("graph.summary_missing")}</p>
      )}
      <h3 className="file-graph__card-subtitle">{t("graph.connections", { count: selectedNeighbors.length })}</h3>
      <ul className="file-graph__links">
        {selectedNeighbors.map(({ node, link }) => {
          const neighbour = model.data.files[node];
          const sentence = sentences.get(neighbour.id);
          // The same document twice: the passages match, or the pair is so
          // close that a one-passage file and its copy cannot be told apart.
          const twin =
            (Boolean(file.content) && neighbour.content === file.content) ||
            link.weight >= DUPLICATE_WEIGHT;
          return (
            <li key={node}>
              <button type="button" className="file-graph__link" onClick={() => selectAndCenter(node)}>
                <span className="file-graph__dot" style={{ background: colorOf(node) }} />
                <span className="file-graph__link-title">{neighbour.title}</span>
                <span className="file-graph__weight">{Math.round(link.weight * 100)}%</span>
              </button>
              {/*
                What the two have in common, in a sentence, and failing that
                the passage the storage kept to justify the tie.
              */}
              {sentence ? (
                <p className="file-graph__sentence">{sentence}</p>
              ) : brief.isPending && explained.includes(neighbour.id) ? (
                <p className="file-graph__loading-lines file-graph__loading-lines--one">
                  <span />
                </p>
              ) : link.reason ? (
                <p className="file-graph__reason">{link.reason}</p>
              ) : null}
              {/*
                Same content on both sides: the one thing a graph can show
                that a folder cannot, so it comes with the answer to it.
              */}
              {twin && (
                <p className="file-graph__twin">
                  <span className="file-graph__twin-note">{t("graph.duplicate_found")}</span>
                  <button
                    type="button"
                    className="file-graph__twin-drop"
                    onClick={() => setDuplicate({ id: neighbour.id, title: neighbour.title })}
                  >
                    <Trash />
                    {t("graph.duplicate_delete")}
                  </button>
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </aside>
  );

  return (
    <div
      className={`file-graph file-graph--${theme}`}
      style={{ "--fg-header-height": `${headerHeight}px` } as React.CSSProperties}
    >
      {/*
        No band above the stage: the graph is a map, so its controls sit on it.
        Everyday reach (find a file, read how many) floats top left, the view
        controls bottom right, and the settings behind them.
      */}
      <div className="file-graph__stage" ref={wrapperRef}>
        <canvas
          ref={canvasRef}
          className={`file-graph__canvas${hovered !== null ? " file-graph__canvas--over" : ""}`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onPointerLeave={onPointerLeave}
          onDoubleClick={onDoubleClick}
        />

        <div className="file-graph__topbar">
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
                      <span className="file-graph__dot" style={{ background: colorOf(i) }} />
                      <span className="file-graph__link-title">{model.data.files[i].title}</span>
                      {matchedInContent(i) && <span className="file-graph__weight">{t("graph.in_content")}</span>}
                    </button>
                  </li>
                ))}
                {matches.size > searchResults.length && (
                  <li className="file-graph__results-empty">{t("graph.results_more", { count: matches.size - searchResults.length })}</li>
                )}
              </ul>
            )}
          </div>
          <Tooltip content={t("graph.hint")}>
            <span className="file-graph__counter">
              {t("graph.stats_files", { count: model.data.files.length })}
              {pendingCount > 0 && (
                <>
                  <span className="file-graph__pulse" />
                  <span className="file-graph__counter-pending">{pendingCount}</span>
                </>
              )}
            </span>
          </Tooltip>
          {scope && (
            // Which folder is being drawn is a state of the stage, like a
            // filter: the name opens it in the explorer, the cross leaves it,
            // and the tooltip says where it sits ‒ "Divers" is the name of a
            // folder in three drives out of four.
            <Tooltip content={scope.path.join(" / ")}>
              <span className="file-graph__scope">
                <Link href={`/explorer/items/${scope.id}`} className="file-graph__scope-name">
                  {scope.title}
                </Link>
                <Link
                  href="/explorer/graph"
                  className="file-graph__scope-leave"
                  aria-label={t("graph.scope.leave")}
                >
                  <Icon name="close" size={16} />
                </Link>
              </span>
            </Tooltip>
          )}
          {demo && (
            <Badge type="accent" uppercased>
              {t("graph.demo_badge")}
            </Badge>
          )}
        </div>

        {/* Filters in force describe the stage, so they sit on it, not in a toolbar. */}
        {facets.length > 0 && (
          <div className="file-graph__facets">
            {facets.map((id) => (
              <Tooltip key={id} content={t("graph.filter_remove")}>
                <Button
                  size="small"
                  variant="secondary"
                  icon={<Icon name="close" />}
                  iconPosition="right"
                  onClick={() => toggleFacet(id)}
                >
                  {filterName(id)}
                </Button>
              </Tooltip>
            ))}
            <span className="file-graph__filter-count">
              {t("graph.stats_files", { count: filteredCount })}
              {facets.length > 1 && (
                <button type="button" className="file-graph__inline-link" onClick={clearFilters}>
                  {t("graph.filter_clear")}
                </button>
              )}
            </span>
          </div>
        )}

        {hovered !== null && hoveredScreen && !gestureRef.current && (
          <div className="file-graph__tooltip" style={{ left: hoveredScreen.sx, top: hoveredScreen.sy - hoveredScreen.sr - 10 }}>
            <strong>{model.data.files[hovered].title}</strong>
            <span>
              {t(`graph.categories.${model.categories[hovered]}`)} ·{" "}
              {t("graph.connections", { count: model.nodes[hovered].degree })}
            </span>
          </div>
        )}

        <aside
          className={`file-graph__legend${panelOpen ? "" : " file-graph__legend--closed"}`}
          aria-label={t("graph.legend")}
        >
          <Tooltip content={t("graph.subject_hint")}>
            <button
              type="button"
              className="file-graph__legend-head"
              aria-expanded={panelOpen}
              onClick={() => setPanelOpen(!panelOpen)}
            >
              {t("graph.subjects")}
              <span className="file-graph__legend-chevron" aria-hidden="true">
                {panelOpen ? <ChevronDown /> : <ChevronRight />}
              </span>
            </button>
          </Tooltip>

          {/* Closed, the panel still answers what the colors mean. */}
          {!panelOpen && model.topics.length > 0 && (
            <div className="file-graph__legend-rail" aria-hidden="true">
              {model.topics.map((topic, i) => (
                <span
                  key={i}
                  className="file-graph__dot"
                  style={{
                    background:
                      THEMES[theme].clusterColor(topic.color),
                  }}
                />
              ))}
            </div>
          )}

          {panelOpen && (
            <>
              <div className="file-graph__legend-body">
              {model.topics.map((topic, i) => {
                const id = `${TOPIC_FILTER_PREFIX}${i}`;
                const color =
                  THEMES[theme].clusterColor(topic.color);
                const subject = model.subjects[i];
                return (
                  <div key={id} className="file-graph__legend-line">
                    <button
                      type="button"
                      className={`file-graph__legend-item${facets.includes(id) ? " file-graph__legend-item--active" : ""}`}
                      style={facets.includes(id) ? { background: `${color}33` } : undefined}
                      onClick={() => toggleFacet(id)}
                      onMouseEnter={() => previewCategory(id)}
                      onMouseLeave={() => previewCategory(null)}
                    >
                      <span className="file-graph__dot" style={{ background: color }} />
                      {topic.label}
                      <span className="file-graph__legend-count">{topic.members.length}</span>
                    </button>
                    {subject && (
                      <button
                        type="button"
                        className="file-graph__subject-edit-open"
                        aria-label={t("graph.subject_edit", { name: subject.name })}
                        onClick={() => setEditingSubject(subject)}
                      >
                        <Edit />
                      </button>
                    )}
                  </div>
                );
              })}

              </div>
              <div className="file-graph__legend-foot">
                {unsorted > 0 && (
                  <p className="file-graph__legend-note">
                    {t("graph.no_subject", { count: unsorted })}
                  </p>
                )}
                {/* Naming a subject is done a handful of times: it earns a button, not a field. */}
                <button
                  type="button"
                  className="file-graph__subject-create"
                  onClick={() => setEditingSubject("new")}
                >
                  <Plus />
                  {t("graph.subject_new")}
                </button>
              </div>
            </>
          )}
        </aside>

        <div
          className={`file-graph__viewtools${selectedFile ? " file-graph__viewtools--aside" : ""}`}
        >
          {/*
            Filters answer four questions about a file ‒ when it arrived, who
            wrote it, what it is, and what may be done with it ‒ and the last
            one is also the legend of the colours on stage. Subjects stay in
            their own panel on the left: they are a filter too, but they are
            what the reader writes, not what the drive already knows.
          */}
          <div className="file-graph__display" ref={filtersRef}>
            {filtersOpen && (
              <div className="file-graph__display-pop file-graph__filters-pop" role="group" aria-label={t("graph.filters")}>
                {datesInUse.length > 1 && (
                  <div className="file-graph__display-section">
                    <span className="file-graph__section-title">{t("graph.date")}</span>
                    {datesInUse.map(({ id, count }) =>
                      renderFacet(DATE_FILTER_PREFIX + id, t(`graph.dates.${id}`), count),
                    )}
                  </div>
                )}
                {authorsInUse.length > 1 && (
                  <div className="file-graph__display-section">
                    <span className="file-graph__section-title">{t("graph.author")}</span>
                    {authorsInUse.map(({ id, name, count }) =>
                      renderFacet(AUTHOR_FILTER_PREFIX + id, name, count),
                    )}
                  </div>
                )}
                {categoriesInUse.length > 1 && (
                  <div className="file-graph__display-section">
                    <span className="file-graph__section-title">{t("graph.types")}</span>
                    {categoriesInUse.map(({ id, count }) =>
                      renderFacet(CATEGORY_FILTER_PREFIX + id, t(`graph.categories.${id}`), count),
                    )}
                  </div>
                )}
                <div className="file-graph__display-section">
                  <span className="file-graph__section-title">{t("graph.rights")}</span>
                  {rightsInUse.map(({ id, count }) =>
                    renderFacet(RIGHT_FILTER_PREFIX + id, t(`graph.rights_of.${id}`), count, THEMES[theme].ownershipColor(id)),
                  )}
                </div>
                {facets.length > 0 && (
                  <button type="button" className="file-graph__inline-link file-graph__filters-clear" onClick={clearFilters}>
                    {t("graph.filter_clear")}
                  </button>
                )}
              </div>
            )}
            <Button
              size="small"
              variant="bordered"
              color="neutral"
              icon={<Filter />}
              active={filtersOpen}
              aria-expanded={filtersOpen}
              onClick={() => {
                setFiltersOpen(!filtersOpen);
                setDisplayOpen(false);
              }}
            >
              {t("graph.filters")}
              {facets.length > 0 && <span className="file-graph__filters-badge">{facets.length}</span>}
            </Button>
          </div>
          <div className="file-graph__display" ref={displayRef}>
            {displayOpen && (
              <div className="file-graph__display-pop" role="group" aria-label={t("graph.display")}>
                {data.folders.length > 0 && (
                  <Select
                    clearable={false}
                    label={t("graph.folder")}
                    value={data.scope?.id ?? ""}
                    options={[
                      { label: t("graph.folder_all"), value: "" },
                      ...data.folders.map((one) => ({
                        label: one.trail.join(" / "),
                        value: one.id,
                      })),
                    ]}
                    onChange={(event) => {
                      const id = (event.target.value as string) || null;
                      setDisplayOpen(false);
                      router.push(
                        id
                          ? { pathname: "/explorer/graph", query: { folder: id } }
                          : { pathname: "/explorer/graph" },
                      );
                    }}
                  />
                )}
                <label className="file-graph__strength">
                  <span className="file-graph__strength-head">
                    {t("graph.strength")}
                    <span className="file-graph__strength-count">
                      {t("graph.threads", { count: threadsAt(strength) })}
                    </span>
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={Math.round(strength * 100)}
                    onChange={(event) => {
                      strengthTouched.current = true;
                      setStrength(Number(event.target.value) / 100);
                    }}
                    aria-label={t("graph.strength")}
                  />
                </label>
                <Switch
                  label={t("graph.theme_dark")}
                  checked={theme === "dark"}
                  onChange={(event) => setTheme(event.target.checked ? "dark" : "light")}
                />
              </div>
            )}
            <Button
              size="small"
              variant="bordered"
              color="neutral"
              icon={<Settings />}
              active={displayOpen}
              aria-expanded={displayOpen}
              onClick={() => {
                setDisplayOpen(!displayOpen);
                setFiltersOpen(false);
              }}
            >
              {t("graph.display")}
            </Button>
          </div>
          <div className="file-graph__zoomtools">
            <ZoomControls zoomIn={() => zoomBy(1.6)} zoomOut={() => zoomBy(1 / 1.6)} resetView={() => fitToNodes()} />
          </div>
        </div>

        {selectedFile && selected !== null && renderCard(selected, selectedFile)}

        {duplicate && selectedFile && (
          <DuplicateModal
            kept={selectedFile.title}
            dropped={duplicate.title}
            busy={removeFile.isPending}
            onClose={() => setDuplicate(null)}
            onConfirm={() =>
              removeFile.mutate(duplicate.id, {
                onSettled: () => setDuplicate(null),
              })
            }
          />
        )}

        {editingSubject && (
          <SubjectModal
            key={editingSubject === "new" ? "new" : editingSubject.id}
            subject={editingSubject === "new" ? null : editingSubject}
            onClose={() => setEditingSubject(null)}
            onSave={(draft) =>
              editingSubject === "new"
                ? subjects.create.mutate(draft)
                : subjects.rename.mutate({ id: editingSubject.id, ...draft })
            }
            onDelete={
              editingSubject === "new" ? undefined : () => subjects.remove.mutate(editingSubject.id)
            }
          />
        )}

        {/*
          A thread under the pointer says which two files it joins and how
          close they are. The figure is drawn on the thread as well; here it
          comes with the names, which the stage can only show one at a time.
        */}
        {activeLink !== null && activeLinkMeta && selected === null && (
          <div className="file-graph__callout">
            <span className="file-graph__callout-pair">
              <span className="file-graph__dot" style={{ background: colorOf(model.links[activeLink].source) }} />
              <span className="file-graph__callout-name">
                {model.data.files[model.links[activeLink].source].title}
              </span>
              <span className="file-graph__dash file-graph__dash--inline" aria-hidden="true" />
              <span className="file-graph__dot" style={{ background: colorOf(model.links[activeLink].target) }} />
              <span className="file-graph__callout-name">
                {model.data.files[model.links[activeLink].target].title}
              </span>
              <span className="file-graph__weight">{Math.round(activeLinkMeta.weight * 100)}%</span>
            </span>
            {activeLinkMeta.reason && (
              <span className="file-graph__callout-reason">{activeLinkMeta.reason}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
