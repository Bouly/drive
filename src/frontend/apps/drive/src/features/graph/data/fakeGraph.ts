/**
 * Demo dataset for the file graph.
 *
 * Until files are parsed and embedded on upload, the graph is fed with this
 * hand-written set: a few thematic clusters of realistic public-sector files,
 * dense links inside each cluster, and a handful of cross-cluster links that
 * stand for the "unexpected connections" the project is about.
 */

export type GraphCluster = {
  id: string;
  label: string;
};

export type GraphFile = {
  id: string;
  title: string;
  mimetype: string;
  /** Bytes. */
  size: number;
  updated_at: string;
  creator: string;
  cluster: string;
};

export type GraphLink = {
  source: string;
  target: string;
  /** 0..1, how close the two files are. */
  weight: number;
  /** "surprise" links join files from different clusters. */
  kind: "semantic" | "surprise";
  reason?: string;
};

export type GraphData = {
  clusters: GraphCluster[];
  files: GraphFile[];
  links: GraphLink[];
};

const MIME = {
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  pdf: "application/pdf",
  png: "image/png",
  jpg: "image/jpeg",
  mp4: "video/mp4",
  zip: "application/zip",
  csv: "text/csv",
  md: "text/markdown",
  folder: "application/x-directory",
} as const;

type Mime = keyof typeof MIME;

const CREATORS = [
  "Camille Roux",
  "Nadia Benali",
  "Julien Morel",
  "Sofia Martins",
  "Théo Lambert",
];

/** [title, extension, size in KB] per cluster, in a stable order. */
const CLUSTERS: { id: string; label: string; files: [string, Mime, number][] }[] = [
  {
    id: "budget",
    label: "Budget 2026",
    files: [
      ["Budget 2026", "folder", 0],
      ["Budget prévisionnel 2026.xlsx", "xlsx", 412],
      ["Note de cadrage budgétaire.docx", "docx", 88],
      ["Arbitrages DG - juillet.pptx", "pptx", 2140],
      ["Exécution budgétaire T2.xlsx", "xlsx", 356],
      ["Subventions associations 2026.csv", "csv", 61],
      ["Courrier DAF - plafond d'emplois.pdf", "pdf", 143],
      ["Synthèse budget - COMEX.pdf", "pdf", 720],
    ],
  },
  {
    id: "marche",
    label: "Marché hébergement cloud",
    files: [
      ["Marché cloud 2026", "folder", 0],
      ["CCTP hébergement cloud.docx", "docx", 264],
      ["Règlement de consultation.docx", "docx", 121],
      ["Grille d'analyse des offres.xlsx", "xlsx", 233],
      ["Offre technique - candidat A.pdf", "pdf", 4820],
      ["Offre technique - candidat B.pdf", "pdf", 3910],
      ["Rapport d'analyse des offres.docx", "docx", 302],
      ["Bordereau des prix.xlsx", "xlsx", 98],
      ["Notification d'attribution.pdf", "pdf", 77],
    ],
  },
  {
    id: "rgpd",
    label: "Conformité RGPD",
    files: [
      ["Registre des traitements.xlsx", "xlsx", 187],
      ["AIPD - téléservice usagers.docx", "docx", 341],
      ["Politique de conservation des données.docx", "docx", 96],
      ["Clauses sous-traitance RGPD.pdf", "pdf", 210],
      ["Procédure violation de données.docx", "docx", 74],
      ["Formation RGPD agents.pptx", "pptx", 5230],
    ],
  },
  {
    id: "rh",
    label: "Ressources humaines",
    files: [
      ["Campagne entretiens 2026", "folder", 0],
      ["Guide de l'entretien professionnel.pdf", "pdf", 512],
      ["Trame entretien annuel.docx", "docx", 45],
      ["Plan de formation 2026.xlsx", "xlsx", 168],
      ["Organigramme direction.png", "png", 1290],
      ["Fiche de poste - chef de projet data.docx", "docx", 58],
      ["Télétravail - charte 2026.pdf", "pdf", 233],
    ],
  },
  {
    id: "produit",
    label: "Refonte du téléservice",
    files: [
      ["Refonte téléservice", "folder", 0],
      ["Cahier des charges fonctionnel.docx", "docx", 388],
      ["Maquettes parcours usager.pdf", "pdf", 6210],
      ["Roadmap produit 2026.pptx", "pptx", 3120],
      ["Résultats tests utilisateurs.xlsx", "xlsx", 144],
      ["Démo sprint 14.mp4", "mp4", 48200],
      ["Architecture cible.png", "png", 940],
      ["Compte rendu comité produit.md", "md", 19],
      ["Backlog export.csv", "csv", 84],
    ],
  },
  {
    id: "com",
    label: "Communication",
    files: [
      ["Kit communication 2026.zip", "zip", 22400],
      ["Charte graphique.pdf", "pdf", 8900],
      ["Visuel réseaux - lancement.jpg", "jpg", 2210],
      ["Communiqué de presse - téléservice.docx", "docx", 52],
      ["Plan de communication interne.pptx", "pptx", 1870],
      ["Photo équipe.jpg", "jpg", 3420],
    ],
  },
  {
    id: "juridique",
    label: "Juridique",
    files: [
      ["Convention de partenariat - région.pdf", "pdf", 310],
      ["Avis juridique - open data.docx", "docx", 127],
      ["Délibération n°2026-14.pdf", "pdf", 88],
      ["Modèle de convention.docx", "docx", 64],
      ["Contentieux - suivi.xlsx", "xlsx", 92],
    ],
  },
];

/** Cross-cluster links: the kind of connection the graph is meant to reveal. */
const SURPRISES: [string, string, number, string][] = [
  [
    "CCTP hébergement cloud.docx",
    "Clauses sous-traitance RGPD.pdf",
    0.82,
    "Le CCTP reprend mot pour mot les clauses de sous-traitance du dossier RGPD.",
  ],
  [
    "Budget prévisionnel 2026.xlsx",
    "Bordereau des prix.xlsx",
    0.74,
    "Les montants du bordereau apparaissent dans la ligne « hébergement » du budget.",
  ],
  [
    "Cahier des charges fonctionnel.docx",
    "AIPD - téléservice usagers.docx",
    0.79,
    "Les deux documents décrivent les mêmes données collectées auprès des usagers.",
  ],
  [
    "Communiqué de presse - téléservice.docx",
    "Roadmap produit 2026.pptx",
    0.68,
    "Le communiqué annonce des fonctionnalités prévues au jalon T3 de la roadmap.",
  ],
  [
    "Fiche de poste - chef de projet data.docx",
    "Architecture cible.png",
    0.57,
    "La fiche de poste cite les briques techniques du schéma d'architecture.",
  ],
  [
    "Convention de partenariat - région.pdf",
    "Subventions associations 2026.csv",
    0.63,
    "La convention engage les subventions listées dans le fichier budgétaire.",
  ],
  [
    "Avis juridique - open data.docx",
    "Politique de conservation des données.docx",
    0.71,
    "L'avis s'appuie sur les durées de conservation définies dans la politique.",
  ],
  [
    "Plan de formation 2026.xlsx",
    "Formation RGPD agents.pptx",
    0.66,
    "Le support de formation correspond à une action inscrite au plan.",
  ],
  [
    "Délibération n°2026-14.pdf",
    "Notification d'attribution.pdf",
    0.6,
    "La délibération autorise la signature du marché notifié.",
  ],
];

/** Deterministic pseudo-random so the demo looks the same on every load. */
const seeded = (seed: number) => () => {
  seed = (seed * 1664525 + 1013904223) % 4294967296;
  return seed / 4294967296;
};

const slug = (title: string) =>
  title
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");

export const buildFakeGraph = (): GraphData => {
  const rand = seeded(42);
  const clusters: GraphCluster[] = [];
  const files: GraphFile[] = [];
  const links: GraphLink[] = [];
  const idByTitle = new Map<string, string>();

  CLUSTERS.forEach((cluster, ci) => {
    clusters.push({ id: cluster.id, label: cluster.label });
    const ids: string[] = [];

    cluster.files.forEach(([title, ext, kb], fi) => {
      const id = slug(title);
      idByTitle.set(title, id);
      ids.push(id);
      const daysAgo = Math.floor(rand() * 120);
      files.push({
        id,
        title,
        mimetype: MIME[ext],
        size: kb * 1024,
        updated_at: new Date(Date.now() - daysAgo * 864e5).toISOString(),
        creator: CREATORS[(ci * 3 + fi) % CREATORS.length],
        cluster: cluster.id,
      });
    });

    // Inside a cluster: a folder (if any) links to everything, and each file
    // links to one or two earlier siblings so the cluster reads as a group.
    const folder = ids.find((id) => files.find((f) => f.id === id)?.mimetype === MIME.folder);
    ids.forEach((id, i) => {
      if (folder && id !== folder) {
        links.push({ source: folder, target: id, weight: 0.5, kind: "semantic" });
      }
      const candidates = ids.slice(0, i).filter((other) => other !== folder);
      const count = Math.min(candidates.length, 1 + Math.floor(rand() * 2));
      for (let k = 0; k < count; k++) {
        const other = candidates.splice(Math.floor(rand() * candidates.length), 1)[0];
        links.push({
          source: other,
          target: id,
          weight: 0.55 + rand() * 0.4,
          kind: "semantic",
        });
      }
    });
  });

  SURPRISES.forEach(([a, b, weight, reason]) => {
    const source = idByTitle.get(a);
    const target = idByTitle.get(b);
    if (source && target) {
      links.push({ source, target, weight, kind: "surprise", reason });
    }
  });

  return { clusters, files, links };
};

export const FOLDER_MIMETYPE = MIME.folder;
