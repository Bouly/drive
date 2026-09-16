"""
Semantic links of an item (step 5), built only on ``storage``.

Each indexed file is linked to the files closest to it, with the cosine
similarity of the two contents as the weight. The page draws a close pair
short and bright, a distant one long and faint.

Only the closest ones: linking every pair means N² links, which measured at
3 million rows and eleven minutes of rebuilding on 1 700 files, and would be
16 million on 4 000. The neighbours of a file are what the graph shows and
what its groups are read from; the pairs beyond them carry no information a
reader could use.
"""

from django.conf import settings
from django.db.models import Q

from core import models

from graph.models import ItemChunk, ItemLink
from graph.services import storage

# A passage is quoted as the reason of a link, and fetching one costs a
# query: only the closest links carry one.
EVIDENCE_LINKS = 3


def live_files():
    """
    Files that are neither in the trash nor inside a trashed folder.

    ``filter_non_deleted`` is not enough: trashing a folder only marks its
    descendants with ``ancestors_deleted_at``, which is what Drive checks too.
    """
    return models.Item.objects.filter(
        type=models.ItemTypeChoices.FILE, ancestors_deleted_at__isnull=True
    )


def indexed_files(candidates=None):
    """The live files that carry passages, the only ones a link can join."""
    candidates = live_files() if candidates is None else candidates
    return candidates.filter(id__in=ItemChunk.objects.values("item_id"))


def semantic_links(item, candidates):
    """
    The links to store for ``item``: one per closest indexed file.

    Empty when the item has no chunk. Similarities are cosine values, kept
    at zero when negative so a weight always reads as "0 to 1".
    """
    vector = storage.item_vector(item)
    if vector is None:
        return []
    neighbours = storage.item_similarities(
        vector, candidates, exclude_item=item, k=settings.GRAPH_LINKS_PER_FILE
    )
    links = []
    for rank, neighbour in enumerate(neighbours):
        evidence = ""
        if rank < EVIDENCE_LINKS:
            passages = storage.nearest_chunks(
                vector, models.Item.objects.filter(id=neighbour.item_id), k=1
            )
            evidence = passages[0].text[:300] if passages else ""
        similarity = max(0.0, neighbour.similarity)
        links.append(
            {
                "target": neighbour.item_id,
                "weight": round(similarity, 3),
                "kind": ItemLink.Kind.SEMANTIC,
                "reason": f"Contenus proches ({round(similarity * 100)} % de similarité)",
                "evidence": evidence,
            }
        )
    return links


def link_item(item, candidates, **_ignored):
    """Rewrite the links of ``item`` among ``candidates``; returns how many were stored."""
    return storage.replace_links(item, semantic_links(item, candidates))


def relink_around(item, candidates=None):
    """
    Rewrite the links of a file and of the files it is closest to.

    A newcomer only changes the neighbourhood it lands in: its own links, and
    those of the files near enough that it may now be one of their closest.
    Everybody else keeps theirs, which is what makes indexing a file cost the
    same whether the drive holds ten files or four thousand.
    """
    candidates = live_files() if candidates is None else candidates
    links = link_item(item, candidates)
    vector = storage.item_vector(item)
    if vector is None:
        return links
    around = storage.item_similarities(
        vector, candidates, exclude_item=item, k=settings.GRAPH_LINKS_PER_FILE
    )
    for neighbour in models.Item.objects.filter(id__in=[n.item_id for n in around]):
        links += link_item(neighbour, candidates)
    return links


def relink_all(candidates=None):
    """
    Rewrite the links of every indexed file, for a full rebuild.

    Used by the command, not on the path of an upload: it costs one query per
    file. Links touching a file that left the graph (trashed, or emptied of
    its passages) are dropped first, as rewriting only covers what remains.
    """
    candidates = live_files() if candidates is None else candidates
    indexed = list(indexed_files(candidates).values_list("id", flat=True))
    ItemLink.objects.exclude(source_id__in=indexed, target_id__in=indexed).delete()
    links = 0
    for item in models.Item.objects.filter(id__in=indexed).iterator(chunk_size=200):
        links += link_item(item, candidates)
    return links


def forget_item(item):
    """
    Take a file out of the graph: drop its links, then mend the neighbourhood.

    Used when a file goes to the trash. The files that pointed at it lost a
    neighbour and are given their next one instead; the rest of the drive is
    left alone.
    """
    pointing = list(
        ItemLink.objects.filter(target=item).values_list("source_id", flat=True).distinct()
    )
    ItemLink.objects.filter(Q(source=item) | Q(target=item)).delete()
    candidates = live_files().exclude(id=item.id)
    links = 0
    for neighbour in models.Item.objects.filter(id__in=pointing):
        links += link_item(neighbour, candidates)
    return links
