"""
Semantic links of an item (step 5), built only on ``storage``.

Every indexed file is linked to every other one; what tells a pair apart is
the weight of its link, the cosine similarity of the two contents. The page
draws a close pair short and bright, a distant one long and faint, so the
whole library reads as one web rather than as separate islands.
"""

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
    The links to store for ``item``: one per other indexed file.

    Empty when the item has no chunk. Similarities are cosine values, kept
    at zero when negative so a weight always reads as "0 to 1".
    """
    vector = storage.item_vector(item)
    if vector is None:
        return []
    neighbours = storage.item_similarities(vector, candidates, exclude_item=item)
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


def relink_all(candidates=None):
    """
    Rewrite the links of every indexed file.

    Since each file points at all the others, one file arriving, changing or
    leaving shifts everybody's list: the whole web is rebuilt. Links touching
    a file that left the graph (trashed, or emptied of its passages) are
    dropped first, as rewriting only covers the files that remain.
    """
    candidates = live_files() if candidates is None else candidates
    indexed = list(indexed_files(candidates).values_list("id", flat=True))
    ItemLink.objects.exclude(source_id__in=indexed, target_id__in=indexed).delete()
    links = 0
    for item in models.Item.objects.filter(id__in=indexed):
        links += link_item(item, candidates)
    return links


def forget_item(item):
    """
    Take a file out of the graph: drop its links, then rebuild the others.

    Used when a file goes to the trash, so nothing keeps pointing at it.
    """
    ItemLink.objects.filter(Q(source=item) | Q(target=item)).delete()
    return relink_all(live_files().exclude(id=item.id))
