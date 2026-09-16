// Folding text for the search, and giving each subject a colour of its own.
import { CLUSTER_COLORS } from "./theme";

export const normalize = (text: string) =>
  text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "");


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
