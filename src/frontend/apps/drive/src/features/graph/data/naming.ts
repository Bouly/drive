// Folding text for the search, and giving each subject a colour of its own.
import { CLUSTER_COLORS } from "./theme";

export const normalize = (text: string) =>
  text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "");


/**
 * The palette slot of a subject. Every subject gets one.
 *
 * The slot comes from the name and not from the rank of the subject, so a
 * subject keeps its color as long as it keeps its name: writing a new one no
 * longer swaps the colors of the others around.
 *
 * Past the sixteenth subject the free slots run out and one is reused, so two
 * subjects share a hue. That used to be answered with gray, on the grounds
 * that a shared hue is a lie while a gray one is only silent ‒ but a drive
 * that has written seventeen subjects has said that colors matter to it, and
 * gray is the one answer that tells it nothing at all. Past sixteen, color
 * has stopped naming a subject on its own anyway; the legend still does.
 */
export const colorOfName = (label: string, taken: Set<number>) => {
  const slots = CLUSTER_COLORS.light.length;
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
  // Every hue is spoken for: the name alone decides, and two subjects share.
  return hash % slots;
};
