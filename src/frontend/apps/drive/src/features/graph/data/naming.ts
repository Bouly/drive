// Extracted from FileGraph.tsx: reading a name for a group of files off the files themselves.
import { CLUSTER_COLORS } from "./theme";
import { GraphFile } from "./types";

/** Words no group can be named after: too short, too common, or a file type. */
const NAME_STOPWORDS = new Set(
  (
    "le la les de des du un une et en au aux pour par sur dans avec sans ce cet cette ces son sa ses leur " +
    "leurs est sont qui que ont plus tres cela dont ainsi ils elles nous vous votre notre pas mais comme " +
    "image images photo photos montre voit fichier fichiers document documents contenus proches similarite " +
    "scaled final version copie jpeg webp docx xlsx pptx " +
    "deux trois quatre cinq sept huit neuf vingt trente quarante cinquante soixante cent cents mille " +
    "million millions milliard milliers dizaines centaines environ plusieurs autres chaque entre " +
    "janvier fevrier mars avril juin juillet aout septembre octobre novembre decembre lundi mardi " +
    "mercredi jeudi vendredi samedi dimanche " +
    "portant relatif relative relatifs relatives concernant presente present presents susvise " +
    "titre alinea paragraphe point points cas lors dont afin " +
    "modifie modifiee modifies modifiees vigueur ci-dessus ci-apres"
  ).split(" "),
);
/** A word must carry this share of a group, and be this rare elsewhere, to name it. */
const NAME_MIN_SCORE = 0.3;
/**
 * How much a word found in a quoted passage weighs against one found in a
 * file name. A name is chosen by someone, a passage is just a fragment the
 * file happens to contain: "mille" in "mille espèces d'abeilles" should never
 * outrank "abeilles".
 */
export const NAME_PASSAGE_WEIGHT = 0.5;

export const normalize = (text: string) =>
  text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "");


/**
 * The words of a text as [folded, as written] pairs: counting needs the
 * accents gone, but the name shown to someone keeps them.
 */
export const nameWords = (text: string) =>
  (text.replace(/[-_.]+/g, " ").match(/[\p{L}\p{N}'’]+/gu) ?? [])
    .map((raw) => [normalize(raw).split(/['’]/).pop() as string, raw.split(/['’]/).pop() as string])
    .filter(([word]) => word.length > 3 && !NAME_STOPWORDS.has(word) && !/^\d+$/.test(word));

/**
 * A name for each group: the words its files share and the other groups do
 * not use. The titles and the passages the links quote are everything the
 * page knows about what a file says, and it is enough: on a drive of photos
 * and reports about bees and bicycles, this reads "Abeilles · Pollinisateurs"
 * and "Velo · Route" without asking a model to name anything.
 */
export const nameTopics = (
  groups: number[][],
  bags: Map<string, number>[],
  spelling: Map<string, string>,
  files: GraphFile[],
  degree: number[],
) => {
  const shares = groups.map((members) => {
    const seen = new Map<string, number>();
    for (const i of members) {
      for (const [word, weight] of bags[i]) {
        seen.set(word, (seen.get(word) ?? 0) + weight);
      }
    }
    return new Map([...seen].map(([word, total]) => [word, total / members.length] as const));
  });

  return groups.map((members, group) => {
    const best = [...shares[group]]
      .map(([word, share]) => {
        const elsewhere = Math.max(
          0,
          ...shares.map((other, i) => (i === group ? 0 : (other.get(word) ?? 0))),
        );
        return { word, score: share * (1 - elsewhere) };
      })
      .filter(({ score }) => score >= NAME_MIN_SCORE)
      .sort((a, b) => b.score - a.score)
      .slice(0, 2);
    if (best.length) {
      return best
        .map(({ word }) => spelling.get(word) ?? word)
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(" · ");
    }
    // Nothing shared to name it with: the group goes by its busiest file.
    const anchor = [...members].sort((a, b) => degree[b] - degree[a])[0];
    const title = files[anchor].title.replace(/\.[^.]+$/, "");
    return title.length > 24 ? `${title.slice(0, 23)}…` : title;
  });
};

/**
 * The palette slot of a name, or null once the four are taken.
 *
 * The slot comes from the name and not from the size of the group, so a topic
 * keeps its color as long as it keeps its subject: a file arriving no longer
 * swaps two colors around. Past the fourth group the color is dropped rather
 * than reused ‒ two groups sharing a hue would be a lie, a gray one is only
 * silent, and its name still shows in the legend.
 */
export const colorOfName = (label: string, taken: Set<number>) => {
  const slots = CLUSTER_COLORS.light.length;
  if (taken.size >= slots) {
    return null;
  }
  let hash = 0;
  for (let i = 0; i < label.length; i++) {
    hash = (hash * 31 + label.charCodeAt(i)) % 100000007;
  }
  for (let step = 0; step < slots; step++) {
    const slot = (hash + step) % slots;
    if (!taken.has(slot)) {
      taken.add(slot);
      return slot;
    }
  }
  return null;
};
